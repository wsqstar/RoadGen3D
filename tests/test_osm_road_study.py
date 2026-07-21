from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.osm_road_study import (
    build_logical_roads,
    preview_bundle_from_raw,
    select_osm_road_study_area,
)
from roadgen3d.osm_ingest import parse_osm_features


BBOX = (113.2600, 23.1200, 113.2700, 23.1300)


def _raw() -> dict:
    nodes = [
        (1, 113.2620, 23.1250),
        (2, 113.2640, 23.1250),
        (3, 113.2660, 23.1250),
        (4, 113.2640, 23.1270),
        (5, 113.2640, 23.12995),
        (6, 113.2680, 23.1250),
        (20, 113.2637, 23.1254),
        (21, 113.2643, 23.1254),
        (22, 113.2643, 23.1260),
        (23, 113.2637, 23.1260),
        (30, 113.2685, 23.1285),
        (31, 113.2690, 23.1285),
        (32, 113.2690, 23.1290),
        (33, 113.2685, 23.1290),
    ]
    return {
        "elements": [
            *[{"type": "node", "id": node_id, "lon": lon, "lat": lat} for node_id, lon, lat in nodes],
            {"type": "node", "id": 40, "lon": 113.2640, "lat": 23.1255, "tags": {"natural": "tree", "species": "ginkgo"}},
            {"type": "node", "id": 41, "lon": 113.2695, "lat": 23.1295, "tags": {"natural": "tree"}},
            {"type": "way", "id": 101, "nodes": [1, 2], "tags": {"highway": "residential", "name": "Main Street"}},
            {"type": "way", "id": 102, "nodes": [2, 3], "tags": {"highway": "residential", "name": "Main Street"}},
            {"type": "way", "id": 201, "nodes": [2, 4, 5], "tags": {"highway": "secondary", "name": "Cross Road"}},
            {"type": "way", "id": 301, "nodes": [3, 6], "tags": {"highway": "residential", "name": "Remote Street"}},
            {"type": "way", "id": 401, "nodes": [20, 21, 22, 23, 20], "tags": {"building": "yes", "building:levels": "4"}},
            {"type": "way", "id": 402, "nodes": [30, 31, 32, 33, 30], "tags": {"building": "yes"}},
        ]
    }


def test_logical_roads_merge_connected_same_name_and_keep_hops():
    parsed = parse_osm_features(_raw())
    roads, adjacency = build_logical_roads(parsed.roads, BBOX)
    main = next(item for item in roads if item.name == "Main Street")
    cross = next(item for item in roads if item.name == "Cross Road")
    assert main.way_ids == (101, 102)
    assert cross.logical_road_id in adjacency[main.logical_road_id]
    assert main.logical_road_id in adjacency[cross.logical_road_id]


def test_selection_filters_roads_and_buildings_with_full_footprint():
    bundle = preview_bundle_from_raw(raw_osm=_raw(), aoi_bbox=BBOX, source_id="fixture", preview_id="preview-fixture")
    seed = next(item for item in bundle.logical_roads if item.name == "Main Street")
    selected = select_osm_road_study_area(
        bundle,
        seed_logical_road_id=seed.logical_road_id,
        hop_count=1,
        context_buffer_m=100,
    )
    ids = {item["id"] for item in selected["filtered_geojson"]["features"]}
    assert {"osm-road-101", "osm-road-102", "osm-road-201", "osm-road-301"}.issubset(ids)
    assert "osm-building-401" in ids
    assert "osm-building-402" not in ids
    assert "osm-tree-40" in ids
    assert "osm-tree-41" not in ids
    building = next(item for item in selected["filtered_geojson"]["features"] if item["id"] == "osm-building-401")
    assert len(building["geometry"]["coordinates"][0]) == 5
    assert selected["study"]["selection"]["hop_count"] == 1
    assert selected["study"]["included_feature_counts"]["buildings"] == 1
    assert selected["study"]["included_feature_counts"]["trees"] == 1
    context = selected["osm_annotation_context"]
    assert context["schema_version"] == "roadgen3d.osm_annotation_context.v1"
    assert context["raw_feature_collection"] == selected["filtered_geojson"]
    assert context["projection"]["crs"] == "EPSG:4326"
    assert context["selected_way_ids"] == [101, 102, 201, 301]


def test_selection_reports_retrieval_boundary_warning():
    bundle = preview_bundle_from_raw(raw_osm=_raw(), aoi_bbox=BBOX, source_id="fixture", preview_id="preview-fixture")
    seed = next(item for item in bundle.logical_roads if item.name == "Cross Road")
    selected = select_osm_road_study_area(
        bundle,
        seed_logical_road_id=seed.logical_road_id,
        hop_count=1,
        context_buffer_m=100,
    )
    assert any("retrieval boundary" in item for item in selected["study"]["warnings"])
