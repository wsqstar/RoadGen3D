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
    _explicit_streetwall_reference_from_graph,
    build_zoning_grid_preview,
    collect_building_footprints,
    generate_frontage_infill_footprints,
    generate_grid_growth_lots,
    height_class_from_height_m,
    infer_theme_segments,
    sample_building_target_height,
)
from roadgen3d.types import (
    DEFAULT_BUILDING_FRONT_SETBACK_MAX_M,
    DEFAULT_BUILDING_FRONT_SETBACK_MIN_M,
    BuildingFootprint,
    RoadSegmentGraph,
    RoadSegmentNode,
    StreetComposeConfig,
    ThemeSegment,
)


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


def _zoning_config(*, seed: int = 42, asymmetry_strength: float = 0.0, left_right_bias: float = 0.0) -> StreetComposeConfig:
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


def test_build_zoning_grid_preview_defaults_to_balanced_land_use_and_widths():
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

    assert left_cell["land_use_type"] == right_cell["land_use_type"] == "commercial"
    assert left_cell["building_buffer_width_m"] == pytest.approx(right_cell["building_buffer_width_m"])
    assert summary["building_buffer_gap_ratio"] <= 0.10
    assert summary["streetwall_reference_gap_ratio"] <= 0.10
    assert summary["side_land_use_counts"]["left"]
    assert summary["side_land_use_counts"]["right"]
    assert summary["asymmetry_strength"] == pytest.approx(0.0)
    assert summary["active_side_counts"] == {}
    assert summary["zoning_preview_mode"] == "parcel_first"
    assert summary["frontage_cell_count"] >= 2
    assert summary["buildable_frontage_by_side"]["left"] > 0.0
    assert summary["buildable_frontage_by_side"]["right"] > 0.0


def test_build_zoning_grid_preview_is_parcel_first_even_when_theme_segments_are_few():
    placement_context = SimpleNamespace(
        carriageway_width_m=8.0,
        left_clear_path_width_m=1.8,
        left_furnishing_width_m=0.7,
        right_clear_path_width_m=1.8,
        right_furnishing_width_m=0.7,
    )
    theme_segments = (
        ThemeSegment(
            theme_id="theme_000",
            theme_name="commercial",
            x_start_m=0.0,
            x_end_m=12.0,
            center_x_m=6.0,
            length_m=12.0,
            segment_ids=("seg_0000",),
        ),
        ThemeSegment(
            theme_id="theme_001",
            theme_name="transit",
            x_start_m=12.0,
            x_end_m=24.0,
            center_x_m=18.0,
            length_m=12.0,
            segment_ids=("seg_0001",),
        ),
        ThemeSegment(
            theme_id="theme_002",
            theme_name="commercial",
            x_start_m=24.0,
            x_end_m=36.0,
            center_x_m=30.0,
            length_m=12.0,
            segment_ids=("seg_0002",),
        ),
    )

    zoning_grid, summary = build_zoning_grid_preview(
        config=StreetComposeConfig(
            query="mixed use corridor",
            length_m=36.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=11,
            topk_per_category=10,
            max_trials_per_slot=10,
            layout_mode="template",
            segment_length_m=18.0,
            zoning_granularity="fine",
            streetwall_continuity=0.95,
        ),
        placement_context=placement_context,
        road_segment_graph=None,
        theme_segments=theme_segments,
        building_footprints=(),
        road_buffer_m=35.0,
    )

    building_cells = [cell for cell in zoning_grid if "building_buffer" in cell["lane_role"]]
    assert summary["zoning_preview_mode"] == "parcel_first"
    assert summary["theme_segment_count"] == 3
    assert summary["frontage_cell_count"] == len(building_cells)
    assert summary["frontage_cell_count"] > summary["theme_segment_count"]


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
        config=_zoning_config(seed=11, asymmetry_strength=0.35, left_right_bias=bias),
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
    assert summary["building_buffer_gap_ratio"] <= 0.10
    assert summary["streetwall_reference_gap_ratio"] <= 0.10
    assert summary["active_side_counts"] == {}


