"""core/config.py — THE single source of every tunable number in the system.

Hard Rule #8 (spec §0): batch size, explore %, every weight, every threshold
lives here, never inline in logic. Every constant carries an inline comment
explaining what it is and why its default is what it is, so this file is
self-explanatory without cross-referencing PC-glossary.md (spec §2.3).

WHY core/ IS TOP-LEVEL (not under app/ as spec §2.1 draws it):
  spec §2.2 forbids ml/ from importing app/, yet the nightly pipeline needs
  REFERENCE_DATA_DIR and the per-feature structured weights, which live here.
  The only non-contradictory placement is a shared top-level package that both
  app/ and ml/ import. `core` holds pure data/constants + framework-free
  helpers (normalize, logging, errors, auth) — never business logic or SQL, so
  it introduces none of the coupling that rule §2.2 protects against.

Secrets (Supabase keys, the shared API secret) are read from the environment /
.env — NEVER hardcoded here. Everything else is a git-versioned constant below.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = the personalised_curation/ directory (parent of core/).
# Override with PC_ROOT if the code is deployed under a different path.
REPO_ROOT = Path(os.environ.get("PC_ROOT", Path(__file__).resolve().parent.parent))


# ===========================================================================
#  Secrets & environment (from .env / environment — never committed)
# ===========================================================================
class EnvSettings(BaseSettings):
    """The only values NOT git-versioned: secrets and per-environment switches."""

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Supabase project (read-only until ENABLE_DB_WRITES flips true).
    supabase_url: str = ""
    supabase_service_key: str = ""
    # The single static shared secret the calling server must send (spec §6.1).
    # Empty ⇒ auth disabled (local dev only).
    internal_key: str = ""
    pc_env: str = "development"
    # Optional override for where the similarity-matrix / reference CSVs live.
    reference_data_dir: str = ""


ENV = EnvSettings()


# ===========================================================================
#  Reference data & artifacts (paths)
# ===========================================================================
# REFERENCE_DATA_DIR (spec §2.2): base folder holding the similarity matrices
# and the raw value lists. Read by the pipeline ONLY, never by the serving app.
# Default is the self-contained in-repo copy (ml/reference_data/) so a fresh
# clone runs the pipeline with no external data; falls back to the delivered
# "past data" folder beside the repo; override with REFERENCE_DATA_DIR in .env.
def _default_reference_dir() -> Path:
    in_repo = REPO_ROOT / "ml" / "reference_data"
    if (in_repo / "Data").exists():
        return in_repo
    return REPO_ROOT.parent / "past data"


REFERENCE_DATA_DIR = Path(ENV.reference_data_dir) if ENV.reference_data_dir else _default_reference_dir()
# The 12 manual similarity matrices (spec §4.3) live under this subpath.
SIMILARITY_MATRICES_DIR = (
    REFERENCE_DATA_DIR
    / "Data" / "Synthetic data" / "Features similarity matrices"
    / "sentence_transformer_manual_04_08_2025"
)
# The full legitimate value list per feature, used for matrix coverage checks.
POSSIBLE_VALUES_CSV = REFERENCE_DATA_DIR / "Data" / "Raw data" / "possible values of product.csv"

# ML artifacts (product_matrix.npy, product_meta.parquet, clusters.json, ...).
# The pipeline writes here; the serving app reads here and nothing else from ml/.
ARTIFACTS_DIR = REPO_ROOT / "ml" / "artifacts"
# Image-download / embedding cache (only new/changed products re-embed).
CACHE_DIR = REPO_ROOT / "ml" / "cache"
# Local spool for durable events while ENABLE_DB_WRITES is false.
SPOOL_DIR = REPO_ROOT / "data" / "spool"


# ===========================================================================
#  Service / infra
# ===========================================================================
HOST = "0.0.0.0"
PORT = 8000
# 1 worker until sticky routing or Redis exists (PC-todo-later §4): >1 worker
# gives each process an independent in-memory cache → stale-vector reads.
WORKERS = 1
LOG_LEVEL = "INFO"
# Origins allowed to call the API. The real caller is a trusted server, but CORS
# is kept tight in case a browser ever hits /healthz directly.
CORS_ALLOWED_ORIGINS = ["http://localhost:3000", "https://cultus.in"]

# MASTER SWITCH for all database writes (matches the curator/ safety model).
# While False: NOTHING is written to Supabase — sessions, items, taste vectors
# spool to data/spool/*.jsonl and the in-memory stores are the source of truth.
# Flip to True ONLY after running migrations/001_pc_tables.sql by hand (Hard
# Rule #2: no migration is ever executed by code).
ENABLE_DB_WRITES = True

# Artifact hot-reload: the app polls manifest.json this often and swaps the
# in-memory matrix atomically when a new pipeline version appears.
ARTIFACT_RELOAD_CHECK_SECONDS = 60


# ===========================================================================
#  Session limits & timers (spec §5, PC-glossary)
# ===========================================================================
CARDS_LIMIT = 40         # products a session serves before products_exhausted (confirmed v9)
RIGHTS_LIMIT = 8         # right-swipes + superlikes before rights_exhausted
SUPERLIKES_LIMIT = 1     # superlikes per session

BATCH_SIZE = 10          # cards returned per refill
SEND_THRESHOLD = 3       # send the next batch when the client queue drops to this
INITIAL_BATCH_SIZE = 10  # cards in the very first batch of a session

# Two DIFFERENT timers (glossary): minutes vs. days — never the same thing.
ABANDON_AFTER_MINUTES = 30       # idle → 'abandoned' for analytics; resumable, forfeits nothing
RENEWAL_PERIOD_DAYS = 7          # a whole cycle's allotment expires after this, from started_at
# started_at is set on the user's FIRST SWIPE, not row creation (spec §5.6), so
# a cycle that's served but never swiped never "starts" and never lapses.


# ===========================================================================
#  Taste vector — swipe / purchase weights & decay (spec §4.5, glossary)
# ===========================================================================
# How strongly one interaction moves the user vector u.
SWIPE_WEIGHTS = {
    "right": 1.0,
    "superlike": 2.5,          # raised from 2.0 — a superlike is a strong signal
    "left": -0.4,              # baseline rejection
    "left_engaged": -0.1,      # looked properly before rejecting → weaker negative
}
# A left swipe counts as "engaged" (weaker penalty) if the user explored it.
LEFT_ENGAGED_MIN_IMAGES = 2    # last_image_discovered ≥ this, OR
LEFT_ENGAGED_DETAILS_READ = True  # details_read is true

# Purchase tiers — applied ONLY by the nightly job, never in the swipe path.
PURCHASE_WEIGHTS = {
    "gift": 1.0,               # confirmed gift — someone else's taste, treat like a right-swipe
    "unknown": 3.5,            # gift status not yet known (default until the flag ships)
    "self": 5.0,               # confirmed self-purchase — the strongest signal there is
}

# Decay: keeps u tracking CURRENT taste rather than averaging all of history.
WITHIN_SESSION_DECAY = 0.95    # u ← 0.95·u + w·p on every swipe during a session
WEEKLY_DECAY = 0.98            # per-week multiplier applied on read (~77% survives 3 months)
DECAY_FLOOR = 0.5              # u is never decayed below 50% no matter how long away


# ===========================================================================
#  Scoring (spec §4.6 step 2, glossary)
# ===========================================================================
# s = cos(u,P) + business nudges − penalties.
PRICE_BAND_AFFINITY_WEIGHT = 0.10   # reward products in the user's learned price band
BESTSELLER_BONUS = 0.06             # small nudge for is_bestseller
NEWNESS_BONUS = 0.05                # small nudge for recently-added products
NEWNESS_WINDOW_DAYS = 21            # a product counts as "new" this long after created_at
N_PRICE_BANDS = 4                   # quantile bands of selling_price

BRAND_FATIGUE_PER_CARD = 0.03       # subtracted per already-served card of the same brand
BRAND_FATIGUE_CAP = 0.12            # …capped here so one brand isn't buried entirely
SEEN_SIMILAR_THRESHOLD = 0.92       # cosine vs already-served cards above which …
SEEN_SIMILAR_PENALTY = 0.08         # …this penalty applies (kills near-duplicate cards)

# recency_penalty (spec §4.6): softens the hard 30-day cliff for left-swiped items
# so they don't become instantly as eligible as never-seen products at day 31.
LEFT_SWIPE_COOLDOWN_DAYS = 30       # hard exclusion window for left-swiped products
RECENCY_PENALTY_WEIGHT = 0.10       # strength of the fading penalty after the cliff
RECENCY_PENALTY_WINDOW_DAYS = 60    # penalty fades linearly to 0 over this many days past day 30

# taste-match % (spec §4.7): percentile-rank of s within the candidate pool,
# mapped into a readable band. Exact method still [OPEN] — see PC-todo-later §8.
PERCENT_MATCH_DISPLAY_MIN = 58
PERCENT_MATCH_DISPLAY_MAX = 97
PERCENT_MATCH_DEFAULT = 75          # shown when no per-pool calibration is possible


# ===========================================================================
#  Diversity & exploration (spec §4.6 steps 3–4)
# ===========================================================================
MMR_LAMBDA = 0.7                    # score' = λ·relevance − (1−λ)·max_sim_to_picked
MMR_POOL_SIZE = 60                  # MMR re-ranks only the top-N by score (keeps it fast)
MAX_PER_BRAND_PER_BATCH = 2         # spec §4.6 step 3: ≤2 of one brand per batch

EXPLORE_PCT = 0.25                  # fraction of a batch that is deliberately exploratory
EXPLORE_MIN_PCT = 0.15              # guardrail floor (spec §9 pitfalls)
EXPLORE_MAX_PCT = 0.30              # guardrail ceiling
BETA_PRIOR_ALPHA = 1.0              # Thompson-sampling Beta prior, successes side
BETA_PRIOR_BETA = 1.0              # Thompson-sampling Beta prior, failures side


# ===========================================================================
#  Cold start (spec §5.2 step 3)
# ===========================================================================
# Brand-new / version-mismatched users get the anchor deck. Vibe-seeded cold
# start (mean of exemplar vectors) is available but currently everyone within
# gender_scope gets the same anchor deck (spec §5.2 step 3, point 8).
EXEMPLARS_PER_VIBE = 10             # top products per vibe used to seed a cold vector


# ===========================================================================
#  Vector composition (spec §4.1–§4.4)
# ===========================================================================
CLIP_IMAGE_WEIGHT = 0.7            # blended embedding = 0.7·image + 0.3·text
CLIP_TEXT_WEIGHT = 0.3
STRUCTURED_BLOCK_WEIGHT = 0.3      # structured block weight vs the CLIP embedding block

# Embedding backend: "clip" (open_clip ViT-B/32, production) or "hash_fallback"
# (deterministic pseudo-embeddings so the whole system runs without torch — dev).
# Default is production "clip"; a dev box without torch can override via the
# PC_EMBEDDING_BACKEND env var without editing this committed default.
EMBEDDING_BACKEND = os.environ.get("PC_EMBEDDING_BACKEND", "clip")
CLIP_MODEL = "ViT-B-32"
CLIP_PRETRAINED = "laion2b_s34b_b79k"
EMBEDDING_DIM = 512
EMBEDDING_DEVICE = "cpu"           # "cuda" if the box ever has a GPU
IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 20
IMAGE_DOWNLOAD_WORKERS = 8

N_CLUSTERS = 12                    # KMeans arms for Thompson-sampling exploration
ANCHOR_DECK_SIZE = 30              # precomputed degradation / cold-start deck size

# The artifact files the pipeline writes and the API reads (order-independent).
ARTIFACT_FILES = [
    "product_matrix.npy", "product_meta.parquet", "clusters.json",
    "anchor_deck.json", "vibe_vectors.npz", "manifest.json",
]

# Remote artifact storage (Supabase Storage, S3-compatible). Used in
# staging/prod so the nightly pipeline publishes the matrix once and every API
# instance downloads the same build for its in-memory hot path. The upload
# (pipeline) and download (API) CALL SITES are commented out for now — enable
# them for staging/prod (see TODO). Create the `pc` bucket manually first.
ARTIFACT_BUCKET = "pc"
ARTIFACT_REMOTE_FOLDER = "artifacts"


# ===========================================================================
#  Categorical embeddings via MDS (spec §4.3) — per-feature, not style-only
# ===========================================================================
# alias → canonical, applied AFTER the standard normalize() pass, to line up
# DB snake_case values with the matrices' display-name labels.
VALUE_ALIASES = {
    "blokecore": "blokecore",
    "bloke_core": "blokecore",
    "y2k_fashion": "y2k_fashion",
    "y2k": "y2k_fashion",
    "ig_core": "ig_core",
    "igcore": "ig_core",
}


def mds_dims_for(n_values: int) -> int:
    """Target MDS dimensionality for a feature with n_values labels (spec §4.3:
    dims scale with value count — 4→2, 41→~6 — not a fixed 3–5 for everything)."""
    import math

    if n_values <= 2:
        return 1
    return max(2, min(n_values - 1, math.ceil(math.sqrt(n_values))))


# The structured block layout. ORDER HERE IS THE VECTOR LAYOUT. Each entry:
#   name           unique sub-block name (also the weights key below)
#   source         DB column it reads (or list column for multihot)
#   encoder        mds | mds_multi | onehot | rgb | numeric_log | numeric_minmax | bool
#   matrix         similarity-matrix file stem (mds/mds_multi only); MDS with graceful
#                  fallback to one-hot if the matrix is malformed (e.g. category fusion bug)
#   applies_to     all | top | bottom  (zero-filled for the other garment class)
#   secondary / secondary_strength   (style only: fold a secondary column in at reduced weight)
#   vocab          explicit list for small fixed one-hots
STRUCTURED_BLOCK = [
    # -- garment-agnostic ----------------------------------------------------
    {"name": "style", "source": "fashion_style_primary", "encoder": "mds",
     "matrix": "fashion_style", "applies_to": "all",
     "secondary": "fashion_style_secondary", "secondary_strength": 0.5},
    {"name": "sub_style", "source": "fashion_sub_style", "encoder": "mds",
     "matrix": "fashion_sub_style", "applies_to": "all"},
    {"name": "category", "source": "category_title", "encoder": "mds",
     "matrix": "category", "applies_to": "all"},   # category matrix is fused → auto one-hot fallback
    {"name": "design", "source": "design", "encoder": "mds",
     "matrix": "design", "applies_to": "all"},
    {"name": "print_type", "source": "print_type", "encoder": "onehot", "applies_to": "all"},
    {"name": "print_keywords", "source": "print_keywords", "encoder": "mds_multi",
     "matrix": "print_key_words", "applies_to": "all"},
    {"name": "wash_type", "source": "wash_type", "encoder": "onehot", "applies_to": "all"},
    {"name": "distressed", "source": "distressed", "encoder": "mds",
     "matrix": "distressed", "applies_to": "all"},
    {"name": "color", "source": "rgb_code_majority", "encoder": "rgb", "applies_to": "all"},
    {"name": "price", "source": "selling_price", "encoder": "numeric_log", "applies_to": "all"},
    {"name": "gsm", "source": "gsm", "encoder": "numeric_minmax", "applies_to": "all"},
    {"name": "gender", "source": "gender", "encoder": "onehot", "applies_to": "all",
     "vocab": ["men", "women", "unisex"]},
    {"name": "stretchable", "source": "stretchable", "encoder": "bool", "applies_to": "all"},
    # -- topwear-only (zeros for bottoms) ------------------------------------
    {"name": "hooded", "source": "hooded", "encoder": "bool", "applies_to": "top"},
    {"name": "sleeves", "source": "sleeves", "encoder": "mds",
     "matrix": "sleeves", "applies_to": "top"},
    {"name": "neck_type", "source": "neck_type", "encoder": "mds",
     "matrix": "neck_type", "applies_to": "top"},
    {"name": "top_fitting", "source": "fitting_type", "encoder": "mds",
     "matrix": "top_fitting", "applies_to": "top"},
    {"name": "length_top", "source": "length", "encoder": "onehot", "applies_to": "top"},
    # -- bottomwear-only (zeros for tops) ------------------------------------
    {"name": "bottom_fitting", "source": "fitting_type", "encoder": "mds",
     "matrix": "bottom_fitting", "applies_to": "bottom"},
    {"name": "waist_type", "source": "waist_type", "encoder": "mds",
     "matrix": "waist_type", "applies_to": "bottom"},
    {"name": "fade", "source": "fade", "encoder": "mds",
     "matrix": "fade", "applies_to": "bottom"},
    {"name": "closure_type", "source": "closure_type", "encoder": "onehot", "applies_to": "bottom"},
    {"name": "length_bottom", "source": "length", "encoder": "onehot", "applies_to": "bottom"},
]

# Per-feature scalar weight (spec §4.4) applied to each sub-block BEFORE the
# structured block is L2-normalized — raise a number to make that attribute
# matter more in similarity. NOTE: adding a feature dilutes every existing one
# under renormalization, so re-tune rather than only appending.
STRUCTURED_FEATURE_WEIGHTS = {
    "style": 3.0, "sub_style": 3.0, "category": 3.0, "design": 1.5,
    "print_type": 1.0, "print_keywords": 1.0, "wash_type": 0.6, "distressed": 0.6,
    "color": 1.2, "price": 2.0, "gsm": 0.4, "gender": 1.0, "stretchable": 0.3,
    "hooded": 0.4, "sleeves": 0.8, "neck_type": 0.6, "top_fitting": 1.2,
    "length_top": 0.8, "bottom_fitting": 1.2, "waist_type": 0.6, "fade": 0.6,
    "closure_type": 0.4, "length_bottom": 0.8,
}


# ===========================================================================
#  Filters (spec §4.6 step 1)
# ===========================================================================
REQUIRE_ACTIVE_STATUS = True
REQUIRE_STOCK = True
GENDER_SCOPE = True            # unknown gender → unisex only; stated → that gender + unisex
SIZE_MATCHING = True           # only serve products available in the user's category size
# Seconds between lightweight Supabase reads refreshing ONLY stock/status/price
# of the in-memory mirror (0 disables; used only when creds set). Runs on a
# background daemon thread (one small paged read of ~1k rows), never in the
# request path — lowering it does not slow serving. 60s ⇒ sold-out items drop
# out within a minute.
STOCK_REFRESH_SECONDS = 60


# ===========================================================================
#  CV attribute extraction (spec §4.1(b), PC-todo-later §11) — deferred workflow
# ===========================================================================
CV_MIN_CONFIDENCE = 0.35       # below this, a proposal is flagged low-confidence in review
CV_IMAGES_PER_PRODUCT = 3      # CV runs on the first 3 images only (spec §11)
CV_PROPOSALS_DIR = REPO_ROOT / "data" / "cv_proposals"
