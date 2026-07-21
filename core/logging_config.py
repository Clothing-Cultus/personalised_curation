"""Uniform logging for the API, the pipeline, and any scripts."""
from __future__ import annotations

import logging
import sys

from core.config import LOG_LEVEL


def setup_logging(level: str | None = None) -> None:
    # Windows consoles default to cp1252 — don't let a unicode char in a log
    # line raise UnicodeEncodeError.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    for noisy in ("httpx", "httpcore", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
