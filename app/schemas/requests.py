"""Inbound payloads (spec §6.3). Hard Rule #6: everything schema-validated."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ProductInteraction(BaseModel):
    product_id: str
    swipe: Literal["left", "right", "superlike"]
    details_read: bool = False
    last_image_discovered: int = 0
    bought_in_session: bool = False
    size_selected: Optional[str] = None            # e.g. "M", "32"
    # 'category' updates user_taste.size_preferences (feeds future FILTER);
    # 'this_product' is logged only and never changes future filtering (Q18).
    size_scope: Optional[Literal["this_product", "category"]] = None


class SuggestRequestBody(BaseModel):
    session_id: str
    interactions: list[ProductInteraction] = Field(default_factory=list)
    products_swiped: int = 0                        # advisory only (Hard Rule #7)
