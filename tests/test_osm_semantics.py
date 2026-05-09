"""Tests for OSM multiblock semantic context."""

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

from roadgen3d.osm_ingest import OsmRoad, OsmSemanticBlock, ProjectedFeatures
from roadgen3d.osm_segment_graph import build_segment_graph
from roadgen3d.osm_semantics import (
    apply_osm_bus_stop_constraints,
    classify_semantic_block,
    evaluate_osm_context_fit,
    prepare_multiblock_projected_features,
    semantic_profile_for_segment,
)
from roadgen3d.services.scene_context_service import build_osm_semantic_preview, resolve_scene_context
from roadgen3d.theme_buildings import infer_theme_segments
from roadgen3d.types import StreetComposeConfig


def _block(
    block_id: str,
    tags: dict[str, str],
    *,
    x0: float = -10.0,
    y0: float = -10.0,
    x1: float = 10.0,
    y1: float = 10.0,
) -> OsmSemanticBlock:
    coords = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return OsmSemanticBlock(
        block_id=block_id,
        osm_id=1,
        source_type="way",
        coords=coords,
        centroid=((x0 + x1) / 2.0, (y0 + y1) / 2.0),
        tags=tags,
    )


def _config(**overrides: object) -> StreetComposeConfig:
    base = {
        "query": "osm multiblock semantic test",
        "length_m": 80.0,
        "road_width_m": 7.0,
        "sidewalk_width_m": 2.4,
        "lane_count": 2,
        "density": 1.0,
        "seed": 42,
        "topk_per_category": 4,
        "max_trials_per_slot": 6,
        "layout_mode": "osm_multiblock",
        "constraint_mode": "off",
        "aoi_bbox": (116.39, 39.90, 116.395, 39.905),
        "road_selection": "all",
    }
    base.update(overrides)
    return StreetComposeConfig(**base)


def test_landuse_rules_classify_core_profiles():
    school = classify_semantic_block(_block("school", {"amenity": "kindergarten"}))
    assert school.semantic_profile_id == "child_friendly_school"

    commercial = classify_semantic_block(
        _block("commercial", {"landuse": "commercial"}),
        semantic_points_by_type={"commercial": [(-1.0, 0.0), (1.0, 0.0)]},
    )
    assert commercial.semantic_profile_id == "walkable_commercial"

    sparse_vehicle = classify_semantic_block(
        _block("vehicle", {"landuse": "retail"}),
        semantic_points_by_type={"vehicle_access": [(0.0, 0.0)]},
    )
    assert sparse_vehicle.semantic_profile_id == "vehicle_access_commercial"

    green = classify_semantic_block(_block("green", {"leisure": "park"}))
    assert green.semantic_profile_id == "green_walkable"

    residential = classify_semantic_block(_block("residential", {"landuse": "residential"}))
    assert residential.semantic_profile_id == "quiet_residential"


def test_segment_rule_fallback_detects_vehicle_access_for_sparse_higher_order_road():
    profile_id, reasons, confidence, block_id = semantic_profile_for_segment(
        highway_type="secondary",
        poi_types=(),
        semantic_block=None,
    )

    assert profile_id == "vehicle_access_commercial"
    assert reasons
    assert confidence > 0.5
    assert block_id == ""


def test_prepare_multiblock_projected_features_keeps_multiple_roads_and_classifies_blocks():
    projected = ProjectedFeatures(
        roads=[
            OsmRoad(osm_id=101, highway_type="residential", coords=[(-30.0, 0.0), (0.0, 0.0)], width_m=6.0),
            OsmRoad(osm_id=102, highway_type="service", coords=[(0.0, 0.0), (30.0, 0.0)], width_m=4.0),
            OsmRoad(osm_id=103, highway_type="tertiary", coords=[(0.0, 0.0), (0.0, 30.0)], width_m=7.0),
        ],
        semantic_blocks=[
            _block("school", {"amenity": "kindergarten"}, x0=-35.0, y0=-15.0, x1=-5.0, y1=15.0),
            _block("retail", {"landuse": "commercial"}, x0=5.0, y0=-15.0, x1=35.0, y1=15.0),
        ],
        semantic_points_by_type={"commercial": [(15.0, 0.0), (20.0, 1.0)]},
        bbox_m=(-45.0, -30.0, 45.0, 45.0),
    )

    prepared, summary = prepare_multiblock_projected_features(projected, _config(osm_multiblock_max_roads=3))

    assert len(prepared.roads) == 3
    assert summary["selected_road_count"] == 3
    assert summary["semantic_block_count"] == 2
    assert {block.semantic_profile_id for block in prepared.semantic_blocks} >= {
        "child_friendly_school",
        "walkable_commercial",
    }