def test_build_zoning_grid_preview_caps_overwide_streetwall_reference_widths():
    graph = _graph_for_theme(highway_type="tertiary", poi_types=("entrance", "bus_stop"))
    placement_context = SimpleNamespace(
        carriageway_width_m=6.0,
        left_clear_path_width_m=4.5,
        left_furnishing_width_m=4.989093382834189,
        right_clear_path_width_m=4.5,
        right_furnishing_width_m=3.2,
    )

    zoning_grid, summary = build_zoning_grid_preview(
        config=_zoning_config(seed=13),
        placement_context=placement_context,
        road_segment_graph=graph,
        theme_segments=_single_theme_segment("commercial"),
        building_footprints=(),
        road_buffer_m=35.0,
    )

    assert zoning_grid
    assert summary["streetwall_reference_width_m"]["left"] <= 5.0
    assert summary["streetwall_reference_width_m"]["right"] <= 5.0
    assert summary["streetwall_reference_gap_ratio"] <= 0.10
    assert summary["streetwall_reference_raw_width_m"]["left"] > summary["streetwall_reference_width_m"]["left"]
    assert summary["streetwall_reference_raw_width_m"]["right"] > summary["streetwall_reference_width_m"]["right"]


def test_build_zoning_grid_preview_uses_explicit_graph_streetwall_widths():
    graph = RoadSegmentGraph(
        nodes=(
            RoadSegmentNode(
                segment_id="seg_0000",
                road_id=1,
                start_xy=(0.0, 0.0),
                end_xy=(10.0, 0.0),
                center_xy=(5.0, 0.0),
                length_m=10.0,
                highway_type="tertiary",
                station_start_m=0.0,
                station_end_m=10.0,
                station_center_m=5.0,
                cross_section_strips=(
                    SimpleNamespace(strip_id="left_furn", zone="left", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="left_walk", zone="left", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="left_frontage", zone="left", kind="frontage_reserve", width_m=2.5, order_index=2),
                    SimpleNamespace(strip_id="right_furn", zone="right", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="right_walk", zone="right", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="right_frontage", zone="right", kind="frontage_reserve", width_m=2.5, order_index=2),
                ),
            ),
        ),
        edges=(),
        mode="annotation",
    )
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

    assert zoning_grid
    assert _explicit_streetwall_reference_from_graph(graph)["left_frontage_reserve_m"] == pytest.approx(2.5)
    assert summary["streetwall_reference_width_m"]["left"] == pytest.approx(5.5)
    assert summary["streetwall_reference_width_m"]["right"] == pytest.approx(5.5)
    left_cells = [cell for cell in zoning_grid if cell["lane_role"] == "left_building_buffer"]
    right_cells = [cell for cell in zoning_grid if cell["lane_role"] == "right_building_buffer"]
    assert left_cells
    assert right_cells
    assert min(point[1] for cell in left_cells for point in cell["polygon_xz"]) == pytest.approx(9.5)
    assert max(point[1] for cell in right_cells for point in cell["polygon_xz"]) == pytest.approx(-9.5)


