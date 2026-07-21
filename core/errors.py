"""Exception → HTTP status mapping (spec §2.1 core/errors.py).

Services raise these framework-free exceptions; the controllers' single error
handler maps them to status codes, so services never import FastAPI.
"""
from __future__ import annotations


class PCError(Exception):
    """Base for all domain errors, carrying the HTTP status to surface."""

    status_code = 500
    detail = "Internal error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.detail)
        if detail:
            self.detail = detail


class Unauthorized(PCError):
    status_code = 401
    detail = "Invalid or missing X-Internal-Key"


class NotFound(PCError):
    status_code = 404
    detail = "Not found"


class ArtifactsNotLoaded(PCError):
    status_code = 503
    detail = "Recommendation artifacts not loaded — the feature pipeline hasn't run"


class BadRequest(PCError):
    status_code = 400
    detail = "Bad request"
