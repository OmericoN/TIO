import base64
import asyncio
import json
import logging
import os
import time
from copy import copy
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal
from urllib.parse import quote
from urllib.request import Request as ProviderRequest, urlopen
from uuid import UUID, uuid4

import psycopg
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, ValidationError, model_validator

from discussion.panel import Panel, Turn, model, passages, review_event, select_stories, source_set
from discussion.agents import DiscussionLine
from discussion.matched import CompletedMatch, InvalidMatch, matched_panel, test_shortlist
from discussion.personas import AgentPersona, load_starter_personas
from discussion.stories import INDEX, available_stories
from research.matcher import REPO_ROOT, create_matcher
from research.openai_client import MatchProcessingError
from recommender.db import connect

log = logging.getLogger(__name__)


def local(request: Request):
    if (not request.client or request.client.host not in {"127.0.0.1", "::1"}
            or request.headers.get("sec-fetch-site") == "cross-site"):
        raise HTTPException(403, "Panel sessions are limited to local development.")


router = APIRouter(prefix="/panel", dependencies=[Depends(local)], tags=["panel"])


class Start(BaseModel):
    profile: str = Field(min_length=1, max_length=3000)
    mode: Literal["panel", "presenter"] = "panel"
    minutes: int = Field(default=4, ge=1, le=15)
    ranked_guests: dict[str, list[UUID]] | None = None
    story_ids: list[str] | None = Field(default=None, min_length=1, max_length=6)
    match_result: CompletedMatch | None = None
    test_shortlist: bool = False

    @model_validator(mode="after")
    def one_guest_source(self):
        if sum((self.ranked_guests is not None, self.match_result is not None, self.test_shortlist)) > 1:
            raise ValueError("Choose one of match_result, test_shortlist, or ranked_guests.")
        if self.story_ids is not None and (self.match_result is not None or self.test_shortlist):
            raise ValueError("Use story_ids for a feed lineup or supply a matcher shortlist.")
        return self


class Step(BaseModel):
    generation: int = Field(ge=0)
    heard: UUID | None = None
    seconds: float = Field(default=0, ge=0, le=300, allow_inf_nan=False)
    question: str = Field(default="", max_length=1000)
    completed: bool = False
    fully_heard: bool = True


@dataclass
class Session:
    panel: Panel
    generation: int = 0
    pending: tuple[UUID, Turn, list] | None = None
    busy: bool = False
    touched: float = 0


sessions: dict[UUID, Session] = {}
matcher = lru_cache(maxsize=1)(create_matcher)


def news_events():
    return [s for s in available_stories() if any(not a.official_release for a in s.articles)]


@router.get("/stories")
def feed():
    events = news_events()
    thumbnails = {}
    if INDEX.exists():
        ids = [a.id for story in events for a in story.articles if not a.official_release]
        try:
            with connect() as db:
                thumbnails = dict(db.execute(
                    "SELECT id, thumb_url FROM public.articles WHERE id = ANY(%s)", (ids,)))
        except (RuntimeError, psycopg.Error):
            log.warning("Article thumbnails are unavailable.")
    return {"sample": not INDEX.exists(), "stories": [
        {"id": s.id, "title": s.headline, "topic": s.articles[0].publisher,
         "thumb_url": next((thumbnails[a.id] for a in s.articles if thumbnails.get(a.id)), None),
         "summary": " · ".join(dict.fromkeys(a.language.upper() for a in s.articles))}
        for s in events]}


