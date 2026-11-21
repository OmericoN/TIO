import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

TOKEN_URL = "https://api.elevenlabs.io/v1/convai/conversation/token"


class VoiceSession(BaseModel):
    token: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)


def request_session(*, api_key: str, agent_id: str) -> VoiceSession:
    request = Request(f"{TOKEN_URL}?{urlencode({'agent_id': agent_id})}")
    request.add_unredirected_header("xi-api-key", api_key)
    with urlopen(request, timeout=5) as upstream:
        return VoiceSession.model_validate(json.load(upstream))
