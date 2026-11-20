from typing import Literal
from urllib.error import URLError
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from briefing.generate import generate
from recommender.db import connect
from recommender.embeddings import EmbeddingError
from recommender.recommend import NoProfileSignal, ProfileNotFound

router = APIRouter(prefix="/briefing", tags=["briefing"])


class GenerateRequest(BaseModel):
    """Sent when the listener presses Generate: the list they were shown, what they picked, and the length."""

    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    article_ids: list[str] = Field(min_length=1, max_length=200, description="The list from POST /api/recommend.")
    selected_article_ids: list[str] = Field(default_factory=list, max_length=50,
                                            description="Articles the listener definitely wants; always kept.")
    minutes: float = Field(ge=1, le=120, description="Podcast length chosen by the listener.")
    mode: Literal["presenter", "debate"] = "presenter"
    focus: str | None = Field(default=None, min_length=2, max_length=500,
                              description="The focus the list was made with, so added articles respect it.")


class Segment(BaseModel):
    article_ids: list[str]
    estimated_seconds: int


class GeneratePlan(BaseModel):
    article_ids: list[str] = Field(description="Final articles, story by story.")
    persona_ids: list[str] = Field(description="Every persona, ordered by the listener's preferences.")
    hosts: list[str] = Field(description="1 (presenter) or 2 (debate) personas, our order minus the matcher's vetoes.")
    hosts_from_matcher: bool
    tones: dict[str, str] = Field(description="Tone per article from the matcher (empty if it failed).")
    segments: list[Segment]
    estimated_minutes: float
    requested_minutes: float
    dropped_article_ids: list[str]
    added_article_ids: list[str]
    matcher_error: str | None


@router.post("/generate")
def generate_briefing(body: GenerateRequest, request: Request) -> GeneratePlan:
    """Fit the recommended list to the chosen length (drop or add articles), then run the tone matcher."""
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Generate is limited to local development.")
    try:
        with connect() as conn:
            return GeneratePlan(**generate(conn, str(body.user_id), body.article_ids, body.selected_article_ids,
                                           body.minutes, body.mode, body.focus))
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail="No profile with that user_id.") from None
    except NoProfileSignal as err:
        raise HTTPException(status_code=422, detail=str(err)) from None
    except (EmbeddingError, URLError, TimeoutError):
        raise HTTPException(status_code=502, detail="Could not create an embedding.") from None
