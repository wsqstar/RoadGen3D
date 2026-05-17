from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.semantic_design_layers import (  # noqa: E402
    apply_street_furniture_profile_defaults,
    resolve_semantic_design_layers,
)
from roadgen3d.types import RoadSegmentGraph, RoadSegmentNode, StreetComposeConfig  # noqa: E402


def _config(**overrides: object) -> StreetComposeConfig:
    base = {
        "query": "semantic design layer test",
        "length_m": 80.0,
        "road_width_m": 10.0,
        "sidewalk_width_m": 2.5,
        "lane_count": 2,
        "density": 0.6,
        "seed": 1,
        "topk_per_category": 3,
        "max_trials_per_slot": 4,
        "layout_mode": "template",
        "constraint_mode": "off",
    }
    base.update(overrides)
    return StreetComposeConfig(**base)


def _node(
    segment_id: str,
    profile: str,
    *,
    source: str = "osm",
    length_m: float = 20.0,
) -> RoadSegmentNode:
    return RoadSegmentNode(
        segment_id=segment_id,
        road_id=1,
        start_xy=(0.0, 0.0),
        end_xy=(length_m, 0.0),
        center_xy=(length_m / 2.0, 0.0),
        length_m=length_m,
        semantic_profile_id=profile,
        semantic_confidence=0.7,
        semantic_reasons=("test_profile",),
        skeleton_design_profile=profile,
        skeleton_design_profile_source=source,
        skeleton_design_profile_confidence=0.7,
        skeleton_design_profile_reasons=("test_profile",),
    )


def test_osm_profile_maps_to_skeleton_design_layer_and_recommended_furniture():
    graph = RoadSegmentGraph(
        nodes=(
            _node("s1", "walkable_commercial", length_m=30.0),
            _node("s2", "walkable_commercial", length_m=30.0),
        ),
        edges=(),
        mode="osm_multiblock",
    )

    layers = resolve_semantic_design_layers(config=_config(), road_segment_graph=graph)

    assert layers["skeleton_design_profile"] == "walkable_commercial"
    assert layers["skeleton_design_profile_source"] == "osm"
    assert layers["street_furniture_profile"] == "commercial_vitality"
    assert layers["street_furniture_profile_source"] == "recommended"
    assert layers["profile_pair"] == "walkable_commercial+commercial_vitality"


def test_manual_skeleton_overrides_llm_and_osm_candidates():
    graph = RoadSegmentGraph(
        nodes=(_node("s1", "quiet_residential", length_m=120.0),),
        edges=(),
        mode="osm_multiblock",
    )
    config = _config(
        skeleton_design_profile="child_friendly_school",
        skeleton_design_profile_source="manual",
        skeleton_design_profile_confidence=1.0,
        skeleton_design_profile_reasons=("annotated_by_user",),
    )

    layers = resolve_semantic_design_layers(config=config, road_segment_graph=graph)

    assert layers["skeleton_design_profile"] == "child_friendly_school"
    assert layers["skeleton_design_profile_source"] == "manual"
    osm_candidate = next(
        item for item in layers["candidates"]["skeleton_design"]
        if item["source"] == "osm"
    )
    assert osm_candidate["overridden_by"] == "manual"


def test_manual_surface_or_zone_annotation_overrides_osm_candidates():
    graph = RoadSegmentGraph(
        nodes=(_node("s1", "quiet_residential", length_m=120.0),),
        edges=(),
        mode="osm_multiblock",
    )
    layers = resolve_semantic_design_layers(
        config=_config(),
        road_segment_graph=graph,
        annotation_records=[
            {
                "skeleton_design_profile": "green_walkable",
                "skeleton_design_profile_source": "manual",
                "skeleton_design_profile_confidence": 0.95,
                "skeleton_design_profile_reasons": ["surface_annotation"],
                "area_m2": 24.0,
            }
        ],
    )

    assert layers["skeleton_design_profile"] == "green_walkable"
    assert layers["skeleton_design_profile_source"] == "manual"
    assert layers["street_furniture_profile"] == "park_landscape"


def test_llm_skeleton_overrides_osm_when_manual_absent():
    graph = RoadSegmentGraph(
        nodes=(_node("s1", "quiet_residential", length_m=120.0),),
        edges=(),
        mode="osm_multiblock",
    )
    config = _config(
        skeleton_design_profile="transit_priority",
        skeleton_design_profile_source="llm",
        skeleton_design_profile_confidence=0.82,
    )

    layers = resolve_semantic_design_layers(config=config, road_segment_graph=graph)

    assert layers["skeleton_design_profile"] == "transit_priority"
    assert layers["skeleton_design_profile_source"] == "llm"
    assert layers["street_furniture_profile"] == "transit_priority"


def test_street_furniture_defaults_use_skeleton_recommendation_then_fallback():
    recommended = apply_street_furniture_profile_defaults({
        "skeleton_design_profile": "green_walkable",
    })
    assert recommended["street_furniture_profile"] == "park_landscape"
    assert recommended["objective_profile"] == "greening"

    fallback = resolve_semantic_design_layers(config=_config(), road_segment_graph=None)
    assert fallback["street_furniture_profile"] == "balanced_complete"
    assert fallback["street_furniture_profile_source"] == "recommended"


def test_street_furniture_none_defaults_disable_asset_generation():
    patch = apply_street_furniture_profile_defaults({
        "street_furniture_profile": "none",
    })

    assert patch["street_furniture_profile"] == "none"
    assert patch["amenity_coverage_mode"] == "off"
    assert patch["curated_street_assets_profile"] == "disabled"
    assert patch["minimum_category_presence"] == ()
    assert patch["optional_category_presence"] == ()
