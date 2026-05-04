from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.branch_runs import BranchRunService
from roadgen3d.services.branch_benchmarks import BranchBenchmarkStore
from roadgen3d.services.design_types import RagEvidence
from roadgen3d.services.optimization_planner import RuleBasedOptimizationPlanner


def test_rule_based_planner_filters_unbounded_llm_patch():
    planner = RuleBasedOptimizationPlanner()
    directives = planner.plan(
        evaluation={
            "walkability": 44,
            "safety": None,
            "beauty": None,
            "indicators": {
                "comfort": 42,
                "sidewalk_adequacy": "Low",
                "tree_shading_rate": "Very Low",
            },
        },
        current_patch={
            "query": "walkable street",
            "density": 1.0,
            "sidewalk_width_m": 2.4,
            "road_width_m": 7.0,
        },
    )

    accepted, rejected = planner.sanitize_candidate_patch(
        {
            "density": 2.0,
            "sidewalk_width_m": 8.0,
            "road_width_m": 20.0,
            "ped_demand_level": "high",
        },
        current_patch={"density": 1.0, "sidewalk_width_m": 2.4, "road_width_m": 7.0},
        directives=directives,
    )

    assert "density" not in accepted
    assert accepted["sidewalk_width_m"] <= 2.75
    assert accepted["ped_demand_level"] == "high"
    assert any(item["field"] == "density" for item in rejected)
    assert any(item["field"] == "road_width_m" for item in rejected)
    assert any("outside_bounds" in item["reason"] for item in rejected)


def test_branch_run_beam_top3_creates_expected_nodes(tmp_path: Path):
    service = BranchRunService(design_service=_FakeBranchDesignService(tmp_path), output_root=tmp_path)
    created = service.submit_run(
        prompt="Create a safer walkable street",
        topk=3,
        rounds=2,
        graph_template_id="hkust_gz_gate",
        knowledge_source="none",
    )
    result = _wait_for_run(service, created["run_id"])

    assert result["status"] == "succeeded"
    assert len(result["nodes"]) == 12
    assert len(result["scatter_points"]) == 12
    assert result["best_node_id"]
    failed = [node for node in result["nodes"] if node["status"] == "failed"]
    assert not failed
    children = [node for node in result["nodes"] if node["depth"] == 1]
    assert len(children) == 9
    assert any(node["rejected_edits"] for node in children)
    manifest = Path(result["artifact_dir"]) / "manifest.json"
    assert manifest.exists()
    assert json.loads(manifest.read_text(encoding="utf-8"))["run_id"] == created["run_id"]


def test_branch_run_initial_nodes_include_scenario_parameter_evidence(tmp_path: Path):
    service = BranchRunService(design_service=_FakeBranchDesignService(tmp_path), output_root=tmp_path)
    created = service.submit_run(
        prompt="Create a safer walkable commercial street",
        topk=1,
        rounds=1,
        graph_template_id="hkust_gz_gate",
        knowledge_source="graph_rag",
    )
    result = _wait_for_run(service, created["run_id"])

    assert result["status"] == "succeeded"
    assert len(result["nodes"]) == 1
    evidence = result["nodes"][0]["rag_evidence"]
    assert any(item["knowledge_source"] == "scenario_parameters" for item in evidence)
    trace = result["nodes"][0]["trace"]
    assert trace["schema_version"] == "generation_trace_v1"
    assert trace["process"]["growth_tree_node"]["node_id"] == result["nodes"][0]["node_id"]
    assert any(item["knowledge_source"] == "scenario_parameters" for item in trace["provenance"]["rag_evidence"])
    assert trace["llm_recommendation"]["derivation_status"] == "branch_llm_candidate"
    assert trace["evaluation"]["status"] == "succeeded"
    trace_path = Path(trace["result"]["generation_trace_path"])
    assert trace_path.exists()
    assert json.loads(trace_path.read_text(encoding="utf-8"))["node_id"] == result["nodes"][0]["node_id"]


