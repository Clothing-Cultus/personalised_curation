"""Nightly feature pipeline orchestrator (spec §4.1, §8.1). Run by cron:

    cd personalised_curation && python -m ml.pipeline.run_pipeline

Steps:
  1. Load catalog (Supabase read-only, or local JSON in dev)
  2. Per-feature MDS embeddings from the 12 similarity matrices (§4.3)
  3. Structured block (MDS where usable, one-hot fallback, per-feature weights §4.4)
  4. Blended CLIP image+text embeddings (cached — only new/changed re-embed)
  5. Final matrix = [embedding | STRUCTURED_BLOCK_WEIGHT·structured], L2-normalized
  6. KMeans clusters, anchor deck, cold-start vibe vectors
  7. Atomically write artifacts; the API hot-reloads within ARTIFACT_RELOAD_CHECK_SECONDS
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.logging_config import setup_logging  # noqa: E402

logger = logging.getLogger("pipeline")


def run() -> None:
    from ml.pipeline.build_anchor_deck import build_anchor_deck, build_vibe_vectors
    from ml.pipeline.build_categorical_embeddings import build_all_embeddings
    from ml.pipeline.build_clusters import build_clusters
    from ml.pipeline.build_matrix import (
        META_COLUMNS,
        build_final_matrix,
        compute_price_bands,
        write_artifacts,
    )
    from ml.pipeline.build_structured import build_structured_block
    from ml.pipeline.catalog_loader import load_catalog
    from ml.pipeline.embed_images import compute_blended_embeddings

    df = load_catalog()
    if df.empty:
        raise SystemExit("Catalog is empty — aborting, artifacts left untouched")
    df = df.reset_index(drop=True)

    embeddings = build_all_embeddings()                       # §4.3 per-feature MDS
    structured_block, layout = build_structured_block(df, embeddings)
    embed_block = compute_blended_embeddings(df)              # §4.1 steps 1–2
    matrix = build_final_matrix(embed_block, structured_block)

    meta = df[META_COLUMNS].copy()
    meta["price_band"] = compute_price_bands(meta)
    meta["items_available_in_each_size"] = meta["items_available_in_each_size"].apply(
        lambda d: json.dumps(d or {})   # parquet can't hold dicts
    )
    meta["created_at"] = pd.to_datetime(meta["created_at"], errors="coerce", utc=True)

    clusters = build_clusters(matrix, meta["id"])
    meta["cluster"] = meta["id"].map(clusters).fillna(0).astype(int)

    version = write_artifacts(
        matrix=matrix,
        meta=meta,
        clusters=clusters,
        anchor_deck=build_anchor_deck(meta),
        vibe_vectors=build_vibe_vectors(meta, matrix),
        block_layout=layout,
    )
    logger.info("Pipeline complete — artifact version %s (dim=%d)", version, matrix.shape[1])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nightly feature pipeline")
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()
    setup_logging(args.log_level)
    run()
