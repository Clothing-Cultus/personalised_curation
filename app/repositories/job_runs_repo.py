"""pc_job_runs (spec §3.2) — observability for the nightly jobs (is it running,
did the last run fail). Separate from pc_purchase_weights' per-item idempotency."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.repositories.base import gateway, supabase

logger = logging.getLogger(__name__)


class JobRunsRepo:
    def start(self, job_name: str) -> dict:  # pragma: no cover - nightly
        rec = {"job_name": job_name, "started_at": datetime.now(timezone.utc).isoformat(),
               "status": "running"}
        gateway.spool("pc_job_runs", rec)
        if gateway.db_enabled:
            gateway.db_async(lambda: supabase().table("pc_job_runs").insert(rec).execute())
        return rec

    def finish(self, job_name: str, status: str, items_processed: int = 0,
               error_detail: str | None = None) -> None:  # pragma: no cover - nightly
        rec = {"job_name": job_name, "status": status,
               "completed_at": datetime.now(timezone.utc).isoformat(),
               "items_processed": items_processed, "error_detail": error_detail}
        gateway.spool("pc_job_runs_finish", rec)
        if gateway.db_enabled:
            gateway.db_async(lambda: supabase().table("pc_job_runs").insert(rec).execute())
