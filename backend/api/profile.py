import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

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
