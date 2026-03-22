"""Shared street-level priors used across program generation and layout solving."""

from __future__ import annotations

from typing import Dict, Tuple

DEFAULT_CATEGORIES: Tuple[str, ...] = (
    "bench",
    "lamp",
    "trash",
    "tree",
    "bus_stop",
    "mailbox",
    "hydrant",
    "bollard",
)

DEFAULT_SPACING_M: Dict[str, float] = {
    "lamp": 18.0,
    "tree": 14.0,
    "bench": 22.0,
    "trash": 18.0,
    "bus_stop": 45.0,
    "mailbox": 40.0,
    "hydrant": 30.0,
    "bollard": 6.0,
}

SIDE_PREF: Dict[str, str] = {
    "bus_stop": "right",
    "mailbox": "right",
    "hydrant": "right",
    "bench": "both",
    "lamp": "both",
    "trash": "both",
    "tree": "both",
    "bollard": "both",
}

CATEGORY_SUBSTITUTIONS: Dict[str, Tuple[str, ...]] = {
    "bus_stop": ("bench", "lamp", "bollard"),
    "mailbox": ("bollard", "lamp"),
    "hydrant": ("bollard", "lamp"),
    "tree": ("lamp", "bench"),
    "bench": ("bollard", "trash"),
    "trash": ("bollard", "bench"),
    "lamp": ("bollard",),
}
