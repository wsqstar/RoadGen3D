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

from roadgen3d.program_generator import PROGRAM_FEATURE_DIM, ProgramGeneratorMLP, ProgramGeneratorRuntime
from roadgen3d.street_program import infer_street_program
from roadgen3d.types import InventorySummary, ProgramGenerationInput, StreetComposeConfig


def _program_input(*, profile: str = "balanced_complete_street_v1", generator: str = "learned_v1") -> ProgramGenerationInput:
    config = StreetComposeConfig(
        query="pedestrian-friendly boulevard with transit access",
        length_m=80.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
        design_rule_profile=profile,
        program_generator=generator,
    )
    inventory = InventorySummary(
        category_counts={"bench": 3, "lamp": 4, "tree": 2, "bus_stop": 1},
        asset_ids_by_category={
            "bench": ("bench_01", "bench_02", "bench_03"),
            "lamp": ("lamp_01", "lamp_02", "lamp_03", "lamp_04"),
            "tree": ("tree_01", "tree_02"),
            "bus_stop": ("bus_stop_01",),
        },
    )
    return ProgramGenerationInput(
        query=config.query,
        compose_config=config,
        available_categories=("bench", "lamp", "tree", "bus_stop"),
        constraint_profile=profile,
        inventory_summary=inventory,
    )


def test_program_generator_runtime_falls_back_to_heuristic_when_learned_runtime_missing():
    runtime = ProgramGeneratorRuntime(backend="heuristic_v1")
    result = runtime.generate(_program_input(generator="learned_v1"))

    assert result.backend_requested == "learned_v1"
    assert result.backend_used == "heuristic_v1"
    assert "fallback" in result.fallback_reason.lower()
    assert result.program.cross_section_type


def test_none_street_furniture_profile_keeps_structure_but_zeroes_requirements():
    config = StreetComposeConfig(
        query="four lane safety island structure preview",
        length_m=80.0,
        road_width_m=13.2,
        sidewalk_width_m=3.5,
        lane_count=4,
        density=0.9,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
        street_furniture_profile="none",
        amenity_coverage_mode="off",
        minimum_category_presence=(),
        optional_category_presence=(),
    )

    program = infer_street_program(
        config,
        available_categories=("bench", "lamp", "tree", "bollard", "trash"),
    )

    assert program.bands
    assert set(program.furniture_requirements) == {"bench", "lamp", "tree", "trash", "bollard"}
    assert all(count == 0 for count in program.furniture_requirements.values())
    assert "street_furniture_disabled" in program.notes


def test_program_generator_runtime_loads_checkpoint_and_returns_learned_program(tmp_path: Path):
    torch = pytest.importorskip("torch")

    model = ProgramGeneratorMLP(input_dim=PROGRAM_FEATURE_DIM)
    ckpt_path = tmp_path / "program_generator.pt"
    torch.save({"input_dim": PROGRAM_FEATURE_DIM, "state_dict": model.state_dict()}, ckpt_path)

    runtime = ProgramGeneratorRuntime.from_checkpoint(ckpt_path, device="cpu")
    result = runtime.generate(_program_input(generator="learned_v1"))

    assert result.backend_requested == "learned_v1"
    assert result.backend_used == "learned_v1"
    assert result.fallback_reason == ""
    assert "learned_program_generator_v1" in result.program.notes
    assert result.program.cross_section_type in {
        "balanced_complete_street",
        "pedestrian_priority",
        "transit_priority",
    }
