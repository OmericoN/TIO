from typing import Literal
from urllib.error import URLError
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from briefing.generate import UnknownSelectedArticles, generate
from briefing.timing import PlannedStory
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
    mode: Literal["debate"] = "debate"
    expert_count: int = Field(default=2, ge=1, le=4, description="Guests in a debate (the player's experts dial).")
    focus: str | None = Field(default=None, min_length=2, max_length=500,
                              description="The focus the list was made with, so added articles respect it.")
    days: int | None = Field(default=None, ge=1, le=365)
    excluded_article_ids: list[str] = Field(default_factory=list, max_length=500)
    favorite_persona_ids: list[UUID] | None = Field(
        default=None, max_length=25, description="The listener's saved voices, in their order (Profile). Guest "
        "experts come from these first. Omitted: the voices picked at sign-up.")


class GeneratePlan(BaseModel):
    article_ids: list[str] = Field(description="Final articles, story by story.")
    profile: str
    persona_ids: list[str] = Field(description="Every persona, ordered by the listener's preferences.")
    hosts: list[str] = Field(description="Up to four guest experts: the listener's favourites first, then their "
                                         "ranking, minus personas too jokey for the plan's most sensitive tone.")
    tones: dict[str, str] = Field(description="Preset tone per article (articles.tone).")
    segments: list[PlannedStory]
    estimated_minutes: float
    requested_minutes: float
    dropped_article_ids: list[str]
    added_article_ids: list[str]
    untoned_article_ids: list[str] = Field(description="Planned articles with no preset tone yet.")


@router.post("/generate")
def generate_briefing(body: GenerateRequest, request: Request) -> GeneratePlan:
    """Fit the recommended list to the chosen length (drop or add articles), then pick hosts that suit the tones."""
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Generate is limited to local development.")
    try:
        with connect() as conn:
            return GeneratePlan(**generate(conn, str(body.user_id), body.article_ids, body.selected_article_ids,
                                           body.minutes, focus=body.focus, days=body.days,
                                           expert_count=body.expert_count,
                                           excluded_article_ids=body.excluded_article_ids,
                                           favourite_persona_ids=None if body.favorite_persona_ids is None
                                           else [str(i) for i in body.favorite_persona_ids]))
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail="No profile with that user_id.") from None
    except (NoProfileSignal, UnknownSelectedArticles) as err:
        raise HTTPException(status_code=422, detail=str(err)) from None
    except (EmbeddingError, URLError, TimeoutError):
        raise HTTPException(status_code=502, detail="Could not create an embedding.") from None
