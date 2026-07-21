"""pc_sessions (spec §3.2, §5.9). In-memory store + spool while DB writes off.

A session is a plain dict with the pc_sessions columns. At most one row per user
is 'active'/'abandoned' at a time (spec §5.9), enforced by find_open().
"""
from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

from app.repositories.base import gateway, supabase

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionRepo:
    def __init__(self, max_in_memory: int = 5000) -> None:
        self._max = max_in_memory
        self._by_id: OrderedDict[str, dict] = OrderedDict()

    def create(self, user_id: str, cards_limit: int, rights_limit: int,
               superlikes_limit: int, deck_version: str) -> dict:
        session = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "status": "active",
            "end_reason": None,
            "cards_limit": cards_limit,
            "rights_limit": rights_limit,
            "superlikes_limit": superlikes_limit,
            "cards_served": 0,
            "cards_swiped": 0,
            "rights_used": 0,
            "superlikes_used": 0,
            "deck_version": deck_version,
            "started_at": None,          # set on the user's FIRST SWIPE (spec §5.6)
            "last_activity_at": _now(),
            "ended_at": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._by_id[session["id"]] = session
        while len(self._by_id) > self._max:
            self._by_id.popitem(last=False)
        gateway.spool("pc_sessions", session)
        if gateway.db_enabled:
            gateway.db_async(lambda: supabase().table("pc_sessions").insert(session).execute())
        return session

    def get(self, session_id: str) -> dict | None:
        s = self._by_id.get(session_id)
        if s is None and gateway.db_enabled:
            rows = (supabase().table("pc_sessions").select("*")
                    .eq("id", session_id).limit(1).execute().data)
            s = rows[0] if rows else None
            if s:
                self._by_id[session_id] = s
        return s

    def find_open(self, user_id: str) -> dict | None:
        """The user's single active/abandoned row, if any (spec §5.9)."""
        for s in reversed(self._by_id.values()):
            if s["user_id"] == user_id and s["status"] in ("active", "abandoned"):
                return s
        if gateway.db_enabled:
            rows = (supabase().table("pc_sessions").select("*")
                    .eq("user_id", user_id).in_("status", ["active", "abandoned"])
                    .order("created_at", desc=True).limit(1).execute().data)
            if rows:
                self._by_id[rows[0]["id"]] = rows[0]
                return rows[0]
        return None

    def update(self, session_id: str, patch: dict) -> dict:
        s = self._by_id.get(session_id)
        if s is None:
            s = self.get(session_id) or {}
        s.update(patch)
        s["updated_at"] = _now()
        self._by_id[session_id] = s
        gateway.spool("pc_session_updates", {"session_id": session_id, **patch})
        if gateway.db_enabled:
            gateway.db_async(
                lambda: supabase().table("pc_sessions").update(patch).eq("id", session_id).execute()
            )
        return s
