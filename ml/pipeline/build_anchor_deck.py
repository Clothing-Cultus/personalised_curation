"""Cold-start / degradation-ladder anchor deck (spec §5.2 step 3) and per-vibe
cold-start seed vectors (spec §5.2 normal path is the stored vector; this seeds
brand-new users if vibe onboarding is ever wired in)."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from core.config import ANCHOR_DECK_SIZE, EXEMPLARS_PER_VIBE

logger = logging.getLogger(__name__)


def build_anchor_deck(meta: pd.DataFrame) -> list[dict]:
    """Best in-stock products spread round-robin across categories so the deck
    doesn't collapse into one category."""
    active = meta[(meta["status"] == "active") & (meta["items_available"] > 0)].copy()
    active = active.sort_values(["is_bestseller", "ranking_score"], ascending=[False, False])
    picked: list[str] = []
    seen: set = set()
    by_cat = {cat: list(grp.itertuples()) for cat, grp in active.groupby("category_title")}
    while len(picked) < ANCHOR_DECK_SIZE and any(by_cat.values()):
        for cat in list(by_cat):
            if by_cat[cat]:
                row = by_cat[cat].pop(0)
                if row.id not in seen:
                    picked.append(row.id)
                    seen.add(row.id)
                if len(picked) >= ANCHOR_DECK_SIZE:
                    break
    return [{"product_id": pid, "percent_match": None} for pid in picked]


def build_vibe_vectors(meta: pd.DataFrame, matrix: np.ndarray) -> dict[str, np.ndarray]:
    """Per vibe (= fashion_sub_style), the mean final vector of its top-ranked
    exemplar products — the cold-start seed if onboarding vibes are provided."""
    out: dict[str, np.ndarray] = {}
    active = meta[meta["status"] == "active"]
    for vibe, grp in active.groupby("fashion_sub_style"):
        if not vibe:
            continue
        rows = grp.sort_values("ranking_score", ascending=False).head(EXEMPLARS_PER_VIBE).index
        vec = matrix[rows].mean(axis=0)
        norm = np.linalg.norm(vec)
        if norm > 0:
            out[str(vibe)] = (vec / norm).astype(np.float32)
    logger.info("Cold-start vibe vectors: %d", len(out))
    return out
