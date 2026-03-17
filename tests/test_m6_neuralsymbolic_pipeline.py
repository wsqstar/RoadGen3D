from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.design_rules import extend_constraint_set, load_constraint_set
from roadgen3d.layout_solver import solve_layout
from roadgen3d.street_program import infer_street_program
from roadgen3d.types import DesignRuleSpec, LayoutSolverInput, RetrievalHit, StreetComposeConfig
from roadgen3d.street_layout import compose_street_scene
import roadgen3d.street_layout as street_layout


def _make_mesh(path: Path, kind: str = "box") -> None:
    trimesh = pytest.importorskip("trimesh")
    if kind == "cylinder":
        mesh = trimesh.creation.cylinder(radius=0.1, height=1.5, sections=16)
    else:
        mesh = trimesh.creation.box(extents=(0.8, 0.5, 0.5))
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)


def _write_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _build_real_rows(base_dir: Path, include_bus_stop: bool = True) -> list[dict[str, object]]:
    categories = [
        ("bench_01", "bench"),
        ("lamp_01", "lamp"),
        ("trash_01", "trash"),
        ("tree_01", "tree"),
        ("mailbox_01", "mailbox"),
        ("hydrant_01", "hydrant"),
        ("bollard_01", "bollard"),
    ]
    if include_bus_stop:
        categories.insert(4, ("bus_stop_01", "bus_stop"))

    rows: list[dict[str, object]] = []
    for asset_id, category in categories:
        mesh_path = base_dir / "meshes" / f"{asset_id}.glb"
        _make_mesh(mesh_path, kind="cylinder" if category in {"lamp", "tree"} else "box")
        rows.append(
            {
                "asset_id": asset_id,
                "category": category,
                "text_desc": f"a roadside {category}",
                "mesh_path": str(mesh_path),
                "latent_path": str(base_dir / "latents" / f"{asset_id}.pt"),
                "license": "cc-by",
                "source": "test",
                "split": "train",
            }
        )
    return rows


def _setup_fake_retrieval(monkeypatch, asset_ids: list[str]) -> None:
    class FakeEmbedder:
        def __init__(self, *args, **kwargs):
            pass

        def encode_texts(self, texts):
            return np.ones((len(texts), 8), dtype=np.float32)

    class FakeIndexStore:
        @classmethod
        def load(cls, *args, **kwargs):
            return cls()

        def search(self, query_embeddings, topk=1):
            ranked = [
                RetrievalHit(asset_id=asset_id, score=float(1.0 - i * 0.01))
                for i, asset_id in enumerate(asset_ids[:topk])
            ]
            return [list(ranked) for _ in range(query_embeddings.shape[0])]

    monkeypatch.setattr(street_layout, "ClipTextEmbedder", FakeEmbedder)
    monkeypatch.setattr(street_layout, "FaissIndexStore", FakeIndexStore)


def _build_config(
    profile: str,
    seed: int = 42,
    *,
    layout_solver: str = "hybrid_milp_v1",
    objective_profile: str = "balanced",
) -> StreetComposeConfig:
    return StreetComposeConfig(
        query="pedestrian-friendly boulevard with transit access",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=seed,
        topk_per_category=20,
        max_trials_per_slot=30,
        design_rule_profile=profile,
        layout_solver=layout_solver,
        objective_profile=objective_profile,
    )


def test_rule_profiles_change_cross_section_and_layout(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    balanced = compose_street_scene(
        config=_build_config("balanced_complete_street_v1", seed=11),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts_a",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts_a",
    )
    pedestrian = compose_street_scene(
        config=_build_config("pedestrian_priority_v1", seed=11),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts_b",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts_b",
    )

    assert balanced.street_program is not None
    assert pedestrian.street_program is not None
    assert balanced.street_program.cross_section_type != pedestrian.street_program.cross_section_type
    assert pedestrian.street_program.sidewalk_width_m > balanced.street_program.sidewalk_width_m
    assert len(pedestrian.solver_result.slot_plans) != len(balanced.solver_result.slot_plans)


def test_solver_reports_replace_edit_for_missing_required_category():
    config = _build_config("transit_priority_v1")
    available_categories = ("bench", "lamp", "trash", "tree", "mailbox", "hydrant", "bollard")
    program = infer_street_program(config, available_categories)
    solver_result = solve_layout(
        LayoutSolverInput(
            program=program,
            config=config,
            available_categories=available_categories,
            constraint_set=load_constraint_set("transit_priority_v1"),
        )
    )

    assert any(edit.action == "replace" for edit in solver_result.edits)
    assert any("bus_stop" in edit.reason for edit in solver_result.edits)
    assert solver_result.conflict_explainability == 1.0


def test_compose_surfaces_conflict_report_when_rule_is_unsatisfied(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    extra_rule = DesignRuleSpec(
        name="require_cycle_parking",
        description="Surface an explicit conflict when the inventory cannot satisfy a required cycle-parking category.",
        target="required_category_available",
        mode="hard",
        operator="present",
        value=True,
        parameters={"category": "cycle_parking"},
    )
    extended = extend_constraint_set(load_constraint_set("balanced_complete_street_v1"), [extra_rule])
    monkeypatch.setattr(street_layout, "load_constraint_set", lambda *_args, **_kwargs: extended)

    result = compose_street_scene(
        config=_build_config("balanced_complete_street_v1", seed=21),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    assert result.solver_result is not None
    assert result.solver_result.conflicts
    assert any("cycle_parking" in conflict.message for conflict in result.solver_result.conflicts)

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    assert summary["solver_conflict_count"] >= 1
    assert summary["conflict_explainability"] == 1.0


def test_compose_exports_hybrid_solver_metadata_and_supervision_sample(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    result = compose_street_scene(
        config=_build_config(
            "balanced_complete_street_v1",
            seed=5,
            layout_solver="hybrid_milp_v1",
            objective_profile="greening",
        ),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    solver_payload = payload["solver"]

    assert summary["objective_profile"] == "greening"
    assert summary["solver_backend_used"] == "hybrid_milp_v1"
    assert "throughput_feasibility" in summary
    assert summary["band_solution_count"] >= 1
    assert solver_payload["band_solutions"]
    assert solver_payload["objective_profile"] == "greening"
    assert payload["supervision_sample"]["labels"]["objective_profile"] == "greening"
    assert payload["supervision_sample"]["labels"]["band_solutions"]