def indexed_or_sample_panel(start):
    events = news_events()
    if start.story_ids is None:
        stories = select_stories(events, start.profile, start.minutes, model())
    else:
        by_id = {s.id: s for s in events}
        if any(i not in by_id for i in start.story_ids) or len(set(start.story_ids)) != len(start.story_ids):
            raise HTTPException(422, "The selected headlines have changed. Refresh the feed.")
        stories = [by_id[i] for i in start.story_ids]
    if INDEX.exists():
        with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as db:
            catalog = [AgentPersona.model_validate(row) for row in db.execute("SELECT * FROM public.agent_personas")]
            personas = {p.id: p for p in catalog}
    else:
        personas = load_starter_personas()
    host = next((p.id for p in personas.values() if p.slug == "main-host"), None)
    if host is None:
        raise ValueError("The persona catalog needs a main-host.")
    candidates = [i for i in personas if i != host]
    guests = {}
    tones = {}
    for story in stories:
        if start.mode == "presenter":
            ids = []
        elif start.ranked_guests is not None:
            ids = start.ranked_guests.get(story.id, [])[:2]
        elif INDEX.exists():
            news_ids = [a.id for a in story.articles if not a.official_release]
            result = matcher().match_personas(news_ids, candidates, 2) if news_ids else {"agent_ids": [], "articles": []}
            ids = [UUID(i) for i in result["agent_ids"]]
            tones[story.id] = result["articles"]
        else:
            ids = candidates[:2]
        if len(set(ids)) != len(ids) or any(i not in candidates for i in ids):
            raise ValueError("Invalid guest roster.")
        guests[story.id] = ids
    return Panel(stories, personas, host, guests, start.profile, start.mode, start.minutes, tones=tones)


def make_panel(start):
    load_dotenv(REPO_ROOT / ".env")
    result = start.match_result
    if start.test_shortlist:
        article_ids, guest_ids, count = test_shortlist()
        result = CompletedMatch.model_validate(matcher().match_personas(article_ids, guest_ids, count))
    if result is not None:
        panel = matched_panel(result, start.profile, start.mode, start.minutes, model())
        stage = "session_evidence"
    else:
        panel = indexed_or_sample_panel(start)
        stage = "event_evidence"
    for story in panel.stories:
        excerpts = passages(story, story.headline)
        rows = [{**a.model_dump(mode="json", exclude={"body"}),
                 "passages": [e.excerpt for e in excerpts if e.article_id == a.id]}
                for a in source_set(story)]
        panel.reviews[story.id] = review_event(model().model, json.dumps(rows, ensure_ascii=False), stage)
    return panel


