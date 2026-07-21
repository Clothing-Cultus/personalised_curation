"""POST /api/suggest (spec §6.2). One interaction (or a small batch) per call;
the ack carries the full SessionData with data:[] most times, a batch occasionally."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.controllers import require_internal_key
from app.deps import session_service
from app.schemas import SessionData, SuggestRequestBody

router = APIRouter()


@router.post("/api/suggest", response_model=SessionData,
             dependencies=[Depends(require_internal_key)])
def suggest(body: SuggestRequestBody) -> SessionData:
    result = session_service.suggest(
        session_id=body.session_id,
        interactions=[i.model_dump() for i in body.interactions],
        products_swiped=body.products_swiped,
    )
    return SessionData(**result)
