from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.theme_buildings import (
    build_zoning_grid_preview,
    generate_grid_growth_lots,
    height_class_from_height_m,
    infer_theme_segments,
    sample_building_target_height,
)
from roadgen3d.types import RoadSegmentGraph, RoadSegmentNode, StreetComposeConfig, ThemeSegment


def _graph_for_theme(*, highway_type: str, poi_types: tuple[str, ...]) -> RoadSegmentGraph:
    return RoadSegmentGraph(
        nodes=(
            RoadSegmentNode(
                segment_id="seg_0000",
                road_id=1,
                start_xy=(0.0, 0.0),
                end_xy=(10.0, 0.0),
                center_xy=(5.0, 0.0),
                length_m=10.0,
                highway_type=highway_type,
                poi_types=poi_types,
                station_start_m=-5.0,
                station_end_m=5.0,
                station_center_m=0.0,
            ),
        ),
        edges=(),
        mode="osm",
    )


def _zoning_config(*, seed: int = 42, asymmetry_strength: float = 0.35, left_right_bias: float = 0.0) -> StreetComposeConfig:
    return StreetComposeConfig(
        query="commercial boulevard",
        length_m=40.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=seed,
        topk_per_category=10,
        max_trials_per_slot=10,
        layout_mode="osm",
        land_use_asymmetry_strength=asymmetry_strength,
        left_right_bias=left_right_bias,
    )


def _single_theme_segment(theme_name: str = "commercial") -> tuple[ThemeSegment, ...]:
    return (
        ThemeSegment(
            theme_id="theme_000",
            theme_name=theme_name,
            x_start_m=-5.0,
            x_end_m=5.0,
            center_x_m=0.0,
            length_m=10.0,
            segment_ids=("seg_0000",),
        ),
    )


@pytest.mark.parametrize(
    ("query", "highway_type", "poi_types", "expected_theme"),
    [
        ("quiet residential neighborhood street", "residential", ("entrance",), "residential"),
        ("downtown commercial shopping street", "tertiary", ("post_box", "waste_basket"), "commercial"),
        ("transit station boulevard", "primary", ("bus_stop", "subway_entrance"), "transit"),
        ("green walkable park edge", "residential", (), "green"),
    ],
)
def test_infer_theme_segments_covers_fixed_theme_vocab(
    query: str,
    highway_type: str,
    poi_types: tuple[str, ...],
    expected_theme: str,
):
    segments = infer_theme_segments(
        _graph_for_theme(highway_type=highway_type, poi_types=poi_types),
        query=query,
        target_street_type="mixed_use",
        fallback_length_m=40.0,
    )
    assert len(segments) == 1
    assert segments[0].theme_name == expected_theme


def test_infer_theme_segments_merges_adjacent_equal_themes():
    graph = RoadSegmentGraph(
        nodes=(
            RoadSegmentNode(
                segment_id="seg_0000",
                road_id=1,
                start_xy=(0.0, 0.0),
                end_xy=(8.0, 0.0),
                center_xy=(4.0, 0.0),
                length_m=8.0,
                highway_type="residential",
                poi_types=("entrance",),
                station_start_m=-12.0,
                station_end_m=-4.0,
                station_center_m=-8.0,
            ),
            RoadSegmentNode(
                segment_id="seg_0001",
                road_id=1,
                start_xy=(8.0, 0.0),
                end_xy=(16.0, 0.0),
                center_xy=(12.0, 0.0),
                length_m=8.0,
                highway_type="residential",
                poi_types=("entrance",),
                station_start_m=-4.0,
                station_end_m=4.0,
                station_center_m=0.0,
            ),
            RoadSegmentNode(
                segment_id="seg_0002",
                road_id=1,
                start_xy=(16.0, 0.0),
                end_xy=(24.0, 0.0),
                center_xy=(20.0, 0.0),
                length_m=8.0,
                highway_type="primary",
                poi_types=("bus_stop",),
                station_start_m=4.0,
                station_end_m=12.0,
                station_center_m=8.0,
            ),
        ),
        edges=(),
        mode="osm",
    )
    segments = infer_theme_segments(
        graph,
        query="transit friendly neighborhood street",
        target_street_type="mixed_use",
        fallback_length_m=40.0,
    )
    assert len(segments) == 2
    assert segments[0].segment_ids == ("seg_0000", "seg_0001")
    assert segments[1].theme_name == "transit"


