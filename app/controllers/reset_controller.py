"""Staging-only full wipes of PC state (sessions, cards, swipe history, taste;
DB and in-memory). 403 when PC_ENV is production.

POST /api/reset_user/{user_id} — one user.
POST /api/reset_all — every user.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from core.errors import BadRequest
from app.controllers import require_internal_key
from app.deps import reset_service
from app.schemas import ResetAllResult, ResetUserResult

router = APIRouter()


@router.post("/api/reset_all", response_model=ResetAllResult,
             dependencies=[Depends(require_internal_key)])
def reset_all() -> ResetAllResult:
    return ResetAllResult(**reset_service.reset_all())


@router.post("/api/reset_user/{user_id}", response_model=ResetUserResult,
             dependencies=[Depends(require_internal_key)])
def reset_user(user_id: str) -> ResetUserResult:
    try:
        uuid.UUID(user_id)
    except ValueError:
        raise BadRequest("user_id must be a valid UUID")
    return ResetUserResult(**reset_service.reset_user(user_id))
