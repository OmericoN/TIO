import base64
import hashlib
import asyncio
import json
import logging
import os
import time
from copy import copy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.error import URLError
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

from discussion.panel import EvidenceReview, Panel, Turn, model, passages, review_event, select_stories, source_set, spoken_text
from discussion.agents import DiscussionLine
from voice.elevenlabs import request_session
from discussion import realtime, visuals
from discussion.matched import CompletedMatch, DiscussionPlan, InvalidMatch, matched_panel, planned_panel, test_shortlist
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
    minutes: float = Field(default=4, ge=1, le=120)
    expert_count: int = Field(default=2, ge=2, le=4, strict=True)
    ranked_guests: dict[str, list[UUID]] | None = None
    story_ids: list[str] | None = Field(default=None, min_length=1, max_length=6)
    match_result: CompletedMatch | None = None
    test_shortlist: bool = False
    briefing_plan: DiscussionPlan | None = None

    @model_validator(mode="after")
    def one_guest_source(self):
        if self.briefing_plan is not None:
            if any((self.ranked_guests is not None, self.match_result is not None,
                    self.test_shortlist, self.story_ids is not None)):
                raise ValueError("A briefing plan already supplies the stories and hosts.")
        elif self.minutes > 15 or self.minutes % 1:
            raise ValueError("Choose whole minutes from 1 to 15 for an unplanned discussion.")
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
class Prefetch:
    after: UUID                 # the segment it assumes was fully heard
    generation: int
    state: tuple                # Panel.state() it was drafted for
    task: asyncio.Task


@dataclass
class Session:
    panel: Panel
    generation: int = 0
    pending: tuple[UUID, Turn, list] | None = None
    busy: bool = False
    touched: float = 0
    pending_seconds: float = 0  # length of the pending segment's audio
    prefetch: Prefetch | None = None
    holding: asyncio.Task | None = None  # the host's pre-voiced holding lines for listener questions
    brief_pending: tuple | None = None   # realtime panel: (brief id, TurnBrief, speaker name, cards) being spoken
    brief_prefetch: Prefetch | None = None
    jump: tuple | None = None  # realtime panel: (story index, brief planned for its opening) after a skip request


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
    if start.mode == "panel" and len(candidates) < start.expert_count:
        raise ValueError(f"Only {len(candidates)} experts are available. Choose a smaller panel or use presenter mode.")
    guests = {}
    tones = {}
    for story in stories:
        if start.mode == "presenter":
            ids = []
        elif start.ranked_guests is not None:
            ids = start.ranked_guests.get(story.id, [])[:start.expert_count]
        elif INDEX.exists():
            news_ids = [a.id for a in story.articles if not a.official_release]
            result = matcher().match_personas(news_ids, candidates, start.expert_count) if news_ids else {"agent_ids": [], "articles": []}
            ids = [UUID(i) for i in result["agent_ids"]]
            tones[story.id] = result["articles"]
        else:
            ids = candidates[:start.expert_count]
        if len(set(ids)) != len(ids) or any(i not in candidates for i in ids):
            raise ValueError("Invalid guest roster.")
        guests[story.id] = ids
    return Panel(stories, personas, host, guests, start.profile, start.mode, start.minutes, tones=tones)


