"""Recommender: the articles that fit a listener, and the hosts that fit those articles.

Output is two lists: article ids and persona ids. Grouping the articles into stories happens downstream; to make
that possible, each chosen event comes with its coverage from other outlets and languages (its "siblings"), right
after it in the list. Events are found on the fly from embeddings, nothing is precomputed.

Returns the listener's top 50 articles (DEFAULT_ARTICLES) whatever the podcast length: the listener picks
must-haves and a length afterwards, and Generate (backend/briefing) fits the list to that time.

Rules, in order of precedence:
1. Hard filters, never broken: the listener's languages, the time window (none unless the focus names one,
   e.g. "last 2 days"), articles they already heard or
   dismissed, and anything matching an exclusion in their focus ("no crypto").
2. Picked articles (pinned_article_ids) always go in, first, in the order picked.
3. With a focus, only matching articles fill the remaining event slots, ranked by the personal score; if too few
   match, the closest other articles follow.
4. Without a focus, 1 slot (2 for long briefings) goes to the most covered event (2+ other outlets), placed after
   the listener's own top pick (before it, with up to 3 slots, when the profile is vague); the rest by personal score.

Personal score = 0.60 · similarity to the listener (the best of their blended profile and each listed interest) + 0.10 · coverage (other outlets on the same event)
+ 0.10 · topic preference + 0.10 · similarity to the listener's default host + 0.10 · recency (over 7 days)
− 0.15 · similarity to what they skipped or disliked (+ 0.5 · similarity to the focus, when there is one).
Events are picked with maximal marginal relevance, so one theme doesn't crowd out the others. A little random noise
(EXPLORATION) is added to the scores first, so each call samples a different list among the close matches.
Every listener is a signed-up user (user_id); there is no anonymous mode.
The listener's vector is built from their profile text, tilted toward the topics they rated on the interest radar
(profiles.interest_levels, 1 somewhat .. 3 very interested), and blended with their listening history once they have one.
OpenAI is used for embeddings only: the listener (once, then cached on the profile), the focus and exclusions.
"""

import re
import time

import numpy as np
from dataclasses import dataclass, field
from datetime import timedelta

from recommender.config import EMBED_MODEL
from recommender.embeddings import cosine, embed_many, embed_query, from_pgvector, normalize, to_pgvector
from recommender.focus import Focus, tsquery_terms, word_patterns
from recommender.hosts import host_affinity, rank_hosts

WEIGHTS = {"listener": 0.60, "coverage": 0.10, "topic_pref": 0.10, "host": 0.10, "recency": 0.10,
           "negative": 0.15}
FOCUS_WEIGHT = 0.5          # with a focus, how much "about the focus" counts on top of the personal score
FOCUS_RECENCY = 0.25        # with a focus, recency counts more: otherwise the focus bonus lifts months-old matches
                            # (54 of 55 picks older than a week came with a focus)
DEFAULT_ARTICLES = 50       # articles returned (stories and their other-outlet coverage); the length of the podcast
                            # is chosen later, when the listener presses Generate (backend/briefing)
RECENCY_DAYS = 7            # recency bonus falls from full (newest article) to 0 over this many days; with no
                            # time window, 0.10 over 7 days cut picks older than a week from 29 % to 1 %
EXPLORATION = 0.02          # random noise added to each score before picking (Gumbel, this scale): the same listener
                            # gets a different, still relevant, sample on each call. 0 = fully deterministic.
                            # Measured on five real listeners (Nov 21): two calls share 48% of their top 10 (66% at
                            # 0.01, 41% at 0.03), and 60% of a top 10 is the noise-free best list (74%, 47%)
MMR_LAMBDA = 0.75           # 1 = relevance only, 0 = diversity only
POOL = 150                  # best-scoring articles considered for coverage, MMR and siblings
SAME_EVENT_MIN = 0.80       # two articles at least this close report the same event (any time apart, unless the
                            # focus names a time range; cross-outlet pairs this close are almost always one event)
COVERAGE_MIN = 0.70         # coverage counts other outlets on the same *story* (looser than SAME_EVENT_MIN: a saga's
                            # articles are different sub-events, ~0.65-0.85 apart); 0.70 is reached by few pairs
