import io
import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from fastapi import HTTPException, Request, Response

from api.voice import create_voice_session
from main import app


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

        self.assertEqual(session.model_dump(), provider_session)
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
