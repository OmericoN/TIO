"""Read the Newstex .jsonl files and turn each record into database rows. No database access here."""

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from psycopg.types.json import Jsonb

SENTIMENT_CODES = {"POSITIVE", "NEGATIVE"}

PUBLISHER_COLUMNS = ["id", "type", "name", "newstex_id", "url", "status", "country"]
PUBLICATION_COLUMNS = [
    "id", "type", "publisher_id", "name", "newstex_id", "url", "code", "status", "country", "content_type", "lc_class",
]
# Columns filled from the JSONL. The other article columns (summary, embeddings, translation_of, ingested_at)
# belong to later steps and are never overwritten by the loader.
ARTICLE_COLUMNS = [
    "id", "type", "external_id", "publication_id", "publisher_id", "headline", "excerpt", "text_content",
    "html_content", "published_at", "received_at", "language", "author", "permalink", "path", "feed",
    "pub_type", "status", "version", "thumb_url", "categories", "provider_categories", "company_tickers",
    "provider_tickers", "category_scores", "category_score_audit", "character_count", "word_count",
    "line_count", "sentiment_score", "content_lock", "content_commit_token", "source_file",
]


@dataclass
class Dataset:
    publishers: dict[str, tuple] = field(default_factory=dict)
    publications: dict[str, tuple] = field(default_factory=dict)
    articles: list[tuple] = field(default_factory=list)
    scores: list[tuple] = field(default_factory=list)  # (article_id, category_code, score)
    category_codes: set[str] = field(default_factory=set)
    per_file: Counter = field(default_factory=Counter)


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _jsonb(value) -> Jsonb | None:
    return Jsonb(value) if value is not None else None


def publisher_row(p: dict) -> tuple:
    return (p["$id"], p.get("$type"), p["name"], p.get("newstex_id"), p.get("url"), p.get("status"), p.get("country"))


def publication_row(p: dict, publisher_id: str) -> tuple:
    return (
        p["$id"], p.get("$type"), publisher_id, p["name"], p.get("newstex_id"), p.get("url"), p.get("code"),
        p.get("status"), p.get("country"), p.get("content_type"), p.get("lc_class"),
    )


def article_row(source_file: str, a: dict) -> tuple:
    stats = a.get("stats") or {}
    return (
        a["$id"],                              # id
        a.get("$type"),                        # type
        a.get("external_id"),
        a["publication"]["$id"],               # publication_id
        a["publisher"]["$id"],                 # publisher_id
        a["headline"],
        a.get("excerpt") or "",
        a.get("text_content") or "",
        a.get("html_content"),
        _time(a["date"]),                      # published_at
        _time(a.get("received_at")),
        a["language"],
        a.get("source"),                       # author (the byline)
        a["permalink"],
        a.get("path"),
        a.get("feed"),
        a.get("pub_type"),
        a.get("status"),
        a.get("version"),
        a.get("thumb_url"),
        a.get("categories") or [],
        a.get("provider_categories") or [],
        a.get("company_tickers") or [],
        a.get("provider_tickers") or [],
        _jsonb(a.get("category_scores")),
        _jsonb(a.get("category_score_audit")),
        stats.get("character_count"),
        stats.get("word_count"),
        stats.get("line_count"),
        stats.get("sentiment_score"),
        a.get("content_lock"),
        a.get("content_commit_token"),
        source_file,
    )


def read_records(data_dir: Path):
    """Yield (file name, article dict) for every line of every .jsonl file."""
    files = sorted(data_dir.glob("*.jsonl"))
    if not files:
        raise SystemExit(f"No .jsonl files found in {data_dir}")
    for path in files:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if line.strip():
                    try:
                        yield path.name, json.loads(line)
                    except json.JSONDecodeError as err:
                        raise SystemExit(f"{path.name}:{line_no}: invalid JSON ({err})") from err


def read_dataset(data_dir: Path) -> Dataset:
    data = Dataset()
    for source_file, a in read_records(data_dir):
        publisher = a["publisher"]
        data.publishers[publisher["$id"]] = publisher_row(publisher)
        data.publications[a["publication"]["$id"]] = publication_row(a["publication"], publisher["$id"])
        data.articles.append(article_row(source_file, a))
        data.per_file[source_file] += 1
        data.category_codes.update(a.get("categories") or [])
        for code, score in (a.get("category_scores") or {}).items():
            data.category_codes.add(code)
            data.scores.append((a["$id"], code, int(score)))
    return data
