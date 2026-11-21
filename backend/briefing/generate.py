"""Generate: turn the recommended list into a plan that fits the listener's chosen length, then pick the hosts.

1. Group the articles into stories (one event, its articles from every outlet), with the recommender's own
   same-event rule, so timing is per story, pruning never splits a story's sources, and the script covers each
   event once.
2. Fit to the length (timing.py): the listener's selected stories first, then the ranked order. Too much: drop the
   rest. Too little: ask the recommender for its next articles (excluding everything already listed) and fit again,
   until the length is filled or nothing new comes back; then the plan is shorter and says so.
3. Personas: the recommender's ordering for this listener (mostly their past preferences).
4. Tones and hosts, without an LLM: each article's tone is preset in articles.desired_tone. Hosts = the first 1 (presenter)
   or 2 (debate) personas in our order whose humor suits the most sensitive tone in the plan (MAX_HUMOR).
"""

import logging

import psycopg

from briefing.timing import DEFAULT_WPM, FILLED, Story, fit, story_seconds, total_seconds
from recommender.embeddings import from_pgvector
from recommender.hosts import host_affinity, rank_hosts
from recommender.focus import parse_focus
from recommender.recommend import Article, _Index, load_listener, recommend
from research.matcher import ARTICLE_TONES

logger = logging.getLogger(__name__)

EXTEND_ROUNDS = 3
EXTEND_BATCH = 30           # articles asked from the recommender per extension round
# A host whose humor_level (1-5) is above this never presents a plan with a story in that tone: no jokes on a death.
MAX_HUMOR = {"somber": 2, "serious": 3, "cautious": 4}


class UnknownSelectedArticles(ValueError):
    pass


def load_articles(conn, ids: list[str]) -> dict[str, tuple[Article, int]]:
    rows = conn.execute(
        """
        select id, publication_id, language::text, translation_of, published_at, summary_embedding::text,
               coalesce(word_count, 0)
        from public.articles where id = any(%s)
        """,
        (ids,),
    ).fetchall()
    out = {}
    for aid, pub, lang, tr, at, emb, words in rows:
        article = Article(aid, pub, lang, tr, at, 0.0, 0.0, 0.0, 0.0, 0.0, False, False,
                          embedding=from_pgvector(emb) if emb else None)
        out[aid] = (article, int(words))
    return out


def group_stories(ids: list[str], articles: dict[str, tuple[Article, int]], selected: set[str],
                  event_hours: float | None = None) -> list[Story]:
    """Stories in the order of their first article. Each article joins the first earlier story it reports on."""
    ordered = [articles[i][0] for i in dict.fromkeys(ids) if i in articles]
    index = _Index(ordered)
    stories: list[Story] = []
    lead_of: list[Article] = []
    counted: list[set[str]] = []
    for article in ordered:
        same = index.same_event(article, event_hours)
        home = next((k for k, lead in enumerate(lead_of)
                     if lead.id in index.position and same[index.position[lead.id]]), None)
        if home is None:
            stories.append(Story(article_ids=[]))
            lead_of.append(article)
            counted.append(set())
            home = len(stories) - 1
        story = stories[home]
        story.article_ids.append(article.id)
        story.outlets.add(article.publication_id)
        story.selected = story.selected or article.id in selected
        key = article.translation_of or article.id  # a translation adds no new material
        if key not in counted[home]:
            counted[home].add(key)
            story.words += articles[article.id][1]
    return stories


def _pace(conn, persona_ids: list[str]) -> float:
    if not persona_ids:
        return DEFAULT_WPM
    paces = [p for (p,) in conn.execute("select pace_wpm from public.agent_personas where id = any(%s::uuid[])",
                                        (persona_ids,)).fetchall() if p]
    return sum(paces) / len(paces) if paces else DEFAULT_WPM


