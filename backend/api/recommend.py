from urllib.error import URLError
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from recommender.db import connect
from recommender.embeddings import EmbeddingError
from recommender.focus import parse_focus
from recommender.recommend import EXPLORATION, NoProfileSignal, ProfileNotFound, load_listener, recommend

router = APIRouter(tags=["recommend"])


class RecommendRequest(BaseModel):
    """Every listener is a signed-up user: languages, interests and history come from their profile."""

    model_config = ConfigDict(extra="forbid")  # e.g. a leftover profile_text is an error, not silently ignored
    user_id: UUID
    limit: int | None = Field(default=None, ge=1, le=200,
                              description="How many articles to return (default 50). The podcast length is chosen "
                                          "later, at POST /api/briefing/generate.")
    days: int | None = Field(default=None, ge=1, le=365,
                             description="Only articles from the last N days. Default: any time, unless the focus names a range.")
    focus: str | None = Field(default=None, min_length=2, max_length=500,
                              description="Restricts the articles, e.g. 'Elon Musk and Tesla, no crypto, last 2 days'.")
    pinned_article_ids: list[str] = Field(default_factory=list, max_length=30,
                                          description="Articles the listener picked; always included, first.")
    excluded_article_ids: list[str] = Field(default_factory=list, max_length=500)
    exploration: float = Field(default=EXPLORATION, ge=0, le=0.2,
                               description="How much the picks vary between calls: 0 always returns the same list.")
    seed: int | None = Field(default=None, description="Repeat a previous random draw (for tests and debugging).")


class Recommendation(BaseModel):
    article_ids: list[str]
    persona_ids: list[str] = Field(description="Every persona, ordered from best to worst fit for this briefing.")


@router.post("/recommend")
def recommend_articles(body: RecommendRequest, request: Request) -> Recommendation:
    """Articles that fit the listener (each event followed by its coverage from other outlets, for story grouping
    downstream), and every persona ordered by how well it fits them."""
    # Like the voice session: no authentication yet, so only serve local development.
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Recommendations are limited to local development.")
    try:
        with connect() as conn:
            listener = load_listener(conn, str(body.user_id))
            return Recommendation(**recommend(
                conn, listener, parse_focus(body.focus) if body.focus else None, body.pinned_article_ids,
                body.excluded_article_ids, body.days, body.limit,
                exploration=body.exploration, seed=body.seed))
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail="No profile with that user_id.") from None
    except NoProfileSignal as err:
        raise HTTPException(status_code=422, detail=str(err)) from None
    except (EmbeddingError, URLError, TimeoutError):
        raise HTTPException(status_code=502, detail="Could not create an embedding.") from None
