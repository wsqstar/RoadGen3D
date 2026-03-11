from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.theme_buildings import generate_grid_growth_lots, infer_theme_segments
from roadgen3d.types import RoadSegmentGraph, RoadSegmentNode


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


def test_generate_grid_growth_lots_respects_land_use_side_and_height_rules():
    zoning_grid = [
        {
            "cell_id": "zone_left_res_0",
            "polygon_xz": [[0.0, 5.0], [8.0, 5.0], [8.0, 9.0], [0.0, 9.0], [0.0, 5.0]],
            "center_xz": [4.0, 7.0],
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

    annotated_cells, generated_lots, summary = generate_grid_growth_lots(zoning_grid, road_type="primary")

    assert len(generated_lots) == 2
    assert {lot.land_use_type for lot in generated_lots} == {"residential", "transit"}
    assert {lot.side for lot in generated_lots} == {"left", "right"}
    assert next(lot for lot in generated_lots if lot.land_use_type == "residential").height_class == "midrise"
    assert next(lot for lot in generated_lots if lot.land_use_type == "transit").height_class == "highrise"
    assert summary["lot_count"] == 2
    assert summary["occupied_lot_cells"] == 3
    lot_ids = {lot.lot_id for lot in generated_lots}
    assert {
        str(cell.get("lot_id", "") or "")
        for cell in annotated_cells
        if str(cell.get("land_use_type", "") or "") in {"residential", "transit"}
    } <= lot_ids
    assert all(
        str(cell.get("lot_id", "") or "") == ""
        for cell in annotated_cells
        if str(cell.get("land_use_type", "") or "") == "green"
    )