def make_panel(start):
    load_dotenv(REPO_ROOT / ".env")
    result = start.match_result
    if start.test_shortlist:
        article_ids, guest_ids, _ = test_shortlist()
        result = CompletedMatch.model_validate(matcher().match_personas(article_ids, guest_ids, start.expert_count))
    if start.briefing_plan is not None:
        panel = planned_panel(start.briefing_plan, start.profile, start.mode, start.minutes)
        stage = "session_evidence"
    elif result is not None:
        panel = matched_panel(result, start.profile, start.mode, start.minutes, model(), start.expert_count)
        stage = "session_evidence"
    else:
        panel = indexed_or_sample_panel(start)
        stage = "event_evidence"
    if (start.mode == "panel" and "expert_count" in start.model_fields_set
            and any(len(roster) != start.expert_count for roster in panel.guests.values())):
        raise InvalidMatch("There are not enough matched experts for this panel. Choose fewer experts.")
    for story in panel.stories:
        excerpts = passages(story, story.headline)
        rows = [{**a.model_dump(mode="json", exclude={"body"}),
                 "passages": [e.excerpt for e in excerpts if e.article_id == a.id]}
                for a in source_set(story)]
        try:
            panel.reviews[story.id] = review_event(model().model, json.dumps(rows, ensure_ascii=False), stage)
        except (MatchProcessingError, ValueError) as exc:
            # One story's review failing (e.g. OpenAI down after retries) shouldn't stop the briefing: the story
            # plays without reviewed facts and its lead passage becomes its only fact card.
            log.warning("Evidence review skipped for %r: %s", story.headline, exc)
            panel.reviews[story.id] = EvidenceReview(findings=[])
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


# Delivery per story tone: steadier for sensitive news, more animated for upbeat stories.
VOICE_SETTINGS = {
    "somber": {"stability": 0.8, "style": 0.0},
    "serious": {"stability": 0.65, "style": 0.1},
    "cautious": {"stability": 0.6, "style": 0.15},
    "analytical": {"stability": 0.55, "style": 0.2},
    "neutral": {"stability": 0.5, "style": 0.3},
    "upbeat": {"stability": 0.35, "style": 0.55},
}
V3_STABILITY = {"somber": 1.0, "serious": 1.0, "upbeat": 0.0}  # eleven_v3 takes 0 (creative), 0.5 or 1 (robust)
MP3_BYTES_PER_SECOND = 128_000 / 8  # ElevenLabs' default mp3_44100_128


def speech_request(text, tone):
    tts_model = os.getenv("PANEL_TTS_MODEL", "").strip()
    if tts_model.startswith("eleven_v3"):
        settings = {"stability": V3_STABILITY.get(tone, 0.5)}
    else:
        settings = {**VOICE_SETTINGS.get(tone, VOICE_SETTINGS["neutral"]), "similarity_boost": 0.75,
                    "use_speaker_boost": True}
    return {"text": text, "voice_settings": settings, **({"model_id": tts_model} if tts_model else {})}


def render(panel, turn):
    persona = panel.personas[panel.speaker()]
    voice = persona.voice_id or os.getenv("PANEL_VOICE_" + persona.slug.upper().replace("-", "_"))
    if not voice:
        raise ValueError(f"Configure a voice for {persona.slug}.")
    audio = eleven("text-to-speech/" + quote(voice, safe=""), speech_request(turn.text, panel.story_tone()))
    log.info("panel_tts characters=%d", len(turn.text))
    return base64.b64encode(audio).decode(), len(audio) / MP3_BYTES_PER_SECOND


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
    # Voice the host's holding lines now, in the background, so they're ready before the first question.
    if os.getenv("ELEVENLABS_API_KEY"):
        sessions[key].holding = asyncio.create_task(run_in_threadpool(holding_lines, panel))
        sessions[key].holding.add_done_callback(lambda task: task.cancelled() or task.exception())
    return {"id": key, "sample": (not start.test_shortlist and start.match_result is None
                                 and start.briefing_plan is None and not INDEX.exists())}


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
        item.prefetch = None


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
    item.prefetch = None
    return {"generation": item.generation}


@router.post("/{key}/next")
async def next_segment(key: UUID, step: Step):
    return await produce_segment(key, step)


