import json
import os
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

import psycopg
from fastapi import APIRouter, Header, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints

from recommender.config import EMBED_MODEL
from recommender.db import connect
from recommender.embeddings import EmbeddingError
from recommender.recommend import NoProfileSignal, ProfileNotFound, ensure_user_embedding

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileEmbedding(BaseModel):
    source: str
    model: str


def signed_in_user_id(authorization: str | None) -> str:
    """The Supabase user behind a bearer access token, checked with Supabase Auth."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Sign in first.")
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    if not supabase_url or not api_key:
        raise HTTPException(status_code=503, detail="Supabase is not configured on the backend.")
    request = UrlRequest(f"{supabase_url}/auth/v1/user",
                         headers={"apikey": api_key, "Authorization": f"Bearer {token.strip()}"})
    try:
        with urlopen(request, timeout=10) as response:
            user = json.load(response)
    except HTTPError as err:
        if err.code in {401, 403}:
            raise HTTPException(status_code=401, detail="Your session has expired. Sign in again.") from None
        raise HTTPException(status_code=502, detail="Supabase could not check the session.") from None
    except (URLError, TimeoutError, ValueError):
        raise HTTPException(status_code=502, detail="Supabase could not check the session.") from None
    if not isinstance(user, dict) or not isinstance(user.get("id"), str):
        raise HTTPException(status_code=502, detail="Supabase could not check the session.")
    return user["id"]


@router.post("/embed")
def embed_profile(authorization: str | None = Header(default=None)) -> ProfileEmbedding:
    """Build the signed-in listener's interest vector, if it is missing, from an older model or behind their history.
    Called at sign-in so the first recommendations don't wait for it. Cheap when the vector is current."""
    user_id = signed_in_user_id(authorization)
    try:
        with connect() as conn:
            _, profile = ensure_user_embedding(conn, user_id)
    except ProfileNotFound:
        raise HTTPException(status_code=404, detail="No profile for this account.") from None
    except NoProfileSignal as err:
        raise HTTPException(status_code=422, detail=str(err)) from None
    except (EmbeddingError, URLError, TimeoutError):
        raise HTTPException(status_code=502, detail="Could not create an embedding.") from None
    return ProfileEmbedding(source=profile["interest_embedding_source"], model=EMBED_MODEL)


# The interest radar: how strongly the listener follows each topic, 1 (somewhat) to 3 (very). Topics at 0 are left
# out. Mirrors the profiles_interest_levels_valid check; interests_text is a comma-separated list, hence no commas.
Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[^,;\n]+$")]
Level = Annotated[int, Field(ge=1, le=3)]


class InterestUpdate(BaseModel):
    levels: dict[Topic, Level] = Field(default_factory=dict, max_length=40)
    other: str = Field(default="", max_length=300)


class SavedInterests(BaseModel):
    levels: dict[str, int]
    interests_text: str | None


def interests_text(levels: dict[str, int], other: str) -> str | None:
    """The recommender's interest list: the strongest topics first (ties keep their order), then the listener's
    own words. The same format sign-up writes."""
    ranked = [topic for topic, _ in sorted(levels.items(), key=lambda item: -item[1])]
    own = " ".join(other.split())
    return ", ".join([*ranked, own] if own else ranked) or None


@router.get("/interests")
def read_interests(authorization: str | None = Header(default=None)) -> SavedInterests:
    """The signed-in listener's saved interest radar and interest list."""
    user_id = signed_in_user_id(authorization)
    try:
        with connect() as conn:
            row = conn.execute("select interest_levels, interests_text from public.profiles where id = %s",
                               (user_id,)).fetchone()
    except (RuntimeError, psycopg.Error):
        raise HTTPException(status_code=503, detail="Saved interests are unavailable.") from None
    if row is None:
        raise HTTPException(status_code=404, detail="No profile for this account.")
    return SavedInterests(levels=row[0] or {}, interests_text=row[1])


@router.put("/interests")
def save_interests(body: InterestUpdate, authorization: str | None = Header(default=None)) -> SavedInterests:
    """Save the radar and rewrite interests_text from it. A changed radar or list drops the interest vector, so
    the recommender rebuilds it on its next call; the topic facets are read from interests_text on every call."""
    user_id = signed_in_user_id(authorization)
    text = interests_text(body.levels, body.other)
    try:
        with connect() as conn:
            row = conn.execute(
                """
                update public.profiles
                set interest_levels = %s,
                    interests_text = %s,
                    interest_embedding = case when interests_text is distinct from %s
                                                or interest_levels is distinct from %s then null
                                              else interest_embedding end
                where id = %s
                returning interest_levels, interests_text
                """,
                (Jsonb(body.levels), text, text, Jsonb(body.levels), user_id),
            ).fetchone()
            conn.commit()
    except (RuntimeError, psycopg.Error):
        raise HTTPException(status_code=503, detail="Your interests could not be saved.") from None
    if row is None:
        raise HTTPException(status_code=404, detail="No profile for this account.")
    return SavedInterests(levels=row[0] or {}, interests_text=row[1])
