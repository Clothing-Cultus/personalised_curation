"""Staging-only user reset: wipe everything PC knows about one user so their
next start_session is a brand-new, cold-start user.

PC keeps sessions, served cards, swipe history and taste vectors in process
memory, so deleting the pc_* / user_taste rows from Supabase alone isn't enough
— the running container would keep serving from its caches. This clears both.
The purchase ledger (pc_purchase_weights) is kept, so a later rebuild doesn't
credit the same purchase twice.
"""
from __future__ import annotations

import logging

from core.config import ENV
from core.errors import Forbidden
from app.repositories.base import gateway

logger = logging.getLogger(__name__)


class ResetService:
    def __init__(self, session_repo, items_repo, events_repo, taste_repo) -> None:
        self.sessions = session_repo
        self.items = items_repo
        self.events = events_repo
        self.taste = taste_repo

    def reset_user(self, user_id: str) -> dict:
        if ENV.pc_env == "production":
            raise Forbidden("User reset is disabled in production")
        # let queued writes land first so none re-create rows after the purge
        gateway.flush()
        session_ids = self.sessions.ids_for_user(user_id)
        # children before pc_sessions (FK order)
        self.items.purge_user(user_id, session_ids)
        self.events.purge_user(user_id)
        self.sessions.purge_user(user_id, session_ids)
        self.taste.purge_user(user_id)
        logger.warning("staging reset_user user_id=%s sessions_deleted=%d",
                       user_id, len(session_ids))
        return {"user_id": user_id, "sessions_deleted": len(session_ids)}