# Played the moment a listener asks a question, while the answer is written, checked and voiced. Long and
# hesitant on purpose: each buys roughly eight to twelve seconds and sounds like the host thinking it through.
HOLDING_LINES = [
    "Ooh, good question. Hmm... let me think about that for a second, because I want to get it right. "
    "Let me pull up what the reporting actually says here... okay.",
    "Right, okay. That's a fair thing to ask. Uh, give me a moment... I'm looking back through the "
    "articles we have on this, so I don't just guess. Bear with me...",
    "Hmm. Yeah, that's a really interesting point. Let me... let me check the sources on that, because "
    "the details matter here. Okay, one second...",
    "Oh, I like that question. So... hmm. I want to be careful with this one. Let me go back to what the "
    "outlets reported, rather than speculate. Just a moment...",
    "Sure, sure. Um, that's worth digging into. Let me have a look at the reporting... there's a fair bit "
    "here, so give me a second to find the part that actually answers it.",
    "Good one. Hmm... okay, so, let me think. I'd rather check than give you half an answer. "
    "Pulling up the articles now... right.",
    "Ah, yes. That's something a lot of people are wondering, I think. Uh, let me look at exactly what was "
    "said, because the wording matters... okay, bear with me.",
    "Mm, okay. Let me take that one. Hmm... I'm just checking the sources we have, to make sure what I "
    "tell you holds up. One moment...",
]
HOLDING_CACHE = Path(os.getenv("PANEL_HOLDING_CACHE") or REPO_ROOT / ".briefing" / "holding")


def holding_lines(panel):
    """The host's holding lines as [{text, audio}], voiced once and cached on disk by voice, text and TTS settings,
    so later briefings reuse them for free. A line that fails to voice is skipped."""
    persona = panel.personas[panel.host]
    voice = persona.voice_id or os.getenv("PANEL_VOICE_" + persona.slug.upper().replace("-", "_"))
    if not voice:
        return []
    HOLDING_CACHE.mkdir(parents=True, exist_ok=True)
    clips = []
    for text in HOLDING_LINES:
        request = speech_request(text, "neutral")
        name = hashlib.sha256(json.dumps([voice, request], sort_keys=True).encode()).hexdigest()[:32]
        path = HOLDING_CACHE / f"{name}.mp3"
        try:
            if not path.exists() or path.stat().st_size < 1024:  # missing or not real audio
                audio = eleven("text-to-speech/" + quote(voice, safe=""), request)
                log.info("panel_tts characters=%d", len(text))
                path.write_bytes(audio)
            clips.append({"text": text, "audio": base64.b64encode(path.read_bytes()).decode()})
        except (ValueError, OSError) as exc:
            log.warning("Holding line not voiced: %s: %s", type(exc).__name__, exc)
    return clips


@router.get("/{key}/holding")
async def holding(key: UUID):
    """The host's pre-voiced holding lines; waits for them on the first call of a session."""
    item = session(key)
    try:
        clips = await asyncio.wait_for(asyncio.shield(item.holding), 60) if item.holding else []
    except Exception:  # noqa: BLE001 - questions still work without holding lines
        clips = []
    return {"speaker": item.panel.personas[item.panel.host].name, "clips": clips}


def segment(panel, progress=None):
    """Draft, verify and voice the next turn of a panel snapshot. Runs in a worker thread."""
    persona = panel.personas[panel.speaker()]
    turn, cards = panel.draft(model(persona.llm_model), model())
    if progress:
        progress({"status": "Rendering verified audio…"})
    audio, seconds = render(panel, turn)
    token = uuid4()
    payload = {"id": token, "speaker": persona.name, "host": panel.personas[panel.host].name,
               "guests": [panel.personas[i].name for i in panel.roster], "story": panel.story.headline,
               "story_id": panel.story.id, "text": spoken_text(turn.text), "sources": cards, "audio": audio}
    return token, turn, cards, seconds, payload


def start_prefetch(item):
    """While the listener hears the pending segment, draft the one after it, assuming it is heard in full.
    produce_segment uses it only if the real floor state matches; otherwise it is discarded."""
    token, turn, cards = item.pending
    panel = item.panel.fork()
    panel.heard(turn, cards, item.pending_seconds, True)
    if panel.index == len(panel.stories):
        return
    item.prefetch = Prefetch(token, item.generation, panel.state(),
                             asyncio.create_task(run_in_threadpool(segment, panel)))
    item.prefetch.task.add_done_callback(lambda task: task.cancelled() or task.exception())  # no unhandled warnings


