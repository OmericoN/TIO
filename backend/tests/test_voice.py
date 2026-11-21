import base64
import io
import json
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.error import URLError
from uuid import UUID

from fastapi import HTTPException, Request, Response

from api.voice import PREVIEW_PROMPT, create_voice_session, voice_catalog
from discussion.personas import AgentPersona
from main import app
from voice.generate_previews import SAMPLE, SAMPLE_RATE, render_audio, save_wav


class VoiceSessionTest(unittest.TestCase):
    def request(self, host: str = "127.0.0.1", headers=()) -> Request:
        return Request({"type": "http", "client": (host, 12345), "headers": headers})

    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test-key", "ELEVENLABS_AGENT_ID": "agent_test"})
    @patch("voice.elevenlabs.urlopen")
    def test_returns_only_session_fields(self, urlopen) -> None:
        self.assertIn("post", app.openapi()["paths"]["/api/voice/session"])
        provider_session = {"token": "test-token", "conversation_id": "conv_test"}
        urlopen.return_value = io.BytesIO(json.dumps({**provider_session, "extra": "ignored"}).encode())
        response = Response()

        session = create_voice_session(self.request(), response)

        self.assertEqual(session.model_dump(), {
            **provider_session,
            "system_prompt": PREVIEW_PROMPT.read_text(encoding="utf-8").strip(),
        })
        self.assertEqual(response.headers["cache-control"], "no-store")
        upstream_request = urlopen.call_args.args[0]
        self.assertEqual(
            upstream_request.full_url,
            "https://api.elevenlabs.io/v1/convai/conversation/token?agent_id=agent_test",
        )
        self.assertEqual(upstream_request.get_header("Xi-api-key"), "test-key")

    @patch("voice.elevenlabs.urlopen")
    def test_denies_remote_and_cross_site_requests(self, urlopen) -> None:
        for request in (
            self.request("192.0.2.1"),
            self.request(headers=[(b"sec-fetch-site", b"cross-site")]),
        ):
            with self.assertRaises(HTTPException) as failure:
                create_voice_session(request, Response())
            self.assertEqual(failure.exception.status_code, 403)
        urlopen.assert_not_called()

    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "", "ELEVENLABS_AGENT_ID": ""})
    @patch("voice.elevenlabs.urlopen")
    def test_missing_configuration(self, urlopen) -> None:
        with self.assertRaises(HTTPException) as failure:
            create_voice_session(self.request(), Response())
        self.assertEqual(failure.exception.status_code, 503)
        urlopen.assert_not_called()

    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test-key", "ELEVENLABS_AGENT_ID": "agent_test"})
    @patch("voice.elevenlabs.urlopen")
    def test_provider_failure_and_invalid_response(self, urlopen) -> None:
        urlopen.side_effect = [URLError("upstream failure"), io.BytesIO(b'{"token": "incomplete"}')]
        for _ in range(2):
            with self.assertRaises(HTTPException) as failure:
                create_voice_session(self.request(), Response())
            self.assertEqual(failure.exception.status_code, 502)
            self.assertEqual(failure.exception.detail, "ElevenLabs could not start a voice session.")


class StoredPreviewTest(unittest.TestCase):
    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "", "OPENAI_API_KEY": ""})
    @patch("api.voice.connect")
    def test_catalog_only_offers_existing_clips(self, connect):
        persona_id = UUID("11111111-1111-4111-8111-111111111111")
        connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [
            (persona_id, "Ada", "Markets", "market-voice"),
            (persona_id, "Grace", "Rates", "missing"),
            (persona_id, "Sam", "World", "../outside"),
        ]
        with TemporaryDirectory() as directory, patch("voice.previews.PREVIEW_DIR", Path(directory)):
            (Path(directory) / "market-voice.wav").touch()
            entries = voice_catalog()
        self.assertEqual(entries[0], {"id": persona_id, "name": "Ada", "tagline": "Markets",
                                      "preview_url": "/api/voice/previews/market-voice.wav"})
        self.assertEqual([row["preview_url"] for row in entries[1:]], [None, None])

    def test_render_writes_a_wav_only_for_the_expected_complete_sample(self):
        persona = AgentPersona(id=UUID(int=1), name="Ada", slug="ada", personality_md="Analyst.", accent="Irish")
        pcm = b"\x01\x00" * SAMPLE_RATE
        events = [
            {"type": "session.updated"},
            {"type": "response.output_audio.delta", "delta": base64.b64encode(pcm).decode()},
            {"type": "response.output_audio_transcript.done", "transcript": SAMPLE},
            {"type": "response.done", "response": {"status": "completed"}},
        ]
        socket = Mock()
        socket.recv.side_effect = map(json.dumps, events)

        audio = render_audio(socket, persona, "sage", "Measured and warm.")

        session, response = (json.loads(call.args[0]) for call in socket.send.call_args_list)
        self.assertEqual(session["session"]["audio"]["output"]["voice"], "sage")
        self.assertIn("Measured and warm.\nAccent: Irish.", response["response"]["instructions"])
        with TemporaryDirectory() as directory:
            clip = Path(directory) / "ada.wav"
            save_wav(clip, audio)
            with wave.open(str(clip), "rb") as stored:
                self.assertEqual((stored.getnchannels(), stored.getsampwidth(), stored.getframerate()), (1, 2, SAMPLE_RATE))
                self.assertEqual(stored.readframes(SAMPLE_RATE), pcm)

        events[2]["transcript"] = "Different text."
        socket.recv.side_effect = map(json.dumps, events)
        with self.assertRaisesRegex(ValueError, "Unexpected sample transcript"):
            render_audio(socket, persona, "sage", "Measured and warm.")