def test_collect_building_footprints_fallback_starts_outside_frontage_reserve():
    graph = RoadSegmentGraph(
        nodes=(
            RoadSegmentNode(
                segment_id="seg_0000",
                road_id=1,
                start_xy=(0.0, 0.0),
                end_xy=(20.0, 0.0),
                center_xy=(10.0, 0.0),
                length_m=20.0,
                highway_type="tertiary",
                station_start_m=0.0,
                station_end_m=20.0,
                station_center_m=10.0,
                cross_section_strips=(
                    SimpleNamespace(strip_id="left_furn", zone="left", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="left_walk", zone="left", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="left_frontage", zone="left", kind="frontage_reserve", width_m=2.5, order_index=2),
                    SimpleNamespace(strip_id="right_furn", zone="right", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="right_walk", zone="right", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="right_frontage", zone="right", kind="frontage_reserve", width_m=2.5, order_index=2),
                ),
            ),
        ),
        edges=(),
        mode="annotation",
    )
    placement_context = SimpleNamespace(
        carriageway_width_m=8.0,
        left_clear_path_width_m=1.8,
        left_furnishing_width_m=0.7,
        right_clear_path_width_m=1.8,
        right_furnishing_width_m=0.7,
    )

    footprints = collect_building_footprints(
        SimpleNamespace(buildings=()),
        placement_context=placement_context,
        theme_segments=_single_theme_segment("commercial"),
        road_segment_graph=graph,
        seed=11,
    )

    assert footprints
    left_footprints = [footprint for footprint in footprints if footprint.side == "left"]
    right_footprints = [footprint for footprint in footprints if footprint.side == "right"]
    assert left_footprints
    assert right_footprints
    assert min(z for footprint in left_footprints for _x, z in footprint.polygon_xz) >= 9.5
    assert max(z for footprint in right_footprints for _x, z in footprint.polygon_xz) <= -9.5


def test_build_zoning_grid_preview_trims_buildable_area_near_junctions_and_road_ends():
    graph = RoadSegmentGraph(
        nodes=(
            RoadSegmentNode(
                segment_id="seg_0000",
                road_id=1,
                start_xy=(0.0, 0.0),
                end_xy=(40.0, 0.0),
                center_xy=(20.0, 0.0),
                length_m=40.0,
                highway_type="tertiary",
                station_start_m=0.0,
                station_end_m=40.0,
                station_center_m=20.0,
                start_junction_id="junction_01",
                cross_section_strips=(
                    SimpleNamespace(strip_id="left_furn", zone="left", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="left_walk", zone="left", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="left_frontage", zone="left", kind="frontage_reserve", width_m=2.5, order_index=2),
                    SimpleNamespace(strip_id="right_furn", zone="right", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="right_walk", zone="right", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="right_frontage", zone="right", kind="frontage_reserve", width_m=2.5, order_index=2),
                ),
            ),
        ),
        edges=(),
        mode="annotation",
    )
    placement_context = SimpleNamespace(
        carriageway_width_m=8.0,
        left_clear_path_width_m=1.8,
        left_furnishing_width_m=0.7,
        right_clear_path_width_m=1.8,
        right_furnishing_width_m=0.7,
        junction_geometries=[{"anchor_xy": [0.0, 0.0]}],
    )

    zoning_grid, summary = build_zoning_grid_preview(
        config=_zoning_config(seed=11),
        placement_context=placement_context,
        road_segment_graph=graph,
        theme_segments=_single_theme_segment("commercial"),
        building_footprints=(),
        road_buffer_m=35.0,
    )

    assert zoning_grid
    left_cells = [cell for cell in zoning_grid if cell["lane_role"] == "left_building_buffer"]
    right_cells = [cell for cell in zoning_grid if cell["lane_role"] == "right_building_buffer"]
    assert left_cells
    assert right_cells
    assert min(point[0] for cell in left_cells for point in cell["polygon_xz"]) >= 10.0
    assert max(point[0] for cell in left_cells for point in cell["polygon_xz"]) <= 30.0
    assert min(point[0] for cell in right_cells for point in cell["polygon_xz"]) >= 10.0
    assert max(point[0] for cell in right_cells for point in cell["polygon_xz"]) <= 30.0
    assert summary["frontage_cell_count"] > 0