def test_build_zoning_grid_preview_defaults_to_side_specific_land_use_and_widths():
    graph = _graph_for_theme(highway_type="tertiary", poi_types=("entrance",))
    placement_context = SimpleNamespace(
        carriageway_width_m=8.0,
        left_clear_path_width_m=1.8,
        left_furnishing_width_m=0.7,
        right_clear_path_width_m=1.8,
        right_furnishing_width_m=0.7,
    )

    zoning_grid, summary = build_zoning_grid_preview(
        config=_zoning_config(seed=11),
        placement_context=placement_context,
        road_segment_graph=graph,
        theme_segments=_single_theme_segment("commercial"),
        building_footprints=(),
        road_buffer_m=35.0,
    )

    left_cell = next(cell for cell in zoning_grid if cell["lane_role"] == "left_building_buffer")
    right_cell = next(cell for cell in zoning_grid if cell["lane_role"] == "right_building_buffer")

    assert left_cell["land_use_type"] != right_cell["land_use_type"]
    assert left_cell["building_buffer_width_m"] != right_cell["building_buffer_width_m"]
    assert summary["side_land_use_counts"]["left"]
    assert summary["side_land_use_counts"]["right"]
    assert summary["asymmetry_strength"] == pytest.approx(0.35)
    assert summary["active_side_counts"]


@pytest.mark.parametrize(
    ("bias", "expected_active_side"),
    [
        (0.4, "left"),
        (-0.4, "right"),
    ],
)
def test_build_zoning_grid_preview_respects_left_right_bias(bias: float, expected_active_side: str):
    graph = _graph_for_theme(highway_type="tertiary", poi_types=("entrance",))
    placement_context = SimpleNamespace(
        carriageway_width_m=8.0,
        left_clear_path_width_m=1.8,
        left_furnishing_width_m=0.7,
        right_clear_path_width_m=1.8,
        right_furnishing_width_m=0.7,
    )

    zoning_grid, summary = build_zoning_grid_preview(
        config=_zoning_config(seed=11, left_right_bias=bias),
        placement_context=placement_context,
        road_segment_graph=graph,
        theme_segments=_single_theme_segment("commercial"),
        building_footprints=(),
        road_buffer_m=35.0,
    )

    left_cell = next(cell for cell in zoning_grid if cell["lane_role"] == "left_building_buffer")
    right_cell = next(cell for cell in zoning_grid if cell["lane_role"] == "right_building_buffer")

    assert summary["active_side_counts"] == {expected_active_side: 1}
    if expected_active_side == "left":
        assert left_cell["land_use_type"] == "commercial"
        assert right_cell["land_use_type"] == "residential"
        assert left_cell["building_buffer_width_m"] > right_cell["building_buffer_width_m"]
    else:
        assert right_cell["land_use_type"] == "commercial"
        assert left_cell["land_use_type"] == "residential"
        assert right_cell["building_buffer_width_m"] > left_cell["building_buffer_width_m"]


