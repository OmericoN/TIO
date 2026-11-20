"""Prepare the panel from a single completed host match."""
import os
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from discussion.panel import Panel, topic_limit
from discussion.personas import AgentPersona
from discussion.stories import Article, group_articles
from research.matcher import ArticleTone


class CompletedMatch(BaseModel):
    articles: list[ArticleTone] = Field(min_length=1, max_length=15)
    agent_ids: list[UUID]


class InvalidMatch(ValueError):
    pass


def test_shortlist():
    with psycopg.connect(os.environ["DATABASE_URL"]) as db:
        articles = db.execute("""
            SELECT id FROM public.articles ORDER BY published_at DESC, id LIMIT 15
        """).fetchall()
        guests = db.execute("""
            SELECT id FROM public.agent_personas WHERE slug <> 'main-host' ORDER BY id
        """).fetchall()
    if not articles:
        raise InvalidMatch("Load at least one database article to start a test briefing.")
    return [row[0] for row in articles], [row[0] for row in guests], 2


def matched_panel(result: CompletedMatch, profile, mode, minutes, client):
    tones = {}
    for entry in result.articles:
        article_id = entry["article_id"]
        if article_id in tones and tones[article_id] != entry:
            raise InvalidMatch("A matched article has conflicting tones.")
        tones[article_id] = entry
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as db:
        catalog = db.execute("""
            SELECT * FROM public.agent_personas WHERE slug = 'main-host' OR id = ANY(%s::uuid[])
        """, (result.agent_ids,))
        personas = {row["id"]: AgentPersona.model_validate(row) for row in catalog}
        sources = db.execute("""
            SELECT a.id, a.headline, a.language, coalesce(p.name, pub.name) AS publisher,
                   a.published_at, a.permalink AS url, coalesce(nullif(a.text_content, ''), a.excerpt) AS body
            FROM public.articles a JOIN public.publications pub ON pub.id = a.publication_id
            LEFT JOIN public.publishers p ON p.id = a.publisher_id WHERE a.id = ANY(%s::text[])
        """, (list(tones),))
        articles = {row["id"]: Article(**row, published=row["published_at"].isoformat()) for row in sources}

    host = next((p.id for p in personas.values() if p.slug == "main-host"), None)
    if host is None:
        raise InvalidMatch("The database needs a main-host persona for Tio.")
    if (host in result.agent_ids or len(set(result.agent_ids)) != len(result.agent_ids)
            or any(i not in personas for i in result.agent_ids)):
        raise InvalidMatch("Supply distinct database guest IDs, excluding Tio.")
    if missing := tones.keys() - articles.keys():
        raise InvalidMatch("Unknown matched article IDs: " + ", ".join(sorted(missing)))

    rank = {article_id: position for position, article_id in enumerate(tones)}
    groups = group_articles([articles[i] for i in tones], client, stage="session_grouping")
    groups.sort(key=lambda story: min(rank[a.id] for a in story.articles))
    stories = groups[:topic_limit(minutes)]
    roster = result.agent_ids[:2] if mode == "panel" else []
    return Panel(stories, personas, host, {s.id: roster for s in stories}, profile, mode, minutes,
                 tones={s.id: [tones[a.id] for a in s.articles] for s in stories})
