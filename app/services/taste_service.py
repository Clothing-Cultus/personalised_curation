"""User taste vector — the update rule, decay, and event→weight mapping (spec
§4.5). Business logic only; no SQL, no FastAPI.

THE reusable function (spec §2.1, §3.3, PC-todo-later §5): `apply_interactions`
takes an ordered list of (product_vector, weight) pairs and returns the resulting
vector. It is called IDENTICALLY two ways — once per live swipe, or in a loop
replaying a user's whole pc_session_items history against a new artifact version.
There is deliberately NO second implementation of "how a swipe moves the vector".
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

import numpy as np

from core.config import (
    DECAY_FLOOR,
    LEFT_ENGAGED_DETAILS_READ,
    LEFT_ENGAGED_MIN_IMAGES,
    SWIPE_WEIGHTS,
    WEEKLY_DECAY,
    WITHIN_SESSION_DECAY,
)

logger = logging.getLogger(__name__)


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return (v / n if n > 0 else v).astype(np.float32)


def apply_interactions(
    base_vector: np.ndarray,
    pairs: Iterable[tuple[np.ndarray, float]],
    decay: float = WITHIN_SESSION_DECAY,
) -> np.ndarray:
    """u ← normalize(decay·u + Σ wᵢ·pᵢ), applied left-to-right in order.

    Live path:   apply_interactions(u, [(product_vec, weight)])          → new u
    Replay path: apply_interactions(zeros(dim), all_history_pairs)       → rebuilt u
    """
    v = base_vector.astype(np.float32).copy()
    for product_vec, weight in pairs:
        v = _normalize(decay * v + weight * product_vec.astype(np.float32))
    return v


def event_weight(interaction: dict) -> float:
    """Map a ProductInteraction to its vector weight (spec §4.5, glossary)."""
    swipe = interaction["swipe"]
    if swipe == "right":
        return SWIPE_WEIGHTS["right"]
    if swipe == "superlike":
        return SWIPE_WEIGHTS["superlike"]
    # left: "engaged" (explored before rejecting) gets a weaker penalty.
    engaged = (
        (interaction.get("last_image_discovered") or 0) >= LEFT_ENGAGED_MIN_IMAGES
        or (LEFT_ENGAGED_DETAILS_READ and bool(interaction.get("details_read")))
    )
    return SWIPE_WEIGHTS["left_engaged"] if engaged else SWIPE_WEIGHTS["left"]


def apply_weekly_decay(vector: np.ndarray, last_decayed_at: str | None) -> np.ndarray:
    """Cross-session decay applied on read (spec §4.5):
        factor = max(WEEKLY_DECAY ** weeks_elapsed, DECAY_FLOOR)
    The floor stops a long-absent user's taste from ever collapsing to nothing."""
    if not last_decayed_at or not np.any(vector):
        return vector
    try:
        last = datetime.fromisoformat(last_decayed_at)
    except ValueError:
        return vector
    weeks = (datetime.now(timezone.utc) - last).total_seconds() / (7 * 86400)
    if weeks <= 0.01:
        return vector
    factor = max(WEEKLY_DECAY ** weeks, DECAY_FLOOR)
    return _normalize(vector * factor)
