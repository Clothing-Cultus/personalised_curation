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
    ABANDON_AFTER_MINUTES,
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
    def __init__(self, session_repo, items_repo, taste_repo, user_repo, artifact_store,
                 events_repo) -> None:
        self.sessions = session_repo
        self.items = items_repo
        self.taste = taste_repo
        self.users = user_repo
        self.artifacts = artifact_store
        self.events = events_repo

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
        cards = self._serve_at_start(session, user_id, taste, gender, n)
        return self._continue_payload(session, cards, taste)

    def _resolve_session(self, user_id, existing, version) -> tuple[dict, bool]:
        """Return (session, blocked). blocked=True ⇒ an exhausted cycle is still
        within RENEWAL_PERIOD; caller returns the summary, serves no cards."""
        if existing is None:
            return self._new_session(user_id, version), False

        started = _parse(existing.get("started_at"))
        if started is None:
            # cards served but never swiped → no cycle has begun; resume as-is.
            self._resume(existing, user_id)
            return existing, False

        lapsed = _now() - started >= timedelta(days=RENEWAL_PERIOD_DAYS)
        if lapsed:
            self.sessions.update(existing["id"], {
                "status": "ended", "end_reason": "renewed", "ended_at": _now().isoformat()})
            self.events.record(existing["id"], user_id, "renewed")
            return self._new_session(user_id, version), False

        # within the cycle → resume this exact row, no limit changes.
        self._resume(existing, user_id)
        return existing, False

    def _resume(self, session, user_id: str) -> None:
        """Resume an open session. Lazily labels it 'abandoned' first if it went
        idle past ABANDON_AFTER (spec §5.7), recording both transitions so the
        start→abandon→return timeline is queryable in pc_session_events."""
        last = _parse(session.get("last_activity_at"))
        if last and _now() - last >= timedelta(minutes=ABANDON_AFTER_MINUTES):
            # record the abandonment at the last known activity (best proxy for
            # when they left), then the resume at now.
            self.events.record(session["id"], user_id, "abandoned",
                               at=session.get("last_activity_at"))
        self.sessions.update(session["id"],
                             {"status": "active", "last_activity_at": _now().isoformat()})
        self.events.record(session["id"], user_id, "resumed")

    def _new_session(self, user_id: str, version: str) -> dict:
        session = self.sessions.create(
            user_id=user_id, cards_limit=CARDS_LIMIT, rights_limit=RIGHTS_LIMIT,
            superlikes_limit=SUPERLIKES_LIMIT, deck_version=version)
        self.events.record(session["id"], user_id, "created")
        return session

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

        # Debug signal: the frontend's advisory count vs. the backend's truth.
        if products_swiped and products_swiped != session["cards_swiped"]:
            logger.info("products_swiped divergence: frontend=%d backend=%d session=%s "
                        "(backend is source of truth; client should re-sync to total_swipes)",
                        products_swiped, session["cards_swiped"], session["id"])

        reason = self._end_reason(session)
        if reason:
            self.sessions.update(session["id"], {
                "status": "ended", "end_reason": reason, "ended_at": _now().isoformat()})
            self.events.record(session["id"], session["user_id"], "ended")
            self.taste.save(taste)
            return self._ended_payload(session)

        # Refill only when the client's queue has drained to the threshold.
        cards: list[dict] = []
        queue_remaining = session["cards_served"] - session["cards_swiped"]
        if queue_remaining <= SEND_THRESHOLD:
            n = batch_service.refill_size(session)
            cards = self._serve(session, session["user_id"], taste, gender, n)
            if not cards and self._buffer_drained(session):
                self.sessions.update(session["id"], {
                    "status": "ended", "end_reason": "products_exhausted",
                    "ended_at": _now().isoformat()})
                self.events.record(session["id"], session["user_id"], "ended")
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
                logger.info("Duplicate swipe ignored (idempotent) session=%s product=%s",
                            session["id"], pid)
                continue  # idempotent per (session, product) — spec §6 protocol
            already_swiped.add(pid)
            row = bundle.id_to_row.get(pid)
            if row is None:
                logger.info("Swipe on product not in matrix (skipped from vector) "
                            "session=%s product=%s", session["id"], pid)

            if first_swipe:
                patch["started_at"] = _now().isoformat()  # starts RENEWAL clock (spec §5.9)
                first_swipe = False
                self.events.record(session["id"], session["user_id"], "first_swipe")

            session["cards_swiped"] += 1
            # right and superlike are INDEPENDENT counters: a superlike consumes a
            # superlike only, never a right. Each is capped at its own limit.
            if it["swipe"] == "right":
                if session["rights_used"] < session["rights_limit"]:
                    session["rights_used"] += 1
            elif it["swipe"] == "superlike":
                if session["superlikes_used"] < session["superlikes_limit"]:
                    session["superlikes_used"] += 1

            self.items.record_swipe(session["id"], session["user_id"], it)

            if row is not None:
                weight = taste_service.event_weight(it)
                taste.vector = taste_service.apply_interactions(
                    taste.vector, [(bundle.matrix[row], weight)])
                taste.swipe_count += 1
                self._update_bandit(taste, bundle, row, positive=it["swipe"] != "left")

            if it.get("size_default_for_category") and it.get("size_selected"):
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
        """Refill during an active session — fresh recommendations only (the
        client still holds its buffer, so we don't recycle here)."""
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
            logger.info("Refill served 0 candidates — pool exhausted for user=%s session=%s",
                        user_id, session["id"])
        return self._persist_served(session, user_id, taste, cards)

    def _serve_at_start(self, session, user_id, taste, gender, n_cards) -> list[dict]:
        """First batch on a fresh client load (start/resume/renewal): re-offer the
        user's un-swiped served cards FIRST (they never actually reached them —
        spec: don't waste them), then fill the rest with fresh recommendations."""
        if n_cards <= 0:
            return []
        bundle = self.artifacts.bundle
        swiped_ids = {h["product_id"] for h in self.items.user_swipes(user_id)}
        owed = self.items.owed_product_ids(user_id)
        recycled = scoring_service.recycle_cards(
            bundle, taste, user_gender=gender, size_prefs=taste.size_preferences,
            owed_ids=owed, swiped_ids=swiped_ids, n=n_cards)
        recycled_pids = {c["product_id"] for c in recycled}

        fresh: list[dict] = []
        remaining = n_cards - len(recycled)
        if remaining > 0:
            served_ids = self.items.served_product_ids(session["id"])
            history = self.items.user_swipes(user_id)
            brand_counts = self._brand_counts(session, served_ids)
            fresh = scoring_service.assemble_batch(
                bundle, taste, user_gender=gender, size_prefs=taste.size_preferences,
                served_ids=served_ids, history=history, brand_counts=brand_counts,
                n_cards=remaining, seed=abs(hash(session["id"])) % (2**32),
                extra_exclude=recycled_pids)
        if recycled:
            logger.info("Recycled %d un-swiped card(s) for returning user=%s", len(recycled), user_id)
        return self._persist_served(session, user_id, taste, recycled + fresh)

    def _persist_served(self, session, user_id, taste, cards) -> list[dict]:
        """Write the served cards (impression log) and refresh counters. cards_served
        is recomputed from the DISTINCT served set, so re-offered (recycled) cards
        don't double-count."""
        if not cards:
            return []
        bundle = self.artifacts.bundle
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
        session["cards_served"] = len(self.items.served_product_ids(session["id"]))
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
        # Compatibility is keyed to the FEATURE LAYOUT, not the per-run version —
        # so a product-only nightly rebuild does NOT cold-start anyone (spec §3.3).
        if st is None or st.vector_version != bundle.layout_signature or st.vector.shape[0] != dim:
            # brand-new or layout changed → cold start (spec §5.2 step 3, §3.3)
            return TasteState(user_id, np.zeros(dim, dtype=np.float32), bundle.layout_signature)
        st.vector = taste_service.apply_weekly_decay(st.vector, st.last_decayed_at)
        st.last_decayed_at = _now().isoformat()
        return st

    # ------------------------------------------------------------------ end
    def _end_reason(self, session) -> str | None:
        if session["rights_used"] >= session["rights_limit"]:
            return "rights_exhausted"
        return None

    def _buffer_drained(self, session) -> bool:
        """True when every card served this session has been swiped (the client
        buffer is empty). Checked ONLY after a refill returns nothing, to detect
        that the user's filtered catalog is exhausted — whether because the 40-card
        cap was hit OR because the filtered pool ran dry (e.g. a narrow gender/size
        filter with fewer than cards_limit matches). Not used pre-serve, so it can't
        end a session that still has products left to fetch."""
        served = self.items.served_product_ids(session["id"])
        swiped = self.items.swiped_product_ids(session["id"])
        return bool(served) and served.issubset(swiped)

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