def test_collect_building_footprints_filters_to_buildable_corridor():
    from shapely.geometry import box

    graph = RoadSegmentGraph(
        nodes=(
            RoadSegmentNode(
                segment_id="seg_0000",
                road_id=1,
                start_xy=(0.0, 0.0),
                end_xy=(40.0, 0.0),
                center_xy=(20.0, 0.0),
                length_m=40.0,
                highway_type="tertiary",
                station_start_m=0.0,
                station_end_m=40.0,
                station_center_m=20.0,
                start_junction_id="junction_01",
                cross_section_strips=(
                    SimpleNamespace(strip_id="left_furn", zone="left", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="left_walk", zone="left", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="left_frontage", zone="left", kind="frontage_reserve", width_m=2.5, order_index=2),
                    SimpleNamespace(strip_id="right_furn", zone="right", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="right_walk", zone="right", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="right_frontage", zone="right", kind="frontage_reserve", width_m=2.5, order_index=2),
                ),
            ),
        ),
        edges=(),
        mode="annotation",
    )
    placement_context = SimpleNamespace(
        carriageway=box(0.0, -4.0, 40.0, 4.0),
        carriageway_width_m=8.0,
        left_clear_path_width_m=1.8,
        left_furnishing_width_m=0.7,
        right_clear_path_width_m=1.8,
        right_furnishing_width_m=0.7,
        junction_geometries=[{"anchor_xy": [0.0, 0.0]}],
    )
    projected_features = SimpleNamespace(
        buildings=[
            SimpleNamespace(osm_id="near_junction", coords=[(1.0, 10.0), (5.0, 10.0), (5.0, 14.0), (1.0, 14.0), (1.0, 10.0)]),
            SimpleNamespace(osm_id="valid_midblock", coords=[(15.0, 10.0), (19.0, 10.0), (19.0, 14.0), (15.0, 14.0), (15.0, 10.0)]),
            SimpleNamespace(osm_id="near_road_end", coords=[(34.0, 10.0), (38.0, 10.0), (38.0, 14.0), (34.0, 14.0), (34.0, 10.0)]),
        ]
    )

    footprints = collect_building_footprints(
        projected_features,
        placement_context=placement_context,
        theme_segments=_single_theme_segment("commercial"),
        road_segment_graph=graph,
        road_buffer_m=35.0,
        seed=11,
    )

    assert len(footprints) == 1
    assert footprints[0].anchor_geom_id == "valid_midblock"
    assert footprints[0].centroid_xz[0] == pytest.approx(17.0)


def test_build_zoning_grid_preview_green_theme_keeps_streetwall_baseline_in_grid_growth():
    graph = _graph_for_theme(highway_type="residential", poi_types=())
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
        theme_segments=_single_theme_segment("green"),
        building_footprints=(),
        road_buffer_m=35.0,
    )

    left_cell = next(cell for cell in zoning_grid if cell["lane_role"] == "left_building_buffer")
    right_cell = next(cell for cell in zoning_grid if cell["lane_role"] == "right_building_buffer")

    assert left_cell["land_use_type"] == "residential"
    assert right_cell["land_use_type"] == "residential"
    assert left_cell["buildable"] is True
    assert right_cell["buildable"] is True
    assert summary["buildable_frontage_by_side"]["left"] > 0.0
    assert summary["buildable_frontage_by_side"]["right"] > 0.0


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

    assert len(generated_lots) >= 3
    assert {lot.land_use_type for lot in generated_lots} == {"residential", "transit"}
    assert {lot.side for lot in generated_lots} == {"left", "right"}
    assert next(lot for lot in generated_lots if lot.land_use_type == "residential").height_class == "midrise"
    assert next(lot for lot in generated_lots if lot.land_use_type == "transit").height_class == "highrise"
    assert summary["lot_count"] == len(generated_lots)
    assert summary["frontage_parcel_count"] == len(generated_lots)
    assert summary["occupied_lot_cells"] == 3
    assert sum(summary["placement_strategy_counts"].values()) == len(generated_lots)
    assert summary["building_balance_policy"] == "balanced_default"
    assert summary["building_balance_ok"] is True
    assert summary["frontage_balance_gap"] <= 0.10
    assert summary["buildable_frontage_by_side"]["left"] > 0.0
    assert summary["buildable_frontage_by_side"]["right"] > 0.0
    assert DEFAULT_BUILDING_FRONT_SETBACK_MIN_M <= summary["front_setback_stats"]["min_m"] <= DEFAULT_BUILDING_FRONT_SETBACK_MAX_M
    assert DEFAULT_BUILDING_FRONT_SETBACK_MIN_M <= summary["front_setback_stats"]["max_m"] <= DEFAULT_BUILDING_FRONT_SETBACK_MAX_M
    assert summary["frontage_coverage_by_side"]["left"]["coverage_ratio"] > 0.7
    assert summary["frontage_coverage_by_side"]["right"]["coverage_ratio"] > 0.7
    lot_ids = {lot.lot_id for lot in generated_lots}
    assert {
        str(cell.get("lot_id", "") or "")
        for cell in annotated_cells
        if str(cell.get("land_use_type", "") or "") in {"residential", "transit"}
    } <= lot_ids
    for lot in generated_lots:
        assert lot.placement_strategy in {"frontage_setback", "frontage_clamped"}
        assert DEFAULT_BUILDING_FRONT_SETBACK_MIN_M <= lot.front_setback_m <= DEFAULT_BUILDING_FRONT_SETBACK_MAX_M
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


