"""§4.1 step 4 — assemble the final product matrix and WRITE the artifacts.

final = [ CLIP_embedding_block | STRUCTURED_BLOCK_WEIGHT · structured_block ],
then L2-normalized so a dot product is exactly cosine at serve time.

Writes are atomic (*.tmp → os.replace); manifest.json is written LAST and the
serving app treats a version change as "reload everything". The app has its own
reader (app/services/artifact_store) — it never imports this module (spec §2.2).
"""
from __future__ import annotations

import io
import json
import logging
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from core.config import ARTIFACTS_DIR, N_PRICE_BANDS, STRUCTURED_BLOCK_WEIGHT

logger = logging.getLogger(__name__)

# Columns the serving app needs from meta (aligned row-for-row with the matrix).
META_COLUMNS = [
    "id", "brand_id", "category_title", "gender", "selling_price", "is_bestseller",
    "ranking_score", "status", "items_available", "available_sizes",
    "items_available_in_each_size", "created_at", "garment_class",
    "fashion_sub_style", "product_title",
]


def build_final_matrix(embed_block: np.ndarray, structured_block: np.ndarray) -> np.ndarray:
    matrix = np.hstack([embed_block, STRUCTURED_BLOCK_WEIGHT * structured_block])
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


def compute_price_bands(meta: pd.DataFrame) -> pd.Series:
    prices = pd.to_numeric(meta["selling_price"], errors="coerce").fillna(0)
    try:
        return pd.qcut(prices.rank(method="first"), N_PRICE_BANDS, labels=False).astype(int)
    except ValueError:
        return pd.Series(0, index=meta.index)


def _atomic_write_bytes(path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _atomic_write_json(path, obj) -> None:
    _atomic_write_bytes(path, json.dumps(obj, indent=2, default=str).encode("utf-8"))


def write_artifacts(matrix, meta, clusters, anchor_deck, vibe_vectors, block_layout) -> str:
    out = ARTIFACTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    buf = io.BytesIO()
    np.save(buf, matrix)
    _atomic_write_bytes(out / "product_matrix.npy", buf.getvalue())

    tmp_meta = out / "product_meta.parquet.tmp"
    meta.to_parquet(tmp_meta, index=False)
    os.replace(tmp_meta, out / "product_meta.parquet")

    _atomic_write_json(out / "clusters.json", clusters)
    _atomic_write_json(out / "anchor_deck.json", anchor_deck)

    buf = io.BytesIO()
    np.savez(buf, **vibe_vectors)
    _atomic_write_bytes(out / "vibe_vectors.npz", buf.getvalue())

    # Manifest LAST — the app reloads everything when `version` changes.
    _atomic_write_json(out / "manifest.json", {
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "n_products": int(matrix.shape[0]),
        "vector_dim": int(matrix.shape[1]),
        "structured_block_layout": block_layout,
    })
    logger.info("Artifacts written: version=%s matrix=%s → %s", version, matrix.shape, out)
    return version
