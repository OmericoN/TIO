"""Embedding jobs: read from Supabase, embed with OpenAI, write the vectors back.

Articles: headline + the extracted subject, event, topics, people and entities (when present) + summary, or the
excerpt when there is no summary.
Users: onboarding vector (interests_text), behavior vector (articles they interacted with), or a hybrid.
"""

import time

from tio_data.db import connect
from tio_data.embeddings import OpenAIEmbedder, from_pgvector, normalize, to_pgvector, weighted_average

# How much each interaction pulls a user's taste vector toward (or away from) an article.
INTERACTION_WEIGHTS = {
    "impression": 0.0,
    "played": 0.5,
    "completed": 1.0,
    "liked": 1.5,
    "shared": 1.0,
    "source_opened": 0.5,
    "skipped": -0.5,
    "disliked": -1.0,
}
BEHAVIOR_SHARE = 0.5          # weight of behavior vs. onboarding in a hybrid
BEHAVIOR_LOOKBACK_DAYS = 60


def check(embedder: OpenAIEmbedder) -> None:
    vector = embedder.embed_queries(["ECB interest rate decision"])[0]
    print(f"OpenAI OK: {embedder.model} returns {len(vector)} dimensions.")


ARTICLE_FIELDS = "id, headline, excerpt, summary, main_subject, event, topics, people, entities"


def _article_text(headline, excerpt, summary, main_subject, event, topics, people, entities) -> tuple[str, str]:
    lines = [headline]
    if main_subject:
        lines.append(f"Subject: {main_subject}")
    if event:
        lines.append(f"Event: {event}")
    for label, values in (("Topics", topics), ("People", people), ("Entities", entities)):
        if values:
            lines.append(f"{label}: {', '.join(values)}")
    if summary and summary.strip():
        return "\n".join(lines) + f"\n\n{summary.strip()}", "summary"
    return "\n".join(lines) + f"\n\n{(excerpt or '').strip()}", "excerpt"


def embed_articles(embedder: OpenAIEmbedder, batch_size: int = 128, force: bool = False,
                   limit: int | None = None) -> None:
    # Without --force: articles with no embedding, one from another model, or one made before a summary or the
    # extracted fields arrived.
    where = "true" if force else (
        "summary_embedding is null "
        "or embedding_model is distinct from %(model)s "
        "or (nullif(trim(summary), '') is not null and embedding_source is distinct from 'summary') "
        "or extracted_at > embedded_at"
    )
    sql = f"select {ARTICLE_FIELDS} from public.articles where {where} order by published_at desc"
    if limit:
        sql += f" limit {int(limit)}"

    with connect() as conn:
        rows = conn.execute(sql, {"model": embedder.model}).fetchall()
        if not rows:
            print("All articles already have an up-to-date embedding.")
            return
        print(f"Embedding {len(rows)} articles with {embedder.model}...")

        started, done = time.monotonic(), 0
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            texts, sources = zip(*(_article_text(*row[1:]) for row in batch))
            vectors = embedder.embed_documents(list(texts))
            with conn.cursor() as cur:
                cur.executemany(
                    "update public.articles set summary_embedding = %s::extensions.vector, embedding_source = %s, "
                    "embedding_model = %s, embedded_at = now() where id = %s",
                    [(to_pgvector(v), src, embedder.model, row[0]) for row, v, src in zip(batch, vectors, sources)],
                )
            conn.commit()  # per batch, so an interrupted run resumes where it stopped
            done += len(batch)
            print(f"  {done}/{len(rows)}  ({done / max(time.monotonic() - started, 1e-6):.1f}/s)")
    print("Done.")


def _behavior_vector(conn, user_id) -> list[float] | None:
    rows = conn.execute(
        """
        select i.type::text, a.summary_embedding::text
        from public.user_article_interactions i
        join public.articles a on a.id = i.article_id
        where i.user_id = %s
          and a.summary_embedding is not null
          and i.created_at >= now() - make_interval(days => %s)
        """,
        (user_id, BEHAVIOR_LOOKBACK_DAYS),
    ).fetchall()
    pairs = [(from_pgvector(emb), INTERACTION_WEIGHTS.get(kind, 0.0)) for kind, emb in rows]
    pairs = [(v, w) for v, w in pairs if w]
    return weighted_average([v for v, _ in pairs], [w for _, w in pairs])


def embed_users(embedder: OpenAIEmbedder) -> None:
    with connect() as conn:
        # past_article_ids / past_articles_embedding follow interactions by trigger, but article embeddings
        # change when articles are re-embedded, so recompute the averages here too.
        conn.execute("select public.refresh_profile_past_articles(id) from public.profiles")
        conn.commit()
        users = conn.execute("select id, interests_text from public.profiles order by created_at").fetchall()
        updated = skipped = 0
        for user_id, interests in users:
            onboarding = embedder.embed_queries([interests.strip()])[0] if interests and interests.strip() else None
            behavior = _behavior_vector(conn, user_id)

            if onboarding and behavior:
                vector = normalize([(1 - BEHAVIOR_SHARE) * o + BEHAVIOR_SHARE * b
                                    for o, b in zip(normalize(onboarding), behavior)])
                source = "hybrid"
            elif onboarding:
                vector, source = onboarding, "onboarding"
            elif behavior:
                vector, source = behavior, "behavior"
            else:
                skipped += 1
                continue

            conn.execute(
                "update public.profiles set interest_embedding = %s::extensions.vector, "
                "interest_embedding_source = %s, interest_embedding_model = %s, interest_embedded_at = now() "
                "where id = %s",
                (to_pgvector(vector), source, embedder.model, user_id),
            )
            conn.commit()
            updated += 1
            print(f"  {user_id}: {source}")
    print(f"Updated {updated} users. Skipped {skipped} with no interests text and no interactions yet.")