def test_generate_grid_growth_lots_respects_zoning_granularity():
    zoning_grid = [
        {
            "cell_id": "long_strip",
            "polygon_xz": [[0.0, 5.0], [30.0, 5.0], [30.0, 11.0], [0.0, 11.0], [0.0, 5.0]],
            "center_xz": [15.0, 8.0],
            "street_edge_xz": [15.0, 5.0],
            "lane_role": "left_building_buffer",
            "theme_id": "theme_commercial",
            "theme_name": "commercial",
            "land_use_type": "commercial",
            "buildable": True,
            "lot_id": "",
            "segment_ids": ["seg_long"],
            "station_range_m": [0.0, 30.0],
        },
    ]

    _, coarse_lots, coarse_summary = generate_grid_growth_lots(
        zoning_grid,
        road_type="primary",
        height_mode="class_only",
        zoning_granularity="coarse",
    )
    _, fine_lots, fine_summary = generate_grid_growth_lots(
        zoning_grid,
        road_type="primary",
        height_mode="class_only",
        zoning_granularity="fine",
    )

    assert len(coarse_lots) < len(fine_lots)
    assert coarse_summary["zoning_granularity"] == "coarse"
    assert fine_summary["zoning_granularity"] == "fine"


def test_generate_frontage_infill_footprints_only_fills_large_gaps():
    zoning_grid = [
        {
            "cell_id": "left_commercial",
            "polygon_xz": [[0.0, 5.0], [24.0, 5.0], [24.0, 11.0], [0.0, 11.0], [0.0, 5.0]],
            "lane_role": "left_building_buffer",
            "theme_id": "theme_commercial",
            "theme_name": "commercial",
            "land_use_type": "commercial",
            "buildable": True,
            "station_range_m": [0.0, 24.0],
        },
        {
            "cell_id": "right_green",
            "polygon_xz": [[0.0, -11.0], [24.0, -11.0], [24.0, -5.0], [0.0, -5.0], [0.0, -11.0]],
            "lane_role": "right_building_buffer",
            "theme_id": "theme_green",
            "theme_name": "green",
            "land_use_type": "green",
            "buildable": False,
            "station_range_m": [0.0, 24.0],
        },
    ]
    existing = (
        BuildingFootprint(
            footprint_id="building_000",
            source="osm",
            polygon_xz=((0.0, 6.0), (6.0, 6.0), (6.0, 10.0), (0.0, 10.0), (0.0, 6.0)),
            centroid_xz=(3.0, 8.0),
            frontage_width_m=6.0,
            depth_m=4.0,
            yaw_deg=0.0,
            theme_id="theme_commercial",
            land_use_type="commercial",
            side="left",
            placement_strategy="footprint_centroid",
        ),
    )

    infill, summary = generate_frontage_infill_footprints(
        zoning_grid,
        existing,
        seed=7,
        height_mode="class_only",
        zoning_granularity="balanced",
        streetwall_continuity=0.85,
        infill_policy="large_gap_only",
        front_setback_min_m=1.0,
        front_setback_max_m=2.0,
    )

    assert infill
    assert all(footprint.source == "infill" for footprint in infill)
    assert all(footprint.side == "left" for footprint in infill)
    assert all(footprint.land_use_type != "green" for footprint in infill)
    assert all(str(footprint.placement_strategy).startswith("frontage_") for footprint in infill)
    assert summary["real_footprint_count"] == 1
    assert summary["infill_footprint_count"] == len(infill)
    assert summary["frontage_coverage_by_side"]["left"]["coverage_ratio"] > 0.7
    assert summary["frontage_coverage_by_side"]["right"]["coverage_ratio"] == 0.0


