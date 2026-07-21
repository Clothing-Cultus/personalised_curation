"""Session lifecycle: start, resume, renewal, swipe handling, end-reason (spec
§5.2, §5.3, §5.5–§5.9). Pure business logic — repos are injected, no SQL/FastAPI.

Renewal model (spec §5.6): a pc_sessions row is one renewal CYCLE's allotment,
not one sitting. started_at is set on the FIRST SWIPE and is what RENEWAL_PERIOD
counts from; limits are fixed at creation and never carried over.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np

from core.config import (
    CARDS_LIMIT,
    N_PRICE_BANDS,
    RENEWAL_PERIOD_DAYS,
    RIGHTS_LIMIT,
    SEND_THRESHOLD,
    SUPERLIKES_LIMIT,
)
from app.repositories.taste_repo import TasteState
from app.services import batch_service, notification_service, scoring_service, taste_service

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


class SessionService:
    def __init__(self, session_repo, items_repo, taste_repo, user_repo, artifact_store) -> None:
        self.sessions = session_repo
        self.items = items_repo
        self.taste = taste_repo
        self.users = user_repo
        self.artifacts = artifact_store

    # ------------------------------------------------------------------ start
    def start_session(self, user_id: str) -> dict:
        bundle = self.artifacts.bundle
        gender = self.users.get_gender(user_id)
        existing = self.sessions.find_open(user_id)

        session, blocked = self._resolve_session(user_id, existing, bundle.version)
        if blocked:
            return self._ended_payload(session)

        taste = self._load_taste(user_id, bundle)
        # First batch (spec §5.2 step 4). Cold-start users score on a zero vector,
        # which yields an in-stock, gender/size-filtered bestseller-led deck —
        # this supersedes the static anchor deck (which ignores those filters);
        # anchor_deck stays reserved for the artifacts-down degradation path.
        n = batch_service.initial_batch_size(session)
        cards = self._serve(session, user_id, taste, gender, n)
        return self._continue_payload(session, cards, taste)

    def _resolve_session(self, user_id, existing, version) -> tuple[dict, bool]:
        """Return (session, blocked). blocked=True ⇒ an exhausted cycle is still
        within RENEWAL_PERIOD; caller returns the summary, serves no cards."""
        if existing is None:
            return self._new_session(user_id, version), False

        started = _parse(existing.get("started_at"))
        if started is None:
            # cards served but never swiped → no cycle has begun; resume as-is.
            self.sessions.update(existing["id"],
                                 {"status": "active", "last_activity_at": _now().isoformat()})
            return existing, False

        lapsed = _now() - started >= timedelta(days=RENEWAL_PERIOD_DAYS)
        if lapsed:
            self.sessions.update(existing["id"], {
                "status": "ended", "end_reason": "renewed", "ended_at": _now().isoformat()})
            return self._new_session(user_id, version), False

        # within the cycle → resume this exact row, no limit changes.
        self.sessions.update(existing["id"],
                             {"status": "active", "last_activity_at": _now().isoformat()})
        return existing, False

    def _new_session(self, user_id: str, version: str) -> dict:
        return self.sessions.create(
            user_id=user_id, cards_limit=CARDS_LIMIT, rights_limit=RIGHTS_LIMIT,
            superlikes_limit=SUPERLIKES_LIMIT, deck_version=version)

    # ------------------------------------------------------------------ suggest
    def suggest(self, session_id: str, interactions: list[dict], products_swiped: int) -> dict:
        from core.errors import NotFound

        session = self.sessions.get(session_id)
        if session is None:
            raise NotFound("Session not found — start a new one")
        if session["status"] == "ended":
            return self._ended_payload(session)

        bundle = self.artifacts.bundle
        gender = self.users.get_gender(session["user_id"])
        taste = self._load_taste(session["user_id"], bundle)

        self._process_interactions(session, taste, interactions)

        reason = self._end_reason(session)
        if reason:
            self.sessions.update(session["id"], {
                "status": "ended", "end_reason": reason, "ended_at": _now().isoformat()})
            self.taste.save(taste)
            return self._ended_payload(session)

        # Refill only when the client's queue has drained to the threshold.
        cards: list[dict] = []
        queue_remaining = session["cards_served"] - session["cards_swiped"]
        if queue_remaining <= SEND_THRESHOLD:
            n = batch_service.refill_size(session)
            cards = self._serve(session, session["user_id"], taste, gender, n)
            if not cards and self._catalog_exhausted(session):
                self.sessions.update(session["id"], {
                    "status": "ended", "end_reason": "products_exhausted",
                    "ended_at": _now().isoformat()})
                self.taste.save(taste)
                return self._ended_payload(session)

        self.taste.save(taste)
        return self._continue_payload(session, cards, taste)

    def _process_interactions(self, session, taste, interactions) -> None:
        bundle = self.artifacts.bundle
        already_swiped = self.items.swiped_product_ids(session["id"])
        first_swipe = session.get("started_at") is None
        patch: dict = {}

        for it in interactions:
            pid = it["product_id"]
            if pid in already_swiped:
                continue  # idempotent per (session, product) — spec §6 protocol
            already_swiped.add(pid)
            row = bundle.id_to_row.get(pid)

            if first_swipe:
                patch["started_at"] = _now().isoformat()  # starts RENEWAL clock (spec §5.9)
                first_swipe = False

            session["cards_swiped"] += 1
            if it["swipe"] in ("right", "superlike"):
                if session["rights_used"] < session["rights_limit"]:
                    session["rights_used"] += 1
                if it["swipe"] == "superlike":
                    session["superlikes_used"] += 1

            self.items.record_swipe(session["id"], session["user_id"], it)

            if row is not None:
                weight = taste_service.event_weight(it)
                taste.vector = taste_service.apply_interactions(
                    taste.vector, [(bundle.matrix[row], weight)])
                taste.swipe_count += 1
                self._update_bandit(taste, bundle, row, positive=it["swipe"] != "left")

            if it.get("size_scope") == "category" and it.get("size_selected"):
                gclass = bundle.meta.iloc[row]["garment_class"] if row is not None else None
                key = "topwear" if gclass == "top" else "bottomwear" if gclass == "bottom" else None
                if key:
                    taste.size_preferences[key] = it["size_selected"]

        patch.update({
            "cards_swiped": session["cards_swiped"],
            "rights_used": session["rights_used"],
            "superlikes_used": session["superlikes_used"],
            "last_activity_at": _now().isoformat(),
        })
        self.sessions.update(session["id"], patch)

    def _update_bandit(self, taste, bundle, row, positive) -> None:
        cluster = int(bundle.meta.iloc[row]["cluster"])
        stats = taste.cluster_stats.setdefault(cluster, [0.0, 0.0])
        stats[0 if positive else 1] += 1.0
        if positive:
            band = int(bundle.meta.iloc[row]["price_band"])
            taste.price_band_counts[band] = taste.price_band_counts.get(band, 0.0) + 1.0

    # ------------------------------------------------------------------ serving
    def _serve(self, session, user_id, taste, gender, n_cards) -> list[dict]:
        if n_cards <= 0:
            return []
        bundle = self.artifacts.bundle
        served_ids = self.items.served_product_ids(session["id"])
        history = self.items.user_swipes(user_id)
        brand_counts = self._brand_counts(session, served_ids)

        cards = scoring_service.assemble_batch(
            bundle, taste, user_gender=gender, size_prefs=taste.size_preferences,
            served_ids=served_ids, history=history, brand_counts=brand_counts,
            n_cards=n_cards, seed=abs(hash(session["id"])) % (2**32))
        if not cards:
            return []

        base_pos = session["cards_served"]
        notif_key, notif_content = notification_service.notification_for(taste.swipe_count)
        items = []
        for i, c in enumerate(cards):
            c["taste_notification_content"] = notif_content
            items.append({
                "product_id": c["product_id"], "card_position": base_pos + i,
                "is_explore": c["is_explore"], "is_sponsored": False,
                "score": c["score"], "percent_match": c["percent_match"],
                "notification_key": notif_key,
            })
        self.items.add_served(session["id"], user_id, items)
        session["cards_served"] += len(cards)
        self.sessions.update(session["id"], {
            "cards_served": session["cards_served"], "deck_version": bundle.version})
        return cards

    def _brand_counts(self, session, served_ids) -> dict:
        bundle = self.artifacts.bundle
        counts: dict = {}
        for pid in served_ids:
            row = bundle.id_to_row.get(pid)
            if row is not None:
                b = bundle.meta.iloc[row]["brand_id"]
                counts[b] = counts.get(b, 0) + 1
        return counts

    # ------------------------------------------------------------------ taste
    def _load_taste(self, user_id, bundle) -> TasteState:
        dim = bundle.matrix.shape[1]
        st = self.taste.get(user_id)
        if st is None or st.vector_version != bundle.version or st.vector.shape[0] != dim:
            # brand-new or version mismatch → cold start (spec §5.2 step 3, §3.3)
            return TasteState(user_id, np.zeros(dim, dtype=np.float32), bundle.version)
        st.vector = taste_service.apply_weekly_decay(st.vector, st.last_decayed_at)
        st.last_decayed_at = _now().isoformat()
        return st

    # ------------------------------------------------------------------ end
    def _end_reason(self, session) -> str | None:
        if session["rights_used"] >= session["rights_limit"]:
            return "rights_exhausted"
        if self._catalog_exhausted(session):
            return "products_exhausted"
        return None

    def _catalog_exhausted(self, session) -> bool:
        if session["cards_served"] < session["cards_limit"]:
            return False
        served = self.items.served_product_ids(session["id"])
        swipes = {h["product_id"] for h in self.items.user_swipes(session["user_id"])}
        return served.issubset(swipes)

    # ------------------------------------------------------------------ payloads
    def _continue_payload(self, session, cards, taste) -> dict:
        return {
            "session_id": session["id"], "user_id": session["user_id"], "type": "continue",
            "data": [{"product_id": c["product_id"], "percent_match": c["percent_match"],
                      "taste_notification_content": c.get("taste_notification_content")}
                     for c in cards],
            "total_swipes": session["cards_swiped"],
            "right_swipes_left": max(0, session["rights_limit"] - session["rights_used"]),
            "products_left": max(0, session["cards_limit"] - session["cards_swiped"]),
            "superlikes_left": max(0, session["superlikes_limit"] - session["superlikes_used"]),
        }

    def _ended_payload(self, session) -> dict:
        right = sum(1 for h in self.items.user_swipes(session["user_id"])
                    if h.get("swipe") in ("right", "superlike"))
        supers = sum(1 for h in self.items.user_swipes(session["user_id"])
                     if h.get("swipe") == "superlike")
        return {
            "session_id": session["id"], "user_id": session["user_id"], "type": "ended",
            "data": {"total_swipes": session["cards_swiped"], "right_swipes": right,
                     "superlikes": supers},
            "total_swipes": session["cards_swiped"],
            "right_swipes_left": max(0, session["rights_limit"] - session["rights_used"]),
            "products_left": max(0, session["cards_limit"] - session["cards_swiped"]),
            "superlikes_left": max(0, session["superlikes_limit"] - session["superlikes_used"]),
        }
