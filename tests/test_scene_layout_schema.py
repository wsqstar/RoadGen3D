from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.scene_layout_payload import SCENE_LAYOUT_SCHEMA_VERSION, build_scene_layout_payload  # noqa: E402


def _minimal_environment_state() -> dict[str, object]:
    return {
        "weather_mode": "clear",
        "weather_intensity": 0.0,
        "time_of_day_hours": 14.0,
        "sun_cycle_enabled": False,
        "sun_cycle_speed": "medium",
        "source": "default_runtime",
    }


def _minimal_scene_layout_payload() -> dict[str, object]:
    environment_state = _minimal_environment_state()
    semantic_design_layers = {
        "schema_version": "roadgen3d_semantic_design_layers_v1",
        "skeleton_design_profile": "quiet_residential",
        "street_furniture_profile": "balanced_complete",
        "profile_pair": "quiet_residential+balanced_complete",
        "resolution_order": ["manual", "llm", "osm_poi"],
    }
    return {
        "schema_version": SCENE_LAYOUT_SCHEMA_VERSION,
        "query": "schema smoke",
        "config": {},
        "street_program": {},
        "constraint_set": {},
        "solver": {},
        "summary": {
            "semantic_design_layers": semantic_design_layers,
            "environment_system": {
                "layer": "environment_runtime_v1",
                "weather_modes": ["clear", "overcast", "rain", "fog"],
                "sun_model": "artistic_day_cycle",
                "runtime_only": True,
                "environment_state": environment_state,
            },
            "osm_semantic_mode": "landuse_rules_v1",
            "semantic_block_count": 0,
            "segment_semantic_profile_counts": {},
        },
        "semantic_design_layers": semantic_design_layers,
        "environment_state": environment_state,
        "osm_semantic_blocks": [],
        "segment_semantic_profiles": [],
        "visual_style": {
            "preset": "civic_clean_v1",
            "lighting_preset": "bright_day",
            "surface_palette": {},
            "surface_roughness": {},
        },
        "placements": [],
        "production_steps": [],
        "outputs": {},
    }


def _fallback_validate_required(schema: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    for key in schema["required"]:
        assert key in payload
    assert payload["schema_version"] == schema["properties"]["schema_version"]["const"]
    environment_state = payload["environment_state"]
    assert isinstance(environment_state, Mapping)
    for key in schema["$defs"]["environment_state"]["required"]:
        assert key in environment_state


def test_scene_layout_schema_matches_exported_version() -> None:
    schema_path = ROOT / "data" / "schemas" / "scene_layout.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["properties"]["schema_version"]["const"] == SCENE_LAYOUT_SCHEMA_VERSION


def test_minimal_scene_layout_payload_matches_v1_schema() -> None:
    schema_path = ROOT / "data" / "schemas" / "scene_layout.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    payload = _minimal_scene_layout_payload()

    try:
        import jsonschema  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        _fallback_validate_required(schema, payload)
    else:
        jsonschema.Draft202012Validator(schema).validate(payload)


def test_scene_layout_payload_replaces_non_finite_values() -> None:
    payload = build_scene_layout_payload(
        query="strict json",
        config={},
        selected_object_backend="test",
        ground_selection={},
        sky_selection={},
        environment_source_dataset="",
        environment_source_datasets=[],
        program_result={},
        theme_zone_programs=[],
        resolved_program={},
        constraint_set={},
        solver_result=SimpleNamespace(road_segment_graph_summary={}),
        summary={"max_distance_m": float("inf")},
        semantic_design_layers={},
        environment_state=_minimal_environment_state(),
        osm_semantic_blocks=[],
        segment_semantic_profiles=[],
        visual_style={},
        placements=[
            {
                "category": "tree",
                "dist_to_nearest_entrance_m": float("inf"),
                "bbox_xz": [0.0, 1.0, float("-inf"), float("nan")],
            }
        ],
        environment_placements=[],
        building_footprints=[],
        generated_lots=[],
        building_placements=[],
        building_retrieval_predictions=[],
        zoning_grid=[],
        placement_context=SimpleNamespace(),
        production_steps=[],
        unplaced_slot_diagnostics=[],
        placement_log_path="",
        placement_log_summary={},
        outputs={},
        inventory_summary={},
    )

    json.dumps(payload, allow_nan=False)
    assert payload["summary"]["max_distance_m"] is None
    placement = payload["placements"][0]
    assert placement["dist_to_nearest_entrance_m"] is None
    assert placement["bbox_xz"] == [0.0, 1.0, None, None]
