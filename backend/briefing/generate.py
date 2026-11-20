"""Generate: turn the recommended list into a plan that fits the listener's chosen length, then add tones.

1. Group the articles into stories (one event, its articles from every outlet), with the recommender's own
   same-event rule, so timing is per story, pruning never splits a story's sources, and the script covers each
   event once.
2. Fit to the length (timing.py): the listener's selected stories first, then the ranked order. Too much: drop the
   rest. Too little: ask the recommender for its next articles (excluding everything already listed) and fit again,
   until the length is filled or nothing new comes back; then the plan is shorter and says so.
3. Personas: the recommender's ordering for this listener (mostly their past preferences).
4. Matcher (research/matcher.py, unchanged): a tone for every kept article, and which of our top personas suit
   those tones. Hosts = the first 1 (presenter) or 2 (debate) of our order that it accepts.
"""

import logging
from threading import Lock

from briefing.timing import DEFAULT_WPM, FILLED, Story, fit, story_seconds, total_seconds
from recommender.embeddings import from_pgvector
from recommender.hosts import host_affinity, rank_hosts
from recommender.focus import parse_focus
from recommender.recommend import Article, _Index, load_listener, recommend

logger = logging.getLogger(__name__)

MATCHER_CANDIDATES = 5      # top personas the matcher rates
MATCHER_CHUNK = 25          # articles per matcher call: its model mixes up IDs on long lists, so long plans go in
                            # chunks (one retry each); a host must suit every chunk
EXTEND_ROUNDS = 3
EXTEND_BATCH = 30           # articles asked from the recommender per extension round

_matcher = None
_matcher_lock = Lock()


def _get_matcher():
    global _matcher
    with _matcher_lock:
        if _matcher is None:
            from research.matcher import create_matcher
            _matcher = create_matcher()
        return _matcher


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


def choose_hosts(ranked: list[str], accepted: set[str] | None, n: int) -> tuple[list[str], bool]:
    """The first n of our order that the matcher accepted. Returns (hosts, fallback_used)."""
    candidates = ranked[:MATCHER_CANDIDATES]
    if accepted:
        hosts = [p for p in candidates if p in accepted][:n]
        if hosts:
            return hosts, False
    return ranked[:n], True


def _pace(conn, persona_ids: list[str]) -> float:
    if not persona_ids:
        return DEFAULT_WPM
    paces = [p for (p,) in conn.execute("select pace_wpm from public.agent_personas where id = any(%s::uuid[])",
                                        (persona_ids,)).fetchall() if p]
    return sum(paces) / len(paces) if paces else DEFAULT_WPM


def run_matcher(matcher, article_ids: list[str], candidates: list[str]) -> tuple[dict[str, str], set[str], str | None]:
    """Tones for every article and the candidates accepted for all of them. Returns (tones, accepted, error)."""
    tones: dict[str, str] = {}
    accepted = set(candidates)
    errors = []
    for start in range(0, len(article_ids), MATCHER_CHUNK):
        chunk = article_ids[start:start + MATCHER_CHUNK]
        for attempt in (1, 2):
            try:
                result = matcher.match_personas(chunk, candidates, len(candidates))
                tones.update({row["article_id"]: row["desired_tone"] for row in result["articles"]})
                accepted &= set(result["agent_ids"])
                break
            except Exception as err:  # noqa: BLE001 - keep the plan usable; report what failed
                if attempt == 2:
                    errors.append(str(err) or type(err).__name__)
                    logger.warning("matcher failed on %d articles: %s", len(chunk), err)
    return tones, accepted, "; ".join(errors) or None


def generate(conn, user_id: str, article_ids: list[str], selected_article_ids: list[str], minutes: float,
             mode: str = "presenter", focus: str | None = None, matcher=None, more=None) -> dict:
    """`focus` is the listener's focus text, so added articles respect it. `more(listener, excluded_ids, limit)`
    returns the recommender's next article ids (injectable for tests)."""
    listener = load_listener(conn, user_id)
    n_hosts = 2 if mode == "debate" else 1
    selected = set(selected_article_ids)
    ids = list(dict.fromkeys([*selected_article_ids, *article_ids]))

    persona_ids = rank_hosts(conn, listener.vector, host_affinity(conn, listener.user_id),
                             listener.default_persona_id, listener.chosen_persona_ids)
    wpm = _pace(conn, persona_ids[:n_hosts])

    parsed_focus = parse_focus(focus) if focus else None
    more = more or (lambda lst, excluded, limit: recommend(conn, lst, parsed_focus, excluded_article_ids=excluded,
                                                           limit=limit)["article_ids"])
    articles = load_articles(conn, ids)
    kept, _ = fit(group_stories(ids, articles, selected), minutes, wpm, mode)
    added: list[str] = []
    for _ in range(EXTEND_ROUNDS):
        if total_seconds(kept, wpm, mode) >= FILLED * minutes * 60:
            break
        new = [i for i in more(listener, ids + added, EXTEND_BATCH) if i not in articles]
        if not new:
            break
        added += new
        articles.update(load_articles(conn, new))
        current = [i for s in kept for i in s.article_ids] + new
        kept, _ = fit(group_stories(current, articles, selected), minutes, wpm, mode)

    final_ids = [i for s in kept for i in s.article_ids]
    tones: dict[str, str] = {}
    accepted: set[str] | None = None
    matcher_error = None
    if final_ids:
        try:
            tones, accepted, matcher_error = run_matcher(matcher or _get_matcher(), final_ids,
                                                         persona_ids[:MATCHER_CANDIDATES])
        except Exception as err:  # noqa: BLE001 - e.g. no OPENAI_API_KEY for the matcher
            matcher_error = str(err) or type(err).__name__
    hosts, fallback = choose_hosts(persona_ids, accepted, n_hosts)

    wpm = _pace(conn, hosts)
    in_list = set(ids)
    return {
        "article_ids": final_ids,
        "persona_ids": persona_ids,
        "hosts": hosts,
        "hosts_from_matcher": not fallback,
        "tones": tones,
        "segments": [{"article_ids": s.article_ids, "estimated_seconds": round(story_seconds(s, wpm, mode))}
                     for s in kept],
        "estimated_minutes": round(total_seconds(kept, wpm, mode) / 60, 1),
        "requested_minutes": minutes,
        "dropped_article_ids": [i for i in ids if i not in set(final_ids)],
        "added_article_ids": [i for i in final_ids if i not in in_list],
        "matcher_error": matcher_error,
    }
