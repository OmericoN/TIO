import os
from pathlib import Path
from urllib.error import URLError

import psycopg

from fastapi import APIRouter, HTTPException, Request, Response

from recommender.db import connect
from voice.elevenlabs import VoiceSession, request_session
from voice.previews import PREVIEW_URL, preview_path

router = APIRouter(prefix="/voice", tags=["voice"])
PREVIEW_PROMPT = Path(__file__).resolve().parents[1] / "editorial" / "personas" / "live_host_preview.md"


class VoicePreviewSession(VoiceSession):
    system_prompt: str


@router.post("/session")
def create_voice_session(request: Request, response: Response) -> VoicePreviewSession:
    if (
        request.client is None
        or request.client.host not in {"127.0.0.1", "::1"}
        or request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise HTTPException(status_code=403, detail="Voice sessions are limited to local development.")

    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    agent_id = os.getenv("ELEVENLABS_AGENT_ID", "").strip()
    if not api_key or not agent_id:
        raise HTTPException(status_code=503, detail="ElevenLabs voice is not configured.")

    system_prompt = PREVIEW_PROMPT.read_text(encoding="utf-8").strip()
    try:
        session = request_session(api_key=api_key, agent_id=agent_id)
    except (URLError, TimeoutError, ValueError):
        raise HTTPException(status_code=502, detail="ElevenLabs could not start a voice session.") from None

    response.headers["Cache-Control"] = "no-store"
    return VoicePreviewSession(
        token=session.token, conversation_id=session.conversation_id, system_prompt=system_prompt
    )


@router.get("/catalog")
def voice_catalog():
    try:
        with connect() as conn:
            rows = conn.execute("""
                select id, name, coalesce(tagline, ''), slug
                from public.agent_personas
                order by (slug = 'main-host') desc, name, id
            """).fetchall()
    except (psycopg.Error, RuntimeError):
        raise HTTPException(503, "The voice catalog is unavailable.") from None

    entries = []
    for persona_id, name, tagline, slug in rows:
        clip = preview_path(slug)
        url = f"{PREVIEW_URL}/{clip.name}" if clip is not None and clip.is_file() else None
        entries.append({"id": persona_id, "name": name, "tagline": tagline, "preview_url": url})
    return entries
