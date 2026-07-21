"""§4.3 — turn each manual similarity matrix into a compact per-value embedding
via classical (Torgerson) MDS, generalized from style-only (v3) to ALL matrices.

One-hot treats every pair of values as equally dissimilar. The manual matrices
know better (grunge↔y2k = 0.85). MDS places each value at a point in a low-D
space so geometric distance ≈ (1 − similarity); similar values end up close in
every product vector that includes that feature's block.

Robustness: a malformed matrix (e.g. `category.csv`'s fused header — 43 columns
vs 41 rows) is detected and that feature falls back to one-hot rather than
producing a wrong embedding. `bottom_fitting.csv`'s asymmetric cells (0.10 vs
0.13) are averaged for free by the symmetrize step.

Output per feature: {normalized_value: embedding[n_dims]} — consumed by
build_structured.py. Features with no usable matrix are simply absent here.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from core.config import SIMILARITY_MATRICES_DIR, STRUCTURED_BLOCK, mds_dims_for
from core.normalize import normalize_value

logger = logging.getLogger(__name__)


class MatrixUnusable(Exception):
    """Raised when a similarity matrix is malformed and must not be embedded."""


def load_similarity_matrix(stem: str) -> pd.DataFrame:
    """Load {stem}.csv, normalize labels, symmetrize and clip to a clean [0,1]
    similarity matrix with unit diagonal. Raises MatrixUnusable if malformed."""
    path = SIMILARITY_MATRICES_DIR / f"{stem}.csv"
    if not path.exists():
        raise MatrixUnusable(f"{stem}: file not found at {path}")
    # keep_default_na=False: "None" is a real label (e.g. distressed/wash) and must
    # NOT be coerced to NaN — pandas does that to index cells but not headers, which
    # would otherwise silently desync a matrix's row and column labels.
    df = pd.read_csv(path, index_col=0, keep_default_na=False, na_values=[])

    # Malformed check: fused headers (category.csv) make it non-square.
    if df.shape[0] != df.shape[1]:
        raise MatrixUnusable(
            f"{stem}: non-square {df.shape} (likely fused header cells) — "
            "falling back to one-hot"
        )

    df.index = [normalize_value(v) for v in df.index]
    df.columns = [normalize_value(v) for v in df.columns]
    if set(df.index) != set(df.columns) or len(set(df.index)) != df.shape[0]:
        raise MatrixUnusable(f"{stem}: row/column labels disagree or duplicate after normalization")
    df = df.reindex(index=df.columns)   # align row order to column order

    try:
        m = df.to_numpy(dtype=np.float64)
    except (ValueError, TypeError) as exc:
        raise MatrixUnusable(f"{stem}: non-numeric cells ({exc})") from None
    if not np.isfinite(m).all():
        raise MatrixUnusable(f"{stem}: contains non-finite values")
    m = np.clip((m + m.T) / 2.0, 0.0, 1.0)   # symmetrize (fixes asymmetric cells) + clip
    np.fill_diagonal(m, 1.0)
    return pd.DataFrame(m, index=df.index, columns=df.columns)


def classical_mds(similarity: pd.DataFrame, n_dims: int) -> dict[str, np.ndarray]:
    """Classical MDS on distance = 1 − similarity.

    Returns {value: embedding[n_dims]}, scaled so the largest norm is 1 (so the
    per-feature weight in STRUCTURED_FEATURE_WEIGHTS is what actually controls
    each block's magnitude, not the raw MDS scale)."""
    d = 1.0 - similarity.to_numpy()
    n = d.shape[0]
    j = np.eye(n) - np.ones((n, n)) / n           # centering matrix
    b = -0.5 * j @ (d**2) @ j                      # double-centered Gram matrix
    eigvals, eigvecs = np.linalg.eigh(b)
    order = np.argsort(eigvals)[::-1][:n_dims]
    vals = np.clip(eigvals[order], 0, None)
    coords = eigvecs[:, order] * np.sqrt(vals)
    max_norm = np.max(np.linalg.norm(coords, axis=1)) or 1.0
    coords = (coords / max_norm).astype(np.float32)
    denom = np.clip(eigvals, 0, None).sum() or 1.0
    explained = float(vals.sum() / denom)
    logger.info(
        "MDS: %d values → %d dims (%.0f%% of matrix structure retained)",
        n, n_dims, 100 * explained,
    )
    return {name: coords[i] for i, name in enumerate(similarity.index)}


def build_all_embeddings() -> dict[str, dict[str, np.ndarray]]:
    """Build the MDS embedding for every mds/mds_multi feature that has a usable
    matrix. Returns {feature_name: {value: embedding}}. Features whose matrix is
    unusable are omitted so build_structured falls back to one-hot for them."""
    out: dict[str, dict[str, np.ndarray]] = {}
    for spec in STRUCTURED_BLOCK:
        if spec["encoder"] not in ("mds", "mds_multi"):
            continue
        stem = spec["matrix"]
        try:
            sim = load_similarity_matrix(stem)
        except MatrixUnusable as exc:
            logger.warning("Feature '%s' → one-hot fallback: %s", spec["name"], exc)
            continue
        n_dims = mds_dims_for(sim.shape[0])
        out[spec["name"]] = classical_mds(sim, n_dims)
        logger.info("Embedded feature '%s' (matrix=%s, %d values)",
                    spec["name"], stem, sim.shape[0])
    return out
