"""Taste notifications (spec §4.8) — rule-based, PC-level confidence, NO LLM in
the request path ever. Full cross-session storage/suppression is deferred
(PC-todo-later §2); phase 1 is a static in-code template set with a min-swipe
confidence gate, so we never claim to "know" a user at swipe 2.
"""
from __future__ import annotations

# key → (min_swipes gate, template). Kept intentionally tiny for phase 1.
_TEMPLATES = [
    ("warming_up", 0, None),                    # no message early on
    ("getting_it", 12, "We're starting to get your style."),
    ("dialed_in", 25, "Your taste is dialed in — these are hand-picked."),
]


def notification_for(swipe_count: int) -> tuple[str | None, str | None]:
    """Return (notification_key, taste_notification_content) for the current
    confidence level. content is None until the swipe-count gate is passed."""
    chosen_key, chosen_text = "warming_up", None
    for key, min_swipes, text in _TEMPLATES:
        if swipe_count >= min_swipes:
            chosen_key, chosen_text = key, text
    return chosen_key, chosen_text
