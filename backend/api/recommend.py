from typing import Literal
from urllib.error import URLError
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from research.db import connect
from research.embeddings import EmbeddingError
from research.recommend import NoProfileSignal, ProfileNotFound, recommend_for_text, recommend_for_user

router = APIRouter(prefix="/recommend", tags=["recommend"])


class RecommendRequest(BaseModel):
    """Either a saved listener (`user_id`) or a live wildcard described in words (`profile_text`)."""

    user_id: UUID | None = None
    profile_text: str | None = Field(default=None, min_length=3, max_length=4000)
    languages: list[Literal["en", "de", "nl"]] = Field(default_factory=lambda: ["en"], min_length=1)
    minutes: int | None = Field(default=None, ge=1, le=60)
    days: int | None = Field(default=7, ge=1, le=365)

    @model_validator(mode="after")
    def one_listener(self):
        if (self.user_id is None) == (self.profile_text is None):
            raise ValueError("Send either user_id or profile_text.")
        return self


@router.post("")
def recommend(body: RecommendRequest, request: Request) -> dict:
    # Like the voice session: no authentication yet, so only serve local development.
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Recommendations are limited to local development.")
    try:
        with connect() as conn:
            if body.user_id is not None:
                return recommend_for_user(conn, str(body.user_id), body.minutes, body.days)
            return recommend_for_text(conn, body.profile_text, body.languages, body.minutes, body.days)
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail="No profile with that user_id.") from None
    except NoProfileSignal as err:
        raise HTTPException(status_code=422, detail=str(err)) from None
    except (EmbeddingError, URLError, TimeoutError):
        raise HTTPException(status_code=502, detail="Could not create the listener embedding.") from None
