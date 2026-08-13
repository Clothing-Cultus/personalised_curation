"""Copy a small product subset from PROD Supabase into your LOCAL Supabase.

READ-ONLY on prod (only .select is ever called). Writes only to local. Refuses
to run if the source and destination URLs are the same, so it can never write
to prod.

Credentials:
  Source (prod, read-only):  PROD_SUPABASE_URL, PROD_SUPABASE_SERVICE_KEY  (env vars)
  Destination (local):       SUPABASE_URL, SUPABASE_SERVICE_KEY            (your .env)

Run AFTER the local schema exists (existing tables cloned from prod + our
migrations/001_pc_tables.sql applied). Example:

    set PROD_SUPABASE_URL=https://<prod-ref>.supabase.co
    set PROD_SUPABASE_SERVICE_KEY=<prod-service-role-key>
    python scripts/seed_local_from_prod.py --limit 80

Tables copied: categories (all), products (limit), topwear/bottomwear (for those
products). Users/orders are NOT copied (privacy) — add a couple of test users by
hand if you want gender-scoped filtering; otherwise everyone sees unisex.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ENV  # noqa: E402


def _client(url: str, key: str):
    from supabase import create_client

    return create_client(url, key)


def _chunks(seq, n=100):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


# Generated columns (e.g. products.discount_percentage) are computed by Postgres
# and CANNOT be inserted — but `select *` from prod returns their values, so we
# must strip them before upserting into local. Detected from the local schema.
_gen_cache: dict[str, set] = {}


def _generated_columns(table: str) -> set:
    if table in _gen_cache:
        return _gen_cache[table]
    cols: set = set()
    try:
        import psycopg2

        url = os.environ.get(
            "LOCAL_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54422/postgres"
        )
        conn = psycopg2.connect(url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema="
            "'public' AND table_name=%s AND is_generated='ALWAYS';",
            (table,),
        )
        cols = {r[0] for r in cur.fetchall()}
        conn.close()
    except Exception:  # noqa: BLE001 — fall back to the known generated column
        cols = {"discount_percentage"} if table == "products" else set()
    _gen_cache[table] = cols
    return cols


def _strip(rows: list[dict], table: str) -> list[dict]:
    gen = _generated_columns(table)
    return [{k: v for k, v in r.items() if k not in gen} for r in rows] if gen else rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=80, help="max products to copy")
    args = parser.parse_args()

    prod_url = os.environ.get("PROD_SUPABASE_URL", "")
    prod_key = os.environ.get("PROD_SUPABASE_SERVICE_KEY", "")
    local_url, local_key = ENV.supabase_url, ENV.supabase_service_key

    if not (prod_url and prod_key):
        raise SystemExit("Set PROD_SUPABASE_URL and PROD_SUPABASE_SERVICE_KEY (source).")
    if not (local_url and local_key):
        raise SystemExit("Set SUPABASE_URL / SUPABASE_SERVICE_KEY in .env (local destination).")
    if prod_url.rstrip("/") == local_url.rstrip("/"):
        raise SystemExit(
            "REFUSING TO RUN: source and destination URLs are identical. "
            "Point .env at your LOCAL Supabase first."
        )

    src = _client(prod_url, prod_key)   # read-only
    dst = _client(local_url, local_key)

    print(f"Source (prod, read-only): {prod_url}")
    print(f"Destination (local):      {local_url}\n")

    # 1) FK targets products depend on, copied FIRST (else the products insert
    #    fails): categories (category_id), brands (brand_id, NOT NULL),
    #    pricing_presets (pricing_preset_id). All small.
    for ref in ("categories", "brands", "pricing_presets"):
        rows = src.table(ref).select("*").execute().data
        for batch in _chunks(rows):
            dst.table(ref).upsert(_strip(batch, ref), on_conflict="id").execute()
        print(f"{ref}: {len(rows)} copied")

    # 2) products (limit, active first for a useful seed)
    products = (src.table("products").select("*")
                .order("status").limit(args.limit).execute().data)
    for batch in _chunks(products):
        dst.table("products").upsert(_strip(batch, "products"), on_conflict="id").execute()
    print(f"products: {len(products)} copied")
    pids = [p["id"] for p in products]

    # 3) garment detail rows for those products
    for table in ("topwear", "bottomwear"):
        rows: list[dict] = []
        for chunk in _chunks(pids):
            rows += src.table(table).select("*").in_("product_id", chunk).execute().data
        for batch in _chunks(rows):
            dst.table(table).upsert(_strip(batch, table), on_conflict="product_id").execute()
        print(f"{table}: {len(rows)} copied")

    print("\nSeed complete. Next: run the pipeline against local, then the API.")


if __name__ == "__main__":
    main()