async def produce_segment(key, step, progress=None):
    item = session(key)
    if item.busy or step.generation != item.generation:
        raise HTTPException(409, "Turn superseded or in progress.")
    prefetch, item.prefetch = item.prefetch, None
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
    item.busy = True
    try:
        result = None
        if (prefetch and prefetch.after == step.heard and prefetch.generation == item.generation
                and prefetch.state == item.panel.state()):
            try:
                result = await prefetch.task
            except (ValueError, RuntimeError, OSError) as exc:
                log.warning("Prefetched turn failed, drafting again: %s: %s", type(exc).__name__, exc)
        if result is None:
            # Snapshot the floor while provider work runs. Articles and reviews are shared read-only.
            result = await run_in_threadpool(segment, item.panel.fork(), progress)
        if item.generation != step.generation:
            raise HTTPException(409, "Turn superseded.")
        token, turn, cards, seconds, payload = result
        item.pending = token, turn, cards
        item.pending_seconds = seconds
        start_prefetch(item)
        return payload
    except (ValueError, RuntimeError, OSError) as exc:
        log.warning("Panel turn failed: %s: %s", type(exc).__name__, exc)
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


class LiveLine(BaseModel):
    role: Literal["listener", "host"]
    text: str = Field(max_length=4000)


class LiveEnd(BaseModel):
    generation: int = Field(ge=0)
    transcript: list[LiveLine] = Field(max_length=80)


class LiveStart(BaseModel):
    mode: Literal["interrupt", "show"] = "interrupt"


