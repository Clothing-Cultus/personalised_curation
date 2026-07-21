"""GET /healthz (spec §6.2) — unauthenticated, for systemd/ALB checks."""
from __future__ import annotations

from fastapi import APIRouter

from core.config import ENABLE_DB_WRITES
from app.schemas import Health
from app.services.artifact_store import artifact_store

router = APIRouter()


@router.get("/healthz", response_model=Health)
def healthz() -> Health:
    loaded = artifact_store.loaded
    bundle = artifact_store.bundle if loaded else None
    return Health(
        status="ok" if loaded else "degraded",
        artifact_version=bundle.version if loaded else None,
        artifact_age_seconds=artifact_store.age_seconds,
        n_products=int(bundle.matrix.shape[0]) if loaded else 0,
        vector_dim=int(bundle.matrix.shape[1]) if loaded else 0,
        db_writes_enabled=bool(ENABLE_DB_WRITES),
    )
