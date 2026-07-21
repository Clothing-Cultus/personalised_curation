"""§4.1 step 2 — product text used for the CLIP text embedding.

The text encoder sees the title plus the taxonomy words (style/design/print/
colour), underscores turned back into spaces so CLIP reads natural language.
"""
from __future__ import annotations

import pandas as pd


def product_text(row: pd.Series) -> str:
    parts = [
        row["product_title"],
        row["category_title"],
        row["fashion_style_primary"],
        row["fashion_sub_style"],
        row["design"],
        row["print_type"],
        " ".join(row["print_keywords"]),
        row["colour_name_majority"],
    ]
    return " ".join(str(p).replace("_", " ") for p in parts if p)
