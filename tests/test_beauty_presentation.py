from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.beauty import (
    apply_composition_pass,
    curate_candidates,
    render_presentation_views,
    shape_program_for_style,
)
from roadgen3d.types import LayoutSlotPlan, StreetBand, StreetComposeConfig, StreetProgram


def _config(**overrides) -> StreetComposeConfig:
    base = {
        "query": "modern transit boulevard",
        "length_m": 60.0,
        "road_width_m": 8.0,
        "sidewalk_width_m": 2.5,
        "lane_count": 2,
        "density": 1.0,
        "seed": 42,
        "topk_per_category": 20,
        "max_trials_per_slot": 20,
        "style_preset": "transit_modern_v1",
        "beauty_mode": "presentation_v1",
        "render_preset": "jury_default_v1",
        "asset_curation_mode": "curated_first",
    }
    base.update(overrides)
    return StreetComposeConfig(**base)


def _program() -> StreetProgram:
    return StreetProgram(
        query="modern transit boulevard",
        road_type="transit_corridor",
        city_context="generic_city",
        target_standard="balanced_complete_street_v1",
        lane_count=2,
        cross_section_type="balanced_complete_street",
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        furnishing_width_m=1.0,
        bands=(
            StreetBand("left_furnishing", "furnishing", "left", 1.2, 4.6, ("bench", "tree")),
            StreetBand("left_clear_path", "clear_path", "left", 2.4, 2.8, ("lamp",)),
            StreetBand("carriageway", "carriageway", "center", 8.0, 0.0, ()),
            StreetBand("right_clear_path", "clear_path", "right", 2.4, -2.8, ("lamp",)),
            StreetBand("right_transit_edge", "transit_edge", "right", 1.4, -4.7, ("bus_stop", "bollard")),
        ),
        furniture_requirements={
            "bench": 4,
            "lamp": 3,
            "trash": 2,
            "tree": 3,
            "bus_stop": 0,
            "mailbox": 0,
            "hydrant": 0,
            "bollard": 6,
        },
        control_points=("entry", "exit"),
        design_goals=("walkability", "clarity"),
        context_conditions={},
        observed_poi_counts={"bus_stop": 1, "subway_entrance": 1},
    )


def test_shape_program_for_style_enforces_poi_minimums():
    shaped = shape_program_for_style(_program(), _config(style_preset="transit_modern_v1"))
    assert shaped.context_conditions["style_preset"] == "transit_modern_v1"
    assert shaped.furniture_requirements["bus_stop"] >= 1
    assert "transit_stop" in shaped.control_points
    assert "transit_access" in shaped.design_goals


def test_curate_candidates_prefers_curated_hero_assets():
    cfg = _config(style_preset="transit_modern_v1")
    curated, meta = curate_candidates(
        [
            (
                {
                    "asset_id": "bus_stop_modern",
                    "category": "bus_stop",
                    "text_desc": "sleek transit shelter",
                    "style_tags": ["transit", "modern", "metal"],
                    "material_family": "metal",
                    "quality_tier": 3,
                    "hero_asset": True,
                },
                0.72,
            ),
            (
                {
                    "asset_id": "bus_stop_legacy",
                    "category": "bus_stop",
                    "text_desc": "old bus shelter",
                    "style_tags": ["legacy", "wood"],
                    "material_family": "wood",
                    "quality_tier": 1,
                    "hero_asset": False,
                },
                0.78,
            ),
        ],
        category="bus_stop",
        config=cfg,
    )
    assert meta["curated_used"] is True
    assert curated[0][0]["asset_id"] == "bus_stop_modern"


def test_apply_composition_pass_preserves_required_and_trims_optional():
    cfg = _config(style_preset="civic_clean_v1")
    slot_plans = (
        LayoutSlotPlan("slot_req", "bus_stop", "right_transit_edge", 0.0, -4.5, 8.0, "right", 1.0, required=True, anchor_poi_type="bus_stop"),
        LayoutSlotPlan("slot_a", "bench", "left_furnishing", 2.0, 4.4, 8.0, "left", 0.6),
        LayoutSlotPlan("slot_b", "bench", "left_furnishing", 2.6, 4.5, 8.0, "left", 0.5),
        LayoutSlotPlan("slot_c", "trash", "left_furnishing", 3.0, 4.7, 8.0, "left", 0.4),
        LayoutSlotPlan("slot_d", "lamp", "right_clear_path", 12.0, -3.0, 10.0, "right", 0.7),
    )
    kept, report = apply_composition_pass(slot_plans, config=cfg, poi_context=None)
    kept_ids = {slot.slot_id for slot in kept}
    assert "slot_req" in kept_ids
    assert len(kept) < len(slot_plans)
    assert report["required_slots_preserved"] == 1


def test_render_presentation_views_outputs_expected_pngs(tmp_path: Path):
    pytest.importorskip("matplotlib")
    payload = {
        "summary": {
            "style_preset": "civic_clean_v1",
            "road_width_m": 8.0,
            "sidewalk_width_m": 2.5,
            "spatial_context": {
                "poi_points_by_type_xz": {
                    "entrance": [[0.0, 4.0]],
                    "bus_stop": [[8.0, -4.0]],
                }
            },
        },
        "placements": [
            {"instance_id": "inst_1", "category": "bench", "position_xyz": [1.0, 0.0, 4.4]},
            {"instance_id": "inst_2", "category": "bus_stop", "position_xyz": [8.0, 0.0, -4.4]},
        ],
    }
    views = render_presentation_views(payload, out_dir=tmp_path, config=_config(style_preset="civic_clean_v1"))
    assert len(views) == 4
    for view in views:
        assert Path(view["path"]).exists()
        assert Path(view["path"]).suffix == ".png"
