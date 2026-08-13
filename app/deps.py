"""Composition root — constructs repositories and injects them into services
(spec §2.1: services receive repo instances by constructor injection, so they're
unit-testable without a database). Controllers import the singletons from here.
"""
from __future__ import annotations

from app.repositories.session_events_repo import SessionEventsRepo
from app.repositories.session_items_repo import SessionItemsRepo
from app.repositories.session_repo import SessionRepo
from app.repositories.taste_repo import TasteRepo
from app.repositories.product_repo import ProductRepo
from app.repositories.user_repo import UserRepo
from app.services.artifact_store import artifact_store
from app.services.session_service import SessionService

# Repositories (single process-wide instances).
session_repo = SessionRepo()
items_repo = SessionItemsRepo()
taste_repo = TasteRepo()
user_repo = UserRepo()
product_repo = ProductRepo()
events_repo = SessionEventsRepo()

# Wire the stock-refresh source into the artifact store.
artifact_store.set_product_repo(product_repo)

# Services.
session_service = SessionService(
    session_repo=session_repo,
    items_repo=items_repo,
    taste_repo=taste_repo,
    user_repo=user_repo,
    artifact_store=artifact_store,
    events_repo=events_repo,
)