class LiveAudit(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/{key}/live")
async def start_live(key: UUID, body: LiveStart | None = None):
    """A live speech-to-speech session with the host for a listener interruption: an ElevenLabs agent token plus the
    prompt and voice that make the agent this host, briefed on the listener and the current story."""
    item = session(key)
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    agent_id = os.getenv("ELEVENLABS_AGENT_ID", "").strip()
    if not api_key or not agent_id:
        raise HTTPException(503, "Live conversation needs ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID.")
    try:
        token = await run_in_threadpool(lambda: request_session(api_key=api_key, agent_id=agent_id))
    except (URLError, TimeoutError, ValueError, OSError):
        raise HTTPException(502, "ElevenLabs could not start a live session.") from None
    host = item.panel.personas[item.panel.host]
    show = body is not None and body.mode == "show"
    return {"token": token.token, "prompt": item.panel.show_prompt() if show else item.panel.live_prompt(),
            "voice_id": host.voice_id, "speaker": host.name,
            "stories": [s.headline for s in item.panel.stories],
            "max_seconds": round(item.panel.minutes * 60 * 1.5 + 300) if show else 600}


@router.post("/{key}/live/audit")
async def audit_live(key: UUID, body: LiveAudit):
    """Source cards for a line the live host just spoke, and a correction note if it stated an unsupported figure."""
    item = session(key)
    cards, correction = item.panel.audit_live(body.text)
    if correction:
        log.warning("Live host figure not in the fact cards: %s | line: %s", correction, body.text)
    return {"cards": cards, "correction": correction,
            "story": cards[0]["headline"] if cards else None}


@router.post("/{key}/live/end")
async def end_live(key: UUID, body: LiveEnd):
    """Fold the live conversation into the panel so the next segment picks the story back up."""
    item = session(key)
    if item.busy or body.generation != item.generation:
        raise HTTPException(409, "Live conversation superseded.")
    item.panel.live_exchange([(line.role, line.text) for line in body.transcript])
    item.prefetch = None
    return {"generation": item.generation}


class RealtimeStep(BaseModel):
    generation: int = Field(ge=0)
    heard: UUID | None = None
    text: str = Field(default="", max_length=4000)      # what the agent actually said
    seconds: float = Field(default=0, ge=0, le=300, allow_inf_nan=False)
    interrupted: bool = False
    listener: list[LiveLine] = Field(default_factory=list, max_length=40)  # a live exchange with the host


@router.post("/{key}/realtime/agents")
async def realtime_agents(key: UUID):
    """One browser-side speech-to-speech agent per speaker: an ephemeral OpenAI Realtime secret with that persona's
    instructions and a distinct voice. Only the host listens to the microphone."""
    item = session(key)
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise HTTPException(503, "The realtime panel needs OPENAI_API_KEY.")
    panel = item.panel
    try:
        chosen = await run_in_threadpool(realtime.voices, panel)  # cast once per persona, then cached
        secrets = await asyncio.gather(*(run_in_threadpool(realtime.create_agent_secret, panel, i, v, direction)
                                         for i, (v, direction) in chosen.items()))
    except (URLError, TimeoutError, ValueError, OSError, KeyError, MatchProcessingError):
        raise HTTPException(502, "OpenAI could not start the realtime agents.") from None
    return {"model": realtime.REALTIME_MODEL, "agents": [
        {"id": str(i), "name": panel.personas[i].name, "role": "host" if i == panel.host else "guest",
         "voice": v, "secret": secret} for (i, (v, _)), secret in zip(chosen.items(), secrets)]}


def plan_turn(panel):
    """The next brief and what the player needs to have its agent speak it. Runs in a worker thread."""
    brief = realtime.make_brief(panel)
    speaker = panel.speaker()
    cards = {c["id"]: c for c in realtime.story_cards(panel)}
    sources = [src for i in brief.card_ids if i in cards for src in cards[i]["sources"]]
    token = uuid4()
    return token, brief, {"id": token, "speaker_id": str(speaker), "speaker": panel.personas[speaker].name,
                          "host": panel.personas[panel.host].name, "guests": [panel.personas[i].name for i in panel.roster],
                          "story": panel.story.headline, "story_id": panel.story.id,
                          "brief": realtime.render_brief(panel, brief),
                          "max_seconds": brief.max_seconds, "sources": sources,
                          "visual": visuals.payload(brief.visual, list(cards.values()),
                                                    visuals.story_pictures(panel.story)
                                                    if brief.visual and brief.visual.kind == "picture" else {})}


def start_brief_prefetch(item):
    """Plan the brief after the one being spoken, assuming its points get made, while the agent is still talking."""
    token, brief, _, cards = item.brief_pending
    panel = item.panel.fork()
    visuals.remember(panel, brief.visual)
    panel.heard(realtime.brief_turn(brief), cards, brief.max_seconds * 0.8, True)
    if panel.index == len(panel.stories):
        return
    item.brief_prefetch = Prefetch(token, item.generation, panel.state(),
                                   asyncio.create_task(run_in_threadpool(plan_turn, panel)))
    item.brief_prefetch.task.add_done_callback(lambda task: task.cancelled() or task.exception())


class ListenerRequest(BaseModel):
    generation: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000)


TEASER = "(The host just teased this story in a few words.)"


@router.post("/{key}/realtime/listen")
async def realtime_listen(key: UUID, body: ListenerRequest):
    """The listener spoke. If they want another story, switch to it now and start planning its opening while the host
    bridges with a short teaser, so the panel is ready when the host stops talking."""
    item = session(key)
    if body.generation != item.generation:
        raise HTTPException(409, "Turn superseded.")
    if visuals.VISUAL_REQUEST.search(body.text) and item.panel.index < len(item.panel.stories):
        # Visuals come from the reporting, not from the agents: build one now from the story's fact cards.
        item.panel.wants_visuals = True
        cards = realtime.story_cards(item.panel)
        try:
            visual = await run_in_threadpool(visuals.for_listener, item.panel, body.text, cards)
        except (MatchProcessingError, ValueError) as exc:
            log.warning("Listener visual failed: %s", exc)
            visual = None
        if visual:
            pictures = visuals.story_pictures(item.panel.story)
            visuals.remember(item.panel, visual)
            return {"intent": "visual", "visual": visuals.payload(visual, cards, pictures),
                    "note": (f"The listener asked to see something, and {visuals.describe(visual, pictures)} is now on "
                             f"their screen. Say so, then talk them through it in two or three sentences, naming the "
                             f"outlet. Don't read out everything on it. From now on the panel shows more visuals.")}
        return {"intent": "visual", "visual": None,
                "note": ("The listener asked to see something, but this story's reporting has nothing that can be shown "
                         "honestly. Say that plainly in a sentence, then answer what they're after from the facts.")}
    target = item.panel.requested_story(body.text)
    if target is None:
        item.jump = None
        return {"intent": "question"}
    panel = item.panel.fork()
    panel.jump_to(target)
    panel.history.append(DiscussionLine(speaker=panel.personas[panel.host].name, text=TEASER))
    task = asyncio.create_task(run_in_threadpool(plan_turn, panel))
    task.add_done_callback(lambda done: done.cancelled() or done.exception())
    item.jump = target, Prefetch(None, item.generation, panel.state(), task)
    story = panel.story
    facts = "; ".join(c["point"] for c in realtime.story_cards(panel)[:3])
    return {"intent": "skip", "headline": story.headline,
            "note": (f"The listener wants to move on. Say yes in a few words, then give a two-sentence teaser of the "
                     f"next story, \"{story.headline}\", and why it might matter to them ({facts}). Don't go into "
                     f"detail and don't ask the guests anything: the panel picks it up from there.")}


