"""Outbound payloads (spec §6.3).

FastAPI returns product IDs + PC fields ONLY — never title/price/images/sizes
(the calling server hydrates those from Supabase). is_explore / is_sponsored are
deliberately absent here; they live on pc_session_items only (spec §7).

`SessionData.data` is ALWAYS an array of ProductData while continuing (empty on
a plain ack), and carries the SessionSummary object only when type == 'ended'.
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


class ProductData(BaseModel):
    product_id: str
    percent_match: int
    taste_notification_content: Optional[str] = None


class SessionSummary(BaseModel):
    total_swipes: int
    right_swipes: int
    superlikes: int


class SessionData(BaseModel):
    session_id: str
    user_id: str
    type: Literal["continue", "ended"]
    # Array of cards while continuing (possibly empty on an ack); SessionSummary
    # when ended. Kept an array-or-object per the explicit contract (spec §6.3).
    data: Union[list[ProductData], SessionSummary] = Field(default_factory=list)
    total_swipes: int
    right_swipes_left: int
    products_left: int
    superlikes_left: int


class Health(BaseModel):
    status: str
    artifact_version: Optional[str]
    artifact_age_seconds: Optional[float]
    n_products: int
    vector_dim: int
    db_writes_enabled: bool


class ResetUserResult(BaseModel):
    user_id: str
    sessions_deleted: int


class ResetAllResult(BaseModel):
    sessions_deleted: int
