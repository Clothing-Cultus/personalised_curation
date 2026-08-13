"""In-memory artifact bundle (spec §2.2 boundary: the app reads ml/artifacts/
directly and NEVER imports ml.pipeline).

Holds product_matrix (N×D) + product_meta (pandas) in process memory, hot-reloads
when the pipeline publishes a new manifest version, and optionally refreshes only
stock/status/price from Supabase every STOCK_REFRESH_SECONDS so the in-memory
mirror stays current (this realizes spec §4.6's FILTER — see note in scoring).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.config import ARTIFACTS_DIR, ARTIFACT_RELOAD_CHECK_SECONDS, STOCK_REFRESH_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class ArtifactBundle:
    version: str                 # per-run timestamp — hot-reload detection
    layout_signature: str        # feature-layout hash — user-vector compatibility
    matrix: np.ndarray
    meta: pd.DataFrame
    id_to_row: dict[str, int]
    clusters: dict[str, int]
    anchor_deck: list[dict]
    vibe_vectors: dict[str, np.ndarray]
    manifest: dict


def _load_bundle() -> ArtifactBundle:
    out = ARTIFACTS_DIR
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    matrix = np.load(out / "product_matrix.npy")
    meta = pd.read_parquet(out / "product_meta.parquet")
    clusters = json.loads((out / "clusters.json").read_text(encoding="utf-8"))
    anchor_deck = json.loads((out / "anchor_deck.json").read_text(encoding="utf-8"))
    with np.load(out / "vibe_vectors.npz") as npz:
        vibe_vectors = {k: npz[k] for k in npz.files}
    return ArtifactBundle(
        version=manifest["version"],
        layout_signature=manifest.get("layout_signature", manifest["version"]),
        matrix=matrix, meta=meta,
        id_to_row={pid: i for i, pid in enumerate(meta["id"])},
        clusters=clusters, anchor_deck=anchor_deck,
        vibe_vectors=vibe_vectors, manifest=manifest,
    )


def _download_artifacts_from_supabase() -> None:
    """STAGING/PROD ONLY (dormant): pull the latest artifacts from Supabase
    Storage (bucket `pc`, folder `artifacts/`) into the local ml/artifacts/ dir
    before loading, so every EC2 instance uses the same nightly build for its
    in-memory hot path. The matching upload is ml/pipeline/publish_artifacts.py.
    Call site below is commented out; enable for staging/prod (see TODO.md)."""
    from supabase import create_client

    from core.config import (
        ARTIFACT_BUCKET,
        ARTIFACT_FILES,
        ARTIFACT_REMOTE_FOLDER,
        ENV,
    )

    sb = create_client(ENV.supabase_url, ENV.supabase_service_key)
    store = sb.storage.from_(ARTIFACT_BUCKET)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    for fname in ARTIFACT_FILES:
        data = store.download(f"{ARTIFACT_REMOTE_FOLDER}/{fname}")
        (ARTIFACTS_DIR / fname).write_bytes(data)
    logger.info("Downloaded %d artifacts from %s/%s", len(ARTIFACT_FILES),
                ARTIFACT_BUCKET, ARTIFACT_REMOTE_FOLDER)


def _current_manifest() -> dict | None:
    path = ARTIFACTS_DIR / "manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


class ArtifactStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bundle: ArtifactBundle | None = None
        self._loaded_at: float | None = None
        self._stop = threading.Event()
        self._product_repo = None   # injected; used only for stock refresh
        self._warned_layout_version = None   # de-dupe the "layout changed" warning

    def set_product_repo(self, repo) -> None:
        self._product_repo = repo

    @property
    def bundle(self) -> ArtifactBundle:
        if self._bundle is None:
            from core.errors import ArtifactsNotLoaded

            raise ArtifactsNotLoaded()
        return self._bundle

    @property
    def loaded(self) -> bool:
        return self._bundle is not None

    @property
    def age_seconds(self) -> float | None:
        return (time.time() - self._loaded_at) if self._loaded_at else None

    def load(self) -> None:
        bundle = _load_bundle()
        with self._lock:
            self._bundle = bundle
            self._loaded_at = time.time()
        logger.info("Artifacts loaded: version=%s products=%d dim=%d",
                    bundle.version, bundle.matrix.shape[0], bundle.matrix.shape[1])

    def try_load(self) -> None:
        # --- STAGING/PROD ONLY (dormant) ---------------------------------
        # Pull the latest nightly artifacts from Supabase Storage before loading
        # so every API instance serves the same build. Uncomment for
        # staging/prod (TODO.md); local dev reads ml/artifacts/ directly.
        # _download_artifacts_from_supabase()
        # -----------------------------------------------------------------
        try:
            self.load()
        except FileNotFoundError:
            logger.warning("No artifacts yet — /api endpoints 503 until the pipeline runs")

    def start_background_tasks(self) -> None:
        threading.Thread(target=self._reload_loop, daemon=True, name="artifact-reload").start()
        from core.config import ENV

        if STOCK_REFRESH_SECONDS > 0 and self._product_repo and ENV.supabase_url:
            threading.Thread(target=self._stock_loop, daemon=True, name="stock-refresh").start()

    def stop(self) -> None:
        self._stop.set()

    def _reload_loop(self) -> None:
        """Hot-reload, but LAYOUT-AWARE. Auto-swaps only builds with the SAME
        feature layout (new/updated products). A build that changes the feature
        layout is NOT auto-adopted — that would cold-start everyone whose vector
        is still on the old layout. Such a build waits for a deliberate restart
        (do it after the nightly vector-rebuild, so the app comes up with the new
        matrix AND already-rebuilt vectors — no cold-start window)."""
        while not self._stop.wait(ARTIFACT_RELOAD_CHECK_SECONDS):
            try:
                m = _current_manifest()
                if not m:
                    continue
                version = m.get("version")
                if not version or (self._bundle and version == self._bundle.version):
                    continue
                new_sig = m.get("layout_signature", version)
                if self._bundle and new_sig != self._bundle.layout_signature:
                    if self._warned_layout_version != version:
                        logger.warning(
                            "New build %s changes the feature layout (%s -> %s) — "
                            "NOT hot-reloading. Restart the API after the vector "
                            "rebuild to adopt it.",
                            version, self._bundle.layout_signature, new_sig,
                        )
                        self._warned_layout_version = version
                    continue
                logger.info("New artifact version %s (same layout) — reloading", version)
                self.load()
            except Exception:  # noqa: BLE001
                logger.exception("Artifact reload failed; keeping current bundle")

    def _stock_loop(self) -> None:
        while not self._stop.wait(STOCK_REFRESH_SECONDS):
            try:
                self.refresh_stock()
            except Exception:  # noqa: BLE001
                logger.exception("Stock refresh failed; will retry")

    def refresh_stock(self) -> None:
        if not self.loaded or not self._product_repo:
            return
        from core.normalize import normalize_size_stock  # shared helper (no ml import)

        fresh = {r["id"]: r for r in self._product_repo.read_stock()}
        with self._lock:
            meta = self._bundle.meta
            for i, pid in enumerate(meta["id"]):
                r = fresh.get(pid)
                if not r:
                    continue
                meta.at[i, "status"] = (r.get("status") or "").lower()
                meta.at[i, "items_available"] = r.get("items_available") or 0
                meta.at[i, "items_available_in_each_size"] = json.dumps(
                    normalize_size_stock(r.get("items_available_in_each_size")))
                meta.at[i, "selling_price"] = r.get("selling_price") or 0
        logger.info("Stock refresh applied for %d products", len(fresh))


artifact_store = ArtifactStore()
