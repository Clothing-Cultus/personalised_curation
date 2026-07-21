"""Canonical value normalization.

The DB stores lowercase snake_case ("high_fashion", "graphic_printed"); the
manual similarity matrices use display names ("High Fashion", "Y2K Fashion").
Every categorical value from ANY source passes through normalize_value() before
comparison, encoding, or matrix lookup. Residual mismatches are fixed with
core.config.VALUE_ALIASES.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from core.config import VALUE_ALIASES

_non_alnum = re.compile(r"[^a-z0-9]+")


def normalize_value(value: Any, aliases: dict[str, str] | None = None) -> str:
    """'High Fashion ' → 'high_fashion'; None/''/'-1'/'nan' → ''.

    Literal 'none' is preserved — it is a real taxonomy value (e.g. wash_type).
    """
    if value is None:
        return ""
    s = str(value).strip().lower()
    if s in ("", "null", "nan", "-1"):
        return ""
    s = _non_alnum.sub("_", s).strip("_")
    aliases = VALUE_ALIASES if aliases is None else aliases
    return aliases.get(s, s)


def normalize_list(values: Any, aliases: dict[str, str] | None = None) -> list[str]:
    """Normalize an array column / comma-separated string into a clean list."""
    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, Iterable):
        return []
    out: list[str] = []
    for v in values:
        n = normalize_value(v, aliases)
        if n and n not in out:
            out.append(n)
    return out


def normalize_size_stock(value: Any) -> dict[str, int]:
    """items_available_in_each_size arrives as a dict {size: qty} or a list
    [{size, qty, color}, ...] (the live format). Normalize to {SIZE: total_qty}.
    Shared by the pipeline (load) and the app (stock refresh), so it lives in core."""
    if isinstance(value, dict):
        return {str(k).upper(): int(v or 0) for k, v in value.items()}
    out: dict[str, int] = {}
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict) and entry.get("size"):
                size = str(entry["size"]).strip().upper()
                out[size] = out.get(size, 0) + int(entry.get("qty") or 0)
    return out


def hex_to_rgb01(value: Any) -> tuple[float, float, float] | None:
    """'#1A2B3C' / '1A2B3C' → (r, g, b) each in [0, 1]; None if unparseable."""
    if not value:
        return None
    s = str(value).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return None
    try:
        return tuple(int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None
