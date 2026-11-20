"""Write the news dataset into Supabase. Safe to re-run: everything is upserted by id."""

from pathlib import Path

from tio_data.db import connect
from tio_data.newstex import (
    ARTICLE_COLUMNS,
    PUBLICATION_COLUMNS,
    PUBLISHER_COLUMNS,
    SENTIMENT_CODES,
    read_dataset,
)

TRANSLATION_WINDOW_SECONDS = 15 * 60

# Invezz DE/NL articles have an English original by the same author, published within 15 minutes.
LINK_TRANSLATIONS_SQL = """
update public.articles t
set translation_of = m.original_id
from (
    select distinct on (d.id) d.id, e.id as original_id
    from public.articles d
    join public.articles e
      on e.language = 'en'
     and e.publisher_id = d.publisher_id
     and e.author = d.author
     and abs(extract(epoch from e.published_at - d.published_at)) <= %(window)s
    where d.language <> 'en'
    order by d.id, abs(extract(epoch from e.published_at - d.published_at))
) m
where t.id = m.id
  and t.translation_of is distinct from m.original_id
"""


def upsert_sql(table: str, columns: list[str], key: str = "id") -> str:
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != key)
    return (
        f"insert into public.{table} ({', '.join(columns)}) values ({placeholders}) "
        f"on conflict ({key}) do update set {updates}"
    )


def load(data_dir: Path, dry_run: bool = False) -> None:
    data = read_dataset(data_dir)

    print(f"Read {len(data.articles)} articles from {len(data.per_file)} files:")
    for name, count in sorted(data.per_file.items()):
        print(f"  {name:<22} {count:>5}")
    print(f"{len(data.publishers)} publishers, {len(data.publications)} publications, "
          f"{len(data.category_codes)} category codes, {len(data.scores)} category scores")
    if dry_run:
        print("Dry run: nothing written.")
        return

    with connect() as conn, conn.cursor() as cur:
        cur.executemany(upsert_sql("publishers", PUBLISHER_COLUMNS), list(data.publishers.values()))
        cur.executemany(upsert_sql("publications", PUBLICATION_COLUMNS), list(data.publications.values()))
        cur.executemany(
            "insert into public.newstex_categories (code, is_sentiment) values (%s, %s) "
            "on conflict (code) do update set is_sentiment = excluded.is_sentiment",
            [(code, code in SENTIMENT_CODES) for code in sorted(data.category_codes)],
        )
        print("1/4 publishers, publications and categories")

        cur.executemany(upsert_sql("articles", ARTICLE_COLUMNS), data.articles)
        print(f"2/4 {len(data.articles)} articles")

        # ~100k score rows: bulk COPY into a temp table, then swap them in.
        cur.execute("create temp table tmp_scores (article_id text, category_code text, score integer) on commit drop")
        with cur.copy("copy tmp_scores (article_id, category_code, score) from stdin") as copy:
            for row in data.scores:
                copy.write_row(row)
        cur.execute(
            "delete from public.article_category_scores s "
            "using (select distinct article_id from tmp_scores) t where s.article_id = t.article_id"
        )
        cur.execute(
            "insert into public.article_category_scores (article_id, category_code, score) "
            "select article_id, category_code, score from tmp_scores"
        )
        print(f"3/4 {len(data.scores)} category scores")

        cur.execute(LINK_TRANSLATIONS_SQL, {"window": TRANSLATION_WINDOW_SECONDS})
        print(f"4/4 linked {cur.rowcount} translations to their English original")

    print("Done.")
