"""§4.1 steps 1–2 — blended CLIP image+text embeddings for products.

blended = CLIP_IMAGE_WEIGHT·image + CLIP_TEXT_WEIGHT·text, L2-normalized;
products with no usable image fall back to text-only. Cached in ml/cache/
embeddings.parquet keyed by (product_id, content_hash), so each nightly run
re-embeds only new/changed products (spec §4.1).
"""
from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from core.config import (
    CACHE_DIR,
    CLIP_IMAGE_WEIGHT,
    CLIP_TEXT_WEIGHT,
    EMBEDDING_DIM,
    IMAGE_DOWNLOAD_TIMEOUT_SECONDS,
    IMAGE_DOWNLOAD_WORKERS,
)
from ml.pipeline.backends import get_backend
from ml.pipeline.embed_text import product_text

logger = logging.getLogger(__name__)


def _content_hash(row: pd.Series) -> str:
    first_image = row["images"][0] if row["images"] else ""
    return hashlib.sha256(f"{first_image}|{product_text(row)}".encode()).hexdigest()[:16]


def _download(url: str, timeout: int) -> bytes | None:
    import requests

    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("Image download failed (%s): %s", url, exc)
        return None


def download_first_images(df: pd.DataFrame) -> dict[str, bytes]:
    """Download each product's first image in parallel → {product_id: bytes}."""
    jobs = {row["id"]: row["images"][0] for _, row in df.iterrows() if row["images"]}
    out: dict[str, bytes] = {}
    with ThreadPoolExecutor(max_workers=IMAGE_DOWNLOAD_WORKERS) as ex:
        futures = {
            ex.submit(_download, url, IMAGE_DOWNLOAD_TIMEOUT_SECONDS): pid
            for pid, url in jobs.items()
        }
        for fut, pid in futures.items():
            data = fut.result()
            if data:
                out[pid] = data
    logger.info("Downloaded %d/%d first images", len(out), len(jobs))
    return out


def compute_blended_embeddings(df: pd.DataFrame) -> np.ndarray:
    """Return an (N, EMBEDDING_DIM) matrix of blended, L2-normalized embeddings."""
    cache_path = CACHE_DIR / "embeddings.parquet"
    cache: dict[tuple[str, str], np.ndarray] = {}
    if cache_path.exists():
        cached_df = pd.read_parquet(cache_path)
        cache = {
            (r["product_id"], r["content_hash"]): np.asarray(r["embedding"], dtype=np.float32)
            for _, r in cached_df.iterrows()
        }
        logger.info("Embedding cache: %d entries", len(cache))

    hashes = df.apply(_content_hash, axis=1)
    misses = df[[(pid, h) not in cache for pid, h in zip(df["id"], hashes)]]

    if len(misses):
        backend = get_backend()
        images = download_first_images(misses)
        for idx, row in misses.iterrows():
            img_bytes = images.get(row["id"])
            img_vec = backend.embed_image(img_bytes) if img_bytes else None
            txt_vec = backend.embed_text(product_text(row))
            if img_vec is not None:
                vec = CLIP_IMAGE_WEIGHT * img_vec + CLIP_TEXT_WEIGHT * txt_vec
            else:
                vec = txt_vec
            norm = np.linalg.norm(vec)
            cache[(row["id"], hashes.loc[idx])] = (vec / norm if norm > 0 else vec).astype(
                np.float32
            )
        logger.info("Embedded %d new/changed products", len(misses))

    # Persist cache pruned to the live catalog, then materialize the matrix.
    live_keys = set(zip(df["id"], hashes))
    cache = {k: v for k, v in cache.items() if k in live_keys}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"product_id": pid, "content_hash": h, "embedding": vec.tolist()}
         for (pid, h), vec in cache.items()]
    ).to_parquet(cache_path, index=False)

    matrix = np.zeros((len(df), EMBEDDING_DIM), dtype=np.float32)
    for i, (pid, h) in enumerate(zip(df["id"], hashes)):
        matrix[i] = cache[(pid, h)]
    return matrix
