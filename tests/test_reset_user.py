"""Staging user reset: wipes a user's in-memory PC state, leaves others alone,
and is refused in production. Runs in memory mode (no DB)."""
from __future__ import annotations

import os

os.environ["PC_ENABLE_DB_WRITES"] = "false"

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.repositories.base import gateway  # noqa: E402
from app.repositories.taste_repo import TasteState  # noqa: E402
from core.config import ENV  # noqa: E402

gateway.db_enabled = False

from app import deps  # noqa: E402
from app.controllers import reset_controller  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from app.main import pc_error_handler  # noqa: E402
from core.errors import PCError  # noqa: E402

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"


def _seed(user_id: str) -> str:
    s = deps.session_repo.create(user_id, 40, 8, 1, "v1")
    deps.items_repo.add_served(s["id"], user_id, [
        {"product_id": f"p-{user_id[:2]}", "card_position": 0, "is_explore": False,
         "is_sponsored": False, "score": 0.5, "percent_match": 80, "notification_key": None},
    ])
    deps.items_repo.record_swipe(s["id"], user_id, {"product_id": f"p-{user_id[:2]}", "swipe": "left"})
    deps.taste_repo.save(TasteState(user_id, np.ones(4), "v1", swipe_count=1))
    return s["id"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ENV, "internal_key", "")
    monkeypatch.setattr(ENV, "pc_env", "staging")
    app = FastAPI()
    app.add_exception_handler(PCError, pc_error_handler)
    app.include_router(reset_controller.router)
    return TestClient(app)


def test_reset_wipes_only_that_user(client):
    sid1 = _seed(U1)
    sid2 = _seed(U2)

    r = client.post(f"/api/reset_user/{U1}")
    assert r.status_code == 200
    assert r.json() == {"user_id": U1, "sessions_deleted": 1}

    assert deps.session_repo.find_open(U1) is None
    assert deps.session_repo.get(sid1) is None
    assert deps.items_repo.user_swipes(U1) == []
    assert deps.items_repo.served_product_ids(sid1) == set()
    assert deps.items_repo.owed_product_ids(U1) == []
    assert deps.taste_repo.get(U1) is None

    assert deps.session_repo.find_open(U2)["id"] == sid2
    assert len(deps.items_repo.user_swipes(U2)) == 1
    assert deps.taste_repo.get(U2) is not None


def test_reset_unknown_user_is_ok(client):
    r = client.post("/api/reset_user/33333333-3333-3333-3333-333333333333")
    assert r.status_code == 200
    assert r.json()["sessions_deleted"] == 0


def test_reset_refused_in_production(client, monkeypatch):
    monkeypatch.setattr(ENV, "pc_env", "production")
    _seed(U1)
    r = client.post(f"/api/reset_user/{U1}")
    assert r.status_code == 403
    assert deps.session_repo.find_open(U1) is not None


def test_reset_requires_internal_key(client, monkeypatch):
    monkeypatch.setattr(ENV, "internal_key", "secret")
    assert client.post(f"/api/reset_user/{U1}").status_code == 401
    ok = client.post(f"/api/reset_user/{U1}", headers={"X-Internal-Key": "secret"})
    assert ok.status_code == 200


def test_reset_rejects_non_uuid(client):
    assert client.post("/api/reset_user/not-a-uuid").status_code == 400
