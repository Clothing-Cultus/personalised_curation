"""Publish nightly artifacts to Supabase Storage (S3-compatible) — for
staging/prod so every API instance downloads the same build.

STATUS: written but DORMANT. The call site in run_pipeline is COMMENTED OUT for
now (local dev reads artifacts straight from ml/artifacts/). Enable for
staging/prod — see TODO.md. The matching download side lives in
app/services/artifact_store.py (also commented at its call site).

Manual prerequisite (staging/prod): create a Storage bucket named `pc` in
Supabase (Dashboard → Storage → New bucket, private). Objects land under
`pc/artifacts/`.
"""
from __future__ import annotations

import logging

from core.config import (
    ARTIFACT_BUCKET,
    ARTIFACT_FILES,
    ARTIFACT_REMOTE_FOLDER,
    ARTIFACTS_DIR,
    ENV,
)

logger = logging.getLogger(__name__)


def upload_artifacts_to_supabase() -> None:
    """Upload the current artifact set to `pc/artifacts/`, overwriting the
    previous build (upsert). Called at the end of the nightly pipeline once
    enabled for staging/prod."""
    from supabase import create_client

    sb = create_client(ENV.supabase_url, ENV.supabase_service_key)
    store = sb.storage.from_(ARTIFACT_BUCKET)
    for fname in ARTIFACT_FILES:
        path = ARTIFACTS_DIR / fname
        if not path.exists():
            logger.warning("artifact missing, skipping upload: %s", fname)
            continue
        remote = f"{ARTIFACT_REMOTE_FOLDER}/{fname}"
        # upsert=true so the same object path is overwritten each night.
        store.upload(remote, path.read_bytes(),
                     {"content-type": "application/octet-stream", "upsert": "true"})
    logger.info("Uploaded %d artifacts to %s/%s", len(ARTIFACT_FILES),
                ARTIFACT_BUCKET, ARTIFACT_REMOTE_FOLDER)
