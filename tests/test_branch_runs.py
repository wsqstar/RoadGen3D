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


def _wait_for_run(service: BranchRunService, run_id: str) -> Mapping[str, Any]:
    deadline = time.time() + 5
    while time.time() < deadline:
        payload = service.get_run(run_id)
        if payload and payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError("branch run did not finish")


class _FakeBranchDesignService:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.generated = 0

    def search_knowledge(self, **_kwargs):
        return []

    def generate_initial_config_candidates_from_graph(self, **kwargs):
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
        layout_path = self.tmp_path / f"layout_{self.generated}.json"
        layout_path.write_text(
            json.dumps({"summary": {"length_m": 80}, "config": draft.compose_config_patch, "placements": []}),
            encoding="utf-8",
        )
        return {
            "compose_config": dict(draft.compose_config_patch),
            "summary": {"instance_count": self.generated},
            "scene_layout_path": str(layout_path),
            "scene_glb_path": str(self.tmp_path / f"scene_{self.generated}.glb"),
        }

    def evaluate_scene_unified(self, *, layout_path: str, **_kwargs):
        index = int(Path(layout_path).stem.split("_")[-1])
        return {
            "walkability": min(100, 45 + index),
            "safety": None,
            "beauty": None,
            "overall": None,
            "evaluation": "walkability only",
            "suggestions": ["improve comfort"],
            "indicators": {
                "comfort": 40,
                "sidewalk_adequacy": "Low",
                "tree_shading_rate": "Low",
            },
            "config_patch": {},
        }
