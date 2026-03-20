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
from roadgen3d.topdown_render import _viewport_from_layout, render_design_zoning_companion
from roadgen3d.types import BuildingFootprint, GeneratedLot
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
        "render_preset": "axonometric_board_v1",
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
    pytest.importorskip("PIL")
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
    assert len(views) == 6
    assert [view["name"] for view in views[:2]] == [
        "final_plan_axonometric",
        "final_oblique_45_axonometric",
    ]
    for view in views:
        assert Path(view["path"]).exists()
        assert Path(view["path"]).suffix == ".png"
    final_plan_view = next(view for view in views if view["name"] == "final_plan_axonometric")
    final_oblique_view = next(view for view in views if view["name"] == "final_oblique_45_axonometric")
    design_view = next(view for view in views if view["name"] == "overview_top_design")
    from PIL import Image

    final_plan_image = Image.open(final_plan_view["path"]).convert("RGBA")
    assert final_plan_image.size[0] > 500
    assert final_plan_image.size[1] > 500
    assert len(final_plan_image.getcolors(maxcolors=1_000_000) or []) > 32

    final_oblique_image = Image.open(final_oblique_view["path"]).convert("RGBA")
    assert final_oblique_image.size[0] > 500
    assert final_oblique_image.size[1] > 300
    assert len(final_oblique_image.getcolors(maxcolors=1_000_000) or []) > 32

    image = Image.open(design_view["path"]).convert("RGBA")
    assert image.size == (2048, 2048)
    assert len(image.getcolors(maxcolors=1_000_000) or []) > 32


def test_render_presentation_views_jury_default_keeps_watercolor_final_views(tmp_path: Path):
    pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    payload = {
        "summary": {
            "style_preset": "civic_clean_v1",
            "road_width_m": 8.0,
            "sidewalk_width_m": 2.5,
        },
        "placements": [
            {"instance_id": "inst_1", "category": "tree", "position_xyz": [1.0, 0.0, 4.4]},
            {"instance_id": "inst_2", "category": "lamp", "position_xyz": [8.0, 0.0, -4.4]},
        ],
    }
    views = render_presentation_views(
        payload,
        out_dir=tmp_path,
        config=_config(style_preset="civic_clean_v1", render_preset="jury_default_v1"),
    )
    assert [view["name"] for view in views[:2]] == [
        "final_plan_watercolor",
        "final_oblique_45_watercolor",
    ]


def test_render_presentation_views_legacy_mode_falls_back_to_vector_overview(tmp_path: Path):
    pytest.importorskip("matplotlib")
    payload = {
        "summary": {
            "style_preset": "civic_clean_v1",
            "road_width_m": 8.0,
            "sidewalk_width_m": 2.5,
        },
        "placements": [],
    }
    views = render_presentation_views(
        payload,
        out_dir=tmp_path,
        config=_config(style_preset="civic_clean_v1", topdown_render_mode="legacy_vector"),
    )
    assert any(view["name"] == "overview_top" for view in views)
    assert not any(view["name"] == "overview_top_design" for view in views)


def test_topdown_viewport_mapping_is_stable():
    payload = {
        "summary": {"road_width_m": 8.0, "sidewalk_width_m": 2.5, "length_m": 60.0},
        "placements": [{"position_xyz": [0.0, 0.0, 0.0]}],
    }
    viewport = _viewport_from_layout(payload, canvas_px=2048)
    px_a = viewport.world_to_pixel(5.0, 3.0)
    px_b = viewport.world_to_pixel(5.0, 3.0)
    assert px_a == px_b
    assert 0.0 <= px_a[0] <= 2048.0
    assert 0.0 <= px_a[1] <= 2048.0


def test_render_design_zoning_companion_outputs_png(tmp_path: Path):
    pytest.importorskip("PIL")
    output_path = tmp_path / "zoning.png"
    config = _config(style_preset="civic_clean_v1")
    palette = {
        "context_ground": (174, 169, 156, 255),
        "carriageway": (71, 76, 84, 255),
        "sidewalk": (195, 194, 186, 255),
        "furnishing": (176, 174, 164, 255),
        "clear_path": (212, 210, 200, 255),
    }
    zoning_grid = [
        {
            "lane_role": "carriageway",
            "theme_name": "commercial",
            "center_xz": [0.0, 0.0],
            "polygon_xz": [[-20.0, -4.0], [20.0, -4.0], [20.0, 4.0], [-20.0, 4.0]],
        },
        {
            "lane_role": "left_sidewalk",
            "theme_name": "commercial",
            "land_use_type": "commercial",
            "center_xz": [0.0, 5.5],
            "polygon_xz": [[-20.0, 4.0], [20.0, 4.0], [20.0, 7.0], [-20.0, 7.0]],
        },
        {
            "lane_role": "left_building_buffer",
            "theme_name": "commercial",
            "land_use_type": "green",
            "center_xz": [0.0, 10.5],
            "polygon_xz": [[-20.0, 7.0], [20.0, 7.0], [20.0, 14.0], [-20.0, 14.0]],
        },
    ]
    footprints = (
        BuildingFootprint(
            footprint_id="fp_001",
            source="osm",
            polygon_xz=((-8.0, 7.8), (-1.0, 7.8), (-1.0, 12.0), (-8.0, 12.0)),
            centroid_xz=(-4.5, 9.9),
            frontage_width_m=7.0,
            depth_m=4.2,
            yaw_deg=0.0,
            theme_id="theme_001",
            height_class="midrise",
        ),
    )
    lots = (
        GeneratedLot(
            lot_id="lot_001",
            polygon_xz=((2.0, 7.8), (10.0, 7.8), (10.0, 12.8), (2.0, 12.8)),
            center_xz=(6.0, 10.3),
            side="left",
            land_use_type="commercial",
            theme_id="theme_001",
            frontage_width_m=8.0,
            depth_m=5.0,
            height_class="midrise",
        ),
    )
    companion_path = render_design_zoning_companion(
        out_path=output_path,
        config=config,
        palette=palette,
        zoning_grid=zoning_grid,
        building_footprints=footprints,
        generated_lots=lots,
        osm_geometry=None,
    )
    assert companion_path == str(output_path.resolve())
    assert output_path.exists()
