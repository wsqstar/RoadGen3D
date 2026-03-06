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


def test_parse_osm_features_empty():
    features = parse_osm_features({"elements": []})
    assert len(features.roads) == 0
    assert len(features.entrances) == 0


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
