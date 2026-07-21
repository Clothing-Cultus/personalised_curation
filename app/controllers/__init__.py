"""Controllers — API definitions ONLY (spec §2.1): routes, request→service
wiring, status codes. No business logic, no SQL. One shared auth dependency."""
from __future__ import annotations

from fastapi import Header

from core.auth import verify_internal_key


def require_internal_key(x_internal_key: str | None = Header(default=None)) -> None:
    """The entire auth mechanism (spec §6.1): match the static shared secret."""
    verify_internal_key(x_internal_key)
