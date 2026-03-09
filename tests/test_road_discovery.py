"""Tests for the POI-rich road discovery module."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.road_discovery import (
    DiscoveredRoad,
    _count_pois_in_buffer,
    compute_road_bbox,
    discover_all_cities,
    discover_poi_roads,
    expand_city_bbox,
    write_discovered_roads_jsonl,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeCity:
    """Minimal stand-in for CityRecord."""
    def __init__(self, name_en: str, bbox: Tuple[float, float, float, float]):
        self.name_en = name_en
        self.name_zh = name_en
        self.province = "test"
        self.bbox = bbox


def _make_overpass_response(
    roads: List[Dict],
    pois: List[Dict],
) -> Dict:
    """Build a minimal Overpass JSON response from road/POI specs.

    Each road dict: {"id": int, "highway": str, "coords": [(lon, lat), ...]}
    Each POI dict:  {"id": int, "lon": float, "lat": float, "tags": {...}}
    """
    elements = []
    node_id_counter = 9000

    for road in roads:
        node_ids = []
        for lon, lat in road["coords"]:
            node_id_counter += 1
            elements.append({"type": "node", "id": node_id_counter, "lon": lon, "lat": lat})
            node_ids.append(node_id_counter)
        elements.append({
            "type": "way",
            "id": road["id"],
            "nodes": node_ids,
            "tags": {"highway": road.get("highway", "residential")},
        })

    for poi in pois:
        elements.append({
            "type": "node",
            "id": poi["id"],
            "lon": poi["lon"],
            "lat": poi["lat"],
            "tags": poi.get("tags", {}),
        })

    return {"elements": elements}


# A long road (~170m at lat 39.9) with 3 nearby POIs
_LONG_ROAD_WITH_POIS = _make_overpass_response(
    roads=[
        {
            "id": 1001,
            "highway": "residential",
            "coords": [(116.390, 39.900), (116.391, 39.900), (116.392, 39.900)],
        },
    ],
    pois=[
        {"id": 2001, "lon": 116.3905, "lat": 39.9001, "tags": {"entrance": "yes"}},
        {"id": 2002, "lon": 116.3910, "lat": 39.9001, "tags": {"highway": "bus_stop"}},
        {"id": 2003, "lon": 116.3915, "lat": 39.8999, "tags": {"emergency": "fire_hydrant"}},
    ],
)

# A short road (~85m) with 5 POIs — too short to qualify
_SHORT_ROAD_WITH_POIS = _make_overpass_response(
    roads=[
        {
            "id": 1002,
            "highway": "primary",
            "coords": [(116.390, 39.900), (116.391, 39.900)],
        },
    ],
    pois=[
        {"id": 2010, "lon": 116.3902, "lat": 39.9001, "tags": {"entrance": "yes"}},
        {"id": 2011, "lon": 116.3903, "lat": 39.9001, "tags": {"entrance": "yes"}},
        {"id": 2012, "lon": 116.3904, "lat": 39.8999, "tags": {"highway": "bus_stop"}},
        {"id": 2013, "lon": 116.3905, "lat": 39.8999, "tags": {"highway": "bus_stop"}},
        {"id": 2014, "lon": 116.3906, "lat": 39.8999, "tags": {"emergency": "fire_hydrant"}},
    ],
)

# A long road with NO POIs
_LONG_ROAD_NO_POIS = _make_overpass_response(
    roads=[
        {
            "id": 1003,
            "highway": "tertiary",
            "coords": [(116.390, 39.900), (116.391, 39.900), (116.392, 39.900)],
        },
    ],
    pois=[],
)


# ---------------------------------------------------------------------------
# expand_city_bbox
# ---------------------------------------------------------------------------

class TestExpandCityBbox:
    def test_default_margin(self):
        bbox = (116.3970, 39.9130, 116.4020, 39.9175)
        result = expand_city_bbox(bbox)
        centre_lon = (116.3970 + 116.4020) / 2.0
        centre_lat = (39.9130 + 39.9175) / 2.0
        assert result == pytest.approx((
            centre_lon - 0.01,
            centre_lat - 0.01,
            centre_lon + 0.01,
            centre_lat + 0.01,
        ))

    def test_custom_margin(self):
        bbox = (116.3970, 39.9130, 116.4020, 39.9175)
        result = expand_city_bbox(bbox, margin_deg=0.02)
        centre_lon = (116.3970 + 116.4020) / 2.0
        centre_lat = (39.9130 + 39.9175) / 2.0
        assert result[0] == pytest.approx(centre_lon - 0.02)
        assert result[3] == pytest.approx(centre_lat + 0.02)


# ---------------------------------------------------------------------------
# compute_road_bbox
# ---------------------------------------------------------------------------

class TestComputeRoadBbox:
    def test_padding_applied(self):
        coords = [(116.390, 39.900), (116.392, 39.900)]
        result = compute_road_bbox(coords, padding_m=30.0)
        # Result should be wider than the raw coordinate range
        assert result[0] < 116.390
        assert result[2] > 116.392
        assert result[1] < 39.900
        assert result[3] > 39.900

    def test_encompasses_all_points(self):
        coords = [(116.390, 39.900), (116.391, 39.901), (116.392, 39.899)]
        result = compute_road_bbox(coords, padding_m=0.0)
        for lon, lat in coords:
            assert result[0] <= lon <= result[2]
            assert result[1] <= lat <= result[3]

    def test_padding_degree_conversion(self):
        """Verify the padding math: 111320 m per degree of latitude."""
        coords = [(0.0, 0.0)]
        result = compute_road_bbox(coords, padding_m=111.32)
        # 111.32 m / 111320 m/deg = 0.001 deg
        expected_lat_pad = 111.32 / 111_320.0
        assert result[1] == pytest.approx(-expected_lat_pad, rel=1e-4)
        assert result[3] == pytest.approx(expected_lat_pad, rel=1e-4)


# ---------------------------------------------------------------------------
# _count_pois_in_buffer
# ---------------------------------------------------------------------------

class TestCountPoisInBuffer:
    def test_counts_all_types(self):
        pytest.importorskip("shapely")
        from shapely.geometry import LineString
        from shapely.prepared import prep

        line = LineString([(0, 0), (100, 0)])
        buffer = prep(line.buffer(10.0))

        entrances = [(50, 5), (200, 0)]  # first inside, second outside
        bus_stops = [(10, -3)]  # inside
        fire_points = [(80, 0), (300, 300)]  # first inside, second outside

        counts = _count_pois_in_buffer(buffer, entrances, bus_stops, fire_points)
        assert counts["entrance"] == 1
        assert counts["bus_stop"] == 1
        assert counts["fire_hydrant"] == 1

    def test_empty_pois(self):
        pytest.importorskip("shapely")
        from shapely.geometry import LineString
        from shapely.prepared import prep

        line = LineString([(0, 0), (100, 0)])
        buffer = prep(line.buffer(10.0))

        counts = _count_pois_in_buffer(buffer, [], [], [])
        assert counts["entrance"] == 0
        assert counts["bus_stop"] == 0
        assert counts["fire_hydrant"] == 0


# ---------------------------------------------------------------------------
# discover_poi_roads (mocked fetch)
# ---------------------------------------------------------------------------

class TestDiscoverPoiRoads:
    @pytest.fixture()
    def city(self):
        return _FakeCity("test_city", (116.390, 39.899, 116.392, 39.901))

    def _patch_fetch(self, response_data):
        return patch(
            "roadgen3d.osm_ingest.fetch_osm_data",
            return_value=response_data,
        )

    def test_long_road_with_pois_discovered(self, city, tmp_path):
        pytest.importorskip("pyproj")
        with self._patch_fetch(_LONG_ROAD_WITH_POIS):
            results = discover_poi_roads(city, tmp_path)
        assert len(results) == 1
        assert results[0].osm_id == 1001
        assert results[0].poi_count == 3
        assert results[0].poi_score == pytest.approx(3.8)
        assert results[0].core_poi_count == 3
        assert results[0].poi_types["entrance"] == 1
        assert results[0].poi_types["bus_stop"] == 1
        assert results[0].poi_types["fire_hydrant"] == 1
        assert results[0].road_length_m > 100.0

    def test_short_road_excluded(self, city, tmp_path):
        pytest.importorskip("pyproj")
        with self._patch_fetch(_SHORT_ROAD_WITH_POIS):
            results = discover_poi_roads(city, tmp_path)
        assert len(results) == 0

    def test_no_pois_excluded(self, city, tmp_path):
        pytest.importorskip("pyproj")
        with self._patch_fetch(_LONG_ROAD_NO_POIS):
            results = discover_poi_roads(city, tmp_path)
        assert len(results) == 0

    def test_one_poi_excluded(self, city, tmp_path):
        """Road with only 1 POI should not qualify (min=2)."""
        pytest.importorskip("pyproj")
        response = _make_overpass_response(
            roads=[{
                "id": 1004,
                "highway": "residential",
                "coords": [(116.390, 39.900), (116.391, 39.900), (116.392, 39.900)],
            }],
            pois=[
                {"id": 2020, "lon": 116.3905, "lat": 39.9001, "tags": {"entrance": "yes"}},
            ],
        )
        with self._patch_fetch(response):
            results = discover_poi_roads(city, tmp_path)
        assert len(results) == 0

    def test_custom_thresholds(self, city, tmp_path):
        """Lowering min_road_length_m should allow short roads through."""
        pytest.importorskip("pyproj")
        with self._patch_fetch(_SHORT_ROAD_WITH_POIS):
            results = discover_poi_roads(city, tmp_path, min_road_length_m=50.0)
        assert len(results) == 1
        assert results[0].osm_id == 1002
        assert results[0].poi_count == 5

    def test_bbox_is_wgs84(self, city, tmp_path):
        """Discovered road bbox should be in WGS-84 coordinate range."""
        pytest.importorskip("pyproj")
        with self._patch_fetch(_LONG_ROAD_WITH_POIS):
            results = discover_poi_roads(city, tmp_path)
        assert len(results) == 1
        bbox = results[0].bbox
        # Should be near Beijing coordinates
        assert 116.0 < bbox[0] < 117.0
        assert 39.0 < bbox[1] < 40.0
        assert bbox[0] < bbox[2]  # min_lon < max_lon
        assert bbox[1] < bbox[3]  # min_lat < max_lat


# ---------------------------------------------------------------------------
# discover_all_cities (deduplication)
# ---------------------------------------------------------------------------

class TestDiscoverAllCities:
    def test_dedup_by_osm_id(self, tmp_path):
        """Same road discovered from two cities should appear only once."""
        pytest.importorskip("pyproj")
        city_a = _FakeCity("city_a", (116.390, 39.899, 116.392, 39.901))
        city_b = _FakeCity("city_b", (116.390, 39.899, 116.392, 39.901))

        with patch(
            "roadgen3d.osm_ingest.fetch_osm_data",
            return_value=_LONG_ROAD_WITH_POIS,
        ):
            results = discover_all_cities([city_a, city_b], tmp_path)

        osm_ids = [r.osm_id for r in results]
        assert len(osm_ids) == len(set(osm_ids)), "Duplicate osm_ids found"


# ---------------------------------------------------------------------------
# JSONL compatibility
# ---------------------------------------------------------------------------

class TestJsonlCompat:
    def test_roundtrip_with_load_bboxes(self, tmp_path):
        """Written JSONL should be readable by m6_01's _load_bboxes()."""
        road = DiscoveredRoad(
            city_name_en="beijing",
            osm_id=12345,
            highway_type="primary",
            road_length_m=156.3,
            poi_count=4,
            poi_types={"entrance": 2, "bus_stop": 1, "fire_hydrant": 1},
            bbox=(116.3900, 39.8990, 116.3920, 39.9010),
        )
        out = tmp_path / "test.jsonl"
        write_discovered_roads_jsonl([road], out)

        # Read back using the same logic as m6_01's _load_bboxes()
        bboxes = []
        for line in out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            bbox = tuple(float(v) for v in payload["bbox"])
            if len(bbox) == 4:
                bboxes.append(bbox)

        assert len(bboxes) == 1
        assert bboxes[0] == pytest.approx(road.bbox)

    def test_metadata_preserved(self, tmp_path):
        road = DiscoveredRoad(
            city_name_en="shanghai",
            osm_id=99999,
            highway_type="tertiary",
            road_length_m=200.0,
            poi_count=5,
            poi_types={"entrance": 3, "bus_stop": 1, "fire_hydrant": 1},
            bbox=(121.469, 31.228, 121.474, 31.232),
        )
        out = tmp_path / "meta.jsonl"
        write_discovered_roads_jsonl([road], out)

        record = json.loads(out.read_text(encoding="utf-8").strip())
        assert record["city"] == "shanghai"
        assert record["osm_id"] == 99999
        assert record["highway_type"] == "tertiary"
        assert record["road_length_m"] == 200.0
        assert record["poi_count"] == 5
        assert record["poi_score"] == pytest.approx(5.8)
        assert record["core_poi_count"] == 5
        assert record["poi_types"]["entrance"] == 3

    def test_non_core_only_pois_do_not_qualify(self, tmp_path):
        pytest.importorskip("pyproj")
        city = _FakeCity("test_city", (116.390, 39.899, 116.392, 39.901))
        response = _make_overpass_response(
            roads=[{
                "id": 1100,
                "highway": "residential",
                "coords": [(116.390, 39.900), (116.391, 39.900), (116.392, 39.900)],
            }],
            pois=[
                {"id": 2100, "lon": 116.3905, "lat": 39.9001, "tags": {"amenity": "post_box"}},
                {"id": 2101, "lon": 116.3908, "lat": 39.9001, "tags": {"amenity": "waste_basket"}},
                {"id": 2102, "lon": 116.3911, "lat": 39.9001, "tags": {"barrier": "bollard"}},
                {"id": 2103, "lon": 116.3914, "lat": 39.9001, "tags": {"barrier": "bollard"}},
                {"id": 2104, "lon": 116.3917, "lat": 39.9001, "tags": {"barrier": "bollard"}},
            ],
        )
        with patch("roadgen3d.osm_ingest.fetch_osm_data", return_value=response):
            results = discover_poi_roads(city, tmp_path)
        assert results == []
