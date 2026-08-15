#!/bin/sh
# Container startup: build the recommendation artifacts, then serve the API.
#  - No SUPABASE_URL set  → in-memory mode with synthetic products (API testing).
#  - SUPABASE_URL set     → reads YOUR Supabase products (needs our migrations +
#                           products in that DB); persists if PC_ENABLE_DB_WRITES=true.
set -e

if [ -z "$SUPABASE_URL" ]; then
  echo "[entrypoint] No SUPABASE_URL — in-memory mode with synthetic products."
  python scripts/make_test_catalog.py
else
  echo "[entrypoint] Using Supabase at $SUPABASE_URL"
fi

echo "[entrypoint] Building recommendation artifacts (hash_fallback, offline)..."
python -m ml.pipeline.run_pipeline

echo "[entrypoint] Starting API on http://0.0.0.0:8000 (docs at /docs)"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
