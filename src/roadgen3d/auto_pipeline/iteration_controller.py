"""Auto-iteration controller: generate → render → evaluate → improve loop."""

from __future__ import annotations

import base64
import json
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ..eval_engine.core.config import EvalConfig
from ..eval_engine_ext.road_metrics.evaluators.safety_eval import evaluate_safety
from ..eval_engine_ext.road_metrics.evaluators.beauty_eval import evaluate_beauty
from ..eval_quality import (
    WalkabilityResult,
    compute_walkability_indicators,
    compute_structured_safety_report,
    compute_structured_beauty_report,
    write_walkability_report,
    write_json_report,
)
from ..llm.design_workflow import DesignAssistantService
from ..services.design_runtime import generate_scene_from_graph_context
from ..services.design_types import (
    SceneGenerationOptions,
    sanitize_compose_config_patch,
)
from .graph_loader import GraphSceneContext
from .scene_renderer import render_topdown_preview


# 默认聚合权重 (与 EvalConfig.AggregationConfig 默认值一致)
DEFAULT_WALKABILITY_WEIGHT = 0.45
DEFAULT_SAFETY_WEIGHT = 0.35
DEFAULT_BEAUTY_WEIGHT = 0.20


@dataclass
class IterationSnapshot:
    """Record of a single iteration in the auto-pipeline loop."""

    iteration: int
    config_patch: Dict[str, Any]
    score: float
    evaluation: str
    suggestions: List[str]
    layout_path: str
    preview_path: str
    scene_path: str
    # Evaluation module fields
    walkability: WalkabilityResult | None = None
    safety_report: Dict[str, Any] | None = None
    beauty_report: Dict[str, Any] | None = None
    evaluation_score: float = 0.0  # Combined evaluation score
    comparison: Dict[str, Any] = field(default_factory=dict)
    cited_evidence: List[str] = field(default_factory=list)
    improvement_reasoning: str = ""


@dataclass
class IterationResult:
    """Final result of the auto-pipeline iteration loop."""

    iterations: List[IterationSnapshot]
    best_iteration: int
    best_score: float
    best_layout_path: str
    best_scene_path: str
    total_iterations: int


