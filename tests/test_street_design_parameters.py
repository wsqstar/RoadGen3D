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
    build_default_street_design_parameter_spec_v2,
    build_street_design_parameter_spec,
    compile_street_design_parameter_spec,
    list_parameter_profiles,
    parameter_control_registry,
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


def test_public_control_registry_exposes_values_without_named_profiles():
    controls = parameter_control_registry()

    assert controls["parameter_schema_version"] == "roadgen3d.street-design-parameters.v2"
    assert controls["skeleton"]["laneCount"]["values"] == {"low": 2, "medium": 4, "high": 6}
    assert controls["skeleton"]["laneWidthM"]["minimum"] == pytest.approx(0.5)
    assert "maximum" not in controls["skeleton"]["laneWidthM"]
    assert controls["skeleton"]["junctionCornerRadiusM"]["values"]["high"] == pytest.approx(8.0)
    assert controls["furniture"]["categories"]["tree"]["values"] == {
        "low": 5.0,
        "medium": 8.0,
        "high": 12.0,
    }
    assert "profiles" not in controls


def test_parameter_compiler_accepts_source_lane_width_outside_preset_band():
    spec = build_default_street_design_parameter_spec_v2(
        source_revision=4,
        source_fingerprint="wide-source",
    )
    spec["skeleton"]["laneWidthM"] = 4.8

    compiled = compile_street_design_parameter_spec(spec)

    assert compiled.compose_config_patch["base_lane_width_m"] == pytest.approx(4.8)


def test_v2_default_has_no_profile_ids_and_disables_optional_generation():
    spec = build_default_street_design_parameter_spec_v2(
        source_revision=4,
        source_fingerprint="source-v2",
    )
    compiled = compile_street_design_parameter_spec(spec)

    assert spec["schemaVersion"] == "roadgen3d.street-design-parameters.v2"
    assert "profileId" not in spec["skeleton"]
    assert "profileId" not in spec["furniture"]
    assert spec["skeleton"]["median"]["enabled"] is False
    assert spec["skeleton"]["busStop"]["enabled"] is False
    assert all(item["enabled"] is False for item in spec["furniture"]["categories"].values())
    assert "skeleton_design_profile" not in compiled.compose_config_patch
    assert "street_furniture_profile" not in compiled.compose_config_patch


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
    assert first.spec["schemaVersion"] == "roadgen3d.street-design-parameters.v2"
    assert "profileId" not in first.spec["skeleton"]


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


def test_v2_median_and_bus_stop_compile_into_explicit_cross_section_bands():
    spec = build_default_street_design_parameter_spec_v2(
        source_revision=8,
        source_fingerprint="source-median",
    )
    spec["skeleton"]["median"] = {"enabled": True, "kind": "planted", "widthM": 2.0}
    spec["skeleton"]["busStop"] = {"enabled": True, "placement": "bay"}
    spec["furniture"]["categories"]["bus_stop"]["targetCountPer100M"] = 1.0
    compiled = compile_street_design_parameter_spec(spec)
    config = build_compose_config_from_draft(_draft(), patch_overrides=compiled.compose_config_patch)

    program = infer_street_program(config, DEFAULT_CATEGORIES)
    bands = {band.name: band for band in program.bands}

    assert compiled.compose_config_patch["road_width_m"] == pytest.approx(15.0)
    assert config.bus_stop_placement == "bay"
    assert bands["center_median_green"].width_m == pytest.approx(2.0)
    assert bands["right_transit_edge"].kind == "transit_edge"
    assert program.furniture_requirements["bus_stop"] == 1
    assert program.reserved_band_categories["right_transit_edge"] == "bus_stop"


def test_v2_global_density_scales_explicit_per_100m_targets():
    spec = build_default_street_design_parameter_spec_v2(
        source_revision=9,
        source_fingerprint="source-density",
    )
    spec["furniture"]["globalDensity"] = 1.4
    spec["furniture"]["categories"]["tree"].update(enabled=True, targetCountPer100M=5.0)
    compiled = compile_street_design_parameter_spec(spec)
    config = build_compose_config_from_draft(_draft(), patch_overrides=compiled.compose_config_patch)

    program = infer_street_program(config, DEFAULT_CATEGORIES)

    assert program.furniture_requirements["tree"] == 6  # 80m * 5/100m * 1.4
    assert program.furniture_requirements["bench"] == 0


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
    assert "street_furniture_profile" not in patch
    assert patch["amenity_coverage_mode"] == "off"
    assert all(item["enabled"] is False for item in patch["furniture_category_parameters"].values())
    assert patch["building_representation"] == "transparent_massing"
    assert options["generation_mode"] == "parametric"
    assert options["knowledge_source"] == "none"
    assert options["street_design_parameter_fingerprint"]
