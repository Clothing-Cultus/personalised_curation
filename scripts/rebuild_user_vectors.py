"""Offline vector-rebuild (replay) job — spec §3.3 / PC-todo-later §5.

After a pipeline change that alters the matrix layout (new/removed feature,
retuned structured weights), every stored user vector is on the OLD version and
would otherwise cold-start. This job replays each affected user's swipe history
(`pc_session_items`) through the SAME vector-update function used live
(`taste_service.rebuild_vector`) against the NEW matrix, and writes the vector
back stamped with the new version — so returning users are personalized, not cold.

Run it right AFTER the pipeline in the nightly job (see scripts/nightly.py), so
by the time users return their vectors are already rebuilt.

    python scripts/rebuild_user_vectors.py

Only meaningful once ENABLE_DB_WRITES is true (history + vectors live in the DB).
With writes off it operates on whatever in-memory/spooled state exists.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logging_config import setup_logging  # noqa: E402

logger = logging.getLogger("rebuild_vectors")


def run() -> int:
    from app.services.artifact_store import artifact_store
    from app.services import taste_service
    import app.deps as deps

    artifact_store.try_load()
    if not artifact_store.loaded:
        raise SystemExit("No artifacts loaded — run the pipeline first.")
    bundle = artifact_store.bundle

    # Stale = vector built under a different FEATURE LAYOUT (not just a different
    # run). Product-only rebuilds keep the same layout_signature → nobody stale.
    sig = bundle.layout_signature
    stale = deps.taste_repo.list_stale_user_ids(sig)
    logger.info("Rebuilding %d user vectors against layout %s", len(stale), sig)

    rebuilt = 0
    for user_id in stale:
        history = deps.items_repo.history_for_user(user_id)
        if not history:
            continue
        vector = taste_service.rebuild_vector(bundle.matrix, bundle.id_to_row, history)
        st = deps.taste_repo.get(user_id)
        if st is None:
            from app.repositories.taste_repo import TasteState

            st = TasteState(user_id, vector, sig, swipe_count=len(history))
        else:
            st.vector = vector
            st.vector_version = sig
            st.swipe_count = len(history)
        deps.taste_repo.save(st)
        rebuilt += 1

    logger.info("Rebuilt %d/%d user vectors", rebuilt, len(stale))
    return rebuilt


if __name__ == "__main__":
    setup_logging("INFO")
    run()
