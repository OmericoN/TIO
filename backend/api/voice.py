import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from uuid import UUID

import psycopg

from fastapi import APIRouter, HTTPException, Request, Response

from recommender.db import connect
from voice.elevenlabs import VoiceSession, request_session, voice_preview_url

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
                select id, name, coalesce(tagline, ''), voice_id is not null as has_voice
                from public.agent_personas
                order by (slug = 'main-host') desc, name, id
            """).fetchall()
    except (psycopg.Error, RuntimeError):
        raise HTTPException(503, "The voice catalog is unavailable.") from None

    return [
        {"id": persona_id, "name": name, "tagline": tagline, "can_preview": has_voice}
        for persona_id, name, tagline, has_voice in rows
    ]


@router.get("/catalog/{persona_id}/preview")
def preview_persona_voice(persona_id: UUID):
    try:
        with connect() as conn:
            row = conn.execute(
                "select voice_id from public.agent_personas where id = %s", (persona_id,)
            ).fetchone()
    except (psycopg.Error, RuntimeError):
        raise HTTPException(503, "The voice catalog is unavailable.") from None
    if not row or not row[0]:
        raise HTTPException(404, "This persona has no voice sample yet.")

    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(503, "Voice previews need ELEVENLABS_API_KEY in the backend environment.")
    try:
        return {"url": voice_preview_url(api_key=api_key, voice_id=row[0])}
    except HTTPError as error:
        if error.code in {401, 403}:
            raise HTTPException(502, "ElevenLabs rejected access to this voice. Check the backend key and its voice permissions.") from None
        if error.code == 404:
            raise HTTPException(404, "This persona has no voice sample yet.") from None
        raise HTTPException(502, "Could not fetch this voice sample.") from None
    except (URLError, TimeoutError, ValueError):
        raise HTTPException(502, "Could not fetch this voice sample.") from None
