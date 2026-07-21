"""FastAPI entry point (spec §2.1 app/main.py).

Run locally:  python -m app.main
Run in prod:  uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from core.config import CORS_ALLOWED_ORIGINS, ENV, HOST, LOG_LEVEL, PORT, WORKERS  # noqa: E402
from core.errors import PCError  # noqa: E402
from core.logging_config import setup_logging  # noqa: E402
from app.controllers import health_controller, session_controller, suggest_controller  # noqa: E402
from app.services.artifact_store import artifact_store  # noqa: E402

logger = logging.getLogger("curator")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(LOG_LEVEL)
    import app.deps  # noqa: F401 — build the composition root (wires product_repo in)

    artifact_store.try_load()
    artifact_store.start_background_tasks()
    logger.info("Curator service up (env=%s)", ENV.pc_env)
    yield
    artifact_store.stop()


app = FastAPI(title="Cultus Personal Curation API", version="9.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PCError)
async def pc_error_handler(_: Request, exc: PCError) -> JSONResponse:
    """Single mapping of domain exceptions → status codes (spec §2.1 errors.py)."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.include_router(session_controller.router)
app.include_router(suggest_controller.router)
app.include_router(health_controller.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, workers=WORKERS, log_level=LOG_LEVEL.lower())