@router.post("/{key}/realtime/next")
async def realtime_next(key: UUID, step: RealtimeStep):
    """Record what the last agent said (and any live exchange with the listener), then hand out the next brief,
    usually already planned while that agent was speaking."""
    item = session(key)
    if item.busy or step.generation != item.generation:
        raise HTTPException(409, "Turn superseded or in progress.")
    prefetch, item.brief_prefetch = item.brief_prefetch, None
    context = None
    if item.brief_pending:
        token, brief, name, cards = item.brief_pending
        if token != step.heard:
            raise HTTPException(409, "Acknowledge the active segment first.")
        said = spoken_text(step.text)
        visuals.remember(item.panel, brief.visual)
        item.panel.heard(realtime.brief_turn(brief, said or None), cards, step.seconds or brief.max_seconds,
                         not step.interrupted)
        item.brief_pending = None
        context = realtime.context_update(item.panel, name, said or " ".join(brief.points))
    if step.listener:
        item.panel.live_exchange([(line.role, line.text) for line in step.listener])
        prefetch = None
    if item.jump:  # the listener asked for another story: its opening was planned while the host bridged
        target, planned = item.jump
        item.jump = None
        item.panel.jump_to(target)
        item.panel.history.append(DiscussionLine(speaker=item.panel.personas[item.panel.host].name, text=TEASER))
        prefetch = Prefetch(step.heard, planned.generation, planned.state, planned.task)
    if item.panel.index == len(item.panel.stories):
        return {"done": True, "context": context}
    item.busy = True
    try:
        result = None
        if (prefetch and prefetch.after == step.heard and prefetch.generation == item.generation
                and prefetch.state == item.panel.state()):
            try:
                result = await prefetch.task
            except (ValueError, RuntimeError, OSError) as exc:
                log.warning("Prefetched brief failed, planning again: %s: %s", type(exc).__name__, exc)
        if result is None:
            result = await run_in_threadpool(plan_turn, item.panel.fork())
        if item.generation != step.generation:
            raise HTTPException(409, "Turn superseded.")
        token, brief, payload = result
        item.brief_pending = token, brief, payload["speaker"], payload["sources"]
        start_brief_prefetch(item)
        # The host always knows the current story, even before anyone has spoken.
        return {**payload, "context": context or realtime.context_update(item.panel)}
    except (ValueError, RuntimeError, OSError) as exc:
        log.warning("Realtime brief failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(502, "The next turn could not be planned.") from None
    finally:
        if item.generation == step.generation:
            item.busy = False


@router.post("/{key}/microphone")
def microphone(key: UUID):
    session(key)
    try:
        return {"token": json.loads(eleven("single-use-token/realtime_scribe", {}))["token"]}
    except (ValueError, OSError, KeyError):
        raise HTTPException(502, "Could not start transcription.") from None
