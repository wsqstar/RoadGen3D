from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.design_runtime import build_compose_config_from_draft
from roadgen3d.services.design_types import DesignDraft
from roadgen3d.services.street_design_parameters import (
    ParameterSpecError,
    build_street_design_parameter_spec,
    compile_street_design_parameter_spec,
    list_parameter_profiles,
)
from roadgen3d.street_priors import DEFAULT_CATEGORIES
from roadgen3d.street_program import infer_street_program
from web.api.route_utils import prepare_scene_generation_request
from web.api.schemas import SceneJobCreateRequestModel


def _draft() -> DesignDraft:
    return DesignDraft(
        normalized_scene_query="walkable complete street",
        compose_config_patch={},
        citations_by_field={},
        design_summary="deterministic test",
    )


def test_registry_exposes_seven_versioned_parametric_profiles():
    profiles = list_parameter_profiles()

    assert [item["profileId"] for item in profiles] == [
        "road_skeleton_none",
        "balanced_complete",
        "pedestrian_friendly",
        "commercial_vitality",
        "transit_priority",
        "park_landscape",
        "quiet_residential",
    ]
    assert all(item["buildings"]["footprintLocked"] is True for item in profiles)


def test_parameter_compiler_is_deterministic_and_forces_zero_retrieval_generation():
    spec = build_street_design_parameter_spec(
        "pedestrian_friendly",
        source_revision=7,
        source_fingerprint="source-abc",
    )

    first = compile_street_design_parameter_spec(spec)
    second = compile_street_design_parameter_spec(copy.deepcopy(spec))

    assert first.fingerprint == second.fingerprint
    assert first.compose_config_patch == second.compose_config_patch
    assert first.compose_config_patch["road_width_m"] == pytest.approx(6.0)
    assert first.compose_config_patch["furnishing_width_m"] == pytest.approx(1.4)
    assert first.compose_config_patch["building_representation"] == "transparent_massing"
    assert first.generation_options["generation_mode"] == "parametric"
    assert first.generation_options["skip_llm"] is True
    assert first.generation_options["derive_parameters_with_llm"] is False
    assert first.generation_options["knowledge_source"] == "none"


def test_parameter_compiler_rejects_geometry_edits_unknown_categories_and_paths():
    spec = build_street_design_parameter_spec(
        "balanced_complete",
        source_revision=1,
        source_fingerprint="source",
    )
    unlocked = copy.deepcopy(spec)
    unlocked["source"]["geometryLocked"] = False
    with pytest.raises(ParameterSpecError, match="locked"):
        compile_street_design_parameter_spec(unlocked)

    unknown = copy.deepcopy(spec)
    unknown["furniture"]["categories"]["fountain"] = {
        "enabled": True,
        "allowedZones": ["frontage"],
    }
    with pytest.raises(ParameterSpecError, match="Unknown furniture"):
        compile_street_design_parameter_spec(unknown)

    unsafe = copy.deepcopy(spec)
    unsafe["furniture"]["categories"]["bench"]["assetRefs"] = [{
        "manifestName": "trusted.jsonl",
        "assetId": "bench-1",
        "fingerprint": "abc",
        "path": "/tmp/bench.glb",
    }]
    with pytest.raises(ParameterSpecError, match="file paths"):
        compile_street_design_parameter_spec(unsafe)


def test_compiled_category_targets_reach_existing_street_program():
    spec = build_street_design_parameter_spec(
        "pedestrian_friendly",
        source_revision=2,
        source_fingerprint="source",
        overrides={"furniture": {"categories": {"bench": {"targetCountPer100M": 2}}}},
    )
    compiled = compile_street_design_parameter_spec(spec)
    config = build_compose_config_from_draft(_draft(), patch_overrides=compiled.compose_config_patch)

    program = infer_street_program(config, DEFAULT_CATEGORIES)

    assert program.furniture_requirements["bench"] == 2
    assert program.furniture_requirements["bus_stop"] == 0
    assert config.furniture_category_parameters["bench"]["allowedZones"]


def test_scene_job_request_compiles_parameter_spec_before_generation():
    spec = build_street_design_parameter_spec(
        "road_skeleton_none",
        source_revision=3,
        source_fingerprint="source",
    )
    request = SceneJobCreateRequestModel(
        draft=_draft().to_dict(),
        generation_options={"parameter_spec": spec},
    )

    draft, _, patch, options = prepare_scene_generation_request(
        request,
        scenario_design_service=object(),
    )

    assert draft.normalized_scene_query == "walkable complete street"
    assert patch["street_furniture_profile"] == "none"
    assert patch["building_representation"] == "transparent_massing"
    assert options["generation_mode"] == "parametric"
    assert options["knowledge_source"] == "none"
    assert options["street_design_parameter_fingerprint"]
