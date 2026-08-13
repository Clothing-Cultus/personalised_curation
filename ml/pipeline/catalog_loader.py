"""Load the product catalog into one normalized DataFrame (spec §3.1, §4.2).

Source:
  Supabase (read-only) when service credentials are present, otherwise a local
  JSON dump at data/exports/catalog.json (same shape) for offline dev/verify.

Reads products / topwear / bottomwear / categories. Existing tables are NEVER
written — Hard Rule #1. One row per product, every categorical value normalized.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from core.config import ENV, REPO_ROOT
from core.normalize import normalize_list, normalize_size_stock, normalize_value

logger = logging.getLogger(__name__)

LOCAL_CATALOG_PATH = REPO_ROOT / "data" / "exports" / "catalog.json"

PRODUCT_COLUMNS = (
    "id, product_title, product_description, brand_id, category_id, gender, "
    "fashion_style_primary, fashion_style_secondary, fashion_sub_style, "
    "design, print_type, print_keywords, wash_type, distressed, "
    "colour_name_majority, rgb_code_majority, secondary_rgb_code, gsm, "
    "selling_price, stretchable, is_bestseller, ranking_score, status, "
    "items_available, available_sizes, items_available_in_each_size, images, "
    "created_at"
)


def _use_supabase() -> bool:
    return bool(ENV.supabase_url and ENV.supabase_service_key)


def _fetch_all(table: str, columns: str = "*", page_size: int = 1000) -> list[dict]:
    """Paged select — Supabase caps a single select at 1000 rows. READ-ONLY."""
    from supabase import create_client

    sb = create_client(ENV.supabase_url, ENV.supabase_service_key)
    rows: list[dict] = []
    offset = 0
    while True:
        page = sb.table(table).select(columns).range(offset, offset + page_size - 1).execute().data
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def _load_raw() -> dict[str, list[dict]]:
    if _use_supabase():
        logger.info("Loading catalog from Supabase (read-only)")
        # topwear/bottomwear use "*" so a schema missing the newer columns
        # (hooded / fade / closure_type, spec §3.1) doesn't error the select —
        # load_catalog reads every field defensively with .get() and defaults.
        return {
            "products": _fetch_all("products", PRODUCT_COLUMNS),
            "topwear": _fetch_all("topwear", "*"),
            "bottomwear": _fetch_all("bottomwear", "*"),
            "categories": _fetch_all("categories", "id, title"),
        }
    logger.info("No Supabase credentials — loading local catalog from %s", LOCAL_CATALOG_PATH)
    with open(LOCAL_CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_catalog() -> pd.DataFrame:
    raw = _load_raw()
    cat_title = {c["id"]: c["title"] for c in raw.get("categories", [])}
    top = {t["product_id"]: t for t in raw.get("topwear", [])}
    bottom = {b["product_id"]: b for b in raw.get("bottomwear", [])}

    rows: list[dict[str, Any]] = []
    for p in raw["products"]:
        pid = p["id"]
        t, b = top.get(pid), bottom.get(pid)
        garment_class = "top" if t else "bottom" if b else "unknown"

        images = p.get("images") or []
        if isinstance(images, str):
            try:
                images = json.loads(images)
            except json.JSONDecodeError:
                images = [images]

        rows.append(
            {
                "id": pid,
                "product_title": p.get("product_title") or "",
                "product_description": p.get("product_description") or "",
                "brand_id": p.get("brand_id"),
                "category_title": normalize_value(cat_title.get(p.get("category_id"))),
                "gender": normalize_value(p.get("gender")) or "unisex",
                "fashion_style_primary": normalize_value(p.get("fashion_style_primary")),
                "fashion_style_secondary": normalize_value(p.get("fashion_style_secondary")),
                "fashion_sub_style": normalize_value(p.get("fashion_sub_style")),
                "design": normalize_value(p.get("design")),
                "print_type": normalize_value(p.get("print_type")),
                "print_keywords": normalize_list(p.get("print_keywords")),
                "wash_type": normalize_value(p.get("wash_type")),
                "distressed": normalize_value(p.get("distressed")),
                "colour_name_majority": normalize_value(p.get("colour_name_majority")),
                "rgb_code_majority": (p.get("rgb_code_majority") or "").strip(),
                "gsm": p.get("gsm"),
                "selling_price": p.get("selling_price") or 0,
                "stretchable": bool(p.get("stretchable")),
                "is_bestseller": bool(p.get("is_bestseller")),
                "ranking_score": p.get("ranking_score") or 0,
                "status": normalize_value(p.get("status")),
                "items_available": p.get("items_available") or 0,
                "available_sizes": [
                    s.strip().upper() for s in (p.get("available_sizes") or []) if s
                ],
                "items_available_in_each_size": normalize_size_stock(
                    p.get("items_available_in_each_size")
                ),
                "images": images,
                "created_at": p.get("created_at"),
                "garment_class": garment_class,
                # garment-specific (topwear / bottomwear), normalized:
                "fitting_type": normalize_value((t or b or {}).get("fitting_type")),
                "neck_type": normalize_value((t or {}).get("neck_type")),
                "sleeves": normalize_value((t or {}).get("sleeves")),
                "hooded": bool((t or {}).get("hooded")),
                "waist_type": normalize_value((b or {}).get("waist_type")),
                "fade": normalize_value((b or {}).get("fade")),
                "closure_type": normalize_value((b or {}).get("closure_type")),
                "length": normalize_value((t or b or {}).get("length")),
            }
        )

    df = pd.DataFrame(rows)
    logger.info(
        "Catalog loaded: %d products (%d active, %d top / %d bottom)",
        len(df),
        int((df["status"] == "active").sum()),
        int((df["garment_class"] == "top").sum()),
        int((df["garment_class"] == "bottom").sum()),
    )
    return df


def export_catalog_to_json(path: Path | None = None) -> Path:
    """Snapshot the DB to data/exports/catalog.json for offline dev (READ-ONLY DB)."""
    path = path or LOCAL_CATALOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_load_raw(), f, default=str)
    logger.info("Catalog exported → %s", path)
    return path
