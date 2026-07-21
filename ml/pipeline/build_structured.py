"""§4.2 / §4.4 — build the structured attribute block for every product.

Layout is driven by core.config.STRUCTURED_BLOCK (order = vector layout). Each
feature is encoded, multiplied by its per-feature weight (§4.4) BEFORE the whole
block is L2-normalized (so raising a weight genuinely raises that attribute's
influence), and garment-specific features (applies_to top/bottom) are zero-filled
for the other class.

MDS features use the embeddings from build_categorical_embeddings; a feature
whose matrix was unusable (absent from that dict) degrades to one-hot here, so
`category` ships one-hot automatically until its fusion bug is fixed at source.

Emits `block_layout`: [{name, start, end, dims, weight, encoder}] into the
manifest so anyone can inspect exactly which dims mean what.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from core.config import STRUCTURED_BLOCK, STRUCTURED_FEATURE_WEIGHTS
from core.normalize import hex_to_rgb01

logger = logging.getLogger(__name__)


def _applies_mask(df: pd.DataFrame, applies_to: str) -> np.ndarray:
    if applies_to == "top":
        return (df["garment_class"] == "top").to_numpy()
    if applies_to == "bottom":
        return (df["garment_class"] == "bottom").to_numpy()
    return np.ones(len(df), dtype=bool)


def _vocab_from_db(df: pd.DataFrame, source: str, row_mask: np.ndarray) -> list[str]:
    sub = df.loc[row_mask, source]
    if sub.apply(lambda v: isinstance(v, list)).any():
        return sorted({v for lst in sub for v in (lst or [])})
    return sorted({v for v in sub if v})


def build_structured_block(
    df: pd.DataFrame, embeddings: dict[str, dict[str, np.ndarray]]
) -> tuple[np.ndarray, list[dict]]:
    """Returns (matrix (N, D_structured), block_layout)."""
    n = len(df)
    columns: list[np.ndarray] = []
    layout: list[dict] = []
    cursor = 0

    for spec in STRUCTURED_BLOCK:
        name, encoder, source = spec["name"], spec["encoder"], spec["source"]
        w = float(STRUCTURED_FEATURE_WEIGHTS.get(name, 1.0))
        row_mask = _applies_mask(df, spec.get("applies_to", "all"))
        emb = embeddings.get(name)   # present only if MDS matrix was usable

        if encoder in ("mds", "mds_multi") and emb is not None:
            dims = len(next(iter(emb.values())))
            block = np.zeros((n, dims), dtype=np.float32)
            mean_vec = np.mean(list(emb.values()), axis=0)
            sec_col = spec.get("secondary")
            sec_strength = float(spec.get("secondary_strength", 0.5))
            for i in range(n):
                if not row_mask[i]:
                    continue
                if encoder == "mds_multi":
                    vals = df.iloc[i][source] or []
                    vecs = [emb[v] for v in vals if v in emb]
                    block[i] = np.mean(vecs, axis=0) if vecs else mean_vec
                else:
                    vecs, wts = [], []
                    primary = df.iloc[i][source]
                    if primary in emb:
                        vecs.append(emb[primary]); wts.append(1.0)
                    if sec_col:
                        sv = df.iloc[i][sec_col]
                        if sv in emb:
                            vecs.append(emb[sv]); wts.append(sec_strength)
                    block[i] = np.average(vecs, axis=0, weights=wts) if vecs else mean_vec
            effective_encoder = encoder

        elif encoder in ("mds", "mds_multi", "onehot"):
            # one-hot (native, or MDS fallback when the matrix was unusable)
            vocab = spec.get("vocab") or _vocab_from_db(df, source, row_mask)
            index = {v: i for i, v in enumerate(vocab)}
            block = np.zeros((n, max(len(vocab), 1)), dtype=np.float32)
            is_list = df[source].apply(lambda v: isinstance(v, list)).any()
            for i in range(n):
                if not row_mask[i]:
                    continue
                if is_list:
                    hits = [index[v] for v in (df.iloc[i][source] or []) if v in index]
                    if hits:
                        block[i, hits] = 1.0 / np.sqrt(len(hits))
                else:
                    v = df.iloc[i][source]
                    if v in index:
                        block[i, index[v]] = 1.0
            effective_encoder = "onehot" if encoder != "onehot" else "onehot"
            if encoder != "onehot":
                effective_encoder = "onehot_fallback"

        elif encoder == "rgb":
            block = np.zeros((n, 3), dtype=np.float32)
            for i in range(n):
                if not row_mask[i]:
                    continue
                rgb = hex_to_rgb01(df.iloc[i][source])
                block[i] = rgb if rgb else (0.5, 0.5, 0.5)
            effective_encoder = "rgb"

        elif encoder == "numeric_log":
            vals = pd.to_numeric(df[source], errors="coerce").fillna(0).to_numpy(dtype=np.float64)
            vals = np.log1p(np.clip(vals, 0, None))
            rng = vals.max() - vals.min()
            col = ((vals - vals.min()) / rng) if rng else np.zeros(n)
            block = (col * row_mask)[:, None].astype(np.float32)
            effective_encoder = "numeric_log"

        elif encoder == "numeric_minmax":
            vals = pd.to_numeric(df[source], errors="coerce")
            median = vals.median() if vals.notna().any() else 0.0
            vals = vals.fillna(median).to_numpy(dtype=np.float64)
            rng = vals.max() - vals.min()
            col = ((vals - vals.min()) / rng) if rng else np.zeros(n)
            block = (col * row_mask)[:, None].astype(np.float32)
            effective_encoder = "numeric_minmax"

        elif encoder == "bool":
            col = df[source].astype(bool).to_numpy(dtype=np.float32) * row_mask
            block = col[:, None].astype(np.float32)
            effective_encoder = "bool"

        else:
            raise ValueError(f"Unknown encoder '{encoder}' for feature '{name}'")

        block = block * w
        columns.append(block)
        layout.append({
            "name": name, "start": cursor, "end": cursor + block.shape[1],
            "dims": int(block.shape[1]), "weight": w, "encoder": effective_encoder,
        })
        cursor += block.shape[1]

    matrix = np.hstack(columns)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = (matrix / norms).astype(np.float32)
    logger.info(
        "Structured block: %d dims (%s)",
        matrix.shape[1],
        ", ".join(f"{l['name']}={l['dims']}({l['encoder']})" for l in layout),
    )
    return matrix, layout
