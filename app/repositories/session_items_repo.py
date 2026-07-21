"""pc_session_items (spec §3.2). The per-card serving log — one row written at
serve time with swipe=NULL, updated in place when the swipe arrives.

This is the durable record of what was shown, and (across a user's sessions) the
source of the FILTER-step exclusions and the raw history the eventual vector
replay job (PC-todo-later §5) reconstructs from.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from app.repositories.base import gateway, supabase

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionItemsRepo:
    def __init__(self) -> None:
        # session_id → {product_id → item dict}
        self._by_session: dict[str, dict[str, dict]] = defaultdict(dict)
        # user_id → list of (product_id, swipe, swiped_at) for cooldown filters
        self._user_history: dict[str, list[dict]] = defaultdict(list)

    def add_served(self, session_id: str, user_id: str, items: list[dict]) -> None:
        """items: [{product_id, card_position, is_explore, is_sponsored, score,
        percent_match, notification_key}]. Written with swipe=NULL at serve time."""
        for it in items:
            row = {
                "session_id": session_id, "swipe": None, "served_at": _now(),
                "swiped_at": None, "bought_in_session": False, **it,
            }
            self._by_session[session_id][it["product_id"]] = row
            gateway.spool("pc_session_items", {"user_id": user_id, **row})
        if gateway.db_enabled:
            rows = [self._by_session[session_id][it["product_id"]] for it in items]
            gateway.db_async(
                lambda rs=rows: supabase().table("pc_session_items")
                .upsert(rs, on_conflict="session_id,product_id").execute()
            )

    def record_swipe(self, session_id: str, user_id: str, interaction: dict) -> None:
        pid = interaction["product_id"]
        row = self._by_session[session_id].get(pid, {"session_id": session_id, "product_id": pid})
        row.update({
            "swipe": interaction["swipe"],
            "details_read": interaction.get("details_read"),
            "last_image_discovered": interaction.get("last_image_discovered"),
            "bought_in_session": interaction.get("bought_in_session", False),
            "size_selected": interaction.get("size_selected"),
            "size_scope": interaction.get("size_scope"),
            "swiped_at": _now(),
        })
        self._by_session[session_id][pid] = row
        self._user_history[user_id].append(
            {"product_id": pid, "swipe": interaction["swipe"], "swiped_at": row["swiped_at"]}
        )
        gateway.spool("pc_session_item_swipes", {"user_id": user_id, **row})
        if gateway.db_enabled:
            patch = {k: row[k] for k in (
                "swipe", "details_read", "last_image_discovered", "bought_in_session",
                "size_selected", "size_scope", "swiped_at")}
            gateway.db_async(
                lambda: supabase().table("pc_session_items").update(patch)
                .eq("session_id", session_id).eq("product_id", pid).execute()
            )

    def served_product_ids(self, session_id: str) -> set[str]:
        return set(self._by_session.get(session_id, {}).keys())

    def swiped_product_ids(self, session_id: str) -> set[str]:
        """Products in this session that already carry a swipe (for idempotency)."""
        return {pid for pid, row in self._by_session.get(session_id, {}).items()
                if row.get("swipe") is not None}

    def user_swipes(self, user_id: str) -> list[dict]:
        """All (product_id, swipe, swiped_at) for a user, across sessions —
        drives left/right cooldown filters (spec §4.6)."""
        return list(self._user_history.get(user_id, []))
