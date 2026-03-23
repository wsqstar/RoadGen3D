"""Helpers for coercing Python objects into strict JSON-safe values."""

from __future__ import annotations

import math
from typing import Any


def make_json_safe(value: Any) -> Any:
    """Recursively replace non-finite floats with ``None`` for strict JSON."""

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [make_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [make_json_safe(item) for item in value]
    return value
