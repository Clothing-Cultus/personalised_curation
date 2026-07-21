"""READ-ONLY access to products (Hard Rule #1). Used only for the lightweight
stock/status/price refresh of the in-memory mirror; scoring/filtering run
against the artifact meta the pipeline produced."""
from __future__ import annotations

import logging

from app.repositories.base import supabase

logger = logging.getLogger(__name__)


class ProductRepo:
    def read_stock(self) -> list[dict]:
        """Volatile columns only, for the periodic mirror refresh."""
        rows: list[dict] = []
        offset = 0
        while True:
            page = (supabase().table("products")
                    .select("id, status, items_available, items_available_in_each_size, selling_price")
                    .range(offset, offset + 999).execute().data)
            rows.extend(page)
            if len(page) < 1000:
                return rows
            offset += 1000
