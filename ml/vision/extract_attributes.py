"""CV attribute extraction — DEFERRED (spec §4.1(b), PC-todo-later §11).

The full workflow (nightly completeness check → CV on the first 3 images →
diff against DB → stage in a temp table → next-day HTML admin review → write to
topwear/bottomwear only on explicit approval) is intentionally NOT built in
phase 1. This module is a placeholder that documents the contract and the
zero-shot approach so the pipeline tree matches spec §2.2 without implying the
workflow ships now.

Key non-deferred obligation (PC-todo-later §7/§11): whenever this DOES ship, CV
accuracy must be validated against manually-filled ground truth before any
extracted attribute is trusted, and provenance (table-sourced vs CV-sourced)
tracked so a bad recommendation can be traced to a bad extraction.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run() -> None:  # pragma: no cover - deferred
    raise NotImplementedError(
        "CV attribute extraction is deferred to a later phase (PC-todo-later §11). "
        "It runs on the first 3 images, stages to a temp table, and writes to "
        "topwear/bottomwear only after explicit admin approval — none of which is "
        "built in phase 1."
    )


if __name__ == "__main__":  # pragma: no cover
    run()
