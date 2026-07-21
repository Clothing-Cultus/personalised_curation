"""Queue accounting (spec §5.3): how many cards to send, and when.

The client holds a local queue; it asks for more when the queue drops to
SEND_THRESHOLD. The backend is the source of truth for the cards_limit ceiling
(Hard Rule #7) — products_swiped from the client is advisory only.
"""
from __future__ import annotations

from core.config import BATCH_SIZE, CARDS_LIMIT, INITIAL_BATCH_SIZE


def initial_batch_size(session: dict) -> int:
    remaining = CARDS_LIMIT - session["cards_served"]
    return max(0, min(INITIAL_BATCH_SIZE, remaining))


def refill_size(session: dict) -> int:
    """How many cards this refill should serve, capped by the session's ceiling."""
    remaining = CARDS_LIMIT - session["cards_served"]
    return max(0, min(BATCH_SIZE, remaining))
