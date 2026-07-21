"""Replay spooled JSONL events into Supabase — run ONCE after you have:
  1. applied migrations/001_pc_tables.sql yourself, and
  2. set core/config.py → ENABLE_DB_WRITES = True.

    python scripts/replay_spool.py --dry-run    # show what would be written
    python scripts/replay_spool.py              # actually write

Idempotent on natural keys (session id, (session_id, product_id), user_id), so
replaying twice is safe. Processed files are renamed *.done.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ENABLE_DB_WRITES, SPOOL_DIR  # noqa: E402
from core.logging_config import setup_logging  # noqa: E402


def load_records(spool_dir: Path) -> dict[str, list[dict]]:
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(spool_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                by_kind[rec.pop("_kind")].append(rec)
    return by_kind


def _clean(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    setup_logging("INFO")

    if not ENABLE_DB_WRITES and not args.dry_run:
        raise SystemExit(
            "ENABLE_DB_WRITES is False — refusing to write. Run migrations, flip "
            "the flag in core/config.py, then re-run (or use --dry-run)."
        )

    by_kind = load_records(SPOOL_DIR)
    for kind, records in by_kind.items():
        print(f"{kind}: {len(records)} records")
    if args.dry_run:
        return

    from app.repositories.base import supabase

    sb = supabase()

    def batched(rows, size=500):
        for i in range(0, len(rows), size):
            yield rows[i : i + size]

    # Order matters: sessions before session_items (FK).
    for rows in batched(_clean(by_kind.get("pc_sessions", []))):
        sb.table("pc_sessions").upsert(rows, on_conflict="id").execute()
    for patch in _clean(by_kind.get("pc_session_updates", [])):
        sid = patch.pop("session_id", None)
        if sid:
            sb.table("pc_sessions").update(patch).eq("id", sid).execute()
    for rows in batched(_clean(by_kind.get("pc_session_items", []))):
        # drop the extra user_id we spooled for local indexing (not a column)
        rows = [{k: v for k, v in r.items() if k != "user_id"} for r in rows]
        sb.table("pc_session_items").upsert(rows, on_conflict="session_id,product_id").execute()
    # latest taste vector per user wins
    latest: dict[str, dict] = {}
    for r in _clean(by_kind.get("user_taste", [])):
        latest[r["user_id"]] = r
    for rows in batched(list(latest.values())):
        sb.table("user_taste").upsert(rows, on_conflict="user_id").execute()

    for path in SPOOL_DIR.glob("*.jsonl"):
        path.rename(path.with_suffix(".jsonl.done"))
    print("Replay complete; spool files renamed *.done")


if __name__ == "__main__":
    main()
