"""Nightly purchase reconciliation (spec §8) — BASIC version.

Reads confirmed order_items (read-only), and for each one NOT already processed,
nudges that buyer's taste vector toward the purchased product and records it in
the pc_purchase_weights ledger (so it's applied exactly once).

    python -m scripts.purchase_reconciliation

Basic-version scope / current limitations:
  - Gift tiers aren't available yet (order_items has no bought_as_gift column), so
    every confirmed purchase uses the 'unknown' weight (+3.5). Once Parth adds
    bought_as_gift / bought_via_pc (TODO), switch to the gift/self tiers here.
  - Applies to a product only if it exists in the current matrix (has a vector).
  - Idempotent: the ledger's UNIQUE(order_item_id) guarantees exactly-once.
  - Only runs meaningfully with ENABLE_DB_WRITES=True (ledger + vectors live in DB).
  - The vector-rebuild job (rebuild_user_vectors) replays these ledger purchases
    after a layout change, so purchase signal survives matrix rebuilds.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import PURCHASE_CONFIRMED_STATUSES, PURCHASE_WEIGHTS
from core.logging_config import setup_logging

logger = logging.getLogger("purchase_reconciliation")


def run() -> int:
    from app.services.artifact_store import artifact_store
    from app.services import taste_service
    from app.repositories.taste_repo import TasteState
    import app.deps as deps

    artifact_store.try_load()
    if not artifact_store.loaded:
        raise SystemExit("No artifacts loaded — run the pipeline first.")
    bundle = artifact_store.bundle

    purchases = deps.order_repo.read_confirmed_purchases(PURCHASE_CONFIRMED_STATUSES)
    logger.info("Purchase reconciliation: %d confirmed order_items to check", len(purchases))

    weight = PURCHASE_WEIGHTS["unknown"]   # basic version: no gift flag yet
    applied = 0
    for p in purchases:
        # claim first (idempotency) — skip if already credited
        newly = deps.purchase_weights_repo.claim(
            p["order_item_id"], p["user_id"], p["product_id"], weight, "unknown")
        if not newly:
            continue
        row = bundle.id_to_row.get(p["product_id"])
        if row is None:
            logger.info("Purchased product not in matrix, skipped vector nudge: %s", p["product_id"])
            continue
        st = deps.taste_repo.get(p["user_id"])
        if st is None:
            st = TasteState(p["user_id"],
                            np.zeros(bundle.matrix.shape[1], dtype=np.float32),
                            bundle.layout_signature)
        st.vector = taste_service.apply_interactions(st.vector, [(bundle.matrix[row], weight)])
        st.swipe_count += 1
        deps.taste_repo.save(st)
        applied += 1

    logger.info("Purchase reconciliation complete — %d new purchases applied", applied)
    return applied


if __name__ == "__main__":
    setup_logging("INFO")
    run()
