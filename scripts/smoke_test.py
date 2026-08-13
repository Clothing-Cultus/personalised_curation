"""End-to-end smoke test against the built artifacts (no server, no DB).
Exercises start_session → suggest (ack + refill) → rights-cap end (spec §5)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from core.config import ENV  # noqa: E402
from app.main import app  # noqa: E402


def main() -> None:
    with TestClient(app) as client:
        # send the shared-secret header on every call (if auth is enabled in .env)
        if ENV.internal_key:
            client.headers.update({"X-Internal-Key": ENV.internal_key})
        h = client.get("/healthz").json()
        print("healthz:", h)
        assert h["status"] == "ok", "artifacts not loaded"

        r = client.post("/api/start_session/00000000-0000-0000-0000-0000000000cc")
        assert r.status_code == 200, r.text
        data = r.json()
        sid = data["session_id"]
        cards = data["data"]
        print(f"start_session: {len(cards)} cards, type={data['type']}, "
              f"rights_left={data['right_swipes_left']}")
        assert data["type"] == "continue" and cards, "no cards served"
        assert all(0 <= c["percent_match"] <= 100 for c in cards)

        # Swipe through the deck: mostly right so we hit the rights cap (8).
        rights = 0
        ended = False
        queue = list(cards)
        for step in range(60):
            if not queue:
                break
            card = queue.pop(0)
            swipe = "right" if step % 2 == 0 else "left"
            body = {"session_id": sid, "ProductInteraction": [{
                "product_id": card["product_id"], "swipe": swipe,
                "details_read": False, "last_image_discovered": 1,
                "bought_in_session": False}], "products_swiped": 1}
            resp = client.post("/api/suggest", json=body).json()
            if swipe == "right":
                rights += 1
            if resp["type"] == "ended":
                print(f"ended after {step+1} swipes: reason via summary={resp['data']}, "
                      f"rights_left={resp['right_swipes_left']}")
                ended = True
                break
            new_cards = resp["data"]
            if new_cards:
                # dedupe: never re-serve an already-queued/served product
                seen = {c["product_id"] for c in cards}
                for nc in new_cards:
                    assert nc["product_id"] not in seen, "re-served a product!"
                    seen.add(nc["product_id"])
                    cards.append(nc)
                queue.extend(new_cards)
                print(f"  step {step+1}: refill +{len(new_cards)} "
                      f"(queue={len(queue)}, rights_left={resp['right_swipes_left']})")

        assert ended, "session never ended despite heavy right-swiping"
        print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()
