"""Local test page for the recommender: http://127.0.0.1:8000/recommender-lab

The page calls POST /api/recommend like any client, then these helpers to show headlines and names for the ids.
Local development only, like the other recommender routes.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from recommender.db import connect

router = APIRouter(tags=["recommender lab"])
LAB_PAGE = Path(__file__).resolve().parents[1] / "recommender" / "lab.html"


def _local_only(request: Request) -> None:
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="The recommender lab is limited to local development.")


@router.get("/recommender-lab", response_class=HTMLResponse, include_in_schema=False)
def lab_page(request: Request) -> HTMLResponse:
    _local_only(request)
    return HTMLResponse(LAB_PAGE.read_text(encoding="utf-8"))


@router.get("/api/recommend/catalog")
def catalog(request: Request) -> dict:
    """Seeded listeners and personas, for the lab's pickers."""
    _local_only(request)
    with connect() as conn:
        users = conn.execute(
            "select id::text, coalesce(display_name, email), occupation, interests_text, briefing_length_minutes "
            "from public.profiles where interests_text is not null order by display_name").fetchall()
        personas = conn.execute(
            "select id::text, slug, name, tagline from public.agent_personas order by name").fetchall()
    return {
        "users": [dict(zip(["id", "name", "occupation", "interests", "minutes"], u)) for u in users],
        "personas": [dict(zip(["id", "slug", "name", "tagline"], p)) for p in personas],
    }


class LookupRequest(BaseModel):
    article_ids: list[str] = Field(max_length=500)


@router.post("/api/recommend/lookup")
def lookup(body: LookupRequest, request: Request) -> dict:
    """Headline, outlet, language, date, link and publisher photo for article ids."""
    _local_only(request)
    with connect() as conn:
        rows = conn.execute(
            """
            select a.id, a.headline, p.name, a.language::text, a.published_at, a.permalink, a.translation_of,
                   a.main_subject, a.people, case when a.thumb_url ~ '^https?://' then a.thumb_url end
            from public.articles a join public.publications p on p.id = a.publication_id
            where a.id = any(%s)
            """,
            (body.article_ids,),
        ).fetchall()
    keys = ["id", "headline", "publication", "language", "published_at", "permalink", "translation_of",
            "main_subject", "people", "thumb_url"]
    return {r[0]: {**dict(zip(keys, r)), "published_at": r[4].isoformat()} for r in rows}
