"""Recommender: pick the articles that fit a listener.

1. Get the listener's vector. A saved user without an up-to-date `interest_embedding` gets one now, from their
   profile text (and their listening history, when they have one). A live wildcard profile is embedded on the fly
   and nothing is stored.
2. Fetch the nearest candidate articles from pgvector, in the listener's languages and time window, skipping
   articles they've already consumed.
3. Re-rank: similarity plus a small recency boost, then pick with maximal marginal relevance (MMR) so near-
   duplicates (the same event from several outlets, or an Invezz translation) don't fill the briefing.
4. Return as many as fit the briefing length.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from research.config import EMBED_MODEL
from research.embeddings import cosine, embed_query, from_pgvector, normalize, to_pgvector

MINUTES_PER_STORY = 1.5
MIN_ITEMS, MAX_ITEMS = 3, 15
CANDIDATES = 80
RECENCY_WEIGHT = 0.05      # newest article gets +0.05, falling to 0 over RECENCY_DAYS
RECENCY_DAYS = 3
MMR_LAMBDA = 0.75          # 1 = relevance only, 0 = diversity only
DUPLICATE_SIMILARITY = 0.9  # two articles at least this close are treated as the same story
HISTORY_SHARE = 0.5         # weight of listening history vs. profile text in a hybrid vector
RISK_WORDS = {1: "very cautious", 2: "cautious", 3: "balanced", 4: "growth-seeking", 5: "speculative"}


class ProfileNotFound(LookupError):
    pass


class NoProfileSignal(ValueError):
    pass


@dataclass
class Candidate:
    id: str
    headline: str
    permalink: str
    published_at: datetime
    publication: str
    language: str
    translation_of: str | None
    main_subject: str | None
    event_type: str | None
    topics: list[str]
    people: list[str]
    embedding: list[float] = field(repr=False)
    similarity: float
    score: float = 0.0

    def as_result(self) -> dict:
        return {
            "id": self.id, "headline": self.headline, "permalink": self.permalink,
            "published_at": self.published_at.isoformat(), "publication": self.publication,
            "language": self.language, "similarity": round(self.similarity, 4), "score": round(self.score, 4),
            "main_subject": self.main_subject, "event_type": self.event_type,
            "topics": self.topics, "people": self.people,
        }


def profile_text(profile: dict) -> str:
    """The English text a listener's onboarding vector is made from."""
    parts = []
    if profile.get("interests_text"):
        parts.append(f"Interests: {profile['interests_text'].strip()}")
    if profile.get("listener_goal"):
        parts.append(f"Goal: {profile['listener_goal'].strip()}")
    if profile.get("occupation"):
        parts.append(f"Occupation: {profile['occupation'].strip()}")
    if profile.get("country"):
        parts.append(f"Based in: {profile['country']}")
    if profile.get("risk_appetite") in RISK_WORDS:
        parts.append(f"Risk appetite: {RISK_WORDS[profile['risk_appetite']]}")
    return "\n".join(parts)


def story_count(minutes: int | None) -> int:
    return max(MIN_ITEMS, min(MAX_ITEMS, round((minutes or 10) / MINUTES_PER_STORY)))


