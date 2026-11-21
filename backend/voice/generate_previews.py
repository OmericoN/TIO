"""Render the signup catalog with OpenAI Realtime as an offline maintenance task."""

import argparse
import base64
import json
import os
import re
import tempfile
import time
import wave
from pathlib import Path
from urllib.parse import urlencode

from psycopg.rows import dict_row

from discussion.personas import AgentPersona
from recommender.db import connect
from discussion.realtime import REALTIME_MODEL, voice_casting, voice_delivery
from voice.previews import preview_path

SAMPLE = "Here is your briefing. Let's look at what changed today and why it matters."
SAMPLE_RATE = 24_000


def render_audio(socket, persona: AgentPersona, voice: str, direction: str) -> bytes:
    socket.send(json.dumps({"type": "session.update", "session": {
        "type": "realtime", "output_modalities": ["audio"],
        "audio": {"output": {"voice": voice, "format": {"type": "audio/pcm", "rate": SAMPLE_RATE}}},
    }}))
    prompt = f"""Read the sample aloud in English, exactly as written, once. Say only the sample text.
{voice_delivery(persona, direction)}

Sample: {SAMPLE}"""
    audio = bytearray()
    transcript = ""
    deadline = time.monotonic() + 45
    while (remaining := deadline - time.monotonic()) > 0:
        event = json.loads(socket.recv(timeout=remaining))
        match event["type"]:
            case "session.updated":
                socket.send(json.dumps({"type": "response.create", "response": {
                    "instructions": prompt, "max_output_tokens": 512,
                }}))
            case "error":
                raise ValueError(event["error"]["message"])
            case "response.output_audio.delta":
                audio.extend(base64.b64decode(event["delta"], validate=True))
                if len(audio) > SAMPLE_RATE * 2 * 12:
                    raise ValueError("Sample exceeded twelve seconds")
            case "response.output_audio_transcript.done":
                transcript = event["transcript"]
            case "response.done":
                response = event["response"]
                if response["status"] != "completed":
                    reason = (response.get("status_details") or {}).get("reason", response["status"])
                    raise ValueError(f"Realtime response ended early ({reason})")
                if len(audio) < SAMPLE_RATE * 2 or len(audio) % 2:
                    raise ValueError("No complete PCM sample")
                if re.sub(r"[^a-z]", "", transcript.lower()) != re.sub(r"[^a-z]", "", SAMPLE.lower()):
                    raise ValueError(f"Unexpected sample transcript: {transcript!r}")
                return bytes(audio)
    raise TimeoutError("Timed out waiting for Realtime audio")


def save_wav(path: Path, audio: bytes) -> None:
    temporary = tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False)
    temp_path = Path(temporary.name)
    try:
        with temporary:
            with wave.open(temporary, "wb") as output:
                output.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                output.writeframes(audio)
        temp_path.chmod(0o644)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> int:
    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect as websocket_connect

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", help="Render or replace the clip for one persona")
    args = parser.parse_args()
    with connect() as db:
        db.row_factory = dict_row
        rows = db.execute("""
            select id, slug, name, personality_md, system_prompt, pace_wpm, accent, voice_settings
            from public.agent_personas where slug = coalesce(%s, slug) order by slug
        """, (args.slug,)).fetchall()
    if not rows:
        parser.error("No matching personas in the voice catalog")

    failed = False
    url = f"wss://api.openai.com/v1/realtime?{urlencode({'model': REALTIME_MODEL})}"
    for row in rows:
        slug = row["slug"]
        path = preview_path(slug)
        if path is None:
            print(f"Skipped invalid persona slug: {slug!r}", flush=True)
            failed = True
            continue
        if path.is_file() and args.slug is None:
            continue
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            parser.error("Set OPENAI_API_KEY to render voice previews")
        try:
            persona = AgentPersona.model_validate(row)
            candidates, direction = voice_casting(persona)
            voice = candidates[0]
            with websocket_connect(url, additional_headers={"Authorization": f"Bearer {key}"},
                                   open_timeout=15, close_timeout=2) as socket:
                audio = render_audio(socket, persona, voice, direction)
            save_wav(path, audio)
            print(f"{slug}: {voice}, {len(audio) / (2 * SAMPLE_RATE):.1f}s", flush=True)
        except (OSError, ValueError, WebSocketException, RuntimeError) as error:
            print(f"{slug}: {error}", flush=True)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
