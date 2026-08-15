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
        # session_id → user_id (so we can find a user's owed cards in-memory)
        self._session_user: dict[str, str] = {}

    def add_served(self, session_id: str, user_id: str, items: list[dict]) -> None:
        """items: [{product_id, card_position, is_explore, is_sponsored, score,
        percent_match, notification_key}]. Written with swipe=NULL at serve time."""
        self._session_user[session_id] = user_id
        for it in items:
            row = {
                "session_id": session_id, "swipe": None, "served_at": _now(),
                "swiped_at": None, "bought_in_session": False, "bought_as_gift": False,
                **it,
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
        # The API field is size_default_for_category (bool); the DB column is the
        # existing size_scope (text) — map the bool onto it so no DB rename is needed.
        if interaction.get("size_default_for_category"):
            size_scope = "category"
        elif interaction.get("size_selected"):
            size_scope = "this_product"
        else:
            size_scope = None
        row.update({
            "swipe": interaction["swipe"],
            "details_read": interaction.get("details_read"),
            "last_image_discovered": interaction.get("last_image_discovered"),
            "bought_in_session": interaction.get("bought_in_session", False),
            "bought_as_gift": interaction.get("bought_as_gift", False),
            "size_selected": interaction.get("size_selected"),
            "size_scope": size_scope,
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
                "bought_as_gift", "size_selected", "size_scope", "swiped_at")}
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

    def owed_product_ids(self, user_id: str) -> list[str]:
        """Un-swiped cards to re-offer when the user returns — ONLY from the
        user's MOST RECENT session (the session holding their latest served card).
        This bounds the lookup to one session and stops cards from long-ago
        sessions haunting forever. It handles both cases automatically:
          - resume: the most-recent session IS the one being resumed;
          - renewal: the brand-new session has no items yet, so the most-recent
            session-with-items is the previous one.
        Loose by design: recycle_cards re-validates stock/gender/size and drops
        any the user later swiped elsewhere."""
        if gateway.db_enabled:
            # the session of the user's most recently served item = their last session
            recent = (supabase().table("pc_session_items")
                      .select("session_id,served_at,pc_sessions!inner(user_id)")
                      .eq("pc_sessions.user_id", user_id)
                      .order("served_at", desc=True).limit(1).execute().data)
            if not recent:
                return []
            last_session = recent[0]["session_id"]
            rows = (supabase().table("pc_session_items")
                    .select("product_id,served_at")
                    .eq("session_id", last_session).is_("swipe", "null")
                    .order("served_at", desc=True).execute().data)
            seen: set = set()
            out: list[str] = []
            for r in rows:
                p = r["product_id"]
                if p not in seen:
                    seen.add(p)
                    out.append(p)
            return out
        # in-memory: find the user's session with the latest served_at, take its unswiped
        best_sid, best_ts = None, ""
        for sid, its in self._by_session.items():
            if self._session_user.get(sid) != user_id:
                continue
            for row in its.values():
                ts = row.get("served_at") or ""
                if ts > best_ts:
                    best_ts, best_sid = ts, sid
        if best_sid is None:
            return []
        swiped = {h["product_id"] for h in self._user_history.get(user_id, [])}
        out = []
        for pid, row in self._by_session[best_sid].items():
            if row.get("swipe") is None and pid not in swiped:
                out.append(pid)
        return out

    def history_for_user(self, user_id: str) -> list[dict]:
        """Ordered swipe history for the offline vector-rebuild job: every
        swiped card across the user's sessions, oldest first, with the fields
        needed to weight each event ({product_id, swipe, details_read,
        last_image_discovered})."""
        if gateway.db_enabled:
            # join pc_session_items → pc_sessions to filter by user_id
            rows = (supabase().table("pc_session_items")
                    .select("product_id,swipe,details_read,last_image_discovered,"
                            "swiped_at,pc_sessions!inner(user_id)")
                    .eq("pc_sessions.user_id", user_id)
                    .not_.is_("swipe", "null")
                    .order("swiped_at").execute().data)
            return rows
        # in-memory fallback (details_read/last_image_discovered default to
        # missing → left swipes use the baseline weight)
        return sorted(self._user_history.get(user_id, []),
                      key=lambda h: h.get("swiped_at") or "")
