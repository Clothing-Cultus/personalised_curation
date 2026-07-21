"""POST /api/start_session/{user_id} (spec §6.2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.controllers import require_internal_key
from app.deps import session_service
from app.schemas import SessionData

router = APIRouter()


@router.post("/api/start_session/{user_id}", response_model=SessionData,
             dependencies=[Depends(require_internal_key)])
def start_session(user_id: str) -> SessionData:
    result = session_service.start_session(user_id)
    return SessionData(**result)