def test_segment_graph_and_theme_segments_carry_semantic_profiles():
    projected = ProjectedFeatures(
        roads=[OsmRoad(osm_id=101, highway_type="residential", coords=[(-20.0, 0.0), (20.0, 0.0)], width_m=6.0)],
        semantic_blocks=[
            classify_semantic_block(
                _block("school", {"amenity": "school"}, x0=-25.0, y0=-10.0, x1=25.0, y1=10.0)
            )
        ],
        bbox_m=(-30.0, -15.0, 30.0, 15.0),
    )
    graph = build_segment_graph(projected, _config(segment_length_m=20.0))
    segments = infer_theme_segments(graph, query="", target_street_type="")

    assert graph.mode == "osm_multiblock"
    assert graph.nodes
    assert {node.semantic_profile_id for node in graph.nodes} == {"child_friendly_school"}
    assert segments
    assert segments[0].theme_name == "green"
    assert "child_friendly_school" in segments[0].semantic_profile_ids
    assert segments[0].design_rule_profile == "pedestrian_priority_v1"


def test_osm_multiblock_resamples_whole_polyline_instead_of_each_osm_edge():
    coords = [(float(idx) * 10.0, 0.0) for idx in range(23)]
    projected = ProjectedFeatures(
        roads=[OsmRoad(osm_id=701, highway_type="service", coords=coords, width_m=4.0)],
        semantic_blocks=[
            classify_semantic_block(_block("school", {"amenity": "school"}, x0=-5.0, y0=-10.0, x1=225.0, y1=10.0))
        ],
        bbox_m=(-5.0, -10.0, 225.0, 10.0),
    )

    graph = build_segment_graph(projected, _config(segment_length_m=35.0))

    assert len(graph.nodes) == 7
    assert 30.0 <= graph.summary()["avg_segment_length_m"] <= 32.0
    assert {node.semantic_profile_id for node in graph.nodes} == {"child_friendly_school"}


def test_osm_multiblock_short_roads_keep_default_style_without_poi_triggers():
    projected = ProjectedFeatures(
        roads=[
            OsmRoad(
                osm_id=702,
                highway_type="service",
                coords=[(0.0, 0.0), (11.0, 0.0)],
                width_m=4.0,
                tags={"name": "short access"},
            )
        ],
        semantic_blocks=[
            classify_semantic_block(_block("school", {"amenity": "school"}, x0=-5.0, y0=-10.0, x1=20.0, y1=10.0))
        ],
        poi_points_by_type={"bus_stop": [(5.5, 0.0)]},
        bbox_m=(-5.0, -10.0, 20.0, 10.0),
    )

    graph = build_segment_graph(
        projected,
        _config(
            segment_length_m=35.0,
            osm_short_road_policy="default_style",
            osm_short_road_min_length_m=20.0,
        ),
    )

    assert len(graph.nodes) == 1
    assert graph.nodes[0].semantic_profile_id == ""
    assert graph.nodes[0].semantic_reasons == ("short road rendered with default style",)
    assert graph.nodes[0].poi_types == ()


def test_osm_context_fit_detects_school_under_provision_and_recommends_child_safety():
    projected = ProjectedFeatures(
        roads=[OsmRoad(osm_id=711, highway_type="service", coords=[(0.0, 0.0), (90.0, 0.0)], width_m=4.0)],
        semantic_blocks=[
            classify_semantic_block(_block("school", {"amenity": "school"}, x0=-5.0, y0=-10.0, x1=95.0, y1=10.0))
        ],
        bbox_m=(-5.0, -10.0, 95.0, 10.0),
    )
    graph = build_segment_graph(projected, _config(segment_length_m=35.0, sidewalk_width_m=2.2))

    fit = evaluate_osm_context_fit(graph, _config(segment_length_m=35.0, sidewalk_width_m=2.2))

    assert fit["ruleset"] == "socioeconomic_fit_v1"
    assert fit["under_provisioned_segment_count"] == len(graph.nodes)
    assert fit["dominant_design_direction"] == "child_safety_upgrade"
    assert fit["scene_recommended_compose_patch"]["design_rule_profile"] == "pedestrian_priority_v1"
    assert fit["scene_recommended_compose_patch"]["sidewalk_width_m"] >= 3.2
    assert "crossing_or_traffic_signals" in fit["segments"][0]["missing_facilities"]


def test_osm_context_fit_keeps_sparse_commercial_vehicle_context_vehicle_oriented():
    projected = ProjectedFeatures(
        roads=[OsmRoad(osm_id=712, highway_type="tertiary", coords=[(0.0, 0.0), (70.0, 0.0)], width_m=7.0)],
        semantic_blocks=[
            classify_semantic_block(_block("retail", {"landuse": "retail"}, x0=-5.0, y0=-10.0, x1=75.0, y1=10.0))
        ],
        bbox_m=(-5.0, -10.0, 75.0, 10.0),
    )
    graph = build_segment_graph(projected, _config(segment_length_m=35.0, sidewalk_width_m=2.4))

    fit = evaluate_osm_context_fit(graph, _config(segment_length_m=35.0, sidewalk_width_m=2.4))

    assert {item["semantic_profile_id"] for item in fit["segments"]} == {"vehicle_access_commercial"}
    assert fit["dominant_design_direction"] == "vehicle_access_upgrade"
    assert fit["scene_recommended_compose_patch"]["vehicle_demand_level"] == "high"
    assert fit["scene_recommended_compose_patch"]["design_rule_profile"] == "balanced_complete_street_v1"