def test_generate_grid_growth_lots_reports_one_sided_geometry_reason():
    zoning_grid = [
        {
            "cell_id": "left_only",
            "polygon_xz": [[0.0, 5.0], [18.0, 5.0], [18.0, 10.0], [0.0, 10.0], [0.0, 5.0]],
            "center_xz": [9.0, 7.5],
            "street_edge_xz": [9.0, 5.0],
            "lane_role": "left_building_buffer",
            "theme_id": "theme_left",
            "theme_name": "commercial",
            "land_use_type": "commercial",
            "buildable": True,
            "lot_id": "",
            "segment_ids": ["seg_left"],
            "station_range_m": [0.0, 18.0],
        },
        {
            "cell_id": "right_green",
            "polygon_xz": [[0.0, -10.0], [18.0, -10.0], [18.0, -5.0], [0.0, -5.0], [0.0, -10.0]],
            "center_xz": [9.0, -7.5],
            "street_edge_xz": [9.0, -5.0],
            "lane_role": "right_building_buffer",
            "theme_id": "theme_right",
            "theme_name": "green",
            "land_use_type": "green",
            "buildable": False,
            "lot_id": "",
            "segment_ids": ["seg_right"],
            "station_range_m": [0.0, 18.0],
        },
    ]

    _, lots, summary = generate_grid_growth_lots(zoning_grid, road_type="tertiary", height_mode="class_only")

    assert lots
    assert summary["building_balance_ok"] is False
    assert summary["building_balance_reason"] == "no buildable right frontage"
    assert summary["buildable_frontage_by_side"]["left"] > 0.0
    assert summary["buildable_frontage_by_side"]["right"] == 0.0


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


