"""§4.6 — deck assembly: FILTER → SCORE → DIVERSIFY (MMR) → EXPLORE (Thompson).

Written as a pluggable scorer (PC-todo-later §3: the eventual collab blend is a
one-file change here). Business logic only — reads the in-memory artifact bundle
and the caller-supplied taste/session/history; no SQL, no FastAPI.

NOTE on the FILTER (spec §4.6 "confirmed live-read"): every scored product must
exist in the nightly matrix to have a vector, so the effective candidate set is
always (in-matrix ∩ live-active-in-stock). We filter against the in-memory meta
mirror (kept current by artifact_store's stock-refresh loop) rather than a
per-request query — same correctness, no N round trips, and it runs offline.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from core.config import (
    BESTSELLER_BONUS,
    BETA_PRIOR_ALPHA,
    BETA_PRIOR_BETA,
    BRAND_FATIGUE_CAP,
    BRAND_FATIGUE_PER_CARD,
    EXPLORE_MAX_PCT,
    EXPLORE_MIN_PCT,
    EXPLORE_PCT,
    GENDER_SCOPE,
    LEFT_SWIPE_COOLDOWN_DAYS,
    MAX_PER_BRAND_PER_BATCH,
    MMR_LAMBDA,
    MMR_POOL_SIZE,
    N_PRICE_BANDS,
    NEWNESS_BONUS,
    NEWNESS_WINDOW_DAYS,
    PERCENT_MATCH_DEFAULT,
    PERCENT_MATCH_DISPLAY_MAX,
    PERCENT_MATCH_DISPLAY_MIN,
    PRICE_BAND_AFFINITY_WEIGHT,
    RECENCY_PENALTY_WEIGHT,
    RECENCY_PENALTY_WINDOW_DAYS,
    REQUIRE_ACTIVE_STATUS,
    REQUIRE_STOCK,
    SEEN_SIMILAR_PENALTY,
    SEEN_SIMILAR_THRESHOLD,
    SIZE_MATCHING,
)
from core.normalize import normalize_size_stock

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  FILTER (spec §4.6 step 1)
# --------------------------------------------------------------------------- #
def _gender_ok(meta_gender: np.ndarray, user_gender: str | None) -> np.ndarray:
    """spec §4.6 (Q16): unknown gender → unisex only; stated → that gender + unisex."""
    if user_gender in ("male", "female"):
        want = "men" if user_gender == "male" else "women"
        return (meta_gender == want) | (meta_gender == "unisex") | (meta_gender == "")
    return (meta_gender == "unisex") | (meta_gender == "")


def _size_ok(meta: pd.DataFrame, size_prefs: dict) -> np.ndarray:
    """Serve only products stocked in the user's category-scoped size (spec §4.6)."""
    n = len(meta)
    ok = np.ones(n, dtype=bool)
    if not size_prefs:
        return ok
    sizes_col = meta["available_sizes"].to_numpy()
    stock_col = meta["items_available_in_each_size"].to_numpy()
    gclass = meta["garment_class"].to_numpy()
    for i in range(n):
        want = size_prefs.get("topwear" if gclass[i] == "top" else "bottomwear")
        if not want:
            continue
        want = str(want).upper()
        listed = [str(s).upper() for s in (sizes_col[i] if sizes_col[i] is not None else [])]
        if want not in listed:
            ok[i] = False
            continue
        try:
            per_size = normalize_size_stock(json.loads(stock_col[i]) if stock_col[i] else {})
        except (TypeError, json.JSONDecodeError):
            per_size = {}
        if per_size and per_size.get(want, 0) <= 0:
            ok[i] = False
    return ok


