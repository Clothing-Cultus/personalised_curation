"""Pydantic request/response models — all payload validation lives here (spec §6.3)."""
from app.schemas.requests import ProductInteraction, SuggestRequestBody
from app.schemas.responses import Health, ProductData, SessionData, SessionSummary

__all__ = [
    "ProductInteraction",
    "SuggestRequestBody",
    "ProductData",
    "SessionSummary",
    "SessionData",
    "Health",
]
