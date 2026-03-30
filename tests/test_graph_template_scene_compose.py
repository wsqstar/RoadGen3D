from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.street_layout import _is_corridor_layout_mode, _validate_config  # noqa: E402
from roadgen3d.types import StreetComposeConfig  # noqa: E402


def _build_config() -> StreetComposeConfig:
    return StreetComposeConfig(
        query="campus gateway boulevard",
        length_m=96.0,
        road_width_m=10.5,
        sidewalk_width_m=3.0,
        lane_count=3,
        density=1.0,
        seed=29,
        topk_per_category=20,
        max_trials_per_slot=20,
        layout_mode="graph_template",
        constraint_mode="off",
        curated_street_assets_profile="disabled",
    )


def test_validate_config_accepts_graph_template_layout():
    _validate_config(_build_config())


def test_graph_template_is_treated_as_corridor_layout():
    assert _is_corridor_layout_mode("graph_template") is True
