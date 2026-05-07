from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.graph_template_scene_bridge import build_graph_template_scene_bridge  # noqa: E402
from roadgen3d.graph_templates import load_graph_template_annotation_payload  # noqa: E402
from roadgen3d.llm.design_workflow import parse_design_draft  # noqa: E402
from roadgen3d.reference_annotation import (  # noqa: E402
    build_reference_annotation_compose_config,
    parse_reference_annotation,
)
from roadgen3d.services.design_types import sanitize_scene_context  # noqa: E402
from roadgen3d.template_patch import (  # noqa: E402
    TEMPLATE_PATCH_SCHEMA_VERSION,
    TemplatePatchError,
    apply_template_patch,
)


def _pedestrian_priority_patch() -> dict:
    return {
        "schema_version": TEMPLATE_PATCH_SCHEMA_VERSION,
        "variant_id": "pedestrian_priority_demo",
        "description": "Reduce the HKUST-GZ template to one drive lane per direction and widen clear sidewalks.",
        "operations": [
            {"op": "remove_strip", "all_centerlines": True, "strip_id": "center_02"},
            {"op": "remove_strip", "all_centerlines": True, "strip_id": "center_05"},
            {"op": "resize_strip", "all_centerlines": True, "strip_id": "center_01", "width_m": 3.0},
            {"op": "resize_strip", "all_centerlines": True, "strip_id": "center_04", "width_m": 3.0},
            {"op": "resize_strip", "all_centerlines": True, "strip_id": "left_02", "width_m": 4.0},
            {"op": "resize_strip", "all_centerlines": True, "strip_id": "right_02", "width_m": 4.0},
        ],
    }


def test_template_patch_schema_file_is_valid_json():
    schema_path = ROOT / "data" / "schemas" / "template_patch.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["properties"]["schema_version"]["const"] == TEMPLATE_PATCH_SCHEMA_VERSION
    assert "operations" in schema["required"]


def test_apply_template_patch_makes_pedestrian_priority_variant():
    base_payload = load_graph_template_annotation_payload("hkust_gz_gate")

    application = apply_template_patch(base_payload, _pedestrian_priority_patch())
    annotation = parse_reference_annotation(application.annotation)

    assert application.summary["variant_id"] == "pedestrian_priority_demo"
    assert application.summary["operation_count"] == 6
    assert application.summary["applied_operation_count"] == 60
    assert parse_reference_annotation(base_payload).centerlines[0].lane_profile()["total_drive_lane_count"] == 4

    first_centerline = annotation.centerlines[0]
    assert first_centerline.lane_profile()["total_drive_lane_count"] == 2
    assert first_centerline.lane_profile()["forward_drive_lane_count"] == 1
    assert first_centerline.lane_profile()["reverse_drive_lane_count"] == 1
    assert first_centerline.cross_section_width_m() == pytest.approx(21.3)
    assert {strip.strip_id for strip in first_centerline.cross_section_strips}.isdisjoint({"center_02", "center_05"})


def test_template_patch_can_add_bus_lane_and_plaza():
    base_payload = load_graph_template_annotation_payload("hkust_gz_gate")
    patch = {
        "schema_version": TEMPLATE_PATCH_SCHEMA_VERSION,
        "variant_id": "bus_plaza_demo",
        "operations": [
            {
                "op": "add_strip",
                "centerline_id": "centerline_04",
                "after_strip_id": "center_03",
                "strip": {
                    "strip_id": "center_bus_01",
                    "zone": "center",
                    "kind": "bus_lane",
                    "width_m": 3.5,
                    "direction": "forward",
                },
            },
            {
                "op": "add_functional_zone",
                "zone": {
                    "id": "entry_plaza_01",
                    "label": "Entry Plaza",
                    "kind": "plaza",
                    "points": [
                        {"x": 430, "y": 350},
                        {"x": 520, "y": 330},
                        {"x": 575, "y": 390},
                        {"x": 460, "y": 430},
                    ],
                    "furniture_instances": [
                        {"instance_id": "plaza_tree_01", "kind": "tree", "x_px": 485, "y_px": 378}
                    ],
                },
            },
        ],
    }

    application = apply_template_patch(base_payload, patch)
    annotation = parse_reference_annotation(application.annotation)
    centerline = next(item for item in annotation.centerlines if item.feature_id == "centerline_04")

    assert centerline.lane_profile()["bus_lane_count"] == 1
    assert any(strip.strip_id == "center_bus_01" and strip.direction == "forward" for strip in centerline.cross_section_strips)
    assert len(annotation.functional_zones) == 1
    assert annotation.functional_zones[0].kind == "plaza"
    assert annotation.functional_zones[0].furniture_instances[0].kind == "tree"