def test_collect_building_footprints_uses_building_region_records_in_declared_order():
    pytest.importorskip("shapely")
    from shapely.geometry import box

    graph = RoadSegmentGraph(
        nodes=(
            RoadSegmentNode(
                segment_id="seg_0000",
                road_id=1,
                start_xy=(0.0, 0.0),
                end_xy=(40.0, 0.0),
                center_xy=(20.0, 0.0),
                length_m=40.0,
                highway_type="tertiary",
                station_start_m=0.0,
                station_end_m=40.0,
                station_center_m=20.0,
                cross_section_strips=(
                    SimpleNamespace(strip_id="left_furn", zone="left", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="left_walk", zone="left", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="left_frontage", zone="left", kind="frontage_reserve", width_m=2.5, order_index=2),
                    SimpleNamespace(strip_id="right_furn", zone="right", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="right_walk", zone="right", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="right_frontage", zone="right", kind="frontage_reserve", width_m=2.5, order_index=2),
                ),
            ),
        ),
        edges=(),
        mode="annotation",
    )
    placement_context = SimpleNamespace(
        carriageway=box(0.0, -4.0, 40.0, 4.0),
        carriageway_width_m=8.0,
        left_clear_path_width_m=1.8,
        left_furnishing_width_m=0.7,
        right_clear_path_width_m=1.8,
        right_furnishing_width_m=0.7,
        building_regions=[
            {
                "region_id": "building_region_01",
                "label": "Primary Court",
                "order_index": 0,
                "yaw_deg": 15.0,
                "polygon_xz": ((10.0, 9.0), (22.0, 9.0), (22.0, 16.0), (10.0, 16.0), (10.0, 9.0)),
            },
            {
                "region_id": "building_region_02",
                "label": "Override Court",
                "order_index": 1,
                "yaw_deg": 60.0,
                "polygon_xz": ((12.0, 10.0), (20.0, 10.0), (20.0, 15.0), (12.0, 15.0), (12.0, 10.0)),
            },
        ],
    )
    projected_features = SimpleNamespace(
        buildings=[
            SimpleNamespace(osm_id="inside_overlap", coords=[(14.0, 10.5), (18.0, 10.5), (18.0, 14.0), (14.0, 14.0), (14.0, 10.5)]),
            SimpleNamespace(osm_id="outside_regions", coords=[(24.0, 10.0), (28.0, 10.0), (28.0, 14.0), (24.0, 14.0), (24.0, 10.0)]),
        ]
    )

    footprints = collect_building_footprints(
        projected_features,
        placement_context=placement_context,
        theme_segments=_single_theme_segment("commercial"),
        road_segment_graph=graph,
        road_buffer_m=35.0,
        seed=11,
    )

    assert [footprint.footprint_id for footprint in footprints] == ["building_region_01", "building_region_02"]
    assert [footprint.anchor_geom_id for footprint in footprints] == ["building_region_01", "building_region_02"]
    assert [footprint.yaw_deg for footprint in footprints] == pytest.approx([15.0, 60.0])


def test_collect_building_footprints_prefers_region_only_generation_when_building_regions_exist():
    pytest.importorskip("shapely")
    from shapely.geometry import box

    graph = RoadSegmentGraph(
        nodes=(
            RoadSegmentNode(
                segment_id="seg_0000",
                road_id=1,
                start_xy=(0.0, 0.0),
                end_xy=(40.0, 0.0),
                center_xy=(20.0, 0.0),
                length_m=40.0,
                highway_type="tertiary",
                station_start_m=0.0,
                station_end_m=40.0,
                station_center_m=20.0,
                cross_section_strips=(
                    SimpleNamespace(strip_id="left_furn", zone="left", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="left_walk", zone="left", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="left_frontage", zone="left", kind="frontage_reserve", width_m=2.5, order_index=2),
                    SimpleNamespace(strip_id="right_furn", zone="right", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="right_walk", zone="right", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="right_frontage", zone="right", kind="frontage_reserve", width_m=2.5, order_index=2),
                ),
            ),
        ),
        edges=(),
        mode="annotation",
    )
    placement_context = SimpleNamespace(
        carriageway=box(0.0, -4.0, 40.0, 4.0),
        carriageway_width_m=8.0,
        left_clear_path_width_m=1.8,
        left_furnishing_width_m=0.7,
        right_clear_path_width_m=1.8,
        right_furnishing_width_m=0.7,
        building_regions=[
            {
                "region_id": "building_region_01",
                "label": "North Court",
                "order_index": 0,
                "yaw_deg": 15.0,
                "polygon_xz": ((8.0, 9.0), (20.0, 9.0), (20.0, 16.0), (8.0, 16.0), (8.0, 9.0)),
            },
            {
                "region_id": "building_region_02",
                "label": "South Court",
                "order_index": 1,
                "yaw_deg": -20.0,
                "polygon_xz": ((22.0, -16.0), (34.0, -16.0), (34.0, -9.0), (22.0, -9.0), (22.0, -16.0)),
            },
        ],
    )
    projected_features = SimpleNamespace(
        buildings=[
            SimpleNamespace(osm_id="inside_region", coords=[(10.0, 10.0), (14.0, 10.0), (14.0, 14.0), (10.0, 14.0), (10.0, 10.0)]),
            SimpleNamespace(osm_id="outside_region", coords=[(24.0, 10.0), (28.0, 10.0), (28.0, 14.0), (24.0, 14.0), (24.0, 10.0)]),
        ]
    )

    footprints = collect_building_footprints(
        projected_features,
        placement_context=placement_context,
        theme_segments=_single_theme_segment("commercial"),
        road_segment_graph=graph,
        road_buffer_m=35.0,
        seed=17,
    )

    assert [footprint.footprint_id for footprint in footprints] == ["building_region_01", "building_region_02"]
    assert all(footprint.source == "building_region" for footprint in footprints)
    assert [footprint.anchor_geom_id for footprint in footprints] == ["building_region_01", "building_region_02"]
    assert [footprint.placement_strategy for footprint in footprints] == ["building_region", "building_region"]
    assert [footprint.yaw_deg for footprint in footprints] == pytest.approx([15.0, -20.0])