class AutoIterationController:
    """Orchestrate the generate → render → evaluate → improve loop."""

    def __init__(
        self,
        graph_ctx: GraphSceneContext,
        *,
        base_map_path: str | None = None,
        manifest_path: str = "data/real/real_assets_manifest.jsonl",
        artifacts_dir: str = "artifacts/auto_pipeline",
        output_dir: str = "artifacts/auto_pipeline/scene",
        max_iterations: int = 5,
        model_dir: str = "models/clip-vit-base-patch32",
        local_files_only: bool = False,
        device: str = "cpu",
        query: str = "modern clean urban street",
        design_service: DesignAssistantService | None = None,
        enable_llm_eval: bool = False,
        eval_config: EvalConfig | None = None,
    ) -> None:
        self.graph_ctx = graph_ctx
        self.base_map_path = Path(base_map_path) if base_map_path else None
        self.max_iterations = max(1, int(max_iterations))
        self.query = query
        self.enable_llm_eval = bool(enable_llm_eval)

        # 评估配置 (支持自定义权重)
        self.eval_config = eval_config or EvalConfig.default()
        self._w_weight = self.eval_config.aggregation.walkability_weight
        self._s_weight = self.eval_config.aggregation.safety_weight
        self._b_weight = self.eval_config.aggregation.beauty_weight

        root = Path(__file__).resolve().parents[3]
        self.output_dir = Path(output_dir).expanduser().resolve()

        self.generation_options = SceneGenerationOptions(
            manifest_path=Path(manifest_path).expanduser().resolve(),
            artifacts_dir=Path(artifacts_dir).expanduser().resolve(),
            out_dir=self.output_dir,
            model_dir=Path(model_dir).expanduser().resolve() if model_dir else None,
            local_files_only=local_files_only,
            device=device,
        )

        self.design_service = design_service or DesignAssistantService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> IterationResult:
        """Execute the full auto-iteration flow and return the best result."""
        snapshots: List[IterationSnapshot] = []

        # Step A – Generate initial design parameters via LLM
        base_map_data_url = self._load_base_map_data_url()
        initial_result = self.design_service.generate_initial_config_from_graph(
            graph_summary=self.graph_ctx.graph_summary,
            base_map_data_url=base_map_data_url,
            user_prompt=self.query,
        )
        current_patch: Dict[str, Any] = dict(initial_result.get("compose_config_patch", {}))
        if self.query and "query" not in current_patch:
            current_patch["query"] = self.query

        best_metric_score = -1.0
        best_iteration = 0
        no_improvement_count = 0

        for i in range(self.max_iterations):
            iter_dir = self.output_dir / f"iter_{i:02d}"
            iter_dir.mkdir(parents=True, exist_ok=True)

            # Override out_dir for this iteration
            iter_options = replace(self.generation_options, out_dir=iter_dir)

            # Save config patch
            config_patch_path = iter_dir / "config_patch.json"
            config_patch_path.write_text(
                json.dumps(current_patch, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            print(f"[auto_pipeline] Iteration {i}: generating scene ...")
            scene_result = generate_scene_from_graph_context(
                compose_config_patch=current_patch,
                road_segment_graph_override=self.graph_ctx.road_segment_graph,
                projected_features_override=self.graph_ctx.projected_features,
                placement_context_override=self.graph_ctx.placement_context,
                generation_options=iter_options,
            )

            layout_path = scene_result.scene_layout_path
            scene_path = scene_result.scene_glb_path

            # Step – Render preview
            preview_path = str(iter_dir / "preview.png")
            try:
                render_topdown_preview(
                    layout_path,
                    preview_path,
                    annotation=self.graph_ctx.annotation,
                    base_map_path=self.base_map_path,
                )
            except Exception as exc:
                print(f"[auto_pipeline] Warning: preview rendering failed: {exc}")
                preview_path = ""

            # Step – LLM evaluation (with before/after comparison from iteration 1 onward)
            print(f"[auto_pipeline] Iteration {i}: evaluating scene ...")
            eval_result = self._evaluate_scene_compat(
                layout_path=layout_path,
                image_path=preview_path or None,
                previous=snapshots[-1] if snapshots else None,
            )

            score = _score_from_eval_payload(eval_result)
            evaluation_text = str(eval_result.get("evaluation", ""))
            suggestions = list(eval_result.get("suggestions", []) or [])
            comparison = dict(eval_result.get("comparison", {}))

            # Step – Compute structured evaluation metrics (walkability, safety, beauty)
            print(f"[auto_pipeline] Iteration {i}: computing evaluation metrics ...")
            layout_payload = json.loads(Path(layout_path).read_text(encoding="utf-8"))
            
            walkability = compute_walkability_indicators(layout_payload)

            # Compute structural safety/beauty first to extract features for LLM eval
            _safety_structural = compute_structured_safety_report(layout_payload, walkability)
            _beauty_structural = compute_structured_beauty_report(layout_payload)

            # Optional LLM-based safety/beauty scoring
            llm_safety_scores = None
            llm_beauty_scores = None
            if self.enable_llm_eval:
                print(f"[auto_pipeline] Iteration {i}: running LLM safety/beauty eval ...")
                llm_safety_scores = evaluate_safety(
                    features=_safety_structural.get("features", {}),
                    image_path=preview_path or None,
                )
                llm_beauty_scores = evaluate_beauty(
                    features=_beauty_structural.get("features", {}),
                    image_path=preview_path or None,
                )

            safety_report = compute_structured_safety_report(layout_payload, walkability, llm_scores=llm_safety_scores)
            beauty_report = compute_structured_beauty_report(layout_payload, llm_scores=llm_beauty_scores)

            # Compute combined evaluation score using configurable weights
            # Default: W=0.45, S=0.35, B=0.20 (可通過 eval_config 調整)
            walkability_index = float(walkability.walkability_index)
            safety_score = float(safety_report.get("final_score", 0.0))
            beauty_score = float(beauty_report.get("final_score", 0.0))
            evaluation_score = round(
                self._w_weight * walkability_index
                + self._s_weight * safety_score
                + self._b_weight * beauty_score,
                4,
            )

            # Save evaluation reports
            eval_path = iter_dir / "evaluation.json"
            eval_path.write_text(
                json.dumps(eval_result, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # Save walkability report
            walkability_path = iter_dir / "walkability.json"
            write_walkability_report(walkability, walkability_path)

            # Save safety report
            safety_path = iter_dir / "safety.json"
            write_json_report(safety_report, safety_path)

            # Save beauty report
            beauty_path = iter_dir / "beauty.json"
            write_json_report(beauty_report, beauty_path)

            snapshot = IterationSnapshot(
                iteration=i,
                config_patch=dict(current_patch),
                score=score,
                evaluation=evaluation_text,
                suggestions=suggestions,
                layout_path=layout_path,
                preview_path=preview_path,
                scene_path=scene_path,
                walkability=walkability,
                safety_report=safety_report,
                beauty_report=beauty_report,
                evaluation_score=evaluation_score,
                comparison=comparison,
            )
            snapshots.append(snapshot)

            print(
                f"[auto_pipeline] Iteration {i}: "
                f"Evaluation={evaluation_score:.2f} "
                f"(W={walkability_index:.2f}×{self._w_weight:.2f}, "
                f"S={safety_score:.2f}×{self._s_weight:.2f}, "
                f"B={beauty_score:.2f}×{self._b_weight:.2f})"
            )

            # Detect regression from comparison
            regressed_areas = list(comparison.get("regressed_areas", []) or [])
            if regressed_areas:
                print(f"[auto_pipeline] Warning: regression detected in {regressed_areas}")

            # Track best by evaluation_score (combined metric)
            if evaluation_score > best_metric_score:
                best_metric_score = evaluation_score
                best_iteration = i
                no_improvement_count = 0
            else:
                no_improvement_count += 1

            # Early stop: consecutive rounds without improvement
            if no_improvement_count >= 2:
                print("[auto_pipeline] Early stopping: no score improvement for 2 consecutive rounds.")
                break

            # Build weakness-aware RAG queries for improvement
            weakness_queries: List[str] = []
            if regressed_areas:
                for area in regressed_areas:
                    weakness_queries.append(f"{area} street design guidelines complete streets")
            if walkability_index < 0.5:
                weakness_queries.append("pedestrian friendly street design walkability")
            if safety_score < 0.5:
                weakness_queries.append("street safety design guidelines")
            if beauty_score < 0.5:
                weakness_queries.append("urban street beauty aesthetics landscape design")

            # Generate reference-grounded improvement patch
            improvement_result = self._propose_improvement_compat(
                eval_result=eval_result,
                current_evaluation=evaluation_text,
                comparison=comparison,
                current_patch=current_patch,
                weakness_queries=weakness_queries or None,
            )
            suggested_patch = sanitize_compose_config_patch(improvement_result.get("config_patch"))
            if suggested_patch:
                current_patch.update(suggested_patch)
            snapshot.cited_evidence = list(improvement_result.get("citations", []) or [])
            snapshot.improvement_reasoning = str(improvement_result.get("reasoning", "") or "").strip()

            # Log improvement details
            improvement_path = iter_dir / "improvement.json"
            improvement_path.write_text(
                json.dumps(
                    {
                        "config_patch": dict(suggested_patch) if suggested_patch else {},
                        "citations": snapshot.cited_evidence,
                        "reasoning": snapshot.improvement_reasoning,
                        "weakness_queries": weakness_queries,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        # Copy best result to final/
        best_snap = snapshots[best_iteration]
        final_dir = self.output_dir / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        for src_path, name in [
            (best_snap.layout_path, "scene_layout.json"),
            (best_snap.scene_path, "scene.glb"),
            (best_snap.preview_path, "preview.png"),
        ]:
            if src_path and Path(src_path).exists():
                shutil.copy2(src_path, final_dir / name)

        result = IterationResult(
            iterations=snapshots,
            best_iteration=best_iteration,
            best_score=round(best_metric_score * 10.0, 3),
            best_layout_path=str(final_dir / "scene_layout.json"),
            best_scene_path=str(final_dir / "scene.glb"),
            total_iterations=len(snapshots),
        )

        # Save global iteration log
        log_path = self.output_dir / "iteration_log.json"
        log_path.write_text(
            json.dumps(_result_to_log(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(
            f"[auto_pipeline] Done. {result.total_iterations} iterations, "
            f"best score={result.best_score:.1f} at iteration {result.best_iteration}."
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_base_map_data_url(self) -> str | None:
        if self.base_map_path is None or not self.base_map_path.exists():
            return None
        try:
            data = self.base_map_path.read_bytes()
            return f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"
        except Exception:
            return None

    def _evaluate_scene_compat(
        self,
        *,
        layout_path: str,
        image_path: str | None,
        previous: IterationSnapshot | None,
    ) -> Dict[str, Any]:
        """Evaluate using the newest service API, with legacy fallback."""
        if previous is not None and hasattr(self.design_service, "evaluate_scene_with_history"):
            return dict(
                self.design_service.evaluate_scene_with_history(
                    layout_path=layout_path,
                    image_path=image_path,
                    previous_layout_path=previous.layout_path,
                    previous_image_path=previous.preview_path or None,
                    previous_score=previous.score,
                    previous_evaluation=previous.evaluation,
                )
            )
        if hasattr(self.design_service, "evaluate_scene_unified"):
            return dict(
                self.design_service.evaluate_scene_unified(
                    layout_path=layout_path,
                    image_path=image_path,
                )
            )
        if hasattr(self.design_service, "evaluate_scene"):
            return dict(
                self.design_service.evaluate_scene(
                    layout_path=layout_path,
                    image_path=image_path,
                )
            )
        raise AttributeError(
            "design_service must provide evaluate_scene_unified, "
            "evaluate_scene_with_history, or evaluate_scene."
        )

    def _propose_improvement_compat(
        self,
        *,
        eval_result: Mapping[str, Any],
        current_evaluation: str,
        comparison: Mapping[str, Any],
        current_patch: Mapping[str, Any],
        weakness_queries: List[str] | None,
    ) -> Dict[str, Any]:
        """Return an improvement patch from the service or legacy eval payload."""
        if hasattr(self.design_service, "propose_improvement"):
            return dict(
                self.design_service.propose_improvement(
                    current_evaluation=current_evaluation,
                    comparison=comparison,
                    current_patch=current_patch,
                    weakness_queries=weakness_queries,
                )
            )
        return {
            "config_patch": dict(eval_result.get("config_patch", {}) or {}),
            "citations": [],
            "reasoning": "Legacy evaluate_scene config_patch fallback.",
        }


def _score_from_eval_payload(eval_result: Mapping[str, Any]) -> float:
    """Normalize known evaluation payload score shapes to a 0-10 score."""
    if eval_result.get("overall") is not None:
        score = float(eval_result.get("overall") or 0.0)
        if score > 10.0:
            score /= 10.0
        return max(0.0, min(score, 10.0))
    if eval_result.get("score") is not None:
        score = float(eval_result.get("score") or 0.0)
        if 0.0 <= score <= 1.0:
            return score * 10.0
        return max(0.0, min(score, 10.0))
    if eval_result.get("evaluation_score") is not None:
        score = float(eval_result.get("evaluation_score") or 0.0)
        if 0.0 <= score <= 1.0:
            return score * 10.0
        if score > 10.0:
            score /= 10.0
        return max(0.0, min(score, 10.0))
    return 0.0


def _result_to_log(result: IterationResult) -> Dict[str, Any]:
    """Serialise *IterationResult* to a JSON-friendly log dict."""
    iterations_data = []
    for s in result.iterations:
        iter_data = {
            "iteration": s.iteration,
            "score": s.score,
            "evaluation": s.evaluation,
            "suggestions": s.suggestions,
            "config_patch": s.config_patch,
            "layout_path": s.layout_path,
            "preview_path": s.preview_path,
            "scene_path": s.scene_path,
            "evaluation_score": s.evaluation_score,
        }
        if s.walkability:
            iter_data["walkability"] = s.walkability.to_dict()
        if s.safety_report:
            iter_data["safety"] = s.safety_report
        if s.beauty_report:
            iter_data["beauty"] = s.beauty_report
        iterations_data.append(iter_data)
    
    return {
        "total_iterations": result.total_iterations,
        "best_iteration": result.best_iteration,
        "best_score": result.best_score,
        "best_layout_path": result.best_layout_path,
        "best_scene_path": result.best_scene_path,
        "iterations": iterations_data,
    }