def test_demo_bus_stop_is_limited_to_eligible_du_xue_road():
    projected = ProjectedFeatures(
        roads=[
            OsmRoad(
                osm_id=801,
                highway_type="tertiary",
                coords=[(0.0, 0.0), (100.0, 0.0)],
                width_m=7.0,
                tags={"name": "笃学路"},
            ),
            OsmRoad(
                osm_id=802,
                highway_type="service",
                coords=[(0.0, 30.0), (100.0, 30.0)],
                width_m=4.0,
                tags={"name": "内三路"},
            ),
        ],
        semantic_blocks=[
            classify_semantic_block(_block("school", {"amenity": "school"}, x0=-5.0, y0=20.0, x1=105.0, y1=40.0))
        ],
        bbox_m=(-5.0, -5.0, 105.0, 45.0),
    )
    projected, summary = apply_osm_bus_stop_constraints(
        projected,
        _config(
            segment_length_m=35.0,
            bus_stop_eligible_road_names=("笃学路",),
            max_bus_stops_per_scene=1,
            allow_demo_bus_stop_when_osm_absent=True,
        ),
    )
    graph = build_segment_graph(projected, _config(segment_length_m=35.0))

    assert summary["counts"] == {"osm": 0, "demo_inferred": 1, "total": 1, "raw_osm": 0}
    assert summary["eligible_road_ids"] == [801]
    assert summary["provenance"][0]["source"] == "demo_inferred"
    assert len(projected.bus_stops) == 1
    assert any("bus_stop" in node.poi_types and node.road_id == 801 for node in graph.nodes)
    assert all("bus_stop" not in node.poi_types for node in graph.nodes if node.road_id == 802)


def test_resolve_scene_context_osm_multiblock_preserves_aoi_without_auto_discovery(monkeypatch, tmp_path: Path):
    import roadgen3d.services.scene_context_service as scene_context_service

    monkeypatch.setattr(
        scene_context_service,
        "select_auto_discovered_road",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("single-road auto discovery should not run")),
    )
    resolved = resolve_scene_context(
        {"layout_mode": "osm_multiblock", "aoi_bbox": [116.39, 39.90, 116.395, 39.905]},
        config=_config(osm_cache_dir=str(tmp_path / "osm_cache")),
        artifacts_dir=tmp_path,
    )

    assert resolved.scene_context.layout_mode == "osm_multiblock"
    assert resolved.effective_aoi_bbox == (116.39, 39.90, 116.395, 39.905)
    assert resolved.road_selection == "all"
    assert resolved.selected_road_osm_id is None
    assert resolved.selected_road_source == "multiblock_aoi"


def test_semantic_preview_returns_blocks_and_segment_profiles(monkeypatch, tmp_path: Path):
    pytest.importorskip("pyproj")
    import roadgen3d.services.scene_context_service as scene_context_service

    raw = {
        "elements": [
            {"type": "node", "id": 1, "lon": 116.3900, "lat": 39.9000},
            {"type": "node", "id": 2, "lon": 116.3910, "lat": 39.9000},
            {"type": "node", "id": 3, "lon": 116.3920, "lat": 39.9000},
            {"type": "node", "id": 10, "lon": 116.3900, "lat": 39.8997},
            {"type": "node", "id": 11, "lon": 116.3910, "lat": 39.8997},
            {"type": "node", "id": 12, "lon": 116.3925, "lat": 39.9003},
            {"type": "node", "id": 13, "lon": 116.3900, "lat": 39.9003},
            {"type": "way", "id": 100, "nodes": [1, 2], "tags": {"highway": "residential"}},
            {"type": "way", "id": 101, "nodes": [2, 3], "tags": {"highway": "service"}},
            {"type": "way", "id": 200, "nodes": [10, 11, 12, 13], "tags": {"amenity": "kindergarten"}},
            {"type": "node", "id": 30, "lon": 116.3905, "lat": 39.9000, "tags": {"amenity": "school"}},
        ]
    }
    monkeypatch.setattr(scene_context_service, "fetch_osm_data", lambda **_kwargs: raw)

    payload = build_osm_semantic_preview(
        aoi_bbox=(116.389, 39.899, 116.393, 39.901),
        osm_cache_dir=tmp_path / "osm_cache",
        compose_config_patch={"osm_multiblock_max_roads": 2},
    )

    assert payload["summary"]["selected_road_count"] == 2
    assert payload["summary"]["semantic_block_count"] == 1
    assert payload["osm_semantic_blocks"][0]["semantic_profile_id"] == "child_friendly_school"
    assert payload["segment_semantic_profiles"]
    assert "child_friendly_school" in {item["semantic_profile_id"] for item in payload["segment_semantic_profiles"]}
    assert payload["osm_context_fit"]["under_provisioned_segment_count"] >= 1
    assert payload["osm_context_fit"]["dominant_design_direction"] == "child_safety_upgrade"
    assert payload["summary"]["osm_context_fit"]["scene_recommended_compose_patch"]["design_rule_profile"] == "pedestrian_priority_v1"
