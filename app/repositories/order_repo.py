"""READ-ONLY access to orders / order_items (Hard Rule #1) — nightly purchase
reconciliation only (spec §8). Never written."""
from __future__ import annotations

import logging

from app.repositories.base import supabase

logger = logging.getLogger(__name__)


class OrderRepo:
    def read_confirmed_purchases(self, statuses: list[str]) -> list[dict]:
        """Every order_item from a confirmed order, with the buyer's user_id
        (joined from orders). Returns [{order_item_id, user_id, product_id,
        quantity}]. Paged (order_items can exceed 1000)."""
        rows: list[dict] = []
        offset = 0
        while True:
            page = (supabase().table("order_items")
                    .select("id,product_id,quantity,orders!inner(user_id,status)")
                    .in_("orders.status", statuses)
                    .range(offset, offset + 999).execute().data)
            rows.extend(page)
            if len(page) < 1000:
                break
            offset += 1000

        out: list[dict] = []
        for r in rows:
            o = r.get("orders") or {}
            if not o.get("user_id"):
                continue
            out.append({
                "order_item_id": r["id"], "product_id": r["product_id"],
                "user_id": o["user_id"], "quantity": r.get("quantity") or 1,
            })
        return out
