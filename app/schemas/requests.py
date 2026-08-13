"""Inbound payloads (spec §6.3). Hard Rule #6: everything schema-validated."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProductInteraction(BaseModel):
    product_id: str
    swipe: Literal["left", "right", "superlike"]
    details_read: bool = False
    last_image_discovered: int = 0
    bought_in_session: bool = False
    bought_as_gift: bool = False                    # was this in-session buy a gift?
    size_selected: Optional[str] = None             # e.g. "M", "32"
    # true  → set this as the user's standing size for that garment category
    #         (writes user_taste.size_preferences, feeds future FILTER)
    # false → one-off size for this product only (logged, no future effect)
    size_default_for_category: bool = False


class SuggestRequestBody(BaseModel):
    # The frontend posts the JSON key "ProductInteraction" (a list of
    # interactions). It's exposed as `interactions` in code via an alias, because
    # a Pydantic field can't share the exact name of its item class.
    model_config = ConfigDict(populate_by_name=True)

    session_id: str
    interactions: List[ProductInteraction] = Field(
        default_factory=list, alias="ProductInteraction"
    )
    products_swiped: int = 0                         # advisory only (Hard Rule #7)
