"""Persistence gateway shared by every repository.

Centralizes the DB-write master switch, the JSONL spool, the background write
executor (so a swipe never blocks on a DB round trip — spec §5.3a), and a
read-only Supabase client accessor.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from core.config import ENABLE_DB_WRITES, ENV, SPOOL_DIR

logger = logging.getLogger(__name__)

_client = None


def supabase():
    """Read-only Supabase client (also used for writes once ENABLE_DB_WRITES).
    Raises if credentials are missing."""
    global _client
    if _client is None:
        if not ENV.supabase_url or not ENV.supabase_service_key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY missing")
        from supabase import create_client

        _client = create_client(ENV.supabase_url, ENV.supabase_service_key)
        logger.info("Supabase client initialised (%s)", ENV.supabase_url)
    return _client


class Gateway:
    """One process-wide instance, shared by all repositories."""

    def __init__(self) -> None:
        self.db_enabled = bool(ENABLE_DB_WRITES)
        SPOOL_DIR.mkdir(parents=True, exist_ok=True)
        self._spool_lock = threading.Lock()
        # ONE worker: DB writes run in submission order, so FK-dependent inserts
        # land in order (pc_sessions before its pc_session_items, etc.). Writes
        # are ms-fast and off the request path, so a single worker keeps up.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="persist")
        if self.db_enabled:
            logger.warning("DB writes ENABLED — pc_* / user_taste tables will be written")
        else:
            logger.info("DB writes DISABLED — spooling to %s (in-memory is source of truth)",
                        SPOOL_DIR)

    def spool(self, kind: str, record: dict[str, Any]) -> None:
        record = {"_kind": kind, "_ts": datetime.now(timezone.utc).isoformat(), **record}
        path = SPOOL_DIR / f"{kind}-{datetime.now(timezone.utc):%Y%m%d}.jsonl"
        line = json.dumps(record, default=str)
        with self._spool_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def db_async(self, fn) -> None:
        """Run a Supabase write off-thread; failures are logged, never raised
        into the request path (spec §5.3a / degradation ladder)."""
        def wrapped() -> None:
            try:
                fn()
            except Exception:  # noqa: BLE001
                logger.exception("Supabase write failed (data remains in spool)")
        self._executor.submit(wrapped)

    def flush(self, timeout: float = 15.0) -> None:
        """Wait until every queued background write has run, so a still-queued
        insert can't land after a purge and leave orphan rows."""
        if self.db_enabled:
            self._executor.submit(lambda: None).result(timeout=timeout)


gateway = Gateway()