def _recent(history: list[dict], swipe: str, days: float) -> set[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = set()
    for h in history:
        if h["swipe"] != swipe:
            continue
        try:
            if datetime.fromisoformat(h["swiped_at"]) >= cutoff:
                out.add(h["product_id"])
        except (ValueError, TypeError):
            out.add(h["product_id"])
    return out


def candidate_mask(bundle, user_gender, size_prefs, served_ids, history) -> np.ndarray:
    meta = bundle.meta
    n = len(meta)
    mask = np.ones(n, dtype=bool)
    if REQUIRE_ACTIVE_STATUS:
        mask &= (meta["status"] == "active").to_numpy()
    if REQUIRE_STOCK:
        mask &= (meta["items_available"] > 0).to_numpy()
    if GENDER_SCOPE:
        mask &= _gender_ok(meta["gender"].to_numpy(), user_gender)
    if SIZE_MATCHING:
        mask &= _size_ok(meta, size_prefs)

    excluded = set(served_ids)
    excluded |= _recent(history, "left", LEFT_SWIPE_COOLDOWN_DAYS)
    # right-swiped stays out for a full freshness cycle (spec §4.6): reuse the
    # recency window as the cycle length here.
    excluded |= _recent(history, "right", LEFT_SWIPE_COOLDOWN_DAYS + RECENCY_PENALTY_WINDOW_DAYS)
    excluded |= _recent(history, "superlike", LEFT_SWIPE_COOLDOWN_DAYS + RECENCY_PENALTY_WINDOW_DAYS)
    if excluded:
        mask &= ~meta["id"].isin(excluded).to_numpy()
    return mask


# --------------------------------------------------------------------------- #
#  SCORE (spec §4.6 step 2)
# --------------------------------------------------------------------------- #
def _recency_penalty(history: list[dict], meta_ids: np.ndarray) -> np.ndarray:
    """Fading penalty for products just past the 30-day left-swipe cliff, so they
    don't re-enter as eligible as never-seen items (spec §4.6)."""
    now = datetime.now(timezone.utc)
    days_by_id: dict[str, float] = {}
    for h in history:
        if h["swipe"] != "left":
            continue
        try:
            d = (now - datetime.fromisoformat(h["swiped_at"])).total_seconds() / 86400
        except (ValueError, TypeError):
            continue
        days_by_id[h["product_id"]] = min(days_by_id.get(h["product_id"], 1e9), d)
    pen = np.zeros(len(meta_ids), dtype=np.float32)
    lo, hi = LEFT_SWIPE_COOLDOWN_DAYS, LEFT_SWIPE_COOLDOWN_DAYS + RECENCY_PENALTY_WINDOW_DAYS
    for i, pid in enumerate(meta_ids):
        d = days_by_id.get(pid)
        if d is not None and lo <= d < hi:
            pen[i] = RECENCY_PENALTY_WEIGHT * max(0.0, 1.0 - (d - lo) / RECENCY_PENALTY_WINDOW_DAYS)
    return pen


def score_candidates(bundle, taste, cand_idx, brand_counts, served_rows, history) -> np.ndarray:
    meta = bundle.meta
    sub = meta.iloc[cand_idx]

    if np.any(taste.vector):
        s = bundle.matrix[cand_idx] @ taste.vector.astype(np.float32)   # cosine (rows normalized)
    else:
        s = np.zeros(len(cand_idx), dtype=np.float32)

    affinity = taste.price_affinity(N_PRICE_BANDS)
    s = s + PRICE_BAND_AFFINITY_WEIGHT * affinity[sub["price_band"].to_numpy()]
    s = s + BESTSELLER_BONUS * sub["is_bestseller"].to_numpy(dtype=np.float32)

    created = pd.to_datetime(sub["created_at"], errors="coerce", utc=True)
    age_days = (pd.Timestamp.now(tz="UTC") - created).dt.total_seconds() / 86400
    s = s + NEWNESS_BONUS * (age_days < NEWNESS_WINDOW_DAYS).fillna(False).to_numpy(dtype=np.float32)

    fatigue = np.array(
        [min(brand_counts.get(b, 0) * BRAND_FATIGUE_PER_CARD, BRAND_FATIGUE_CAP)
         for b in sub["brand_id"]], dtype=np.float32)
    s = s - fatigue

    if served_rows:
        sims = bundle.matrix[cand_idx] @ bundle.matrix[served_rows].T
        s = s - SEEN_SIMILAR_PENALTY * (sims.max(axis=1) > SEEN_SIMILAR_THRESHOLD)

    s = s - _recency_penalty(history, sub["id"].to_numpy())
    return s.astype(np.float32)


def percent_match(scores: np.ndarray) -> np.ndarray:
    """Percentile-rank within the pool → readable band (spec §4.7, [OPEN] method)."""
    n = len(scores)
    lo, hi = PERCENT_MATCH_DISPLAY_MIN, PERCENT_MATCH_DISPLAY_MAX
    if n == 0:
        return np.array([], dtype=int)
    if n == 1:
        return np.array([PERCENT_MATCH_DEFAULT], dtype=int)
    ranks = scores.argsort().argsort().astype(np.float64) / (n - 1)
    return np.rint(lo + ranks * (hi - lo)).astype(int)


# --------------------------------------------------------------------------- #
#  DIVERSIFY (MMR, ≤2/brand per batch) + EXPLORE (Thompson)  (spec §4.6 3–4)
# --------------------------------------------------------------------------- #
def mmr_select(bundle, cand_idx, scores, k, brand_counts_batch) -> list[int]:
    if len(cand_idx) == 0 or k <= 0:
        return []
    order = np.argsort(scores)[::-1][:MMR_POOL_SIZE]
    pool = cand_idx[order]
    pool_scores = scores[order]
    vecs = bundle.matrix[pool]
    sim = vecs @ vecs.T
    brands = bundle.meta["brand_id"].to_numpy()

    picked: list[int] = []
    picked_pos: list[int] = []
    remaining = set(range(len(pool)))
    while len(picked) < min(k, len(pool)) and remaining:
        best_j, best_val = None, -np.inf
        for j in remaining:
            brand = brands[int(pool[j])]
            if brand_counts_batch.get(brand, 0) >= MAX_PER_BRAND_PER_BATCH:
                continue
            penalty = sim[j, picked_pos].max() if picked_pos else 0.0
            val = MMR_LAMBDA * pool_scores[j] - (1 - MMR_LAMBDA) * penalty
            if val > best_val:
                best_j, best_val = j, val
        if best_j is None:      # every remaining candidate hit the brand cap
            break
        picked.append(int(pool[best_j]))
        picked_pos.append(best_j)
        remaining.discard(best_j)
        brand_counts_batch[brands[int(pool[best_j])]] = (
            brand_counts_batch.get(brands[int(pool[best_j])], 0) + 1)
    return picked


def n_explore_cards(n_cards: int) -> int:
    n = int(round(EXPLORE_PCT * n_cards))
    n = max(n, int(np.floor(EXPLORE_MIN_PCT * n_cards)))
    n = min(n, int(np.floor(EXPLORE_MAX_PCT * n_cards)), n_cards)
    return max(n, 0)


def pick_explore(bundle, taste, cand_idx, scores, n, exclude_rows, rng=None) -> list[int]:
    if n <= 0 or len(cand_idx) == 0:
        return []
    rng = rng or np.random.default_rng()
    clusters = bundle.meta["cluster"].to_numpy()
    by_cluster: dict[int, list[int]] = {}
    for pos in np.argsort(scores)[::-1]:
        row = int(cand_idx[pos])
        if row in exclude_rows:
            continue
        by_cluster.setdefault(int(clusters[row]), []).append(row)

    picked: list[int] = []
    for _ in range(n):
        arms = [c for c, rows in by_cluster.items() if rows]
        if not arms:
            break
        samples = []
        for c in arms:
            succ, fail = taste.cluster_stats.get(c, [0.0, 0.0])
            samples.append(rng.beta(BETA_PRIOR_ALPHA + succ, BETA_PRIOR_BETA + fail))
        winner = arms[int(np.argmax(samples))]
        row = by_cluster[winner].pop(0)
        picked.append(row)
        exclude_rows.add(row)
    return picked


# --------------------------------------------------------------------------- #
#  Orchestration
# --------------------------------------------------------------------------- #
def assemble_batch(bundle, taste, *, user_gender, size_prefs, served_ids, history,
                   brand_counts, n_cards, seed) -> list[dict]:
    """Return up to n_cards card dicts: {product_id, percent_match, is_explore, score}."""
    mask = candidate_mask(bundle, user_gender, size_prefs, served_ids, history)
    cand_idx = np.flatnonzero(mask)
    if len(cand_idx) == 0:
        return []

    served_rows = [bundle.id_to_row[p] for p in served_ids if p in bundle.id_to_row]
    scores = score_candidates(bundle, taste, cand_idx, brand_counts, served_rows, history)
    pcts = percent_match(scores)
    pct_by_row = {int(r): int(p) for r, p in zip(cand_idx, pcts)}
    score_by_row = {int(r): float(s) for r, s in zip(cand_idx, scores)}

    n_exp = n_explore_cards(n_cards) if np.any(taste.vector) else 0
    batch_brands: dict = {}
    main_rows = mmr_select(bundle, cand_idx, scores, n_cards - n_exp, batch_brands)
    explore_rows = pick_explore(bundle, taste, cand_idx, scores,
                                n_cards - len(main_rows), exclude_rows=set(main_rows), rng=np.random.default_rng(seed))

    cards = [{"product_id": bundle.meta.iloc[r]["id"], "percent_match": pct_by_row.get(r, PERCENT_MATCH_DEFAULT),
              "is_explore": False, "score": score_by_row.get(r, 0.0)} for r in main_rows]
    cards += [{"product_id": bundle.meta.iloc[r]["id"], "percent_match": pct_by_row.get(r, PERCENT_MATCH_DEFAULT),
               "is_explore": True, "score": score_by_row.get(r, 0.0)} for r in explore_rows]

    rng = np.random.default_rng(seed)
    rng.shuffle(cards)   # spread explore cards through the batch
    return cards[:n_cards]