def test_build_zoning_grid_preview_asymmetry_strength_zero_restores_symmetric_baseline():
    graph = _graph_for_theme(highway_type="tertiary", poi_types=("entrance",))
    placement_context = SimpleNamespace(
        carriageway_width_m=8.0,
        left_clear_path_width_m=1.8,
        left_furnishing_width_m=0.7,
        right_clear_path_width_m=1.8,
        right_furnishing_width_m=0.7,
    )

    zoning_grid, summary = build_zoning_grid_preview(
        config=_zoning_config(seed=11, asymmetry_strength=0.0),
        placement_context=placement_context,
        road_segment_graph=graph,
        theme_segments=_single_theme_segment("commercial"),
        building_footprints=(),
        road_buffer_m=35.0,
    )

    left_cell = next(cell for cell in zoning_grid if cell["lane_role"] == "left_building_buffer")
    right_cell = next(cell for cell in zoning_grid if cell["lane_role"] == "right_building_buffer")

    assert left_cell["land_use_type"] == right_cell["land_use_type"] == "commercial"
    assert left_cell["building_buffer_width_m"] == pytest.approx(right_cell["building_buffer_width_m"])
    assert summary["active_side_counts"] == {}


def test_generate_grid_growth_lots_respects_land_use_side_and_height_rules():
    zoning_grid = [
        {
            "cell_id": "zone_left_res_0",
            "polygon_xz": [[0.0, 5.0], [8.0, 5.0], [8.0, 9.0], [0.0, 9.0], [0.0, 5.0]],
            "center_xz": [4.0, 7.0],
            "street_edge_xz": [4.0, 5.0],
            "lane_role": "left_building_buffer",
            "theme_id": "theme_res",
            "theme_name": "residential",
            "land_use_type": "residential",
            "buildable": True,
            "lot_id": "",
            "segment_ids": ["seg_0000"],
            "station_range_m": [0.0, 8.0],
        },
        {
            "cell_id": "zone_left_res_1",
            "polygon_xz": [[8.0, 5.0], [16.0, 5.0], [16.0, 9.0], [8.0, 9.0], [8.0, 5.0]],
            "center_xz": [12.0, 7.0],
            "street_edge_xz": [12.0, 5.0],
            "lane_role": "left_building_buffer",
            "theme_id": "theme_res",
            "theme_name": "residential",
            "land_use_type": "residential",
            "buildable": True,
            "lot_id": "",
            "segment_ids": ["seg_0001"],
            "station_range_m": [8.0, 16.0],
        },
        {
            "cell_id": "zone_left_green",
            "polygon_xz": [[16.0, 5.0], [24.0, 5.0], [24.0, 9.0], [16.0, 9.0], [16.0, 5.0]],
            "center_xz": [20.0, 7.0],
            "street_edge_xz": [20.0, 5.0],
            "lane_role": "left_building_buffer",
            "theme_id": "theme_green",
            "theme_name": "green",
            "land_use_type": "green",
            "buildable": False,
            "lot_id": "",
            "segment_ids": ["seg_0002"],
            "station_range_m": [16.0, 24.0],
        },
        {
            "cell_id": "zone_right_transit",
            "polygon_xz": [[0.0, -9.0], [8.0, -9.0], [8.0, -5.0], [0.0, -5.0], [0.0, -9.0]],
            "center_xz": [4.0, -7.0],
            "street_edge_xz": [4.0, -5.0],
            "lane_role": "right_building_buffer",
            "theme_id": "theme_transit",
            "theme_name": "transit",
            "land_use_type": "transit",
            "buildable": True,
            "lot_id": "",
            "segment_ids": ["seg_1000"],
            "station_range_m": [0.0, 8.0],
        },
    ]

    annotated_cells, generated_lots, summary = generate_grid_growth_lots(zoning_grid, road_type="primary", height_mode="class_only")

    assert len(generated_lots) == 2
    assert {lot.land_use_type for lot in generated_lots} == {"residential", "transit"}
    assert {lot.side for lot in generated_lots} == {"left", "right"}
    assert next(lot for lot in generated_lots if lot.land_use_type == "residential").height_class == "midrise"
    assert next(lot for lot in generated_lots if lot.land_use_type == "transit").height_class == "highrise"
    assert summary["lot_count"] == 2
    assert summary["occupied_lot_cells"] == 3
    assert sum(summary["placement_strategy_counts"].values()) == len(generated_lots)
    assert 1.0 <= summary["front_setback_stats"]["min_m"] <= 2.0
    assert 1.0 <= summary["front_setback_stats"]["max_m"] <= 2.0
    lot_ids = {lot.lot_id for lot in generated_lots}
    assert {
        str(cell.get("lot_id", "") or "")
        for cell in annotated_cells
        if str(cell.get("land_use_type", "") or "") in {"residential", "transit"}
    } <= lot_ids
    for lot in generated_lots:
        assert lot.placement_strategy in {"frontage_setback", "frontage_clamped"}
        assert 1.0 <= lot.front_setback_m <= 2.0
        if lot.side == "left":
            front_edge_z = lot.placement_xz[1] - lot.building_depth_m / 2.0
            assert front_edge_z == pytest.approx(lot.street_edge_xz[1] + lot.front_setback_m, abs=1e-6)
        elif lot.side == "right":
            front_edge_z = lot.placement_xz[1] + lot.building_depth_m / 2.0
            assert front_edge_z == pytest.approx(lot.street_edge_xz[1] - lot.front_setback_m, abs=1e-6)
    assert all(
        str(cell.get("lot_id", "") or "") == ""
        for cell in annotated_cells
        if str(cell.get("land_use_type", "") or "") == "green"
    )


