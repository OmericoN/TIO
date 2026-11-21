"""Offline check of the recommender on the seeded listeners.

For each user with interactions: hide their latest HOLDOUT share of positive interactions (played, completed,
liked, shared, source_opened), rebuild their vector from their profile text, interest radar and the remaining history only, rank
every candidate article, and count how many hidden articles land in the top k. Compares the full personal score
with similarity alone, so the presentation can show what coverage, topic preferences and hosts add.

    cd backend && uv run python -m recommender.evaluate
"""

from recommender import recommend as rec
from recommender.db import connect
from recommender.embeddings import embed_many, from_pgvector, normalize

HOLDOUT = 0.3
KS = (10, 20, 50)
POSITIVE = ("played", "completed", "liked", "shared", "source_opened")


def _split(conn, user_id: str) -> tuple[list[str], list[str]]:
    rows = conn.execute(
        """
        select article_id from (
          select article_id, max(created_at) as at from public.user_article_interactions
          where user_id = %s and type::text = any(%s) group by article_id) x
        order by at
        """,
        (user_id, list(POSITIVE)),
    ).fetchall()
    ids = [r[0] for r in rows]
    cut = max(1, round(len(ids) * (1 - HOLDOUT)))
    return ids[:cut], ids[cut:]


def _listener(conn, user_id: str, train: list[str]) -> rec.Listener:
    profile = dict(zip(rec.PROFILE_KEYS, conn.execute(
        """
        select interests_text, listener_goal, occupation, country, risk_appetite, preferred_language::text,
               secondary_languages::text[], briefing_length_minutes, null, null, null, null, '{}'::text[],
               default_persona_id::text, false, chosen_persona_ids::text[], coalesce(interest_levels, '{}'::jsonb)
        from public.profiles where id = %s
        """, (user_id,)).fetchone()))
    vector = rec.onboarding_vector(profile)
    history = conn.execute("select avg(summary_embedding)::text from public.articles where id = any(%s)",
                           (train,)).fetchone()[0]
    if history:
        h = normalize(from_pgvector(history))
        vector = normalize([(1 - rec.HISTORY_SHARE) * o + rec.HISTORY_SHARE * x for o, x in zip(vector, h)])
    languages = list(dict.fromkeys([profile["preferred_language"] or "en", *(profile["secondary_languages"] or [])]))
    facets = [normalize(v) for v in embed_many(rec.interest_facets(profile["interests_text"]))]
    return rec.Listener(vector=vector, languages=languages, user_id=user_id,
                        default_persona_id=profile["default_persona_id"], consumed_article_ids=train, facets=facets)


def _hits(order: list[str], held: set[str]) -> dict[int, float]:
    return {k: len(held & set(order[:k])) / len(held) for k in KS}


def main() -> None:
    with connect() as conn:
        users = conn.execute(
            "select p.id::text, p.display_name from public.profiles p "
            "where exists (select 1 from public.user_article_interactions i where i.user_id = p.id) "
            "order by p.display_name").fetchall()
        totals = {"personal": {k: 0.0 for k in KS}, "similarity only": {k: 0.0 for k in KS}}
        evaluated, candidates = 0, 0
        print(f"{'listener':<14} {'hidden':>6}  " + "  ".join(f"pers@{k:<3} sim@{k:<3}" for k in KS))
        for user_id, name in users:
            train, held = _split(conn, user_id)
            listener = _listener(conn, user_id, train)
            articles = rec.fetch_articles(conn, listener, None, None, None, None, [])
            held_set = set(held) & {a.id for a in articles}
            if not held_set:
                continue
            rec.load_coverage(conn, articles)
            rec.score(articles)
            personal = _hits([a.id for a in sorted(articles, key=lambda a: a.score, reverse=True)], held_set)
            similarity = _hits([a.id for a in sorted(articles, key=lambda a: a.sim_listener, reverse=True)], held_set)
            for k in KS:
                totals["personal"][k] += personal[k]
                totals["similarity only"][k] += similarity[k]
            evaluated, candidates = evaluated + 1, len(articles)
            print(f"{name:<14} {len(held_set):>6}  "
                  + "  ".join(f"{personal[k]:>7.0%}  {similarity[k]:>6.0%}" for k in KS))
        print(f"\nMean over {evaluated} listeners: share of hidden articles in the top k of ~{candidates} candidates")
        for label, values in totals.items():
            print(f"  {label:<16} " + "  ".join(f"@{k}: {values[k] / max(evaluated, 1):.0%}" for k in KS))


if __name__ == "__main__":
    main()
