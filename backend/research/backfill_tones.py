"""Populate missing article delivery tones. Run from backend with python -m research.backfill_tones."""

import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from itertools import batched

import psycopg

from research.matcher import ARTICLE_TONES, create_matcher


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    matcher = create_matcher()
    classify = partial(matcher.match_personas, agent_ids=[], n=1)

    with psycopg.connect(matcher.repository.database_url, autocommit=True, connect_timeout=15) as conn:
        pending = conn.execute(
            "SELECT id FROM public.articles WHERE desired_tone IS NULL ORDER BY id"
        ).fetchall()
        logging.info("Found %d articles without a tone.", len(pending))
        saved = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            batches = batched((article_id for (article_id,) in pending), 16)
            for result in pool.map(classify, batches):
                labels = result["articles"]
                with conn.transaction(), conn.cursor() as cursor:
                    cursor.executemany(
                        """
                        UPDATE public.articles SET desired_tone = %s
                        WHERE id = %s AND desired_tone IS NULL
                        """,
                        [(label["desired_tone"], label["article_id"]) for label in labels],
                    )
                    saved += cursor.rowcount
                logging.info("Saved %d/%d pending article tones.", saved, len(pending))

        counts = dict(conn.execute(
            "SELECT desired_tone, count(*) FROM public.articles GROUP BY desired_tone"
        ).fetchall())
        if not counts or counts.keys() - ARTICLE_TONES.keys():
            raise RuntimeError("The article table is empty or still contains missing or unknown tones.")
        logging.info("Verified %d article tones: %s", sum(counts.values()), counts)


if __name__ == "__main__":
    main()