def test_build_zoning_grid_preview_limits_buildable_cells_to_building_regions_and_carries_region_yaw():
    graph = RoadSegmentGraph(
        nodes=(
            RoadSegmentNode(
                segment_id="seg_0000",
                road_id=1,
                start_xy=(0.0, 0.0),
                end_xy=(40.0, 0.0),
                center_xy=(20.0, 0.0),
                length_m=40.0,
                highway_type="tertiary",
                station_start_m=0.0,
                station_end_m=40.0,
                station_center_m=20.0,
                cross_section_strips=(
                    SimpleNamespace(strip_id="left_furn", zone="left", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="left_walk", zone="left", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="left_frontage", zone="left", kind="frontage_reserve", width_m=2.5, order_index=2),
                    SimpleNamespace(strip_id="right_furn", zone="right", kind="nearroad_furnishing", width_m=1.0, order_index=0),
                    SimpleNamespace(strip_id="right_walk", zone="right", kind="clear_sidewalk", width_m=2.0, order_index=1),
                    SimpleNamespace(strip_id="right_frontage", zone="right", kind="frontage_reserve", width_m=2.5, order_index=2),
                ),
            ),
        ),
        edges=(),
        mode="annotation",
    )
    placement_context = SimpleNamespace(
        carriageway_width_m=8.0,
        left_clear_path_width_m=1.8,
        left_furnishing_width_m=0.7,
        right_clear_path_width_m=1.8,
        right_furnishing_width_m=0.7,
        building_regions=[
            {
                "region_id": "building_region_left",
                "label": "Left Court",
                "order_index": 0,
                "yaw_deg": 33.0,
                "polygon_xz": ((10.0, 9.0), (30.0, 9.0), (30.0, 16.0), (10.0, 16.0), (10.0, 9.0)),
            }
        ],
    )

    zoning_grid, summary = build_zoning_grid_preview(
        config=_zoning_config(seed=11),
        placement_context=placement_context,
        road_segment_graph=graph,
        theme_segments=_single_theme_segment("commercial"),
        building_footprints=(),
        road_buffer_m=35.0,
    )

    left_cells = [cell for cell in zoning_grid if cell["lane_role"] == "left_building_buffer"]
    right_cells = [cell for cell in zoning_grid if cell["lane_role"] == "right_building_buffer"]

    assert left_cells
    assert right_cells
    assert any(cell["buildable"] for cell in left_cells)
    assert all(cell["building_region_id"] == "building_region_left" for cell in left_cells if cell["buildable"])
    assert all(cell["building_region_yaw_deg"] == pytest.approx(33.0) for cell in left_cells if cell["buildable"])
    assert all(cell["buildable"] is False for cell in right_cells)
    assert summary["building_region_count"] == 1
    assert summary["active_building_region_count"] == 1
