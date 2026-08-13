"""user_taste (spec §3.2). The persisted taste vector + the in-memory derived
learning state (cluster stats, price-band counts) used during serving.

The stored vector is a cache of interaction history (spec §3.3); the derived
bandit/price state is rebuilt as swipes arrive within a process lifetime.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime, timezone

import numpy as np

from app.repositories.base import gateway, supabase

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TasteState:
    """In-memory working copy of a user's taste state."""

    def __init__(self, user_id: str, vector: np.ndarray, vector_version: str,
                 swipe_count: int = 0, last_decayed_at: str | None = None,
                 size_preferences: dict | None = None) -> None:
        self.user_id = user_id
        self.vector = vector.astype(np.float32)
        self.vector_version = vector_version
        self.swipe_count = swipe_count
        self.last_decayed_at = last_decayed_at or _now()
        self.size_preferences = size_preferences or {}
        # derived (not persisted columns): rebuilt as swipes arrive this process
        self.cluster_stats: dict[int, list[float]] = {}      # cluster → [successes, failures]
        self.price_band_counts: dict[int, float] = {}

    def price_affinity(self, n_bands: int) -> np.ndarray:
        counts = np.array([self.price_band_counts.get(b, 0.0) for b in range(n_bands)])
        total = counts.sum()
        return counts / total if total > 0 else np.full(n_bands, 1.0 / n_bands)


class TasteRepo:
    def __init__(self, cache_size: int = 10000) -> None:
        self._max = cache_size
        self._cache: OrderedDict[str, TasteState] = OrderedDict()

    def get(self, user_id: str) -> TasteState | None:
        st = self._cache.get(user_id)
        if st is not None:
            self._cache.move_to_end(user_id)
            return st
        if gateway.db_enabled:
            rows = (supabase().table("user_taste").select("*")
                    .eq("user_id", user_id).limit(1).execute().data)
            if rows:
                r = rows[0]
                st = TasteState(
                    user_id=user_id,
                    vector=np.asarray(r.get("vector") or [], dtype=np.float32),
                    vector_version=r.get("vector_version") or "",
                    swipe_count=r.get("swipe_count") or 0,
                    last_decayed_at=r.get("last_decayed_at"),
                    size_preferences=r.get("size_preferences") or {},
                )
                self._put(st)
        return st

    def _put(self, st: TasteState) -> None:
        self._cache[st.user_id] = st
        self._cache.move_to_end(st.user_id)
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)

    def list_stale_user_ids(self, current_version: str) -> list[str]:
        """User ids whose stored vector_version != the current artifact version —
        the set the offline rebuild job must replay (spec §3.3)."""
        if gateway.db_enabled:
            rows = (supabase().table("user_taste").select("user_id")
                    .neq("vector_version", current_version).execute().data)
            return [r["user_id"] for r in rows]
        return [uid for uid, st in self._cache.items() if st.vector_version != current_version]

    def save(self, st: TasteState) -> None:
        st.last_decayed_at = st.last_decayed_at or _now()
        self._put(st)
        record = {
            "user_id": st.user_id,
            "vector": st.vector.tolist(),
            "vector_version": st.vector_version,
            "swipe_count": st.swipe_count,
            "last_decayed_at": st.last_decayed_at,
            "size_preferences": st.size_preferences,
            "updated_at": _now(),
        }
        gateway.spool("user_taste", record)
        if gateway.db_enabled:
            gateway.db_async(
                lambda: supabase().table("user_taste")
                .upsert(record, on_conflict="user_id").execute()
            )
