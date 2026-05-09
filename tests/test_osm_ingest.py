"""Tests for M5 OSM data ingestion module."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.osm_ingest import (
    OsmFeatures,
    auto_detect_utm_epsg,
    fetch_osm_data,
    parse_osm_features,
    project_to_local,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MINIMAL_OVERPASS_RESPONSE = {
    "elements": [
        # node coords (referenced by ways)
        {"type": "node", "id": 1, "lon": 116.390, "lat": 39.900},
        {"type": "node", "id": 2, "lon": 116.391, "lat": 39.900},
        {"type": "node", "id": 3, "lon": 116.392, "lat": 39.900},
        # a road
        {
            "type": "way",
            "id": 100,
            "nodes": [1, 2, 3],
            "tags": {"highway": "residential"},
        },
        # an entrance
        {
            "type": "node",
            "id": 10,
            "lon": 116.3905,
            "lat": 39.9005,
            "tags": {"entrance": "yes"},
        },
        # a bus stop
        {
            "type": "node",
            "id": 11,
            "lon": 116.3908,
            "lat": 39.9002,
            "tags": {"highway": "bus_stop"},
        },
        # a fire hydrant
        {
            "type": "node",
            "id": 12,
            "lon": 116.3912,
            "lat": 39.9001,
            "tags": {"emergency": "fire_hydrant"},
        },
    ]
}


# ---------------------------------------------------------------------------
# UTM zone detection
# ---------------------------------------------------------------------------


def test_auto_detect_utm_epsg_beijing():
    assert auto_detect_utm_epsg(116.4, 39.9) == 32650


def test_auto_detect_utm_epsg_nyc():
    assert auto_detect_utm_epsg(-74.0, 40.7) == 32618


def test_auto_detect_utm_epsg_london():
    assert auto_detect_utm_epsg(-0.12, 51.5) == 32630


def test_auto_detect_utm_epsg_southern_hemisphere():
    # Sydney
    assert auto_detect_utm_epsg(151.2, -33.9) == 32756


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_osm_features_road_count():
    features = parse_osm_features(_MINIMAL_OVERPASS_RESPONSE)
    assert len(features.roads) == 1
    assert features.roads[0].highway_type == "residential"
    assert len(features.roads[0].coords) == 3


def test_parse_osm_features_poi_counts():
    features = parse_osm_features(_MINIMAL_OVERPASS_RESPONSE)
    assert len(features.entrances) == 1
    assert len(features.bus_stops) == 1
    assert len(features.fire_points) == 1


def test_parse_osm_features_extended_poi_taxonomy():
    data = {
        "elements": [
            {"type": "node", "id": 1, "lon": 116.3900, "lat": 39.9000},
            {"type": "node", "id": 2, "lon": 116.3910, "lat": 39.9000},
            {
                "type": "way",
                "id": 100,
                "nodes": [1, 2],
                "tags": {"highway": "residential"},
            },
            {"type": "node", "id": 10, "lon": 116.3901, "lat": 39.9001, "tags": {"highway": "crossing"}},
            {"type": "node", "id": 11, "lon": 116.3902, "lat": 39.9001, "tags": {"highway": "traffic_signals"}},
            {"type": "node", "id": 12, "lon": 116.3903, "lat": 39.9001, "tags": {"amenity": "parking_entrance"}},
            {"type": "node", "id": 13, "lon": 116.3904, "lat": 39.9001, "tags": {"railway": "subway_entrance"}},
            {"type": "node", "id": 14, "lon": 116.3905, "lat": 39.9001, "tags": {"amenity": "post_box"}},
            {"type": "node", "id": 15, "lon": 116.3906, "lat": 39.9001, "tags": {"amenity": "waste_basket"}},
            {"type": "node", "id": 16, "lon": 116.3907, "lat": 39.9001, "tags": {"barrier": "bollard"}},
        ]
    }
    features = parse_osm_features(data)
    assert len(features.poi_points_by_type["crossing"]) == 1
    assert len(features.poi_points_by_type["traffic_signals"]) == 1
    assert len(features.poi_points_by_type["parking_entrance"]) == 1
    assert len(features.poi_points_by_type["subway_entrance"]) == 1
    assert len(features.poi_points_by_type["post_box"]) == 1
    assert len(features.poi_points_by_type["waste_basket"]) == 1
    assert len(features.poi_points_by_type["bollard"]) == 1


def test_parse_osm_features_road_default_width():
    features = parse_osm_features(_MINIMAL_OVERPASS_RESPONSE)
    assert features.roads[0].width_m == 6.0  # residential default


def test_parse_osm_features_with_width_tag():
    data = {
        "elements": [
            {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0},
            {"type": "node", "id": 2, "lon": 0.001, "lat": 0.0},
            {
                "type": "way",
                "id": 200,
                "nodes": [1, 2],
                "tags": {"highway": "tertiary", "width": "10.5"},
            },
        ]
    }
    features = parse_osm_features(data)
    assert features.roads[0].width_m == pytest.approx(10.5)


def test_parse_osm_features_unclassified_default_width():
    data = {
        "elements": [
            {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0},
            {"type": "node", "id": 2, "lon": 0.001, "lat": 0.0},
            {
                "type": "way",
                "id": 201,
                "nodes": [1, 2],
                "tags": {"highway": "unclassified"},
            },
        ]
    }
    features = parse_osm_features(data)
    assert features.roads[0].width_m == pytest.approx(6.0)


def test_parse_osm_features_empty():
    features = parse_osm_features({"elements": []})
    assert len(features.roads) == 0
    assert len(features.entrances) == 0


def test_parse_osm_features_extracts_building_footprints():
    data = {
        "elements": [
            {"type": "node", "id": 1, "lon": 116.3900, "lat": 39.9000},
            {"type": "node", "id": 2, "lon": 116.3910, "lat": 39.9000},
            {"type": "node", "id": 10, "lon": 116.3902, "lat": 39.9002},
            {"type": "node", "id": 11, "lon": 116.3904, "lat": 39.9002},
            {"type": "node", "id": 12, "lon": 116.3904, "lat": 39.9004},
            {"type": "node", "id": 13, "lon": 116.3902, "lat": 39.9004},
            {"type": "way", "id": 100, "nodes": [1, 2], "tags": {"highway": "residential"}},
            {"type": "way", "id": 200, "nodes": [10, 11, 12, 13], "tags": {"building": "yes"}},
        ]
    }
    features = parse_osm_features(data)
    assert len(features.buildings) == 1
    assert features.buildings[0].coords[0] == features.buildings[0].coords[-1]


def test_parse_osm_features_extracts_semantic_polygons_and_points():
    data = {
        "elements": [
            {"type": "node", "id": 1, "lon": 116.3900, "lat": 39.9000},
            {"type": "node", "id": 2, "lon": 116.3910, "lat": 39.9000},
            {"type": "node", "id": 3, "lon": 116.3910, "lat": 39.9010},
            {"type": "node", "id": 4, "lon": 116.3900, "lat": 39.9010},
            {"type": "node", "id": 5, "lon": 116.3920, "lat": 39.9000},
            {"type": "node", "id": 6, "lon": 116.3930, "lat": 39.9000},
            {"type": "node", "id": 7, "lon": 116.3930, "lat": 39.9010},
            {"type": "node", "id": 8, "lon": 116.3920, "lat": 39.9010},
            {"type": "node", "id": 9, "lon": 116.3940, "lat": 39.9000},
            {"type": "node", "id": 10, "lon": 116.3950, "lat": 39.9000},
            {"type": "node", "id": 11, "lon": 116.3950, "lat": 39.9010},
            {"type": "node", "id": 12, "lon": 116.3940, "lat": 39.9010},
            {"type": "way", "id": 100, "nodes": [1, 2], "tags": {"highway": "residential"}},
            {"type": "way", "id": 200, "nodes": [1, 2, 3, 4], "tags": {"amenity": "kindergarten"}},
            {"type": "way", "id": 201, "nodes": [5, 6, 7, 8], "tags": {"landuse": "commercial"}},
            {"type": "way", "id": 202, "nodes": [9, 10, 11, 12], "tags": {}},
            {
                "type": "relation",
                "id": 300,
                "members": [{"type": "way", "ref": 202, "role": "outer"}],
                "tags": {"type": "multipolygon", "leisure": "park"},
            },
            {"type": "node", "id": 30, "lon": 116.3905, "lat": 39.9005, "tags": {"amenity": "school"}},
            {"type": "node", "id": 31, "lon": 116.3925, "lat": 39.9005, "tags": {"shop": "bakery"}},
            {"type": "node", "id": 32, "lon": 116.3935, "lat": 39.9005, "tags": {"amenity": "parking"}},
            {"type": "node", "id": 33, "lon": 116.3945, "lat": 39.9005, "tags": {"leisure": "park"}},
        ]
    }

    features = parse_osm_features(data)

    assert len(features.land_use_polygons) == 3
    assert len(features.semantic_blocks) == 3
    assert {polygon.tags.get("amenity") for polygon in features.land_use_polygons} >= {"kindergarten"}
    assert {polygon.tags.get("landuse") for polygon in features.land_use_polygons} >= {"commercial"}
    assert {polygon.source_type for polygon in features.land_use_polygons} >= {"relation"}
    assert len(features.semantic_points_by_type["education"]) == 1
    assert len(features.semantic_points_by_type["commercial"]) == 1
    assert len(features.semantic_points_by_type["vehicle_access"]) == 1
    assert len(features.semantic_points_by_type["green"]) == 1


def test_project_to_local_projects_semantic_blocks():
    pytest.importorskip("pyproj")
    data = {
        "elements": [
            {"type": "node", "id": 1, "lon": 116.3900, "lat": 39.9000},
            {"type": "node", "id": 2, "lon": 116.3910, "lat": 39.9000},
            {"type": "node", "id": 3, "lon": 116.3910, "lat": 39.9010},
            {"type": "node", "id": 4, "lon": 116.3900, "lat": 39.9010},
            {"type": "way", "id": 100, "nodes": [1, 2], "tags": {"highway": "residential"}},
            {"type": "way", "id": 200, "nodes": [1, 2, 3, 4], "tags": {"landuse": "retail"}},
            {"type": "node", "id": 30, "lon": 116.3905, "lat": 39.9005, "tags": {"shop": "bakery"}},
        ]
    }
    features = parse_osm_features(data)
    projected = project_to_local(features, (116.389, 39.899, 116.392, 39.902))

    assert len(projected.land_use_polygons) == 1
    assert len(projected.semantic_blocks) == 1
    assert len(projected.semantic_points_by_type["commercial"]) == 1
    assert projected.semantic_blocks[0].centroid != features.semantic_blocks[0].centroid


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def test_project_to_local_origin_near_zero():
    """After projection, bbox centre should be near (0, 0)."""
    pytest.importorskip("pyproj")
    features = parse_osm_features(_MINIMAL_OVERPASS_RESPONSE)
    bbox = (116.390, 39.900, 116.392, 39.901)
    projected = project_to_local(features, bbox)

    # The origin is at bbox centre, so bbox_m should straddle 0
    assert projected.bbox_m[0] < 0 < projected.bbox_m[2]
    assert projected.bbox_m[1] < 0 < projected.bbox_m[3]


def test_project_preserves_relative_distances():
    """Two points ~100m apart in WGS-84 should be ~100m apart after projection."""
    pytest.importorskip("pyproj")
    import math

    # Two points approx 111m apart along lon at lat=39.9
    features = OsmFeatures(
        entrances=[(116.390, 39.900), (116.391, 39.900)],
    )
    bbox = (116.389, 39.899, 116.392, 39.901)
    projected = project_to_local(features, bbox)

    p1, p2 = projected.entrances[0], projected.entrances[1]
    dist = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
    # 0.001 degree of longitude at lat=39.9 ≈ 85m
    assert 60.0 < dist < 120.0, f"Projected distance {dist:.1f}m seems wrong"


def test_project_to_local_projects_buildings():
    pytest.importorskip("pyproj")
    data = {
        "elements": [
            {"type": "node", "id": 1, "lon": 116.3900, "lat": 39.9000},
            {"type": "node", "id": 2, "lon": 116.3910, "lat": 39.9000},
            {"type": "node", "id": 10, "lon": 116.3902, "lat": 39.9002},
            {"type": "node", "id": 11, "lon": 116.3904, "lat": 39.9002},
            {"type": "node", "id": 12, "lon": 116.3904, "lat": 39.9004},
            {"type": "node", "id": 13, "lon": 116.3902, "lat": 39.9004},
            {"type": "way", "id": 100, "nodes": [1, 2], "tags": {"highway": "residential"}},
            {"type": "way", "id": 200, "nodes": [10, 11, 12, 13], "tags": {"building": "yes"}},
        ]
    }
    features = parse_osm_features(data)
    projected = project_to_local(features, (116.389, 39.899, 116.392, 39.901))
    assert len(projected.buildings) == 1
    assert len(projected.buildings[0].coords) >= 4


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_hit_no_network(tmp_path: Path):
    """When cache exists, fetch_osm_data should not make HTTP requests."""
    bbox = (116.39, 39.90, 116.40, 39.91)

    # Pre-populate cache
    from roadgen3d.osm_ingest import _bbox_hash
    cache_path = tmp_path / f"overpass_{_bbox_hash(bbox)}.json"
    cache_path.write_text(json.dumps(_MINIMAL_OVERPASS_RESPONSE), encoding="utf-8")

    # Patch requests.post at the module where it will be imported
    with patch.dict("sys.modules", {"requests": type(sys)("requests")}):
        # Inject a post that would fail if called
        sys.modules["requests"].post = lambda *a, **kw: (_ for _ in ()).throw(  # type: ignore
            RuntimeError("Should not fetch")
        )
        # Should not raise because it reads from cache
        data = fetch_osm_data(bbox=bbox, cache_dir=tmp_path)

    assert "elements" in data
    assert len(data["elements"]) > 0