def test_branch_run_target_samples_builds_100_scored_provenance_points(tmp_path: Path):
    service = BranchRunService(design_service=_FakeBranchDesignService(tmp_path), output_root=tmp_path)
    created = service.submit_run(
        prompt="Create one hundred scored alternatives with provenance",
        topk=5,
        rounds=2,
        target_samples=100,
        graph_template_id="hkust_gz_gate",
        knowledge_source="graph_rag",
    )
    result = _wait_for_run(service, created["run_id"])

    assert result["status"] == "succeeded"
    assert result["target_samples"] == 100
    assert result["completed_samples"] == 100
    assert result["attempted_samples"] == 100
    assert len(result["nodes"]) == 100
    assert len(result["scatter_points"]) == 100
    point = result["scatter_points"][0]
    assert point["walkability"] is not None
    assert point["safety"] is not None
    assert point["beauty"] is not None
    assert point["z"] == point["beauty"]
    assert "is_pareto_front" in point
    assert "pareto_rank" in point
    assert "dominated_by_count" in point
    child_point = next(item for item in result["scatter_points"] if item["parent_id"])
    assert child_point["delta_walkability"] is not None
    node = next(item for item in result["nodes"] if item["rejected_edits"])
    row_types = {item["source_type"] for item in node["influence_rows"]}
    assert {"rag", "parameter_triple", "llm_patch", "directive", "constraint"}.issubset(row_types)
    assert any(item["active"] for item in node["influence_rows"] if item["source_type"] == "parameter_triple")
    assert service.design_service.generation_options[0]["export_format"] == "none"
    assert service.design_service.generation_options[0]["preset_id"] == "skip_llm"
    assert service.design_service.generation_options[0]["build_production_artifacts"] is False
    assert service.design_service.generation_options[0]["render_presentation_artifacts"] is False

    manifest = json.loads((Path(result["artifact_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["target_samples"] == 100
    assert manifest["completed_samples"] == 100


def test_branch_run_pareto_mode_uses_traditional_search_and_early_stops(tmp_path: Path):
    fake_service = _DominatedParetoDesignService(tmp_path)
    service = BranchRunService(design_service=fake_service, output_root=tmp_path)
    created = service.submit_run(
        prompt="Find a Pareto surface for street scores",
        topk=5,
        rounds=5,
        target_samples=100,
        search_mode="pareto",
        early_stop_patience=3,
        graph_template_id="hkust_gz_gate",
        knowledge_source="graph_rag",
    )
    result = _wait_for_run(service, created["run_id"])

    assert result["status"] == "succeeded"
    assert result["search_mode"] == "pareto"
    assert result["early_stop_triggered"] is True
    assert result["completed_samples"] == 4
    assert result["attempted_samples"] == 4
    assert fake_service.initial_candidate_calls == 0
    assert fake_service.improvement_candidate_calls == 0
    assert result["pareto_front"]
    assert result["pareto_front_size"] == len(result["pareto_front"])
    first_point = result["scatter_points"][0]
    dominated_point = result["scatter_points"][-1]
    assert first_point["is_pareto_front"] is True
    assert dominated_point["is_pareto_front"] is False
    assert dominated_point["dominated_by_count"] >= 1
    node = result["nodes"][0]
    row_types = {item["source_type"] for item in node["influence_rows"]}
    assert {"rag", "parameter_triple", "search_patch"}.issubset(row_types)
    assert all(item["source"] != "branch_llm_candidate" for item in node["influence_rows"] if item["source_type"] == "search_patch")


def test_branch_run_preset_pareto_samples_are_persisted_to_benchmark_store(tmp_path: Path):
    store = BranchBenchmarkStore(tmp_path / "bench")
    fake_service = _FakeBranchDesignService(tmp_path)
    service = BranchRunService(design_service=fake_service, output_root=tmp_path / "runs", benchmark_store=store)
    created = service.submit_run(
        prompt="Run a pedestrian preset benchmark sample",
        topk=2,
        rounds=2,
        target_samples=3,
        search_mode="pareto",
        graph_template_id="hkust_gz_gate",
        knowledge_source="graph_rag",
        preset_id="pedestrian_friendly",
        preset_config_patch={
            "objective_profile": "balanced",
            "design_rule_profile": "pedestrian_priority_v1",
            "ped_demand_level": "high",
            "vehicle_demand_level": "low",
        },
        benchmark_id="batch-demo",
        batch_id="batch-demo",
        persist_to_benchmark=True,
        retain_topk_artifacts=2,
    )
    result = _wait_for_run(service, created["run_id"])

    assert result["status"] == "succeeded"
    assert result["preset_id"] == "pedestrian_friendly"
    assert result["benchmark_id"] == "batch-demo"
    assert result["nodes"][0]["config_patch"]["objective_profile"] == "balanced"
    assert result["nodes"][0]["config_patch"]["ped_demand_level"] == "high"
    assert fake_service.generation_options[0]["preset_id"] == "skip_llm"
    assert fake_service.generation_options[0]["benchmark_preset_id"] == "pedestrian_friendly"
    assert any(node["can_restore_artifact"] for node in result["nodes"])

    stored = store.query_samples(preset_id="pedestrian_friendly", limit=10)
    assert stored["total"] == 3
    assert stored["items"][0]["preset_id"] == "pedestrian_friendly"
    assert stored["items"][0]["batch_id"] == "batch-demo"
    assert any(item["can_restore_artifact"] for item in stored["items"])
    assert any(item["is_pareto_front"] for item in stored["items"])


def test_branch_run_retains_only_top_scored_artifacts(tmp_path: Path):
    fake_service = _FakeBranchDesignService(tmp_path)
    service = BranchRunService(design_service=fake_service, output_root=tmp_path)
    created = service.submit_run(
        prompt="Score scenes with temporary render artifacts",
        topk=5,
        rounds=5,
        target_samples=12,
        search_mode="pareto",
        graph_template_id="hkust_gz_gate",
        knowledge_source="graph_rag",
        retain_topk_artifacts=3,
        score_with_rendered_views=True,
    )
    result = _wait_for_run(service, created["run_id"])

    assert result["status"] == "succeeded"
    assert result["retain_topk_artifacts"] == 3
    assert result["score_with_rendered_views"] is True
    assert result["retained_artifact_count"] == 3
    retained_ids = set(result["retained_artifact_nodes"])
    expected_ids = {
        item["node_id"]
        for item in sorted(result["nodes"], key=lambda node: node["score"], reverse=True)[:3]
    }
    assert retained_ids == expected_ids
    assert all(len(item) == 3 for item in fake_service.rendered_view_batches)

    for node in result["nodes"]:
        glb_path = Path(node.get("scene_glb_path") or "")
        view_dir = Path(result["artifact_dir"]) / node["node_id"] / "presentation_views"
        if node["node_id"] in retained_ids:
            assert node["artifacts_retained"] is True
            assert glb_path.exists()
            assert view_dir.exists()
        else:
            assert node["artifacts_retained"] is False
            assert node.get("scene_glb_path") in {"", None}
            assert not (Path(result["artifact_dir"]) / node["node_id"] / "scene.glb").exists()
            assert not view_dir.exists()


def test_branch_run_fills_branch_scores_when_visual_eval_is_unavailable(tmp_path: Path):
    service = BranchRunService(
        design_service=_FakeBranchDesignService(tmp_path, visual_scores=False),
        output_root=tmp_path,
    )
    created = service.submit_run(
        prompt="Create scored alternatives without visual LLM inputs",
        topk=1,
        rounds=1,
        target_samples=1,
        graph_template_id="hkust_gz_gate",
        knowledge_source="none",
    )
    result = _wait_for_run(service, created["run_id"])

    assert result["status"] == "succeeded"
    point = result["scatter_points"][0]
    assert point["walkability"] is not None
    assert point["safety"] is not None
    assert point["beauty"] is not None
    assert point["overall"] is not None
    evaluation = result["nodes"][0]["evaluation"]
    assert evaluation["branch_score_fallback"]["safety"]["source"] == "structural_walkability_proxy"
    assert evaluation["branch_score_fallback"]["beauty"]["source"] == "structural_walkability_proxy"
    assert evaluation["branch_score_fallback"]["overall"]["source"] == "weighted_branch_scores"


def test_branch_run_service_lists_historical_manifests_after_restart(tmp_path: Path):
    run_dir = tmp_path / "historic-100"
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": "historic-100",
        "status": "succeeded",
        "stage": "succeeded",
        "progress": 100,
        "created_at": "2026-05-04T06:00:00+00:00",
        "target_samples": 100,
        "completed_samples": 84,
        "attempted_samples": 84,
        "nodes": [{"node_id": "node-a", "status": "succeeded"}],
        "scatter_points": [{"node_id": "node-a", "walkability": 70, "safety": 68, "beauty": 72}],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    service = BranchRunService(design_service=_FakeBranchDesignService(tmp_path), output_root=tmp_path)

    listed = service.list_runs(limit=10)
    assert listed[0]["run_id"] == "historic-100"
    assert listed[0]["nodes"] == []
    assert listed[0]["target_samples"] == 100

    loaded = service.get_run("historic-100")
    assert loaded is not None
    assert loaded["completed_samples"] == 84
    assert loaded["nodes"][0]["node_id"] == "node-a"


def _wait_for_run(service: BranchRunService, run_id: str) -> Mapping[str, Any]:
    deadline = time.time() + 90
    while time.time() < deadline:
        payload = service.get_run(run_id)
        if payload and payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError("branch run did not finish")


class _FakeBranchDesignService:
    def __init__(self, tmp_path: Path, *, visual_scores: bool = True) -> None:
        self.tmp_path = tmp_path
        self.generated = 0
        self.visual_scores = visual_scores
        self.generation_options: list[Mapping[str, Any]] = []
        self.rendered_view_batches: list[list[Mapping[str, Any]]] = []
        self.initial_candidate_calls = 0
        self.improvement_candidate_calls = 0

    def search_knowledge(self, **kwargs):
        knowledge_source = str(kwargs.get("knowledge_source", "graph_rag"))
        if knowledge_source == "none":
            return []
        return [
            RagEvidence(
                chunk_id="guide-001",
                doc_id="complete-streets",
                section_title="Pedestrian guidance",
                page_start=1,
                page_end=1,
                text="Use generous clear paths.",
                source_path="/tmp/guide.pdf",
                score=0.84,
                knowledge_source=knowledge_source,
            )
        ]

    def _retrieve_scenario_parameter_evidence(self, *, queries, topk, parameter_names=None):
        return [
            RagEvidence(
                chunk_id=(
                    "scenario_parameters::matrix::street_type_walkable_commercial_corridor::"
                    "sidewalk_width_m"
                ),
                doc_id="scenario_parameter_triples",
                section_title="Walkable Commercial Corridor / sidewalk_width_m",
                page_start=0,
                page_end=0,
                text=(
                    '{"scenario_label":"Walkable Commercial Corridor",'
                    '"parameter_name":"sidewalk_width_m","normalized_value":3.658,"unit":"m"}'
                ),
                source_path="knowledge/scenario_parameter_triples.jsonl",
                score=0.97,
                knowledge_source="scenario_parameters",
            )
        ][:topk]

    def generate_initial_config_candidates_from_graph(self, **kwargs):
        self.initial_candidate_calls += 1
        topk = int(kwargs.get("topk", 3))
        return [
            {
                "candidate_id": f"init_{index}",
                "rank": index,
                "compose_config_patch": {
                    "query": "Create a safer walkable street",
                    "density": 0.8 + index * 0.1,
                    "sidewalk_width_m": 2.4 + index * 0.1,
                    "road_width_m": 7.0,
                    "lane_count": 2,
                },
                "reasoning": f"initial candidate {index}",
            }
            for index in range(1, topk + 1)
        ]

    def propose_improvement_candidates(self, **kwargs):
        self.improvement_candidate_calls += 1
        topk = int(kwargs.get("topk", 3))
        current = dict(kwargs.get("current_patch", {}) or {})
        return [
            {
                "candidate_id": f"child_{index}",
                "rank": index,
                "compose_config_patch": {
                    "density": float(current.get("density", 1.0)) + 0.5,
                    "sidewalk_width_m": float(current.get("sidewalk_width_m", 2.4)) + 0.5,
                    "road_width_m": 18.0,
                    "ped_demand_level": "high",
                },
                "directive_ids": ["restore-clear-sidewalk"],
                "reasoning": f"bounded child {index}",
            }
            for index in range(1, topk + 1)
        ]

    def generate_scene(self, draft, **_kwargs):
        self.generated += 1
        generation_options = dict(_kwargs.get("generation_options", {}) or {})
        self.generation_options.append(generation_options)
        out_dir = Path(str(generation_options.get("out_dir") or self.tmp_path))
        out_dir.mkdir(parents=True, exist_ok=True)
        layout_path = out_dir / f"layout_{self.generated}.json"
        glb_path = out_dir / "scene.glb"
        render_views = []
        if generation_options.get("export_format") == "glb":
            glb_path.write_text("glb", encoding="utf-8")
        if generation_options.get("render_presentation_artifacts"):
            view_dir = out_dir / "presentation_views"
            view_dir.mkdir(parents=True, exist_ok=True)
            for index in range(3):
                view_path = view_dir / f"final_view_{index + 1}.png"
                view_path.write_bytes(b"png")
                render_views.append({
                    "name": f"final_view_{index + 1}",
                    "title": f"Final View {index + 1}",
                    "path": str(view_path),
                })
        layout_path.write_text(
            json.dumps({
                "summary": {"length_m": 80, "render_views": render_views},
                "config": draft.compose_config_patch,
                "placements": [],
            }),
            encoding="utf-8",
        )
        return {
            "compose_config": dict(draft.compose_config_patch),
            "summary": {"instance_count": self.generated},
            "scene_layout_path": str(layout_path),
            "scene_glb_path": str(glb_path),
        }

    def evaluate_scene_unified(self, *, layout_path: str, **_kwargs):
        self.rendered_view_batches.append(list(_kwargs.get("rendered_views", []) or []))
        index = int(Path(layout_path).stem.split("_")[-1])
        return {
            "walkability": min(100, 45 + index),
            "safety": min(100, 50 + index * 0.7) if self.visual_scores else None,
            "beauty": min(100, 55 + index * 0.5) if self.visual_scores else None,
            "overall": None,
            "evaluation": "three-system score",
            "suggestions": ["improve comfort"],
            "indicators": {
                "protection": 48 + index,
                "comfort": 40,
                "delight": 42 + index,
                "sidewalk_adequacy": "Low",
                "tree_shading_rate": "Low",
            },
            "config_patch": {},
        }


class _DominatedParetoDesignService(_FakeBranchDesignService):
    def evaluate_scene_unified(self, *, layout_path: str, **_kwargs):
        index = int(Path(layout_path).stem.split("_")[-1])
        score = 91 - index * 5
        return {
            "walkability": score,
            "safety": score,
            "beauty": score,
            "overall": None,
            "evaluation": "dominated synthetic score",
            "suggestions": ["stop if dominated"],
            "indicators": {
                "protection": score,
                "comfort": score,
                "delight": score,
                "sidewalk_adequacy": "Low",
                "tree_shading_rate": "Low",
            },
            "config_patch": {},
        }
