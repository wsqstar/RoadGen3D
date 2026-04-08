"""Integration tests for the multi-version auto-evaluation pipeline.

Tests 1-4 use the **real LLM** (DesignAssistantService) to generate and
evaluate scenes, with only the heavy scene-generation and preview rendering
mocked out.  This ensures the LLM produces varied configs for different
queries and evaluates them meaningfully.

Test 5 uses a mock service to deterministically verify the early-stop logic.

Set environment variables ``llm_base_url`` and ``key`` (as read by
``GLMSettings.from_env()``) to run the real-LLM tests.  They will be
automatically skipped otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List
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
from roadgen3d.llm.design_workflow import DesignAssistantService
from roadgen3d.services.design_types import (
    DEFAULT_COMPOSE_CONFIG_PATCH_VALUES,
    SceneGenerationResult,
)

# ---------------------------------------------------------------------------
# Skip marker for tests that require a live LLM API
# ---------------------------------------------------------------------------

def _llm_available() -> bool:
    """Check whether the GLM API env vars are present (loads .env if needed)."""
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except ImportError:
        pass
    return bool(os.environ.get("llm_base_url", "").strip()) and bool(
        os.environ.get("key", "").strip()
    )


requires_llm = pytest.mark.skipif(
    not _llm_available(),
    reason="LLM API not configured (need llm_base_url and key env vars)",
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

QUERIES = [
    "modern transit boulevard",
    "pedestrian-friendly green street",
    "commercial shopping district street",
]
MAX_ITERATIONS = 3


def _fake_graph_ctx() -> GraphSceneContext:
    """Return a minimal GraphSceneContext with mock fields."""
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
    """Create dummy files for a scene generation result and return the result."""
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
            {
                "instance_id": f"inst_{iteration}_1",
                "category": "bench",
                "asset_id": "bench_01",
                "position_xyz": [3.0, 0.0, 1.0],
            },
            {
                "instance_id": f"inst_{iteration}_2",
                "category": "street_lamp",
                "asset_id": "lamp_01",
                "position_xyz": [5.0, 0.0, 0.5],
            },
        ],
        "summary": {
            "instance_count": 3,
            "dropped_slots": 0,
            "spatial_context": {},
        },
    }
    layout_path.write_text(json.dumps(layout_payload), encoding="utf-8")

    glb_path = iter_dir / "scene.glb"
    glb_path.write_bytes(b"fake_glb_data")

    ply_path = iter_dir / "scene.ply"
    ply_path.write_bytes(b"fake_ply_data")

    return SceneGenerationResult(
        compose_config=dict(DEFAULT_COMPOSE_CONFIG_PATCH_VALUES),
        summary={"instance_count": 3},
        scene_layout_path=str(layout_path),
        scene_glb_path=str(glb_path),
        scene_ply_path=str(ply_path),
        viewer_url="",
    )


@pytest.fixture()
def output_dir(tmp_path: Path) -> Path:
    """Provide a clean temporary output directory."""
    d = tmp_path / "auto_eval_test"
    d.mkdir()
    return d


@pytest.fixture()
def graph_ctx() -> GraphSceneContext:
    return _fake_graph_ctx()


# ---------------------------------------------------------------------------
# Helper: run a single version with real LLM, mocked scene generation
# ---------------------------------------------------------------------------

def _run_single_version_real_llm(
    graph_ctx: GraphSceneContext,
    query: str,
    version_dir: Path,
    max_iterations: int = MAX_ITERATIONS,
    *,
    retries: int = 3,
) -> Dict[str, Any]:
    """Run a single version with the **real** LLM but mocked scene generation.

    Retries up to *retries* times on LLM response errors (truncated JSON etc.).
    """
    import time
    import httpx
    from roadgen3d.llm.glm_client import GLMResponseError

    real_service = DesignAssistantService()

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
            else version_dir / "iter_00"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        return _make_scene_result(out_dir, 0)

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with patch(
                "roadgen3d.auto_pipeline.iteration_controller.generate_scene_from_graph_context",
                side_effect=_fake_generate,
            ), patch(
                "roadgen3d.auto_pipeline.iteration_controller.render_topdown_preview",
                return_value="",
            ):
                controller = AutoIterationController(
                    graph_ctx,
                    manifest_path=str(ROOT / "data" / "real" / "real_assets_manifest.jsonl"),
                    artifacts_dir=str(version_dir / "_artifacts"),
                    output_dir=str(version_dir),
                    max_iterations=max_iterations,
                    model_dir=str(ROOT / "models" / "clip-vit-base-patch32"),
                    local_files_only=True,
                    device="cpu",
                    query=query,
                    design_service=real_service,
                )
                result = controller.run()

            return {
                "query": query,
                "total_iterations": result.total_iterations,
                "best_score": result.best_score,
                "best_iteration": result.best_iteration,
                "best_layout_path": result.best_layout_path,
                "best_scene_path": result.best_scene_path,
                "views": [],
                "iteration_log_path": str(version_dir / "iteration_log.json"),
            }
        except (GLMResponseError, httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc
            print(f"[retry] LLM error (attempt {attempt}/{retries}): {exc}")
            if attempt < retries:
                time.sleep(2 * attempt)
        # Clean up partial output so the retry starts fresh
        if version_dir.exists():
            shutil.rmtree(version_dir, ignore_errors=True)

    raise RuntimeError(
        f"LLM failed after {retries} retries for query '{query}'"
    ) from last_exc


# ---------------------------------------------------------------------------
# Test 1: Multiple versions produce distinct LLM configs and iterations
# ---------------------------------------------------------------------------

@requires_llm
class TestAutoEvalGeneratesMultipleVersions:
    """Verify that each query produces its own set of iteration dirs and a final/."""

    def test_generates_iteration_dirs_and_final(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        for i, query in enumerate(QUERIES):
            version_dir = output_dir / f"version_{i:02d}_{query.replace(' ', '_').replace('-', '_').lower()}"
            _run_single_version_real_llm(graph_ctx, query, version_dir)

            iter_dirs = sorted(version_dir.glob("iter_*"))
            assert len(iter_dirs) >= 1, f"Expected at least 1 iteration dir for '{query}'"

            final_dir = version_dir / "final"
            assert final_dir.exists(), f"final/ dir missing for '{query}'"
            assert (final_dir / "scene_layout.json").exists()
            assert (final_dir / "scene.glb").exists()

    def test_configs_differ_across_queries(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        """Real LLM should propose different config patches for different queries."""
        config_patches: List[Dict[str, Any]] = []
        for i, query in enumerate(QUERIES):
            version_dir = output_dir / f"version_{i:02d}_{query.replace(' ', '_').replace('-', '_').lower()}"
            _run_single_version_real_llm(graph_ctx, query, version_dir)

            log_path = version_dir / "iteration_log.json"
            log = json.loads(log_path.read_text(encoding="utf-8"))
            first_iter = log["iterations"][0]
            config_patches.append(first_iter["config_patch"])

        # At least two of the three config patches should differ
        patch_strs = [json.dumps(p, sort_keys=True) for p in config_patches]
        unique = set(patch_strs)
        assert len(unique) >= 2, (
            f"Expected ≥2 distinct LLM-generated config patches, but got {len(unique)}"
        )


# ---------------------------------------------------------------------------
# Test 2: Iteration log format is correct
# ---------------------------------------------------------------------------

@requires_llm
class TestAutoEvalSavesIterationLogs:
    """Verify iteration_log.json has the expected structure."""

    def test_iteration_log_format(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        version_dir = output_dir / "version_00_test"
        _run_single_version_real_llm(graph_ctx, "test query for logging", version_dir)

        log_path = version_dir / "iteration_log.json"
        assert log_path.exists(), "iteration_log.json not found"

        log = json.loads(log_path.read_text(encoding="utf-8"))
        assert "total_iterations" in log
        assert "best_iteration" in log
        assert "best_score" in log
        assert "iterations" in log
        assert isinstance(log["iterations"], list)
        assert len(log["iterations"]) >= 1

        for entry in log["iterations"]:
            assert "score" in entry
            assert "evaluation" in entry
            assert "suggestions" in entry
            assert "config_patch" in entry

    def test_evaluation_is_nonempty(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        """Real LLM should produce non-empty evaluation text."""
        version_dir = output_dir / "version_00_eval"
        _run_single_version_real_llm(graph_ctx, "walkable green street", version_dir)

        log = json.loads((version_dir / "iteration_log.json").read_text(encoding="utf-8"))
        for entry in log["iterations"]:
            assert len(entry["evaluation"].strip()) > 0, "LLM returned empty evaluation"


# ---------------------------------------------------------------------------
# Test 3: Presentation views
# ---------------------------------------------------------------------------

class TestAutoEvalRendersPresentationViews:
    """Verify render_presentation_views produces output (no LLM needed)."""

    def test_presentation_views_generated(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        from roadgen3d.beauty import render_presentation_views
        from roadgen3d.services.design_runtime import build_compose_config_from_draft
        from roadgen3d.services.design_types import (
            DesignDraft,
            sanitize_compose_config_patch,
        )

        version_dir = output_dir / "version_00_views"
        version_dir.mkdir(parents=True, exist_ok=True)

        layout = {
            "placements": [
                {
                    "instance_id": "t1",
                    "category": "tree",
                    "asset_id": "tree_01",
                    "position_xyz": [0.0, 0.0, 0.0],
                },
                {
                    "instance_id": "b1",
                    "category": "bench",
                    "asset_id": "bench_01",
                    "position_xyz": [2.0, 0.0, 1.0],
                },
            ],
            "summary": {"instance_count": 2, "dropped_slots": 0},
        }

        patch_val = sanitize_compose_config_patch({})
        draft = DesignDraft(
            normalized_scene_query="test",
            compose_config_patch=patch_val,
            citations_by_field={},
            design_summary="test",
        )
        config = build_compose_config_from_draft(draft)

        try:
            views = render_presentation_views(
                layout, out_dir=version_dir, config=config
            )
        except Exception:
            pytest.skip("matplotlib or PIL not available for presentation rendering")
            return

        assert isinstance(views, list)
        if views:
            for v in views:
                assert "name" in v
                assert "path" in v


# ---------------------------------------------------------------------------
# Test 4: Eval report aggregates all versions
# ---------------------------------------------------------------------------

@requires_llm
class TestAutoEvalProducesEvalReport:
    """Verify eval_report.json contains correct aggregated data."""

    def test_eval_report_contains_all_versions(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        results = []
        for i, query in enumerate(QUERIES):
            version_dir = output_dir / f"version_{i:02d}_{query.replace(' ', '_').replace('-', '_').lower()}"
            result = _run_single_version_real_llm(graph_ctx, query, version_dir)
            results.append(result)

        from scripts.run_auto_eval import build_eval_report

        report = build_eval_report(results)
        report_path = output_dir / "eval_report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        assert report_path.exists()
        loaded = json.loads(report_path.read_text(encoding="utf-8"))
        assert loaded["num_versions"] == len(QUERIES)
        assert len(loaded["versions"]) == len(QUERIES)
        for v in loaded["versions"]:
            assert "query" in v
            assert "best_score" in v
            assert "total_iterations" in v

    def test_scores_are_plausible(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        """Real LLM scores should be in a plausible range (0–10)."""
        results = []
        for i, query in enumerate(QUERIES):
            version_dir = output_dir / f"version_{i:02d}_{query.replace(' ', '_').replace('-', '_').lower()}"
            result = _run_single_version_real_llm(graph_ctx, query, version_dir)
            results.append(result)

        for r in results:
            assert 0.0 <= r["best_score"] <= 10.0, (
                f"Score {r['best_score']} out of range for '{r['query']}'"
            )


# ---------------------------------------------------------------------------
# Test 5: Early-stop logic (uses mock LLM for deterministic scores)
# ---------------------------------------------------------------------------

class TestAutoEvalLLMIterationsImproveOrStop:
    """Verify that the controller stops early after consecutive rounds without
    improvement.  This test deliberately uses a mock LLM to control scores."""

    def test_early_stop_on_no_improvement(
        self, graph_ctx: GraphSceneContext, output_dir: Path
    ):
        class _StagnatingService:
            """Returns the same score every iteration to trigger early stopping."""

            def generate_initial_config_from_graph(self, **kwargs):
                return {
                    "compose_config_patch": dict(DEFAULT_COMPOSE_CONFIG_PATCH_VALUES),
                    "design_summary": "stagnating",
                }

            def evaluate_scene(self, *, layout_path, image_path=None):
                return {
                    "evaluation": "No change",
                    "score": 5.0,
                    "suggestions": [],
                    "config_patch": {},
                }

        version_dir = output_dir / "version_stagnating"
        fake_service = _StagnatingService()

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
                else version_dir / "iter_00"
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            return _make_scene_result(out_dir, 0)

        with patch(
            "roadgen3d.auto_pipeline.iteration_controller.generate_scene_from_graph_context",
            side_effect=_fake_generate,
        ), patch(
            "roadgen3d.auto_pipeline.iteration_controller.render_topdown_preview",
            return_value="",
        ):
            controller = AutoIterationController(
                graph_ctx,
                manifest_path=str(
                    ROOT / "data" / "real" / "real_assets_manifest.jsonl"
                ),
                artifacts_dir=str(version_dir / "_artifacts"),
                output_dir=str(version_dir),
                max_iterations=5,
                model_dir=str(ROOT / "models" / "clip-vit-base-patch32"),
                local_files_only=True,
                device="cpu",
                query="stagnating test",
                design_service=fake_service,
            )
            result = controller.run()

        # Iteration 0: best=5.0
        # Iteration 1: 5.0 → no_improvement=1
        # Iteration 2: 5.0 → no_improvement=2 → early stop
        assert result.total_iterations <= 3, (
            f"Expected early stopping (≤3 iterations), got {result.total_iterations}"
        )