def load_tones(conn, ids: list[str]) -> dict[str, str]:
    """Preset tone per article (articles.desired_tone). Articles without a known tone (tones.json) are left out; the
    hosts then deliver them neutrally."""
    if not ids:
        return {}
    try:
        with conn.transaction():  # savepoint: a database without the column yet must not break the plan
            rows = conn.execute("select id, desired_tone from public.articles "
                                "where id = any(%s) and desired_tone is not null", (ids,)).fetchall()
    except psycopg.errors.UndefinedColumn:
        logger.warning("public.articles.desired_tone is missing; planning without tones")
        return {}
    unknown = {tone for _, tone in rows if tone not in ARTICLE_TONES}
    if unknown:
        logger.warning("Ignoring tones missing from tones.json: %s", ", ".join(sorted(unknown)))
    return {i: tone for i, tone in rows if tone in ARTICLE_TONES}


def pick_hosts(conn, ranked: list[str], tones: dict[str, str], n: int) -> list[str]:
    """The first n personas in the listener's order whose humor suits every tone in the plan."""
    limit = min((MAX_HUMOR.get(t, 5) for t in tones.values()), default=5)
    humor = dict(conn.execute("select id::text, humor_level from public.agent_personas where id = any(%s::uuid[])",
                              (ranked,)).fetchall()) if ranked else {}
    return [i for i in ranked if (humor.get(i) or 1) <= limit][:n]


def generate(conn, user_id: str, article_ids: list[str], selected_article_ids: list[str], minutes: float,
             mode: str = "presenter", focus: str | None = None, *,
             days: int | None = None, excluded_article_ids: list[str] = ()) -> dict:
    listener = load_listener(conn, user_id)
    n_hosts = 2 if mode == "debate" else 1
    selected = set(selected_article_ids)
    excluded = set(excluded_article_ids) - selected
    ids = list(dict.fromkeys([*selected_article_ids, *(i for i in article_ids if i not in excluded)]))

    persona_ids = rank_hosts(conn, listener.vector, host_affinity(conn, listener.user_id),
                             listener.default_persona_id, listener.chosen_persona_ids)
    moderator = conn.execute("select id::text from public.agent_personas where slug = 'main-host'").fetchone()
    moderator_id = moderator[0] if moderator else None
    candidates = [i for i in persona_ids if mode != "debate" or i != moderator_id]
    wpm = _pace(conn, candidates[:n_hosts])

    parsed_focus = parse_focus(focus) if focus else None
    window_days = (parsed_focus.since_days if parsed_focus else None) or days
    event_hours = window_days * 24 if window_days else None
    articles = load_articles(conn, ids)
    if missing := selected - articles.keys():
        raise UnknownSelectedArticles("Unknown selected articles: " + ", ".join(sorted(missing)))
    kept, _ = fit(group_stories(ids, articles, selected, event_hours), minutes, wpm, mode)
    added: list[str] = []
    for _ in range(EXTEND_ROUNDS):
        if total_seconds(kept, wpm, mode) >= FILLED * minutes * 60:
            break
        new = recommend(conn, listener, parsed_focus, excluded_article_ids=list(excluded) + ids + added,
                        days=days, limit=EXTEND_BATCH)["article_ids"]
        if not new:
            break
        added += new
        articles.update(load_articles(conn, new))
        current = [i for s in kept for i in s.article_ids] + new
        kept, _ = fit(group_stories(current, articles, selected, event_hours), minutes, wpm, mode)

    final_ids = [i for s in kept for i in s.article_ids]
    tones = load_tones(conn, final_ids)
    hosts = pick_hosts(conn, candidates, tones, n_hosts)

    wpm = _pace(conn, hosts or ([moderator_id] if moderator_id else []))
    in_list = set(ids)
    return {
        "article_ids": final_ids,
        "profile": listener.preferences[:3000] or "Explain what changed and why it matters.",
        "persona_ids": persona_ids,
        "hosts": hosts,
        "tones": tones,
        "segments": [{"article_ids": s.article_ids, "estimated_seconds": round(story_seconds(s, wpm, mode))}
                     for s in kept],
        "estimated_minutes": round(total_seconds(kept, wpm, mode) / 60, 1),
        "requested_minutes": minutes,
        "dropped_article_ids": [i for i in ids if i not in set(final_ids)],
        "added_article_ids": [i for i in final_ids if i not in in_list],
        "untoned_article_ids": [i for i in final_ids if i not in tones],
    }
