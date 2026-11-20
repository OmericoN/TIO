import os
from urllib.error import URLError

from fastapi import APIRouter, HTTPException, Request, Response

from voice.elevenlabs import VoiceSession, request_session

router = APIRouter(prefix="/voice", tags=["voice"])


@router.post("/session")
def create_voice_session(request: Request, response: Response) -> VoiceSession:
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

    try:
        session = request_session(api_key=api_key, agent_id=agent_id)
    except (URLError, TimeoutError, ValueError):
        raise HTTPException(status_code=502, detail="ElevenLabs could not start a voice session.") from None

    response.headers["Cache-Control"] = "no-store"
    return session
