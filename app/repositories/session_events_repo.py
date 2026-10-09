"""pc_session_events (spec §5.7) — one row per session lifecycle transition
(created / first_swipe / abandoned / resumed / renewed / ended), for later
analysis of how users start, abandon, and return."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.repositories.base import gateway, supabase

logger = logging.getLogger(__name__)


class SessionEventsRepo:
    def record(self, session_id: str, user_id: str, event: str, at: str | None = None) -> None:
        rec = {
            "session_id": session_id, "user_id": user_id, "event": event,
            "at": at or datetime.now(timezone.utc).isoformat(),
        }
        gateway.spool("pc_session_events", rec)
        if gateway.db_enabled:
            gateway.db_async(lambda: supabase().table("pc_session_events").insert(rec).execute())

    def purge_user(self, user_id: str) -> None:
        gateway.spool("pc_session_events_purge_user", {"user_id": user_id})
        if gateway.db_enabled:
            supabase().table("pc_session_events").delete().eq("user_id", user_id).execute()

    def purge_all(self) -> None:
        gateway.spool("pc_session_events_purge_all", {})
        if gateway.db_enabled:
            supabase().table("pc_session_events").delete().not_.is_("id", "null").execute()
