"""READ-ONLY access to orders / order_items (Hard Rule #1) — nightly purchase
reconciliation only (spec §8). Reads the future bought_from_tray / bought_as_gift
columns once Parth adds them. Placeholder until the nightly job is wired."""
from __future__ import annotations

import logging

from app.repositories.base import supabase

logger = logging.getLogger(__name__)


class OrderRepo:
    def read_unprocessed_items(self, since_iso: str | None = None) -> list[dict]:  # pragma: no cover
        q = supabase().table("order_items").select("*")
        if since_iso:
            q = q.gte("created_at", since_iso)
        return q.execute().data
