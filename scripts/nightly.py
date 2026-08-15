"""Nightly job orchestrator — run BOTH steps in one go so users returning the
next day never practically see cold-start (spec §3.3):

  1. run_pipeline        → rebuild product_matrix + artifacts (new version)
  2. rebuild_user_vectors → replay every stale user's history onto the new
                            matrix and write their vector back at the new version

Because the pipeline swaps artifacts atomically (manifest written last) and the
API keeps serving the OLD matrix until it reloads, users swiping DURING this job
are unaffected. Running the rebuild immediately after means the window in which
any user is on a stale version is as small as the rebuild takes.

    cd personalised_curation && python -m scripts.nightly
    # (schedule this via cron on EC2)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logging_config import setup_logging  # noqa: E402

logger = logging.getLogger("nightly")


def main() -> None:
    setup_logging("INFO")
    from ml.pipeline import run_pipeline
    from scripts import rebuild_user_vectors

    logger.info("Nightly step 1/3: pipeline")
    run_pipeline.run()

    logger.info("Nightly step 2/3: rebuild user vectors (replays swipes + ledger purchases)")
    rebuild_user_vectors.run()

    logger.info("Nightly step 3/3: purchase reconciliation")
    from scripts import purchase_reconciliation
    purchase_reconciliation.run()

    logger.info("Nightly job complete")


if __name__ == "__main__":
    main()
