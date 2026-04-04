"""Tests for the snapshot_diff pipeline.

Uses the mock LLM pattern from test_auto_eval.py to run the full
snapshot_diff pipeline deterministically, then verifies output structure,
config diffs, comparison images, and HTML report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.auto_pipeline.graph_loader import GraphSceneContext
from roadgen3d.auto_pipeline.iteration_controller import (
    AutoIterationController,
    IterationResult,
    IterationSnapshot,
)
from roadgen3d.services.design_types import (
    DEFAULT_COMPOSE_CONFIG_PATCH_VALUES,
    SceneGenerationResult,
)

sys.path.insert(0, str(ROOT / "scripts"))
from snapshot_diff import (
    build_html_report,
    compute_config_diff,
    plot_score_progression,
    run_snapshot_pipeline,
    stitch_preview_pair,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _fake_graph_ctx() -> GraphSceneContext:
    return GraphSceneContext(
        road_segment_graph=MagicMock(),
        projected_features=MagicMock(),
        placement_context=MagicMock(),
        annotation=MagicMock(),
        graph_summary={
            "centerline_count": 2,
            "junction_count": 1,
            "road_widths": [7.0, 7.0],
            "building_regions_count": 3,
        },
    )


def _make_scene_result(iter_dir: Path, iteration: int) -> SceneGenerationResult:
    iter_dir.mkdir(parents=True, exist_ok=True)
    layout_path = iter_dir / "scene_layout.json"
    layout_payload = {
        "placements": [
            {
                "instance_id": f"inst_{iteration}_0",
                "category": "tree",
                "asset_id": "tree_01",
                "position_xyz": [1.0, 0.0, 2.0],
            },
        ],
        "summary": {"instance_count": 1, "dropped_slots": 0, "spatial_context": {}},
    }
    layout_path.write_text(json.dumps(layout_payload), encoding="utf-8")

    glb_path = iter_dir / "scene.glb"
    glb_path.write_bytes(b"fake_glb_data")

    return SceneGenerationResult(
        compose_config=dict(DEFAULT_COMPOSE_CONFIG_PATCH_VALUES),
        summary={"instance_count": 1},
        scene_layout_path=str(layout_path),
        scene_glb_path=str(glb_path),
        scene_ply_path=str(iter_dir / "scene.ply"),
        viewer_url="",
    )


class _ImprovingService:
    """Mock LLM that improves score each iteration and adjusts config."""

    def __init__(self) -> None:
        self._call = 0

    def generate_initial_config_from_graph(self, **kwargs):
        return {
            "compose_config_patch": {
                **DEFAULT_COMPOSE_CONFIG_PATCH_VALUES,
                "density": 0.8,
                "road_width_m": 7.0,
            },
            "design_summary": "initial",
        }

    def evaluate_scene(self, *, layout_path, image_path=None):
        self._call += 1
        score = min(4.0 + self._call, 9.0)
        new_density = 0.8 + self._call * 0.1
        return {
            "evaluation": f"Evaluation round {self._call}",
            "score": score,
            "suggestions": [f"increase density to {new_density}"],
            "config_patch": {"density": new_density},
        }


def _run_pipeline_with_mock(
    graph_ctx: GraphSceneContext,
    output_dir: Path,
    query: str = "test query",
    max_iterations: int = 3,
) -> Dict[str, Any]:
    """Helper to run the pipeline with the mock _ImprovingService."""
    fake_service = _ImprovingService()

    def _fake_generate(
        *,
        compose_config_patch,
        road_segment_graph_override,
        projected_features_override,
        placement_context_override,
        generation_options=None,
        extra_summary=None,
    ):
        out_dir = (
            Path(generation_options.out_dir)
            if generation_options
            else output_dir / "iter_00"
        )
        return _make_scene_result(out_dir, 0)

    with patch(
        "roadgen3d.auto_pipeline.iteration_controller.generate_scene_from_graph_context",
        side_effect=_fake_generate,
    ), patch(
        "roadgen3d.auto_pipeline.iteration_controller.render_topdown_preview",
        return_value="",
    ):
        return run_snapshot_pipeline(
            graph_ctx=graph_ctx,
            query=query,
            output_dir=output_dir,
            max_iterations=max_iterations,
            manifest_path=str(ROOT / "data" / "real" / "real_assets_manifest.jsonl"),
            model_dir=str(ROOT / "models" / "clip-vit-base-patch32"),
            local_files_only=True,
            device="cpu",
            design_service=fake_service,
        )


@pytest.fixture()
def output_dir(tmp_path: Path) -> Path:
    d = tmp_path / "snapshot_diff_test"
    d.mkdir()
    return d


@pytest.fixture()
def graph_ctx() -> GraphSceneContext:
    return _fake_graph_ctx()


# ---------------------------------------------------------------------------
# Unit tests: compute_config_diff
# ---------------------------------------------------------------------------

class TestComputeConfigDiff:
    """Verify field-level JSON diff logic."""

    def test_no_changes(self):
        patch = {"a": 1, "b": "hello"}
        diff = compute_config_diff(patch, patch)
        assert diff["added"] == {}
        assert diff["removed"] == {}
        assert diff["changed"] == {}

    def test_added_field(self):
        old = {"a": 1}
        new = {"a": 1, "b": 2}
        diff = compute_config_diff(old, new)
        assert diff["added"] == {"b": 2}
        assert diff["removed"] == {}
        assert diff["changed"] == {}

    def test_removed_field(self):
        old = {"a": 1, "b": 2}
        new = {"a": 1}
        diff = compute_config_diff(old, new)
        assert diff["added"] == {}
        assert diff["removed"] == {"b": 2}
        assert diff["changed"] == {}

    def test_changed_field(self):
        old = {"density": 0.8, "road_width_m": 7.0}
        new = {"density": 1.0, "road_width_m": 7.0}
        diff = compute_config_diff(old, new)
        assert diff["changed"]["density"] == {"old": 0.8, "new": 1.0}
        assert "road_width_m" not in diff["changed"]


# ---------------------------------------------------------------------------
# Integration: full snapshot pipeline with mock LLM
# ---------------------------------------------------------------------------

class TestSnapshotDiffPipeline:
    """Run the full pipeline with a mock improving LLM and verify output."""

    def test_pipeline_output_structure(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        result = _run_pipeline_with_mock(graph_ctx, output_dir, "test query for snapshot diff")

        # Verify iteration directories
        for i in range(result["total_iterations"]):
            iter_dir = output_dir / f"iter_{i:02d}"
            assert iter_dir.exists(), f"Missing iter_{i:02d}/"
            assert (iter_dir / "config_patch.json").exists()
            assert (iter_dir / "evaluation.json").exists()
            assert (iter_dir / "scene_layout.json").exists()

        # Verify final/
        final_dir = output_dir / "final"
        assert final_dir.exists()
        assert (final_dir / "scene_layout.json").exists()
        assert (final_dir / "scene.glb").exists()

        # Verify diffs/
        diffs_dir = output_dir / "diffs"
        assert diffs_dir.exists()
        assert result["total_iterations"] - 1 == len(result["config_diffs"])

        # Verify top-level files
        assert (output_dir / "iteration_log.json").exists()
        assert (output_dir / "eval_report.json").exists()
        assert (output_dir / "report.html").exists()

    def test_config_diffs_show_changes(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        """Config diffs should reflect the density changes from _ImprovingService."""
        result = _run_pipeline_with_mock(graph_ctx, output_dir, "density change test")

        # _ImprovingService patches density from 0.8 → 0.9 → 1.0
        for diff in result["config_diffs"]:
            assert "density" in diff["changed"], (
                f"Expected 'density' in changed fields, got: {diff['changed']}"
            )

    def test_html_report_is_valid(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        result = _run_pipeline_with_mock(graph_ctx, output_dir, "html report test")

        html_path = Path(result["html_report_path"])
        assert html_path.exists()
        html_content = html_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html_content
        assert "Snapshot Diff Report" in html_content
        assert "html report test" in html_content

    def test_score_chart_generated(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        result = _run_pipeline_with_mock(graph_ctx, output_dir, "score chart test")

        score_chart = Path(result["score_chart_path"])
        assert score_chart.exists(), "score_progression.png should exist"
        assert score_chart.stat().st_size > 0

    def test_eval_report_structure(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        result = _run_pipeline_with_mock(graph_ctx, output_dir, "eval report test")

        report_path = Path(result["eval_report_path"])
        assert report_path.exists()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert "query" in report
        assert "total_iterations" in report
        assert "best_iteration" in report
        assert "best_score" in report
        assert "iterations" in report
        assert "config_diffs" in report
        assert isinstance(report["iterations"], list)
        assert len(report["iterations"]) == result["total_iterations"]