def eleven(path, body):
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise ValueError("Configure ELEVENLABS_API_KEY for audio.")
    request = ProviderRequest("https://api.elevenlabs.io/v1/" + path,
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    request.add_unredirected_header("xi-api-key", key)
    with urlopen(request, timeout=45) as response:
        return response.read()


def render(panel, turn):
    persona = panel.personas[panel.speaker()]
    voice = persona.voice_id or os.getenv("PANEL_VOICE_" + persona.slug.upper().replace("-", "_"))
    if not voice:
        raise ValueError(f"Configure a voice for {persona.slug}.")
    audio = eleven("text-to-speech/" + quote(voice, safe=""), {"text": turn.text})
    log.info("panel_tts characters=%d", len(turn.text))
    return base64.b64encode(audio).decode()


@router.post("")
async def start_panel(start: Start):
    now = time.monotonic()
    for key in list(sessions):
        if sessions[key].touched < now - 1800:
            sessions.pop(key)
    if len(sessions) >= 32:
        raise HTTPException(429, "Panel session limit reached.")
    try:
        panel = await run_in_threadpool(make_panel, start)
    except InvalidMatch as exc:
        raise HTTPException(422, str(exc)) from None
    except (ValueError, RuntimeError, KeyError, OSError, psycopg.Error) as exc:
        if isinstance(exc, ValidationError):
            field = exc.errors(include_input=False, include_context=False)[0]["loc"]
            detail = f"Invalid {exc.title} data at {'.'.join(map(str, field))}."
        elif isinstance(exc, MatchProcessingError) or type(exc) in (ValueError, RuntimeError):
            detail = str(exc)
        elif isinstance(exc, psycopg.Error):
            detail = "Could not read the panel database. Check DATABASE_URL and the database connection."
        else:
            detail = f"Check the panel environment and source data ({type(exc).__name__})."
        log.warning("Panel setup failed: %s", detail)
        raise HTTPException(503, detail) from None
    key = uuid4()
    sessions[key] = Session(panel, touched=now)
    return {"id": key, "sample": not start.test_shortlist and start.match_result is None and not INDEX.exists()}


def session(key):
    item = sessions.get(key)
    if not item or time.monotonic() - item.touched > 1800:
        sessions.pop(key, None)
        raise HTTPException(404, "Session expired.")
    item.touched = time.monotonic()
    return item


@router.delete("/{key}")
async def stop(key: UUID):
    item = sessions.pop(key, None)
    if item:
        item.generation += 1


@router.post("/{key}/interrupt")
async def interrupt(key: UUID, step: Step):
    item = session(key)
    if step.generation <= item.generation:
        return {"generation": item.generation}
    if item.pending and step.heard == item.pending[0] and step.seconds:
        if step.completed:
            item.panel.heard(item.pending[1], item.pending[2], step.seconds, step.fully_heard)
        else:
            item.panel.last_sources = item.pending[2]
            item.panel.reference = f"Interrupted {item.panel.personas[item.panel.speaker()].name} at {step.seconds:.1f}s. Use the active source cards to resolve references."
    item.generation = step.generation
    item.busy = False
    item.pending = None
    return {"generation": item.generation}


@router.post("/{key}/next")
async def next_segment(key: UUID, step: Step):
    return await produce_segment(key, step)


async def produce_segment(key, step, progress=None):
    item = session(key)
    if item.busy or step.generation != item.generation:
        raise HTTPException(409, "Turn superseded or in progress.")
    if item.pending:
        token, turn, cards = item.pending
        if token != step.heard:
            raise HTTPException(409, "Acknowledge the active segment first.")
        item.panel.heard(turn, cards, step.seconds, step.fully_heard)
        item.pending = None
    if item.panel.index == len(item.panel.stories):
        return {"done": True}
    if step.question.strip():
        item.panel.question = step.question.strip()
        item.panel.history.append(DiscussionLine(speaker="Listener", text=item.panel.question))
    # Snapshot the floor while provider work runs. Articles and reviews are shared read-only.
    panel = copy(item.panel)
    panel.history = list(item.panel.history)
    persona = panel.personas[panel.speaker()]
    item.busy = True
    try:
        turn, cards = await run_in_threadpool(panel.draft, model(persona.llm_model), model())
        if item.generation != step.generation:
            raise HTTPException(409, "Turn superseded.")
        if progress:
            progress({"status": "Rendering verified audio…"})
        audio = await run_in_threadpool(render, panel, turn)
        if item.generation != step.generation:
            raise HTTPException(409, "Audio superseded.")
        token = uuid4()
        item.pending = token, turn, cards
        return {"id": token, "speaker": persona.name, "host": panel.personas[panel.host].name,
                "guests": [panel.personas[i].name for i in panel.roster], "story": panel.story.headline,
                "text": turn.text, "sources": cards, "audio": audio}
    except (ValueError, RuntimeError, OSError) as exc:
        log.warning("Panel turn failed: %s", type(exc).__name__)
        raise HTTPException(502, "Turn unavailable. Check model and speaker voice settings.") from None
    finally:
        if item.generation == step.generation:
            item.busy = False


@router.post("/{key}/events")
async def stream_turn(key: UUID, step: Step):
    session(key)

    async def events():
        queue = asyncio.Queue()

        async def generate():
            try:
                value = await produce_segment(key, step, queue.put_nowait)
                queue.put_nowait({"result": value})
            except HTTPException as error:
                queue.put_nowait({"error": error.detail})
            except Exception:
                log.exception("Panel event stream failed")
                queue.put_nowait({"error": "Panel generation failed."})

        task = asyncio.create_task(generate())
        try:
            while True:
                event = await queue.get()
                yield json.dumps(event, default=str) + "\n"
                if "status" not in event:
                    break
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


@router.post("/{key}/microphone")
def microphone(key: UUID):
    session(key)
    try:
        return {"token": json.loads(eleven("single-use-token/realtime_scribe", {}))["token"]}
    except (ValueError, OSError, KeyError):
        raise HTTPException(502, "Could not start transcription.") from None
