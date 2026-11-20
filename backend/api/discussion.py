from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from discussion.speakers import plan_speaker

router = APIRouter(prefix="/discussion", tags=["discussion"])


class SpeakerRequest(BaseModel):
    ranked_host_ids: list[UUID]
    addressed_host_id: UUID | None = None
    reply_to_host_id: UUID | None = None


class SpeakerPlan(BaseModel):
    roster: list[UUID]
    next_host_id: UUID | None


@router.post("/next-speaker")
def next_speaker(request: SpeakerRequest) -> SpeakerPlan:
    try:
        roster, next_host_id = plan_speaker(
            request.ranked_host_ids,
            addressed_host_id=request.addressed_host_id,
            reply_to_host_id=request.reply_to_host_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return SpeakerPlan(roster=roster, next_host_id=next_host_id)
