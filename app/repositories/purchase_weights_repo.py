"""pc_purchase_weights (spec §3.2) — the per-order-item idempotency ledger that
guarantees a purchase moves the vector EXACTLY once. Nightly job only.

INSERT ... ON CONFLICT (order_item_id) DO NOTHING is both the idempotency
mechanism and the "unprocessed only" mechanism (spec §8 / Update #4).
"""
from __future__ import annotations

import logging

from app.repositories.base import gateway, supabase

logger = logging.getLogger(__name__)


class PurchaseWeightsRepo:
    def claim(self, order_item_id: str, user_id: str, product_id: str,
              weight: float, gift_status: str) -> bool:  # pragma: no cover - nightly
        """Return True if this order_item was newly claimed (should be applied),
        False if already processed. Only meaningful when DB writes are enabled."""
        record = {
            "order_item_id": order_item_id, "user_id": user_id, "product_id": product_id,
            "weight_applied": weight, "gift_status": gift_status,
        }
        gateway.spool("pc_purchase_weights", record)
        if not gateway.db_enabled:
            logger.info("Purchase weight (spool only, DB off): user=%s product=%s weight=%s gift=%s",
                        user_id, product_id, weight, gift_status)
            return False
        res = (supabase().table("pc_purchase_weights")
               .upsert(record, on_conflict="order_item_id", ignore_duplicates=True).execute())
        newly = bool(res.data)
        if newly:
            logger.info("Applied purchase weight +%s to user=%s for product=%s (gift=%s, order_item=%s)",
                        weight, user_id, product_id, gift_status, order_item_id)
        else:
            logger.info("Purchase already credited, skipped: order_item=%s user=%s", order_item_id, user_id)
        return newly