def ensure_user_embedding(conn, user_id: str, embed=embed_query) -> tuple[list[float], dict]:
    """Return the user's interest vector, creating it first if it is missing or from another model."""
    row = conn.execute(
        """
        select interests_text, listener_goal, occupation, country, risk_appetite,
               preferred_language::text, secondary_languages::text[], briefing_length_minutes,
               interest_embedding::text, interest_embedding_source, interest_embedding_model,
               past_articles_embedding::text, coalesce(past_article_ids, '{}')
        from public.profiles where id = %s
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        raise ProfileNotFound(user_id)
    keys = ["interests_text", "listener_goal", "occupation", "country", "risk_appetite", "preferred_language",
            "secondary_languages", "briefing_length_minutes", "interest_embedding", "interest_embedding_source",
            "interest_embedding_model", "past_articles_embedding", "past_article_ids"]
    profile = dict(zip(keys, row))

    if profile["interest_embedding"] and profile["interest_embedding_model"] == EMBED_MODEL:
        return from_pgvector(profile["interest_embedding"]), profile

    text = profile_text(profile)
    onboarding = normalize(embed(text)) if text else None
    history = normalize(from_pgvector(profile["past_articles_embedding"])) \
        if profile["past_articles_embedding"] else None
    if onboarding and history:
        vector = normalize([(1 - HISTORY_SHARE) * o + HISTORY_SHARE * h for o, h in zip(onboarding, history)])
        source = "hybrid"
    elif onboarding:
        vector, source = onboarding, "onboarding"
    elif history:
        vector, source = history, "behavior"
    else:
        raise NoProfileSignal("The profile has no interests, goal or occupation and no listening history yet.")

    conn.execute(
        "update public.profiles set interest_embedding = %s::extensions.vector, interest_embedding_source = %s, "
        "interest_embedding_model = %s, interest_embedded_at = now() where id = %s",
        (to_pgvector(vector), source, EMBED_MODEL, user_id),
    )
    conn.commit()
    profile["interest_embedding_source"] = source
    return vector, profile


def fetch_candidates(conn, vector: list[float], languages: list[str], days: int | None,
                     exclude_ids: list[str], limit: int = CANDIDATES) -> list[Candidate]:
    """Nearest articles by cosine distance, embedded with the current model, in the listener's languages.
    The time window counts back from the newest article, so the fixed hackathon dataset behaves like a live feed."""
    rows = conn.execute(
        """
        with newest as (select max(published_at) as at from public.articles)
        select a.id, a.headline, a.permalink, a.published_at, pub.name, a.language::text, a.translation_of,
               a.main_subject, a.event_type, coalesce(a.topics, '{}'), coalesce(a.people, '{}'),
               a.summary_embedding::text,
               1 - (a.summary_embedding <=> %(q)s::extensions.vector) as similarity
        from public.articles a
        join public.publications pub on pub.id = a.publication_id
        cross join newest
        where a.summary_embedding is not null
          and a.embedding_model = %(model)s
          and a.language::text = any(%(languages)s)
          and (%(days)s::int is null or a.published_at >= newest.at - make_interval(days => %(days)s::int))
          and not (a.id = any(%(exclude)s))
        order by a.summary_embedding <=> %(q)s::extensions.vector
        limit %(limit)s
        """,
        {"q": to_pgvector(vector), "model": EMBED_MODEL, "languages": languages, "days": days,
         "exclude": exclude_ids, "limit": limit},
    ).fetchall()
    return [Candidate(*r[:11], embedding=from_pgvector(r[11]), similarity=float(r[12])) for r in rows]


def rerank(candidates: list[Candidate], count: int) -> list[Candidate]:
    """Score by similarity + recency, then choose with MMR and drop same-story duplicates and translations."""
    if not candidates:
        return []
    newest = max(c.published_at for c in candidates)
    for c in candidates:
        age_days = (newest - c.published_at) / timedelta(days=1)
        c.score = c.similarity + RECENCY_WEIGHT * max(0.0, 1 - age_days / RECENCY_DAYS)

    picked: list[Candidate] = []
    remaining = sorted(candidates, key=lambda c: c.score, reverse=True)
    while remaining and len(picked) < count:
        best, best_value = None, float("-inf")
        for c in remaining:
            overlap = max((cosine(c.embedding, p.embedding) for p in picked), default=0.0)
            value = MMR_LAMBDA * c.score - (1 - MMR_LAMBDA) * overlap
            if value > best_value:
                best, best_value = c, value
        remaining.remove(best)
        picked_ids = {p.id for p in picked}
        is_translation = best.translation_of in picked_ids or any(p.translation_of == best.id for p in picked)
        is_duplicate = any(cosine(best.embedding, p.embedding) >= DUPLICATE_SIMILARITY for p in picked)
        if not (is_translation or is_duplicate):
            picked.append(best)
    return picked


def recommend_for_user(conn, user_id: str, minutes: int | None = None, days: int | None = 7,
                       embed=embed_query) -> dict:
    vector, profile = ensure_user_embedding(conn, user_id, embed)
    languages = list({profile["preferred_language"] or "en", *(profile["secondary_languages"] or [])})
    count = story_count(minutes or profile["briefing_length_minutes"])
    candidates = fetch_candidates(conn, vector, languages, days, list(profile["past_article_ids"]))
    return {
        "listener": {"user_id": user_id, "embedding_source": profile["interest_embedding_source"],
                     "languages": sorted(languages)},
        "embedding_model": EMBED_MODEL,
        "articles": [c.as_result() for c in rerank(candidates, count)],
    }


def recommend_for_text(conn, text: str, languages: list[str], minutes: int | None = None, days: int | None = 7,
                       embed=embed_query) -> dict:
    """Live wildcard: the listener is just a description; nothing is stored."""
    vector = normalize(embed(text))
    candidates = fetch_candidates(conn, vector, languages, days, [])
    return {
        "listener": {"wildcard": True, "embedding_source": "onboarding", "languages": sorted(languages)},
        "embedding_model": EMBED_MODEL,
        "articles": [c.as_result() for c in rerank(candidates, story_count(minutes))],
    }