ESSENTIAL_COVERAGE = 2 / 3  # the "most covered" slot needs 2+ other outlets on the same story
ESSENTIAL_DAYS = 7          # the most covered stories are looked for among the last week's articles
WEAK_MATCH = 0.40           # a listener whose best personal match is below this has a vague profile ("what happened
                            # in the world"): the most covered stories lead and get up to 3 slots
MAX_FACETS = 10
MAX_SIBLINGS = 4            # other-outlet / other-language articles listed after each chosen article
FOCUS_SIM_MIN = 0.45        # an article whose embedding is this close to the focus text matches it
EXCLUDE_SIM_MIN = 0.50      # ... and this close to an exclusion is removed
RELATED_MIN = 0.35          # below this an article isn't related to the focus: real matches score ~0.45-0.6 against
                            # it, padding like generic market wraps 0.23-0.32
HISTORY_SHARE = 0.5         # weight of listening history vs. profile text in a hybrid vector
RADAR_SHARE = 0.4           # weight of the interest radar vs. the profile text in the onboarding vector: the rated
                            # topics' embeddings, each counted in proportion to its level, so "very interested" pulls
                            # three times as hard as "somewhat"
RISK_WORDS = {1: "very cautious", 2: "cautious", 3: "balanced", 4: "growth-seeking", 5: "speculative"}


class ProfileNotFound(LookupError):
    pass


class NoProfileSignal(ValueError):
    pass


# --------------------------------------------------------------------------------------------------------------
# Listener
# --------------------------------------------------------------------------------------------------------------
@dataclass
class Listener:
    vector: list[float]
    languages: list[str]
    user_id: str | None = None
    negative: list[float] | None = None
    default_persona_id: str | None = None
    consumed_article_ids: list[str] = field(default_factory=list)
    facets: list[list[float]] = field(default_factory=list)  # one vector per interest the listener listed
    chosen_persona_ids: list[str] = field(default_factory=list)  # the three personas picked at sign-up  # one vector per interest the listener listed
    preferences: str = ""


def interest_facets(text: str | None) -> list[str]:
    """The separate interests in a profile ('Currencies, the ECB, inflation and jobs data' -> three). An article is
    matched against each one as well as the blended profile vector, so a secondary interest isn't averaged away."""
    if not text:
        return []
    parts = [" ".join(p.split()).strip(" .:-") for p in re.split(r"[,;\n]|\s+-\s+", text)]
    return list(dict.fromkeys(p for p in parts if len(p) >= 3))[:MAX_FACETS]


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


PROFILE_KEYS = ["interests_text", "listener_goal", "occupation", "country", "risk_appetite", "preferred_language",
                "secondary_languages", "briefing_length_minutes", "interest_embedding", "interest_embedding_source",
                "interest_embedding_model", "past_articles_embedding", "past_article_ids", "default_persona_id",
                "stale", "chosen_persona_ids", "interest_levels"]


def radar_levels(profile: dict) -> dict[str, int]:
    """The topics the listener rated on the interest radar, with their level 1-3."""
    levels = profile.get("interest_levels") or {}
    return {topic: int(level) for topic, level in levels.items() if level in (1, 2, 3)}


def onboarding_vector(profile: dict, embed=embed_query, embed_batch=embed_many) -> list[float] | None:
    """The profile text as a vector, tilted toward the radar: each rated topic's embedding counts in proportion
    to its level. None when the profile has neither text nor a radar."""
    text = profile_text(profile)
    levels = radar_levels(profile)
    vector = normalize(embed(text)) if text else None
    if not levels:
        return vector
    topics = list(levels)
    weights = np.array([levels[t] for t in topics], dtype=float)
    radar = normalize((weights[:, None] * np.array(embed_batch(topics))).sum(axis=0).tolist())
    if vector is None:
        return radar
    return normalize([(1 - RADAR_SHARE) * o + RADAR_SHARE * r for o, r in zip(vector, radar)])