def test_template_patch_can_upsert_surface_annotation():
    base_payload = load_graph_template_annotation_payload("hkust_gz_gate")
    patch = {
        "schema_version": TEMPLATE_PATCH_SCHEMA_VERSION,
        "variant_id": "surface_annotation_demo",
        "operations": [
            {
                "op": "upsert_surface_annotation",
                "surface": {
                    "id": "surface_bus_lane_001",
                    "label": "临时公交车道拓宽",
                    "kind": "bus_lane_widening",
                    "surface_role": "bus_lane",
                    "centerline_id": "centerline_04",
                    "station_start_m": 24.0,
                    "station_end_m": 86.0,
                    "lateral_start_m": 3.5,
                    "lateral_end_m": 7.0,
                    "material": {"preset": "bus_lane_green"},
                },
            },
        ],
    }

    application = apply_template_patch(base_payload, patch)
    annotation = parse_reference_annotation(application.annotation)

    assert application.summary["surface_annotation_count"] == 1
    assert len(annotation.surface_annotations) == 1
    surface = annotation.surface_annotations[0]
    assert surface.feature_id == "surface_bus_lane_001"
    assert surface.surface_role == "bus_lane"
    assert surface.centerline_id == "centerline_04"
    assert surface.material.preset == "bus_lane_green"


def test_template_patch_can_upsert_station_strip_patch():
    base_payload = load_graph_template_annotation_payload("hkust_gz_gate")
    patch = {
        "schema_version": TEMPLATE_PATCH_SCHEMA_VERSION,
        "variant_id": "station_strip_patch_demo",
        "operations": [
            {
                "op": "upsert_station_strip_patch",
                "patch": {
                    "id": "local_tree_island_001",
                    "label": "Local tree safety island",
                    "centerline_id": "centerline_04",
                    "strip_id": "center_03",
                    "station_start_m": 40.0,
                    "station_end_m": 41.0,
                    "updates": {"kind": "grass_belt", "width_m": 1.0, "direction": "none"},
                },
            },
        ],
    }

    application = apply_template_patch(base_payload, patch)
    annotation = parse_reference_annotation(application.annotation)

    assert application.summary["station_strip_patch_count"] == 1
    assert len(annotation.station_strip_patches) == 1
    local_patch = annotation.station_strip_patches[0]
    assert local_patch.feature_id == "local_tree_island_001"
    assert local_patch.centerline_id == "centerline_04"
    assert local_patch.strip_id == "center_03"
    assert local_patch.kind == "grass_belt"
    assert local_patch.width_m == pytest.approx(1.0)


def test_template_patch_enforces_bidirectional_drive_lane_constraints():
    base_payload = load_graph_template_annotation_payload("hkust_gz_gate")
    patch = {
        "schema_version": TEMPLATE_PATCH_SCHEMA_VERSION,
        "operations": [
            {"op": "remove_strip", "centerline_id": "centerline_04", "strip_id": "center_04"},
            {"op": "remove_strip", "centerline_id": "centerline_04", "strip_id": "center_05"},
        ],
    }

    with pytest.raises(TemplatePatchError, match="one drive lane in each direction"):
        apply_template_patch(base_payload, patch)


def test_design_draft_and_scene_context_preserve_template_patch():
    template_patch = _pedestrian_priority_patch()
    draft = parse_design_draft(
        {
            "normalized_scene_query": "pedestrian friendly campus gateway",
            "compose_config_patch": {"sidewalk_width_m": 4.0, "template_patch": {"bad": "location"}},
            "template_patch": template_patch,
            "design_summary": "summary",
        },
        evidence=(),
        fallback_query="campus gateway",
    )
    scene_context = sanitize_scene_context({
        "layout_mode": "graph_template",
        "graph_template_id": "hkust_gz_gate",
        "template_patch": template_patch,
    })

    assert draft.template_patch == template_patch
    assert "template_patch" not in draft.compose_config_patch
    assert scene_context.template_patch == template_patch


def test_graph_template_bridge_uses_template_patch_summary():
    pytest.importorskip("shapely")

    bridge = build_graph_template_scene_bridge(
        build_reference_annotation_compose_config({"segment_length_m": 9.0}),
        template_id="hkust_gz_gate",
        template_patch=_pedestrian_priority_patch(),
    )

    assert bridge.annotation.centerlines[0].lane_profile()["total_drive_lane_count"] == 2
    assert bridge.summary_metadata["template_patch"]["variant_id"] == "pedestrian_priority_demo"
