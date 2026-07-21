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
    version: str
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
        version=manifest["version"], matrix=matrix, meta=meta,
        id_to_row={pid: i for i, pid in enumerate(meta["id"])},
        clusters=clusters, anchor_deck=anchor_deck,
        vibe_vectors=vibe_vectors, manifest=manifest,
    )


def _current_version() -> str | None:
    path = ARTIFACTS_DIR / "manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("version")
    except json.JSONDecodeError:
        return None


class ArtifactStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bundle: ArtifactBundle | None = None
        self._loaded_at: float | None = None
        self._stop = threading.Event()
        self._product_repo = None   # injected; used only for stock refresh

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
        while not self._stop.wait(ARTIFACT_RELOAD_CHECK_SECONDS):
            try:
                version = _current_version()
                if version and (not self._bundle or version != self._bundle.version):
                    logger.info("New artifact version %s — reloading", version)
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
