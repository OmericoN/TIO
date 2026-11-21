"""Persona ranking: the listener's favourite voices first, then every other persona by what they've liked before.
No LLM.

Favourites (the voices the listener saved at sign-up or in Profile) always lead, in the listener's own order:
a briefing's guest experts come from them first (briefing/generate.py pick_hosts).
Everyone else: score = 0.70 · past reactions (affinity) + 0.10 · default host + 0.10 · fit to the listener's profile.

- Past reactions: the listener's interactions with briefings each persona voiced (user_article_interactions.persona_ids),
  weighted like the interest embedding (liked +1.5, completed +1, shared +1, played/source opened +0.5,
  skipped −0.5, disliked −1), shrunk toward 0 when there are few, in [-1, 1].
- Favourites: the voices passed in (Profile's saved voices, else profiles.chosen_persona_ids from sign-up). They
  stay first however the listener reacts to others; past reactions order the rest.
- Default host: their usual host (profiles.default_persona_id), a small nudge.
- Profile fit: cosine(persona embedding, the listener's interest vector). Orders everything after that.

The ranking deliberately ignores which articles were picked: when it voted by article topic, the same handful of
topic desks (AI and chips, earnings, rates and FX, crypto, energy) won for almost everyone. The article-by-article tone
check is separate (research/matcher.py).
"""

from recommender.embeddings import cosine, from_pgvector

W_AFFINITY, W_DEFAULT, W_PROFILE = 0.70, 0.10, 0.10
INTERACTION_WEIGHTS = {"impression": 0.0, "played": 0.5, "completed": 1.0, "liked": 1.5, "shared": 1.0,
                       "source_opened": 0.5, "skipped": -0.5, "disliked": -1.0}


def host_affinity(conn, user_id: str | None) -> dict[str, float]:
    if not user_id:
        return {}
    rows = conn.execute(
        """
        select p::text, i.type::text, count(*)
        from public.user_article_interactions i, unnest(i.persona_ids) as p
        where i.user_id = %s
        group by 1, 2
        """,
        (user_id,),
    ).fetchall()
    totals: dict[str, list[float]] = {}
    for persona_id, kind, count in rows:
        total = totals.setdefault(persona_id, [0.0, 0])
        total[0] += INTERACTION_WEIGHTS.get(kind, 0.0) * count
        total[1] += count
    return {pid: max(-1.0, min(1.0, s / (n + 3))) for pid, (s, n) in totals.items()}


def score_hosts(rows: list[tuple[str, float]], affinity: dict[str, float], default_persona_id: str | None,
                chosen: list[str] = ()) -> list[str]:
    """rows: (persona id, fit to the listener's profile). Returns persona ids: the favourites in `chosen` first, in
    that order, then everyone else best first."""
    known = {pid for pid, _ in rows}
    favourites = [pid for pid in dict.fromkeys(chosen) if pid in known]
    scored = [(W_AFFINITY * affinity.get(pid, 0.0) + W_DEFAULT * (pid == default_persona_id) + W_PROFILE * fit, pid)
              for pid, fit in rows if pid not in favourites]
    return favourites + [pid for _, pid in sorted(scored, reverse=True)]


def rank_hosts(conn, listener_vector: list[float] | None, affinity: dict[str, float],
               default_persona_id: str | None, chosen: list[str] = ()) -> list[str]:
    """Every persona, best first. A persona without an embedding still ranks on past reactions and default."""
    personas = [(pid, from_pgvector(emb) if emb else None) for pid, emb in
                conn.execute("select id::text, embedding::text from public.agent_personas order by slug").fetchall()]
    rows = [(pid, cosine(listener_vector, emb) if emb and listener_vector else 0.0) for pid, emb in personas]
    return score_hosts(rows, affinity, default_persona_id, chosen)
