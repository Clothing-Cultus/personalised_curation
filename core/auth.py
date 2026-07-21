"""Shared-secret verification — the ENTIRE auth mechanism (spec §6.1).

No tokens, no issuance, no per-user identity check at this layer. The calling
server has already authenticated the end user; FastAPI's only job is "does this
request carry the right static secret". Missing/wrong → 401.

Kept framework-free (a plain function, no FastAPI import) so it lives in shared
core/; the controller wires it in as a dependency.
"""
from __future__ import annotations

import hmac

from core.config import ENV
from core.errors import Unauthorized


def verify_internal_key(provided: str | None) -> None:
    """Raise Unauthorized unless `provided` matches the configured secret.

    Empty configured secret ⇒ auth disabled (local dev only). Uses a constant-
    time compare so a wrong key can't be timing-probed.
    """
    expected = ENV.internal_key
    if not expected:
        return  # auth disabled for local dev
    if not provided or not hmac.compare_digest(provided, expected):
        raise Unauthorized()