def test_height_class_from_height_m_thresholds():
    assert height_class_from_height_m(0.0) == "lowrise"
    assert height_class_from_height_m(11.9) == "lowrise"
    assert height_class_from_height_m(12.0) == "midrise"
    assert height_class_from_height_m(24.9) == "midrise"
    assert height_class_from_height_m(25.0) == "highrise"
    assert height_class_from_height_m(100.0) == "highrise"


def test_sample_building_target_height_deterministic():
    h1 = sample_building_target_height(seed=42, target_id="lot_001", theme_name="residential", frontage_width_m=12.0, depth_m=10.0)
    h2 = sample_building_target_height(seed=42, target_id="lot_001", theme_name="residential", frontage_width_m=12.0, depth_m=10.0)
    assert h1 == h2
    assert h1 > 0.0


def test_sample_building_target_height_variation_across_targets():
    heights = {
        sample_building_target_height(seed=42, target_id=f"lot_{i:03d}", theme_name="commercial", frontage_width_m=15.0, depth_m=12.0)
        for i in range(10)
    }
    assert len(heights) > 1, "Expected different heights for different target_ids"


def test_sample_building_target_height_area_cap():
    # 8m * 8m = 64 m² < 100 → cap at 18m
    h = sample_building_target_height(seed=99, target_id="small_lot", theme_name="transit", frontage_width_m=8.0, depth_m=8.0)
    assert h <= 18.0


def test_sample_building_target_height_within_theme_range():
    for _ in range(50):
        h = sample_building_target_height(seed=_, target_id=f"t_{_}", theme_name="residential", frontage_width_m=20.0, depth_m=15.0)
        assert 9.0 <= h <= 22.0, f"residential height {h} out of range"


def test_generate_grid_growth_lots_theme_random_produces_target_height():
    zoning_grid = [
        {
            "cell_id": "c0",
            "polygon_xz": [[0.0, 5.0], [12.0, 5.0], [12.0, 9.0], [0.0, 9.0], [0.0, 5.0]],
            "center_xz": [6.0, 7.0],
            "lane_role": "left_building_buffer",
            "theme_id": "t_res",
            "theme_name": "residential",
            "land_use_type": "residential",
            "buildable": True,
            "lot_id": "",
            "segment_ids": ["seg_0"],
            "station_range_m": [0.0, 12.0],
        },
    ]
    _, lots, summary = generate_grid_growth_lots(zoning_grid, road_type="primary", seed=42, height_mode="theme_random")
    assert len(lots) >= 1
    for lot in lots:
        assert lot.target_height_m > 0.0
        assert lot.height_class == height_class_from_height_m(lot.target_height_m)
    assert "target_height_stats" in summary
