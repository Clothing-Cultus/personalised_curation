"""READ-ONLY access to users (Hard Rule #1) — gender for scope filtering (spec §4.6)."""
from __future__ import annotations

import logging

from app.repositories.base import gateway, supabase

logger = logging.getLogger(__name__)


class UserRepo:
    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}

    def get_gender(self, user_id: str) -> str | None:
        """'male' | 'female' | None. None ⇒ user sees only unisex inventory."""
        if user_id in self._cache:
            return self._cache[user_id]
        gender: str | None = None
        if gateway.db_enabled or _has_creds():
            try:
                rows = (supabase().table("users").select("gender")
                        .eq("id", user_id).limit(1).execute().data)
                gender = (rows[0].get("gender") if rows else None)
                gender = gender.lower().strip() if isinstance(gender, str) else None
            except Exception:  # noqa: BLE001 — no creds / offline dev
                gender = None
        self._cache[user_id] = gender
        return gender


def _has_creds() -> bool:
    from core.config import ENV

    return bool(ENV.supabase_url and ENV.supabase_service_key)
