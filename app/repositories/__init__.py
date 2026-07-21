"""Repositories — ALL db queries live here (spec §2.1). No business logic, no
FastAPI imports. Returns plain dicts. Services receive repo instances by
constructor injection and never import these implementations directly.

DB-write safety (matches the curator/ model, Hard Rule #2): while
ENABLE_DB_WRITES is False, nothing is written to Supabase — durable records are
appended to data/spool/*.jsonl and the in-memory stores are the source of
truth. Reads of existing tables (products/users) still hit Supabase when
credentials are present; pc_* reads fall back to the in-memory stores.
"""