def ensure_user_embedding(conn, user_id: str, embed=embed_query, persist: bool = True, rebuild: bool = False,
                          embed_batch=embed_many) -> tuple[list[float], dict]:
    """The user's interest vector, rebuilt first when missing, from another model, or older than their history
    (or always, with `rebuild`)."""
    row = conn.execute(
        """
        select interests_text, listener_goal, occupation, country, risk_appetite,
               preferred_language::text, secondary_languages::text[], briefing_length_minutes,
               interest_embedding::text, interest_embedding_source, interest_embedding_model,
               past_articles_embedding::text, coalesce(past_article_ids, '{}'), default_persona_id::text,
               coalesce(interest_embedded_at < past_articles_updated_at, false), chosen_persona_ids::text[],
               coalesce(interest_levels, '{}'::jsonb)
        from public.profiles where id = %s
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        raise ProfileNotFound(user_id)
    profile = dict(zip(PROFILE_KEYS, row))

    if not rebuild and profile["interest_embedding"] and profile["interest_embedding_model"] == EMBED_MODEL \
            and not profile["stale"]:
        return from_pgvector(profile["interest_embedding"]), profile

    onboarding = onboarding_vector(profile, embed, embed_batch)
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

    if persist:
        conn.execute(
            "update public.profiles set interest_embedding = %s::extensions.vector, interest_embedding_source = %s, "
            "interest_embedding_model = %s, interest_embedded_at = now() where id = %s",
            (to_pgvector(vector), source, EMBED_MODEL, user_id),
        )
        conn.commit()
    profile["interest_embedding_source"] = source
    return vector, profile


def load_listener(conn, user_id: str, embed=embed_query, embed_batch=embed_many) -> Listener:
    vector, profile = ensure_user_embedding(conn, user_id, embed)
    negative = conn.execute(
        """
        select avg(a.summary_embedding)::text
        from public.user_article_interactions i join public.articles a on a.id = i.article_id
        where i.user_id = %s and i.type in ('skipped', 'disliked') and a.embedding_model = %s
        """,
        (user_id, EMBED_MODEL),
    ).fetchone()[0]
    languages = list(dict.fromkeys([profile["preferred_language"] or "en", *(profile["secondary_languages"] or [])]))
    return Listener(vector=vector, languages=languages,
                    user_id=user_id, negative=from_pgvector(negative) if negative else None,
                    default_persona_id=profile["default_persona_id"],
                    consumed_article_ids=list(profile["past_article_ids"]),
                    facets=[normalize(v) for v in embed_batch(interest_facets(profile["interests_text"]))]
                    if profile["interests_text"] else [],
                    chosen_persona_ids=list(profile["chosen_persona_ids"] or []),
                    preferences=profile_text(profile))


# --------------------------------------------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------------------------------------------
@dataclass
class Article:
    id: str
    publication_id: str
    language: str
    translation_of: str | None
    published_at: object
    sim_listener: float
    sim_negative: float
    sim_host: float
    sim_focus: float
    topic_pref: float
    focus_match: bool
    excluded: bool
    sim_profile: float = 1.0  # similarity to the blended profile vector alone (no facets): measures a vague profile
    embedding: list[float] | None = None
    coverage: float = 0.0     # 0..1, how many other outlets report the same event
    score: float = 0.0


ARTICLE_SELECT = """
    a.id, a.publication_id, a.language::text, a.translation_of, a.published_at,
    LISTENER_SIM,
    coalesce(1 - (a.summary_embedding <=> %(negative)s::extensions.vector), 0),
    coalesce(1 - (a.summary_embedding <=> (select embedding from public.agent_personas where id = %(host)s::uuid)), 0),
    coalesce(1 - (a.summary_embedding <=> %(focus_vec)s::extensions.vector), 0),
    coalesce((select coalesce(max(p.weight) filter (where p.weight > 0), 0)
                     + coalesce(min(p.weight) filter (where p.weight < 0), 0)
              from public.article_category_scores sc
              join public.user_topic_preferences p on p.category_code = sc.category_code and p.user_id = %(uid)s::uuid
              where sc.article_id = coalesce(a.translation_of, a.id) and sc.score >= 10), 0),
    -- focus match: a person/entity named (whole words), the words in headline/people/entities/subject/event/topics,
    -- or an embedding close to the focus text
    (%(has_focus)s and (
        exists (select 1 from unnest(coalesce(a.people, '{}') || coalesce(a.entities, '{}')) n
                where n ~* any(%(term_patterns)s::text[]))
        or a.search_vector @@ to_tsquery('simple', %(focus_tsq)s)
        or 1 - (a.summary_embedding <=> %(focus_vec)s::extensions.vector) >= %(focus_min)s)),
    -- focus exclusion: the same tests against the excluded terms
    (%(has_exclude)s and (
        exists (select 1 from unnest(coalesce(a.people, '{}') || coalesce(a.entities, '{}')) n
                where n ~* any(%(exclude_patterns)s::text[]))
        or a.search_vector @@ to_tsquery('simple', %(exclude_tsq)s)
        or 1 - (a.summary_embedding <=> %(exclude_vec)s::extensions.vector) >= %(exclude_min)s)),
    1 - (a.summary_embedding <=> %(listener)s::extensions.vector)
"""


def _select(listener: Listener) -> str:
    """ARTICLE_SELECT with the listener similarity: the best of the blended profile vector and each interest."""
    terms = ["1 - (a.summary_embedding <=> %(listener)s::extensions.vector)"]
    terms += [f"1 - (a.summary_embedding <=> %(facet{i})s::extensions.vector)" for i in range(len(listener.facets))]
    return ARTICLE_SELECT.replace("LISTENER_SIM", f"greatest({', '.join(terms)})")


def _params(listener: Listener, focus: Focus | None, focus_vec, exclude_vec) -> dict:
    f = focus or Focus("")
    return {**{f"facet{i}": to_pgvector(v) for i, v in enumerate(listener.facets)},
        "listener": to_pgvector(listener.vector),
        "negative": to_pgvector(listener.negative) if listener.negative else None,
        "host": listener.default_persona_id,
        "uid": listener.user_id,
        "focus_vec": to_pgvector(focus_vec) if focus_vec else None,
        "exclude_vec": to_pgvector(exclude_vec) if exclude_vec else None,
        "has_focus": bool(f.terms),
        "term_patterns": word_patterns(f.terms),
        "focus_tsq": tsquery_terms(f.terms),
        "focus_min": FOCUS_SIM_MIN,
        "has_exclude": bool(f.exclude),
        "exclude_patterns": word_patterns(f.exclude),
        "exclude_tsq": tsquery_terms(f.exclude),
        "exclude_min": EXCLUDE_SIM_MIN,
    }


def fetch_articles(conn, listener: Listener, focus: Focus | None, focus_vec, exclude_vec, days: int | None,
                   dismissed: list[str]) -> list[Article]:
    """Every article that passes the hard filters, with the numbers needed to score it. The time window counts
    back from the newest article, so the fixed hackathon dataset behaves like a live feed."""
    params = _params(listener, focus, focus_vec, exclude_vec)
    params.update(model=EMBED_MODEL, languages=listener.languages,
                  days=(focus.since_days if focus and focus.since_days else days),
                  consumed=listener.consumed_article_ids, dismissed=dismissed)
    rows = conn.execute(
        f"""
        with newest as (select max(published_at) as at from public.articles)
        select {_select(listener)}
        from public.articles a cross join newest
        where a.summary_embedding is not null and a.embedding_model = %(model)s
          and a.language::text = any(%(languages)s::text[])
          and (%(days)s::int is null or a.published_at >= newest.at - make_interval(days => %(days)s::int))
          and not (a.id = any(%(consumed)s::text[])) and not (a.id = any(%(dismissed)s::text[]))
          -- a dismissed (or already listed) article excludes its other language versions too
          and not (coalesce(a.translation_of, '') = any(%(dismissed)s::text[]))
          and not exists (select 1 from public.articles d
                          where d.id = any(%(dismissed)s::text[]) and d.translation_of = a.id)
          and not exists (select 1 from public.articles d
                          where d.id = any(%(dismissed)s::text[]) and d.translation_of = a.translation_of)
          and not (coalesce(a.translation_of, '') = any(%(consumed)s::text[]))
        """,
        params,
    ).fetchall()
    return [Article(*row) for row in rows]


def fetch_articles_by_id(conn, listener: Listener, ids: list[str]) -> list[Article]:
    """Picked articles: no hard filters, in the order given."""
    if not ids:
        return []
    params = _params(listener, None, None, None)
    params["ids"] = ids
    rows = conn.execute(f"select {_select(listener)} from public.articles a where a.id = any(%(ids)s::text[]) "
                        f"and a.summary_embedding is not null", params).fetchall()
    by_id = {row[0]: Article(*row) for row in rows}
    return [by_id[i] for i in dict.fromkeys(ids) if i in by_id]


def load_embeddings(conn, articles: list[Article]) -> None:
    missing = [a.id for a in articles if a.embedding is None]
    if missing:
        found = dict(conn.execute("select id, summary_embedding::text from public.articles where id = any(%s)",
                                  (missing,)).fetchall())
        for a in articles:
            if a.embedding is None and a.id in found:
                a.embedding = from_pgvector(found[a.id])


_COVERAGE_CACHE: dict[tuple[str, float | None], tuple[float, int]] = {}  # (article, window) -> (time, outlets)
COVERAGE_TTL = 600  # seconds; coverage is the same for every listener, so it is computed once and shared


def load_coverage(conn, articles: list[Article], event_hours: float | None = None) -> None:
    """How many other outlets report the same story, found on the fly: nearest neighbours (within event_hours when
    the focus names a time range, otherwise any time) whose embedding is at least COVERAGE_MIN close, counted by
    distinct publication. 0 → 0, 3+ other outlets → 1. Cached for COVERAGE_TTL seconds."""
    now = time.monotonic()
    # A translation shares its original's coverage (otherwise the original would count as "another outlet").
    keys = {a.id: a.translation_of or a.id for a in articles}
    missing = list({k for k in keys.values() if now - _COVERAGE_CACHE.get((k, event_hours), (-1e9, 0))[0] > COVERAGE_TTL})
    if missing:
        rows = dict(conn.execute(
            """
            select a.id, count(distinct n.publication_id) filter (where n.publication_id <> a.publication_id)
            from public.articles a
            cross join lateral (
                select b.publication_id, 1 - (b.summary_embedding <=> a.summary_embedding) as sim
                from public.articles b
                where b.id <> a.id and b.translation_of is null and b.embedding_model = a.embedding_model
                  and (%(window)s::float is null or abs(extract(epoch from b.published_at - a.published_at)) <= %(window)s)
                order by b.summary_embedding <=> a.summary_embedding
                limit 30
            ) n
            where a.id = any(%(ids)s) and n.sim >= %(min)s
            group by a.id
            """,
            {"ids": missing, "window": event_hours * 3600 if event_hours else None, "min": COVERAGE_MIN},
        ).fetchall())
        if len(_COVERAGE_CACHE) > 50_000:
            _COVERAGE_CACHE.clear()
        for aid in missing:
            _COVERAGE_CACHE[(aid, event_hours)] = (now, rows.get(aid, 0))
    for a in articles:
        a.coverage = min(1.0, _COVERAGE_CACHE[(keys[a.id], event_hours)][1] / 3)


# --------------------------------------------------------------------------------------------------------------
# Scoring and selection (pure: testable without a database)
# --------------------------------------------------------------------------------------------------------------
def score(articles: list[Article], has_focus: bool = False) -> None:
    if not articles:
        return
    newest = max(a.published_at for a in articles)
    for a in articles:
        age_days = (newest - a.published_at) / timedelta(days=1)
        recency = max(0.0, 1 - age_days / RECENCY_DAYS)
        a.score = (WEIGHTS["listener"] * a.sim_listener + WEIGHTS["coverage"] * a.coverage
                   + WEIGHTS["topic_pref"] * a.topic_pref + WEIGHTS["host"] * a.sim_host
                   + (FOCUS_RECENCY if has_focus else WEIGHTS["recency"]) * recency
                   - WEIGHTS["negative"] * a.sim_negative
                   + (FOCUS_WEIGHT * a.sim_focus if has_focus else 0.0))


def explore(articles: list[Article], rng: np.random.Generator, scale: float = EXPLORATION) -> None:
    """Perturb scores with Gumbel noise, so picking the best is like sampling each article with probability
    proportional to exp(score / scale): the strong matches still win most of the time, close ones trade places."""
    if scale <= 0 or not articles:
        return
    for a, noise in zip(articles, rng.gumbel(-np.euler_gamma, 1.0, len(articles))):
        a.score += scale * noise


def same_event(a: Article, b: Article, event_hours: float | None = None) -> bool:
    """Translations of each other, or close embeddings (published within event_hours of each other, if given)."""
    if a.translation_of == b.id or b.translation_of == a.id or (a.translation_of and a.translation_of == b.translation_of):
        return True
    if a.embedding is None or b.embedding is None:
        return False
    hours = abs((a.published_at - b.published_at) / timedelta(hours=1))
    return (event_hours is None or hours <= event_hours) and cosine(a.embedding, b.embedding) >= SAME_EVENT_MIN


class _Index:
    """The pool's embeddings as one normalized matrix, for fast similarity and same-event checks."""

    def __init__(self, pool: list[Article]):
        self.pool = [a for a in pool if a.embedding is not None]
        matrix = (np.array([a.embedding for a in self.pool], dtype=np.float32) if self.pool
                  else np.zeros((0, 1), dtype=np.float32))
        self.matrix = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
        self.keys = np.array([a.translation_of or a.id for a in self.pool], dtype=object)
        self.hours = np.array([a.published_at.timestamp() / 3600 for a in self.pool])
        self.position = {a.id: i for i, a in enumerate(self.pool)}

    def vector(self, a: Article) -> np.ndarray | None:
        if a.id in self.position:
            return self.matrix[self.position[a.id]]
        if a.embedding is None:
            return None
        v = np.asarray(a.embedding, dtype=np.float32)
        return v / max(float(np.linalg.norm(v)), 1e-12)

    def same_event(self, a: Article, event_hours: float | None = None) -> np.ndarray:
        """Mask of pool articles reporting the same event as `a` (translation, or close embedding in time)."""
        same = self.keys == (a.translation_of or a.id)
        v = self.vector(a)
        if v is not None and len(self.pool):
            close = self.matrix @ v >= SAME_EVENT_MIN
            if event_hours is not None:
                close &= np.abs(self.hours - a.published_at.timestamp() / 3600) <= event_hours
            same |= close
        return same


def _mmr(pool: list[Article], count: int, chosen: list[Article], event_hours: float | None = None) -> list[Article]:
    """Pick `count` articles by maximal marginal relevance, one per event, against everything already chosen."""
    index = _Index(pool)
    if not index.pool or count <= 0:
        return []
    scores = np.array([a.score for a in index.pool])
    alive = np.ones(len(index.pool), dtype=bool)
    overlap = np.zeros(len(index.pool))
    for c in chosen:
        v = index.vector(c)
        if v is not None:
            overlap = np.maximum(overlap, index.matrix @ v)
        alive &= ~index.same_event(c, event_hours)
    picked: list[Article] = []
    while len(picked) < count and alive.any():
        value = np.where(alive, MMR_LAMBDA * scores - (1 - MMR_LAMBDA) * overlap, -np.inf)
        best = index.pool[int(np.argmax(value))]
        picked.append(best)
        overlap = np.maximum(overlap, index.matrix @ index.vector(best))
        alive &= ~index.same_event(best, event_hours)
        alive[index.position[best.id]] = False
    return picked


def select_events(articles: list[Article], pinned: list[Article], count: int, has_focus: bool,
                  primary_language: str, event_hours: float | None = None) -> list[Article]:
    """One article per event (the listener's first language when it exists), in listening order."""
    pinned_ids = {a.id for a in pinned}
    pool = sorted((a for a in articles if a.id not in pinned_ids and not a.excluded), key=lambda a: a.score,
                  reverse=True)
    chosen = list(pinned)
    slots = max(0, count - len(chosen))

    if has_focus:
        matches = [a for a in pool if a.focus_match]
        chosen += _mmr(matches, slots, chosen, event_hours)
        if len(chosen) < count:
            related = sorted((a for a in pool if not a.focus_match and a.sim_focus >= RELATED_MIN),
                             key=lambda a: a.sim_focus, reverse=True)
            chosen += _mmr(related, count - len(chosen), chosen, event_hours)
    else:
        best_match = max((a.sim_profile for a in pool), default=0.0)
        vague = best_match < WEAK_MATCH
        essential_slots = min(slots, 3 if vague else 2 if count >= 8 else 1)
        essentials: list[Article] = []
        for a in sorted((a for a in pool if a.coverage >= ESSENTIAL_COVERAGE), key=lambda a: (a.coverage, a.score),
                        reverse=True):
            if len(essentials) == essential_slots:
                break
            if not any(same_event(a, c, event_hours) for c in chosen + essentials):
                essentials.append(a)
        personal = _mmr([a for a in pool if a not in essentials], slots - len(essentials), chosen + essentials,
                        event_hours)
        # Lead with the listener's own top story, then the most covered one(s), then the rest; for a vague profile
        # the most covered stories lead.
        chosen += essentials + personal if vague else personal[:1] + essentials + personal[1:]
    return chosen


def prefer_language(articles: list[Article], primary: str) -> list[Article]:
    """One version per translated article: the listener's first language when it exists, carrying the best score
    of its versions (a listener whose history is Dutch still hears the English original if English comes first)."""
    groups: dict[str, list[Article]] = {}
    for a in articles:
        groups.setdefault(a.translation_of or a.id, []).append(a)
    kept = []
    for versions in groups.values():
        best = max(versions, key=lambda a: a.score)
        chosen = next((a for a in versions if a.language == primary), best)
        chosen.score = max(chosen.score, best.score)
        chosen.focus_match = any(a.focus_match for a in versions)
        chosen.excluded = any(a.excluded for a in versions)
        kept.append(chosen)
    return kept


def siblings(event: Article, pool: list[Article], taken: set[str], event_hours: float | None = None,
             index: "_Index | None" = None) -> list[Article]:
    """Other coverage of the same event (other outlets, other listener languages), best first."""
    index = index or _Index(pool)
    mask = index.same_event(event, event_hours)
    found = [a for a, same in zip(index.pool, mask) if same and a.id not in taken and a.id != event.id and not a.excluded]
    return sorted(found, key=lambda a: a.score, reverse=True)[:MAX_SIBLINGS]


# --------------------------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------------------------
def recommend(conn, listener: Listener, focus: Focus | None = None, pinned_article_ids: list[str] = (),
              excluded_article_ids: list[str] = (), days: int | None = None, limit: int | None = None,
              embed=embed_query, exploration: float = EXPLORATION, seed: int | None = None) -> dict:
    """`exploration` sets how much the picks vary between calls (0 = always the same list); `seed` repeats a draw."""
    focus_vec = normalize(embed(focus.positive_text)) if focus and focus.terms else None
    exclude_vec = normalize(embed(", ".join(focus.exclude))) if focus and focus.exclude else None
    has_focus = bool(focus and focus.terms)

    # Any time frame unless the focus text names one ("last 2 days") or the caller passes `days`.
    window_days = focus.since_days if focus and focus.since_days else days
    event_hours = window_days * 24 if window_days else None
    articles = fetch_articles(conn, listener, focus, focus_vec, exclude_vec, days, list(excluded_article_ids))
    pinned = fetch_articles_by_id(conn, listener, list(pinned_article_ids))
    score(articles + pinned, has_focus)
    articles = prefer_language(articles, listener.languages[0])
    pool = sorted(articles, key=lambda a: a.score, reverse=True)[:POOL]
    if has_focus:  # make sure every focus match can be considered, not just the top of the personal ranking
        pool += [a for a in articles if a.focus_match and a not in pool][:POOL]
    load_coverage(conn, pool + pinned, event_hours)
    if not has_focus:  # the most covered stories of the last week are candidates for every listener, however generic
        newest = max((a.published_at for a in articles), default=None)
        in_pool = {id(a) for a in pool}
        week = [a for a in articles if id(a) not in in_pool and newest - a.published_at <= timedelta(days=ESSENTIAL_DAYS)]
        load_coverage(conn, week, event_hours)
        pool += [a for a in week if a.coverage >= ESSENTIAL_COVERAGE]
    score(pool + pinned, has_focus)
    explore(pool, np.random.default_rng(seed), exploration)  # picked articles stay first whatever the noise
    load_embeddings(conn, pool + pinned)

    wanted = max(limit or DEFAULT_ARTICLES, len(pinned))
    events = select_events(pool, pinned, wanted, has_focus, listener.languages[0], event_hours)

    # Each story's article followed by its coverage from other outlets, until `wanted` articles (picked ones kept).
    index = _Index(pool + pinned)
    article_ids = [a.id for a in pinned]
    pinned_ids = set(article_ids)
    taken = {a.id for a in events}
    for event in events:
        if len(article_ids) >= wanted:
            break
        if event.id not in pinned_ids:
            article_ids.append(event.id)
        for sibling in siblings(event, pool, taken, event_hours, index):
            if len(article_ids) >= wanted:
                break
            article_ids.append(sibling.id)
            taken.add(sibling.id)

    # Every persona, ordered mostly by the listener's past reactions to each (see hosts.py); the article-dependent
    # tone check lives in research/matcher.py, separately.
    persona_ids = rank_hosts(conn, listener.vector, host_affinity(conn, listener.user_id), listener.default_persona_id,
                             listener.chosen_persona_ids)
    return {"article_ids": article_ids, "persona_ids": persona_ids}

