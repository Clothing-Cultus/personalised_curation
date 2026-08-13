"""POST /api/start_session/{user_id} (spec §6.2)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from core.errors import BadRequest
from app.controllers import require_internal_key
from app.deps import session_service
from app.schemas import SessionData

router = APIRouter()


@router.post("/api/start_session/{user_id}", response_model=SessionData,
             dependencies=[Depends(require_internal_key)])
def start_session(user_id: str) -> SessionData:
    # user_id maps to uuid columns (pc_sessions.user_id, user_taste.user_id) and
    # is used to look up users — reject a non-UUID cleanly (400) instead of
    # letting a downstream DB query blow up as a 500.
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise BadRequest("user_id must be a valid UUID")
    result = session_service.start_session(user_id)
    return SessionData(**result)
