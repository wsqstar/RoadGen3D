"""Tests for configured OSM semantic-preview artifact generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import osm_semantic_preview


def _config(tmp_path: Path, *, output_path: Path | None = None, cache_dir: Path | None = None) -> Path:
    path = tmp_path / "demo.json"
    data = {
        "demo_id": "test_demo",
        "name": "Test OSM demo",
        "address": "No.1 Demo Road",
        "address_source_url": "https://example.test/address",
        "center": {"lat": 22.0, "lon": 113.0},
        "bbox": [113.0, 22.0, 113.001, 22.001],
        "layout_mode": "osm_multiblock",
        "osm_cache_dir": str(cache_dir or (tmp_path / "osm_cache")),
        "output_path": str(output_path or (tmp_path / "semantic_preview.json")),
        "compose_config": {
            "segment_length_m": 35,
            "osm_multiblock_max_roads": 12,
            "osm_multiblock_max_extent_m": 350,
            "osm_short_road_policy": "default_style",
            "osm_short_road_min_length_m": 20,
            "osm_context_fit_mode": "auto_design",
            "bus_stop_eligible_road_names": ["笃学路"],
            "max_bus_stops_per_scene": 1,
            "allow_demo_bus_stop_when_osm_absent": True,
            "road_width_m": 7,
            "sidewalk_width_m": 2.4,
            "lane_count": 2,
            "seed": 42,
        },
        "quality_gate": {
            "min_road_count": 2,
            "require_segment_semantic_profiles": True,
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _preview_payload() -> dict[str, object]:
    return {
        "semantic_mode": "landuse_rules_v1",
        "aoi_bbox": [113.0, 22.0, 113.001, 22.001],
        "osm_cache_dir": "/tmp/cache",
        "input": {
            "road_count": 3,
            "building_count": 1,
            "land_use_polygon_count": 2,
        },
        "summary": {
            "selected_road_count": 2,
            "semantic_block_count": 2,
            "semantic_profile_counts": {"walkable_commercial": 1, "green_walkable": 1},
        },
        "selected_roads": [{"osm_id": 101, "highway_type": "residential", "point_count": 2}],
        "short_roads_default_style": [{"osm_id": 102, "length_m": 11.0}],
        "bus_stop_counts": {"osm": 0, "demo_inferred": 1, "total": 1, "raw_osm": 0},
        "bus_stop_eligible_road_ids": [101],
        "bus_stop_provenance": [{"source": "demo_inferred", "road_id": 101}],
        "osm_context_fit": {
            "ruleset": "socioeconomic_fit_v1",
            "dominant_design_direction": "child_safety_upgrade",
            "scene_recommended_compose_patch": {"design_rule_profile": "pedestrian_priority_v1"},
        },
        "osm_semantic_blocks": [
            {"block_id": "block_1", "semantic_profile_id": "walkable_commercial"},
            {"block_id": "block_2", "semantic_profile_id": "green_walkable"},
        ],
        "segment_semantic_profiles": [
            {"segment_id": "101:0", "semantic_profile_id": "walkable_commercial"},
            {"segment_id": "101:1", "semantic_profile_id": "walkable_commercial"},
            {"segment_id": "102:0", "semantic_profile_id": "green_walkable"},
        ],
        "road_segment_graph_summary": {"node_count": 3},
    }


def test_configured_cli_writes_commit_ready_semantic_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    output_path = tmp_path / "out" / "preview.json"
    cache_dir = tmp_path / "cache"
    config_path = _config(tmp_path, output_path=output_path, cache_dir=cache_dir)
    calls: dict[str, object] = {}

    def fake_preview(**kwargs: object) -> dict[str, object]:
        calls.update(kwargs)
        return _preview_payload()

    monkeypatch.setattr(osm_semantic_preview, "build_osm_semantic_preview", fake_preview)

    payload = osm_semantic_preview.generate_semantic_preview_from_config(config_path)

    assert output_path.exists()
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == written
    assert calls["aoi_bbox"] == (113.0, 22.0, 113.001, 22.001)
    assert calls["osm_cache_dir"] == cache_dir.resolve()
    assert written["schema_version"] == "roadgen3d_osm_semantic_preview_v1"
    assert written["compose_config"]["segment_length_m"] == 35.0
    assert written["compose_config"]["osm_context_fit_mode"] == "auto_design"
    assert written["compose_config"]["bus_stop_eligible_road_names"] == ["笃学路"]
    assert written["compose_config"]["allow_demo_bus_stop_when_osm_absent"] is True
    assert written["road_count"] == 3
    assert written["land_use_polygon_count"] == 2
    assert written["semantic_block_count"] == 2
    assert written["segment_semantic_profile_counts"] == {
        "green_walkable": 1,
        "walkable_commercial": 2,
    }
    assert written["summary"]["segment_semantic_profile_counts"]["walkable_commercial"] == 2
    assert written["bus_stop_counts"]["demo_inferred"] == 1
    assert written["bus_stop_provenance"][0]["source"] == "demo_inferred"
    assert written["osm_context_fit"]["dominant_design_direction"] == "child_safety_upgrade"
    assert written["short_roads_default_style"][0]["osm_id"] == 102
    assert written["osm_semantic_blocks"]
    assert written["segment_semantic_profiles"]


def test_configured_cli_rejects_sparse_osm_preview(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config_path = _config(tmp_path)
    weak_preview = _preview_payload()
    weak_preview["input"] = {"road_count": 1, "building_count": 0, "land_use_polygon_count": 0}
    weak_preview["segment_semantic_profiles"] = []
    monkeypatch.setattr(osm_semantic_preview, "build_osm_semantic_preview", lambda **_kwargs: weak_preview)

    with pytest.raises(osm_semantic_preview.SemanticPreviewQualityError):
        osm_semantic_preview.generate_semantic_preview_from_config(config_path)
