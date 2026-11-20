"""Prepare the panel from a single completed host match."""
import os
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, model_validator

from briefing.timing import PlannedStory
from discussion.panel import Panel, topic_limit
from discussion.personas import AgentPersona
from discussion.stories import Article, Story, group_articles
from research.matcher import ArticleTone, Tone


class CompletedMatch(BaseModel):
    articles: list[ArticleTone] = Field(min_length=1, max_length=15)
    agent_ids: list[UUID]


class DiscussionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments: list[PlannedStory] = Field(min_length=1, max_length=340)
    hosts: list[UUID] = Field(max_length=2)
    tones: dict[str, Tone] = Field(max_length=340)

    @model_validator(mode="after")
    def validate_contents(self):
        article_ids = [i for segment in self.segments for i in segment.article_ids]
        if (len(article_ids) > 340 or len(set(article_ids)) != len(article_ids)
                or any(not i.strip() for i in article_ids)):
            raise ValueError("Supply up to 340 distinct, nonempty planned article IDs.")
        if not self.tones.keys() <= set(article_ids):
            raise ValueError("Tones must refer to planned articles.")
        if len(set(self.hosts)) != len(self.hosts):
            raise ValueError("Planned hosts must be distinct.")
        return self


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


def load_panel_sources(article_ids, persona_ids):
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as db:
        catalog = db.execute("""
            SELECT * FROM public.agent_personas WHERE slug = 'main-host' OR id = ANY(%s::uuid[])
        """, (persona_ids,))
        personas = {row["id"]: AgentPersona.model_validate(row) for row in catalog}
        sources = db.execute("""
            SELECT a.id, a.headline, a.language, coalesce(p.name, pub.name) AS publisher,
                   a.published_at, a.permalink AS url, coalesce(nullif(a.text_content, ''), a.excerpt, '') AS body
            FROM public.articles a JOIN public.publications pub ON pub.id = a.publication_id
            LEFT JOIN public.publishers p ON p.id = a.publisher_id WHERE a.id = ANY(%s::text[])
        """, (article_ids,))
        articles = {row["id"]: Article(**row, published=row["published_at"].isoformat()) for row in sources}

    host = next((p.id for p in personas.values() if p.slug == "main-host"), None)
    if host is None:
        raise InvalidMatch("The database needs a main-host persona for Tio.")
    return articles, personas, host


def matched_panel(result: CompletedMatch, profile, mode, minutes, client):
    tones = {}
    for entry in result.articles:
        article_id = entry["article_id"]
        if article_id in tones and tones[article_id] != entry:
            raise InvalidMatch("A matched article has conflicting tones.")
        tones[article_id] = entry
    articles, personas, host = load_panel_sources(list(tones), result.agent_ids)
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


def planned_panel(plan: DiscussionPlan, profile, mode, minutes):
    ids = [i for segment in plan.segments for i in segment.article_ids]
    articles, personas, moderator = load_panel_sources(ids, plan.hosts)
    if missing := set(ids) - articles.keys():
        raise InvalidMatch("Unknown planned article IDs: " + ", ".join(sorted(missing)))
    if any(i not in personas for i in plan.hosts):
        raise InvalidMatch("Supply known database persona IDs.")
    if mode == "panel" and moderator in plan.hosts:
        raise InvalidMatch("Tio moderates the panel. Supply only guest IDs.")
    if mode == "presenter" and len(plan.hosts) > 1:
        raise InvalidMatch("Presenter mode supports one presenter.")

    stories = [Story(id=s.article_ids[0], headline=articles[s.article_ids[0]].headline,
                     articles=[articles[i] for i in s.article_ids]) for s in plan.segments]
    host = plan.hosts[0] if mode == "presenter" and plan.hosts else moderator
    roster = plan.hosts if mode == "panel" else []
    return Panel(stories, personas, host, {s.id: roster for s in stories}, profile, mode, minutes,
                 tones={s.id: [{"article_id": a.id, "desired_tone": plan.tones[a.id]}
                               for a in s.articles if a.id in plan.tones] for s in stories},
                 story_budgets={s.id: p.estimated_seconds for s, p in zip(stories, plan.segments, strict=True)})
