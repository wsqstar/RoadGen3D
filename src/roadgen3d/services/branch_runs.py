"""Asynchronous beam-search branch runs for design evolution."""

from __future__ import annotations

import json
import base64
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Condition, Lock, Thread
from typing import Any, Dict, List, Mapping, Sequence
from uuid import uuid4

from ..graph_templates import get_graph_template
from ..capture_3d import capture_view_paths, layout_capture_failed
from ..json_safe import make_json_safe
from ..llm.design_workflow import DesignAssistantService
from ..presets import SCENE_PRESETS
from .design_types import (
    DEFAULT_COMPOSE_CONFIG_PATCH_VALUES,
    DesignDraft,
    RagEvidence,
    sanitize_compose_config_patch,
    sanitize_scene_context,
)
from .optimization_planner import RuleBasedOptimizationPlanner


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BRANCH_RUN_DIR = (ROOT / "artifacts" / "branch_runs").resolve()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class BranchScatterPoint:
    node_id: str
    parent_id: str | None
    depth: int
    rank: int
    x: float | None
    y: float | None
    z: float | None
    overall: float | None
    walkability: float | None
    safety: float | None
    beauty: float | None
    delta_walkability: float | None
    delta_safety: float | None
    delta_beauty: float | None
    delta_overall: float | None
    is_pareto_front: bool
    pareto_rank: int | None
    dominated_by_count: int
    label: str
    status: str
    influence_summary: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dict(make_json_safe(asdict(self)))


@dataclass
class BranchNode:
    node_id: str
    parent_id: str | None
    depth: int
    rank: int
    status: str = "pending"
    prompt: str = ""
    config_patch: Dict[str, Any] = field(default_factory=dict)
    rag_evidence: List[Dict[str, Any]] = field(default_factory=list)
    optimization_directives: List[Dict[str, Any]] = field(default_factory=list)
    llm_candidate_reasoning: str = ""
    candidate_source: str = "branch_llm_candidate"
    directive_ids: List[str] = field(default_factory=list)
    rejected_edits: List[Dict[str, Any]] = field(default_factory=list)
    scene_layout_path: str = ""
    scene_glb_path: str = ""
    preview_path: str = ""
    evaluation: Dict[str, Any] = field(default_factory=dict)
    trace: Dict[str, Any] = field(default_factory=dict)
    influence_rows: List[Dict[str, Any]] = field(default_factory=list)
    artifacts_retained: bool = False
    artifact_rank: int | None = None
    artifact_paths: List[str] = field(default_factory=list)
    can_restore_artifact: bool = False
    score: float = 0.0
    error: str = ""
    blocker_details: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)
    finished_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(make_json_safe(asdict(self)))


@dataclass
class _BranchRunState:
    run_id: str
    prompt: str
    topk: int
    rounds: int
    graph_template_id: str
    knowledge_source: str
    scene_context: Dict[str, Any]
    generation_options: Dict[str, Any]
    evaluation_weights: Dict[str, float]
    output_dir: Path
    preset_id: str = ""
    preset_name: str = ""
    preset_color: str = ""
    preset_config_patch: Dict[str, Any] = field(default_factory=dict)
    benchmark_id: str = ""
    batch_id: str = ""
    persist_to_benchmark: bool = False
    target_samples: int | None = None
    search_mode: str = "llm_branch"
    early_stop_patience: int | None = None
    early_stop_min_delta: float = 0.25
    early_stop_triggered: bool = False
    early_stop_reason: str = ""
    retain_topk_artifacts: int | None = None
    score_with_rendered_views: bool = False
    status: str = "queued"
    stage: str = "queued"
    progress: int = 0
    created_at: str = field(default_factory=_utc_now)
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    best_node_id: str = ""
    frontier: List[str] = field(default_factory=list)
    nodes: List[BranchNode] = field(default_factory=list)
    operations: List[Dict[str, Any]] = field(default_factory=list)


class BranchRunService:
    """Single-process background worker for branch design runs."""

    def __init__(
        self,
        *,
        design_service: DesignAssistantService | None = None,
        output_root: str | Path | None = None,
        planner: RuleBasedOptimizationPlanner | None = None,
        benchmark_store: Any | None = None,
    ) -> None:
        self.design_service = design_service or DesignAssistantService()
        self.output_root = Path(output_root or DEFAULT_BRANCH_RUN_DIR).expanduser().resolve()
        self.planner = planner or RuleBasedOptimizationPlanner()
        self.benchmark_store = benchmark_store
        self._runs: Dict[str, _BranchRunState] = {}
        self._queue: Queue[str] = Queue()
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._worker: Thread | None = None

    def submit_run(
        self,
        *,
        prompt: str,
        topk: int = 3,
        rounds: int = 2,
        graph_template_id: str = "hkust_gz_gate",
        knowledge_source: str = "graph_rag",
        scene_context: Mapping[str, Any] | None = None,
        generation_options: Mapping[str, Any] | None = None,
        evaluation_weights: Mapping[str, float] | None = None,
        preset_id: str = "",
        preset_config_patch: Mapping[str, Any] | None = None,
        benchmark_id: str = "",
        batch_id: str = "",
        persist_to_benchmark: bool = False,
        target_samples: int | None = None,
        search_mode: str = "llm_branch",
        early_stop_patience: int | None = None,
        retain_topk_artifacts: int | None = None,
        score_with_rendered_views: bool = False,
    ) -> Dict[str, Any]:
        self._ensure_worker()
        run_id = uuid4().hex
        bounded_topk = max(1, min(int(topk or 3), 5))
        bounded_target = _bounded_target_samples(target_samples)
        bounded_rounds = max(1, min(int(rounds or 2), 5 if bounded_target else 3))
        normalized_search_mode = _normalize_search_mode(search_mode)
        preset_meta = _preset_meta(preset_id)
        normalized_preset_patch = dict(preset_config_patch or preset_meta.get("configPatch") or {})
        output_dir = self.output_root / run_id
        state = _BranchRunState(
            run_id=run_id,
            prompt=str(prompt or "").strip() or "Generate a complete street scene",
            topk=bounded_topk,
            rounds=bounded_rounds,
            graph_template_id=str(graph_template_id or "hkust_gz_gate").strip().lower(),
            knowledge_source=str(knowledge_source or "graph_rag").strip() or "graph_rag",
            scene_context=dict(scene_context or {}),
            generation_options=dict(generation_options or {}),
            evaluation_weights=_normalize_weights(evaluation_weights),
            output_dir=output_dir,
            preset_id=str(preset_meta.get("id") or preset_id or "").strip(),
            preset_name=str(preset_meta.get("nameEn") or preset_meta.get("name") or "").strip(),
            preset_color=str(preset_meta.get("color") or "").strip(),
            preset_config_patch=normalized_preset_patch,
            benchmark_id=str(benchmark_id or "").strip(),
            batch_id=str(batch_id or "").strip(),
            persist_to_benchmark=bool(persist_to_benchmark),
            target_samples=bounded_target,
            search_mode=normalized_search_mode,
            early_stop_patience=_bounded_early_stop_patience(
                early_stop_patience,
                default=20 if normalized_search_mode == "pareto" and bounded_target else None,
            ),
            retain_topk_artifacts=_bounded_retained_artifact_count(retain_topk_artifacts),
            score_with_rendered_views=bool(score_with_rendered_views),
        )
        with self._condition:
            self._runs[run_id] = state
            self._queue.put(run_id)
            self._condition.notify_all()
        return {"run_id": run_id, "status": state.status, "created_at": state.created_at}

    def get_run(self, run_id: str) -> Dict[str, Any] | None:
        run_key = str(run_id or "").strip()
        with self._lock:
            state = self._runs.get(run_key)
            if state is not None:
                return self._to_payload(state)
        return self._read_manifest_payload(run_key)

    def list_runs(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        safe_limit = max(1, int(limit))
        with self._lock:
            payloads = [self._to_payload(item, include_nodes=False) for item in self._runs.values()]
        seen = {str(item.get("run_id", "")) for item in payloads}
        for manifest_path in self.output_root.glob("*/manifest.json"):
            run_id = manifest_path.parent.name
            if run_id in seen:
                continue
            payload = self._read_manifest_path(manifest_path, include_nodes=False)
            if payload is None:
                continue
            payloads.append(payload)
            seen.add(run_id)
        payloads.sort(key=_run_sort_key, reverse=True)
        return payloads[:safe_limit]

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = Thread(target=self._worker_loop, name="roadgen3d-branch-run-worker", daemon=True)
        self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            run_id = self._queue.get()
            with self._condition:
                state = self._runs.get(run_id)
                if state is None:
                    self._condition.notify_all()
                    continue
                state.status = "running"
                state.started_at = _utc_now()
                self._record_locked(state, "context", 5, "Resolving branch-run context.")
            try:
                if state.search_mode == "pareto":
                    self._execute_pareto_run(state)
                else:
                    self._execute_run(state)
            except Exception as exc:
                with self._condition:
                    latest = self._runs.get(run_id)
                    if latest is not None:
                        latest.status = "failed"
                        latest.error = str(exc)
                        latest.finished_at = _utc_now()
                        self._record_locked(latest, "failed", latest.progress, str(exc), {"error": str(exc)})
                    self._condition.notify_all()

    def _execute_run(self, state: _BranchRunState) -> None:
        state.output_dir.mkdir(parents=True, exist_ok=True)
        graph_summary = self._build_graph_summary(state.graph_template_id)
        evidence = self._retrieve_initial_evidence(state)
        target_message = f" for up to {state.target_samples} scored samples" if state.target_samples else ""
        self._update_state(state.run_id, "llm_candidates", 10, f"Requesting initial top-k LLM candidates{target_message}.")
        candidates = self.design_service.generate_initial_config_candidates_from_graph(
            graph_summary=graph_summary,
            user_prompt=state.prompt,
            current_patch={"query": state.prompt},
            evidence=evidence,
            topk=state.topk,
        )

        current_level: List[BranchNode] = []
        for index, candidate in enumerate(candidates[: state.topk]):
            if _sample_budget_exhausted(state):
                break
            node = self._build_node_from_candidate(
                state,
                candidate,
                depth=0,
                rank=index + 1,
                parent=None,
                evidence=evidence,
            )
            self._run_node(state, node)
            current_level.append(node)

        depth = 1
        while current_level:
            if state.target_samples is None and depth >= state.rounds:
                break
            if _sample_budget_exhausted(state):
                break
            frontier = self._rank_frontier(current_level, state.topk)
            self._set_frontier(state.run_id, [node.node_id for node in frontier])
            next_level: List[BranchNode] = []
            for parent in frontier:
                if _sample_budget_exhausted(state):
                    break
                if parent.status != "succeeded":
                    continue
                weakness_queries = _weakness_queries(parent.optimization_directives)
                child_evidence = self._retrieve_branch_evidence(
                    state,
                    queries=weakness_queries,
                    topk=max(state.topk, 6),
                )
                self._update_state(
                    state.run_id,
                    "llm_improvement",
                    _progress_for_branch_state(state, depth),
                    f"Requesting improvement candidates for {parent.node_id}.",
                )
                child_candidates = self.design_service.propose_improvement_candidates(
                    current_evaluation=str(parent.evaluation.get("evaluation", "")),
                    comparison={},
                    current_patch=parent.config_patch,
                    optimization_directives=parent.optimization_directives,
                    topk=state.topk,
                    weakness_queries=weakness_queries,
                    knowledge_source=state.knowledge_source,
                    evidence=child_evidence,
                )
                for index, candidate in enumerate(child_candidates[: state.topk]):
                    if _sample_budget_exhausted(state):
                        break
                    accepted_patch, rejected = self.planner.sanitize_candidate_patch(
                        candidate.get("compose_config_patch"),
                        current_patch=parent.config_patch,
                        directives=parent.optimization_directives,
                    )
                    merged_patch = dict(parent.config_patch)
                    merged_patch.update(accepted_patch)
                    child = self._build_node_from_candidate(
                        state,
                        {
                            **candidate,
                            "compose_config_patch": merged_patch,
                            "rejected_edits": rejected,
                        },
                        depth=depth,
                        rank=index + 1,
                        parent=parent,
                        evidence=child_evidence,
                    )
                    self._run_node(state, child)
                    next_level.append(child)
            current_level = next_level
            if not current_level:
                break
            depth += 1

        with self._condition:
            latest = self._runs[state.run_id]
            best = self._best_node(latest.nodes)
            latest.best_node_id = best.node_id if best else ""
            latest.frontier = [node.node_id for node in self._rank_frontier(latest.nodes, latest.topk)]
            latest.status = "succeeded"
            latest.stage = "succeeded"
            latest.progress = 100
            latest.finished_at = _utc_now()
            latest.operations.append(_operation("succeeded", 100, "Branch run completed."))
            self._write_manifest(latest)
            self._condition.notify_all()

    def _execute_pareto_run(self, state: _BranchRunState) -> None:
        state.output_dir.mkdir(parents=True, exist_ok=True)
        evidence = self._retrieve_initial_evidence(state)
        target_goal = state.target_samples or max(1, state.topk * state.rounds)
        max_attempts = max(1, target_goal * 2)
        self._update_state(
            state.run_id,
            "pareto_search",
            10,
            f"Sampling up to {target_goal} scored scenes with deterministic Pareto search.",
        )

        no_gain_streak = 0
        best_score = -1.0
        sample_index = 0
        while _completed_sample_count(state.nodes) < target_goal and _attempted_sample_count(state.nodes) < max_attempts:
            previous_succeeded = _succeeded_scored_nodes(state.nodes)
            parent = _select_pareto_parent(previous_succeeded, sample_index)
            candidate = _build_pareto_search_candidate(
                state,
                evidence=evidence,
                sample_index=sample_index,
                parent=parent,
            )
            node = self._build_node_from_candidate(
                state,
                candidate,
                depth=sample_index // max(1, state.topk),
                rank=(sample_index % max(1, state.topk)) + 1,
                parent=parent,
                evidence=evidence,
            )
            self._run_node(state, node)
            sample_index += 1

            if node.status != "succeeded":
                continue

            is_new_pareto = _is_non_dominated_against(node, previous_succeeded)
            score_gain = node.score - best_score
            improved_overall = score_gain >= state.early_stop_min_delta
            best_score = max(best_score, node.score)
            if is_new_pareto or improved_overall:
                no_gain_streak = 0
            else:
                no_gain_streak += 1

            pareto_ids = _pareto_front_ids(state.nodes)
            self._set_frontier(state.run_id, pareto_ids)
            completed = _completed_sample_count(state.nodes)
            self._update_state(
                state.run_id,
                "pareto_search",
                min(98, 10 + int((completed / max(target_goal, 1)) * 86)),
                f"Pareto search scored {completed}/{target_goal} scenes.",
                {
                    "completed_samples": completed,
                    "pareto_front_size": len(pareto_ids),
                    "no_gain_streak": no_gain_streak,
                },
            )

            if state.early_stop_patience and no_gain_streak >= state.early_stop_patience:
                with self._condition:
                    latest = self._runs[state.run_id]
                    latest.early_stop_triggered = True
                    latest.early_stop_reason = (
                        f"No Pareto-front or overall-score improvement for "
                        f"{state.early_stop_patience} consecutive scored samples."
                    )
                    latest.operations.append(_operation(
                        "early_stop",
                        latest.progress,
                        latest.early_stop_reason,
                        {"no_gain_streak": no_gain_streak, "pareto_front_size": len(pareto_ids)},
                    ))
                    self._condition.notify_all()
                break

        with self._condition:
            latest = self._runs[state.run_id]
            best = self._best_node(latest.nodes)
            latest.best_node_id = best.node_id if best else ""
            latest.frontier = _pareto_front_ids(latest.nodes)
            latest.status = "succeeded"
            latest.stage = "succeeded"
            latest.progress = 100
            latest.finished_at = _utc_now()
            complete_message = (
                f"Pareto search completed with {_completed_sample_count(latest.nodes)} scored samples."
                if not latest.early_stop_triggered
                else f"Pareto search early-stopped with {_completed_sample_count(latest.nodes)} scored samples."
            )
            latest.operations.append(_operation("succeeded", 100, complete_message))
            self._write_manifest(latest)
            self._condition.notify_all()

    def _build_node_from_candidate(
        self,
        state: _BranchRunState,
        candidate: Mapping[str, Any],
        *,
        depth: int,
        rank: int,
        parent: BranchNode | None,
        evidence: Sequence[RagEvidence],
    ) -> BranchNode:
        node_id = f"d{depth}_r{rank}_{uuid4().hex[:8]}"
        patch = sanitize_compose_config_patch(candidate.get("compose_config_patch"))
        for field_name, default_value in DEFAULT_COMPOSE_CONFIG_PATCH_VALUES.items():
            patch.setdefault(field_name, default_value)
        patch.setdefault("query", state.prompt)
        node = BranchNode(
            node_id=node_id,
            parent_id=parent.node_id if parent else None,
            depth=depth,
            rank=rank,
            prompt=state.prompt,
            config_patch=patch,
            rag_evidence=[item.to_dict() for item in evidence],
            llm_candidate_reasoning=str(candidate.get("reasoning", "") or candidate.get("design_summary", "") or ""),
            candidate_source=str(candidate.get("candidate_source") or "branch_llm_candidate"),
            directive_ids=[str(item) for item in candidate.get("directive_ids", []) or []],
            rejected_edits=list(candidate.get("rejected_edits", []) or []),
        )
        node.influence_rows = _build_influence_rows(node, parent=parent)
        node.trace = _build_branch_node_trace(state, node)
        return node

    def _run_node(self, state: _BranchRunState, node: BranchNode) -> None:
        node_dir = state.output_dir / node.node_id
        node_dir.mkdir(parents=True, exist_ok=True)
        node.status = "running"
        node.influence_rows = _build_influence_rows(node, parent=_find_node(state.nodes, node.parent_id))
        node.trace = _build_branch_node_trace(state, node, artifact_dir=node_dir)
        self._append_node(state.run_id, node)
        _write_json(node_dir / "config_patch.json", node.config_patch)
        try:
            draft = DesignDraft(
                normalized_scene_query=str(node.config_patch.get("query", state.prompt)),
                compose_config_patch=node.config_patch,
                citations_by_field={},
                design_summary=node.llm_candidate_reasoning,
                parameter_sources_by_field={key: node.candidate_source for key in node.config_patch},
            )
            generation_options = {
                **state.generation_options,
                "out_dir": str(node_dir),
                "preset_id": "skip_llm",
                "benchmark_preset_id": state.preset_id,
                "random_seed": int(1000 + node.depth * 100 + node.rank),
            }
            if state.target_samples:
                if state.retain_topk_artifacts:
                    generation_options.setdefault("export_format", "glb")
                    generation_options.setdefault("capture_defer_glb_retention", True)
                    generation_options.setdefault("build_production_artifacts", False)
                    generation_options.setdefault("render_presentation_artifacts", bool(state.score_with_rendered_views))
                else:
                    generation_options.setdefault("export_format", "none")
                    generation_options.setdefault("build_production_artifacts", False)
                    generation_options.setdefault("render_presentation_artifacts", False)
            scene_context = {
                "layout_mode": "graph_template",
                "graph_template_id": state.graph_template_id,
                **state.scene_context,
            }
            result = self.design_service.generate_scene(
                draft,
                scene_context=sanitize_scene_context(scene_context),
                generation_options=generation_options,
            )
            node.scene_layout_path = result.get("scene_layout_path", "") if isinstance(result, Mapping) else result.scene_layout_path
            node.scene_glb_path = result.get("scene_glb_path", "") if isinstance(result, Mapping) else result.scene_glb_path
            node.can_restore_artifact = _node_has_restorable_glb(node)
            rendered_views = (
                _rendered_views_for_evaluation(node.scene_layout_path, limit=3)
                if state.score_with_rendered_views
                else []
            )
            evaluation = self.design_service.evaluate_scene_unified(
                layout_path=node.scene_layout_path,
                rendered_views=rendered_views,
            )
            branch_evaluation = _ensure_branch_evaluation_scores(evaluation, state.evaluation_weights)
            node.evaluation = dict(branch_evaluation)
            node.score = _combined_score(branch_evaluation, state.evaluation_weights)
            directives = self.planner.plan(
                evaluation=branch_evaluation,
                current_patch=node.config_patch,
                generation_diagnostics=getattr(result, "summary", {}) if not isinstance(result, Mapping) else result.get("summary", {}),
            )
            node.optimization_directives = [directive.to_dict() for directive in directives]
            node.status = "succeeded"
            node.finished_at = _utc_now()
            node.influence_rows = _build_influence_rows(node, parent=_find_node(state.nodes, node.parent_id))
            node.trace = _build_branch_node_trace(state, node, artifact_dir=node_dir)
            _write_json(node_dir / "evaluation.json", node.evaluation)
            _write_json(node_dir / "optimization_directives.json", node.optimization_directives)
        except Exception as exc:
            node.status = "failed"
            node.error = str(exc)
            node.blocker_details = {"error": str(exc), "stage": "generate_or_evaluate"}
            node.finished_at = _utc_now()
            node.influence_rows = _build_influence_rows(node, parent=_find_node(state.nodes, node.parent_id))
            node.trace = _build_branch_node_trace(state, node, artifact_dir=node_dir)
        finally:
            _write_json(node_dir / "generation_trace.json", node.trace)
            _write_json(node_dir / "node.json", node.to_dict())
            self._replace_node(state.run_id, node)
            if state.retain_topk_artifacts:
                self._enforce_artifact_retention(state.run_id)

    def _retrieve_initial_evidence(self, state: _BranchRunState) -> List[RagEvidence]:
        return self._retrieve_branch_evidence(
            state,
            queries=(state.prompt,),
            topk=max(state.topk, 6),
        )

    def _retrieve_branch_evidence(
        self,
        state: _BranchRunState,
        *,
        queries: Sequence[str],
        topk: int,
    ) -> List[RagEvidence]:
        items: List[RagEvidence] = []
        if state.knowledge_source != "none":
            for query in queries:
                query_text = str(query or "").strip()
                if not query_text:
                    continue
                try:
                    items.extend(self.design_service.search_knowledge(
                        query=query_text,
                        topk=topk,
                        knowledge_source=state.knowledge_source,
                    ))
                except Exception:
                    continue
        retrieve_scenario_parameters = getattr(self.design_service, "_retrieve_scenario_parameter_evidence", None)
        if callable(retrieve_scenario_parameters) and state.knowledge_source != "none":
            try:
                items.extend(retrieve_scenario_parameters(
                    queries=queries,
                    topk=topk,
                ))
            except Exception:
                pass
        return _merge_rag_evidence(items, topk=max(1, int(topk)))

    def _build_graph_summary(self, graph_template_id: str) -> Dict[str, Any]:
        try:
            return dict(get_graph_template(graph_template_id).to_dict())
        except Exception:
            return {"template_id": graph_template_id, "description": "Unknown graph template"}

    def _append_node(self, run_id: str, node: BranchNode) -> None:
        with self._condition:
            state = self._runs[run_id]
            state.nodes.append(node)
            state.operations.append(_operation("node_running", state.progress, f"Running {node.node_id}.", {"node_id": node.node_id}))
            self._condition.notify_all()

    def _replace_node(self, run_id: str, node: BranchNode) -> None:
        with self._condition:
            state = self._runs[run_id]
            state.nodes = [node if item.node_id == node.node_id else item for item in state.nodes]
            if state.target_samples:
                completed = _completed_sample_count(state.nodes)
                sample_progress = int(min(98, 10 + (completed / max(state.target_samples, 1)) * 86))
                state.progress = max(state.progress, sample_progress)
            if node.status == "succeeded":
                best = self._best_node(state.nodes)
                state.best_node_id = best.node_id if best else ""
            state.operations.append(_operation(
                f"node_{node.status}",
                state.progress,
                f"{node.node_id} {node.status}.",
                {
                    "node_id": node.node_id,
                    "score": node.score,
                    "error": node.error,
                    "completed_samples": _completed_sample_count(state.nodes),
                    "attempted_samples": _attempted_sample_count(state.nodes),
                },
            ))
            self._write_manifest(state)
            self._condition.notify_all()

    def _enforce_artifact_retention(self, run_id: str) -> None:
        with self._condition:
            state = self._runs[run_id]
            retain_count = state.retain_topk_artifacts
            if not retain_count:
                return
            ranked = self._rank_frontier(state.nodes, retain_count)
            keep_ids = {node.node_id for node in ranked}
            rank_by_id = {node.node_id: index + 1 for index, node in enumerate(ranked)}
            changed = False
            for node in state.nodes:
                if node.status != "succeeded":
                    continue
                node_dir = state.output_dir / node.node_id
                if node.node_id in keep_ids:
                    paths = _existing_retained_artifact_paths(node, node_dir)
                    next_rank = rank_by_id.get(node.node_id)
                    can_restore = _node_has_restorable_glb(node)
                    if (
                        not node.artifacts_retained
                        or node.artifact_rank != next_rank
                        or node.artifact_paths != paths
                        or node.can_restore_artifact != can_restore
                    ):
                        node.artifacts_retained = True
                        node.artifact_rank = next_rank
                        node.artifact_paths = paths
                        node.can_restore_artifact = can_restore
                        node.trace = _build_branch_node_trace(state, node, artifact_dir=node_dir)
                        _write_json(node_dir / "generation_trace.json", node.trace)
                        _write_json(node_dir / "node.json", node.to_dict())
                        changed = True
                    continue
                capture_failed = layout_capture_failed(node.scene_layout_path)
                if _prune_node_artifacts(node, node_dir, state.output_dir):
                    changed = True
                if node.artifacts_retained or node.artifact_rank is not None or node.artifact_paths or (node.scene_glb_path and not capture_failed):
                    node.artifacts_retained = False
                    node.artifact_rank = None
                    node.artifact_paths = []
                    if not capture_failed:
                        node.scene_glb_path = ""
                    node.preview_path = ""
                    node.can_restore_artifact = bool(capture_failed and _node_has_restorable_glb(node))
                    node.trace = _build_branch_node_trace(state, node, artifact_dir=node_dir)
                    _write_json(node_dir / "generation_trace.json", node.trace)
                    _write_json(node_dir / "node.json", node.to_dict())
                    changed = True
            if changed:
                state.operations.append(_operation(
                    "artifact_retention",
                    state.progress,
                    f"Retained artifacts for top {retain_count} scored nodes.",
                    {
                        "retain_topk_artifacts": retain_count,
                        "retained_artifact_nodes": [node.node_id for node in ranked],
                    },
                ))
                self._write_manifest(state)
                self._condition.notify_all()

    def _update_state(self, run_id: str, stage: str, progress: int, message: str, detail: Mapping[str, Any] | None = None) -> None:
        with self._condition:
            state = self._runs[run_id]
            self._record_locked(state, stage, progress, message, detail)
            self._condition.notify_all()

    def _set_frontier(self, run_id: str, frontier: List[str]) -> None:
        with self._condition:
            state = self._runs[run_id]
            state.frontier = list(frontier)
            self._condition.notify_all()

    def _record_locked(
        self,
        state: _BranchRunState,
        stage: str,
        progress: int,
        message: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        state.stage = str(stage)
        state.progress = max(int(state.progress), max(0, min(100, int(progress))))
        state.operations.append(_operation(stage, state.progress, message, detail))

    def _to_payload(self, state: _BranchRunState, *, include_nodes: bool = True) -> Dict[str, Any]:
        nodes = list(state.nodes)
        pareto_front = _pareto_front_ids(nodes)
        retained_artifact_nodes = [
            node.node_id for node in sorted(
                (item for item in nodes if item.artifacts_retained),
                key=lambda item: item.artifact_rank or 10_000,
            )
        ]
        return dict(make_json_safe({
            "run_id": state.run_id,
            "status": state.status,
            "stage": state.stage,
            "progress": state.progress,
            "created_at": state.created_at,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "error": state.error,
            "prompt": state.prompt,
            "topk": state.topk,
            "rounds": state.rounds,
            "target_samples": state.target_samples,
            "search_mode": state.search_mode,
            "preset_id": state.preset_id,
            "preset_name": state.preset_name,
            "preset_color": state.preset_color,
            "preset_config_patch": state.preset_config_patch,
            "benchmark_id": state.benchmark_id,
            "batch_id": state.batch_id,
            "persist_to_benchmark": state.persist_to_benchmark,
            "early_stop_patience": state.early_stop_patience,
            "early_stop_triggered": state.early_stop_triggered,
            "early_stop_reason": state.early_stop_reason,
            "retain_topk_artifacts": state.retain_topk_artifacts,
            "score_with_rendered_views": state.score_with_rendered_views,
            "retained_artifact_nodes": retained_artifact_nodes,
            "retained_artifact_count": len(retained_artifact_nodes),
            "completed_samples": _completed_sample_count(nodes),
            "attempted_samples": _attempted_sample_count(nodes),
            "graph_template_id": state.graph_template_id,
            "knowledge_source": state.knowledge_source,
            "best_node_id": state.best_node_id,
            "frontier": list(state.frontier),
            "pareto_front": pareto_front,
            "pareto_front_size": len(pareto_front),
            "nodes": [node.to_dict() for node in nodes] if include_nodes else [],
            "scatter_points": [point.to_dict() for point in _scatter_points(nodes)],
            "operations": list(state.operations[-100:]),
            "artifact_dir": str(state.output_dir),
        }))

    def _write_manifest(self, state: _BranchRunState) -> None:
        state.output_dir.mkdir(parents=True, exist_ok=True)
        payload = self._to_payload(state)
        _write_json(state.output_dir / "manifest.json", payload)
        if state.persist_to_benchmark and self.benchmark_store is not None:
            try:
                self.benchmark_store.upsert_branch_run(payload, default_preset_id=state.preset_id or "custom_legacy")
            except Exception:
                pass

    def _read_manifest_payload(self, run_id: str) -> Dict[str, Any] | None:
        if not run_id or "/" in run_id or "\\" in run_id:
            return None
        manifest_path = (self.output_root / run_id / "manifest.json").resolve()
        try:
            manifest_path.relative_to(self.output_root)
        except ValueError:
            return None
        return self._read_manifest_path(manifest_path)

    def _read_manifest_path(self, manifest_path: Path, *, include_nodes: bool = True) -> Dict[str, Any] | None:
        if not manifest_path.exists() or not manifest_path.is_file():
            return None
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        payload.setdefault("run_id", manifest_path.parent.name)
        payload.setdefault("artifact_dir", str(manifest_path.parent))
        if not include_nodes:
            payload["nodes"] = []
        return dict(make_json_safe(payload))

    @staticmethod
    def _rank_frontier(nodes: Sequence[BranchNode], topk: int) -> List[BranchNode]:
        succeeded = [node for node in nodes if node.status == "succeeded"]
        return sorted(succeeded, key=lambda item: item.score, reverse=True)[: max(1, int(topk))]

    @staticmethod
    def _best_node(nodes: Sequence[BranchNode]) -> BranchNode | None:
        ranked = BranchRunService._rank_frontier(nodes, 1)
        return ranked[0] if ranked else None


def _run_sort_key(payload: Mapping[str, Any]) -> str:
    return str(
        payload.get("created_at")
        or payload.get("started_at")
        or payload.get("finished_at")
        or ""
    )


def _preset_meta(preset_id: str | None) -> Dict[str, Any]:
    normalized = str(preset_id or "").strip()
    if not normalized:
        return {}
    for preset in SCENE_PRESETS:
        if str(preset.get("id") or "") == normalized:
            return dict(preset)
    return {"id": normalized}


def _select_pareto_parent(nodes: Sequence[BranchNode], sample_index: int) -> BranchNode | None:
    if not nodes:
        return None
    front_ids = set(_pareto_front_ids(nodes))
    pool = [node for node in nodes if node.node_id in front_ids] or list(nodes)
    ranked = sorted(pool, key=lambda item: item.score, reverse=True)
    return ranked[sample_index % len(ranked)] if ranked else None


def _build_pareto_search_candidate(
    state: _BranchRunState,
    *,
    evidence: Sequence[RagEvidence],
    sample_index: int,
    parent: BranchNode | None,
) -> Dict[str, Any]:
    patch = _pareto_search_patch(state, evidence=evidence, sample_index=sample_index)
    parent_label = f" against parent {parent.node_id}" if parent else ""
    return {
        "candidate_id": f"pareto_{sample_index + 1:03d}",
        "candidate_source": "pareto_search",
        "compose_config_patch": patch,
        "directive_ids": [],
        "rejected_edits": [],
        "reasoning": (
            f"Deterministic Pareto sample {sample_index + 1}{parent_label}: "
            f"sidewalk={patch.get('sidewalk_width_m')}m, road={patch.get('road_width_m')}m, "
            f"density={patch.get('density')}, objective={patch.get('objective_profile')}."
        ),
    }


def _pareto_search_patch(
    state: _BranchRunState,
    *,
    evidence: Sequence[RagEvidence],
    sample_index: int,
) -> Dict[str, Any]:
    base_patch = sanitize_compose_config_patch(state.preset_config_patch)
    hints = _numeric_parameter_hints(evidence)
    n = sample_index + 1
    sidewalk_min, sidewalk_max = _hinted_range(2.0, 6.2, hints.get("sidewalk_width_m"))
    road_min, road_max = _hinted_range(6.0, 15.5, hints.get("road_width_m"))
    density_min, density_max = _hinted_range(0.45, 1.35, hints.get("density"))
    building_density_min, building_density_max = _hinted_range(0.25, 0.9, hints.get("building_density"))
    building_max_min, building_max_max = _hinted_range(4.0, 16.0, hints.get("building_max_per_100m"))

    street_types = (
        "mixed_use",
        "walkable_commercial_corridor",
        "transit_priority",
        "quiet_residential",
        "balanced_complete",
    )
    objective_profiles = ("balanced", "greening", "commerce", "transit")
    demand_levels = ("low", "medium", "high")
    lane_count = 1 + min(3, int(_halton(n, 17) * 4))

    sampled_patch = {
        "query": state.prompt,
        "target_street_type": base_patch.get("target_street_type") or street_types[sample_index % len(street_types)],
        "objective_profile": base_patch.get("objective_profile") or objective_profiles[(sample_index // len(street_types)) % len(objective_profiles)],
        "sidewalk_width_m": round(_lerp(sidewalk_min, sidewalk_max, _halton(n, 2)), 2),
        "road_width_m": round(_lerp(road_min, road_max, _halton(n, 3)), 2),
        "density": round(_lerp(density_min, density_max, _halton(n, 5)), 3),
        "building_density": round(_lerp(building_density_min, building_density_max, _halton(n, 7)), 3),
        "building_max_per_100m": round(_lerp(building_max_min, building_max_max, _halton(n, 11)), 1),
        "lane_count": lane_count,
        "ped_demand_level": base_patch.get("ped_demand_level") or demand_levels[min(2, int(_halton(n, 13) * 3))],
        "bike_demand_level": base_patch.get("bike_demand_level") or demand_levels[min(2, int(_halton(n + 7, 13) * 3))],
        "transit_demand_level": base_patch.get("transit_demand_level") or demand_levels[min(2, int(_halton(n, 19) * 3))],
        "vehicle_demand_level": base_patch.get("vehicle_demand_level") or demand_levels[min(2, int(_halton(n + 11, 19) * 3))],
        "layout_solver": "hybrid_milp_v1",
        "allow_solver_fallback": True,
    }
    merged = dict(base_patch)
    merged.update(sampled_patch)
    return merged


def _numeric_parameter_hints(evidence: Sequence[RagEvidence]) -> Dict[str, float]:
    hints: Dict[str, float] = {}
    for item in evidence:
        payload = item.to_dict() if hasattr(item, "to_dict") else dict(item)  # type: ignore[arg-type]
        if not _is_scenario_parameter_evidence(payload):
            continue
        parsed = _parse_evidence_text(payload.get("text"))
        name = str(parsed.get("parameter_name") or _parameter_name_from_chunk_id(str(payload.get("chunk_id", ""))))
        value = _nullable_float(parsed.get("normalized_value", parsed.get("raw_value")))
        if name and value is not None:
            hints[name] = value
    return hints


def _hinted_range(default_min: float, default_max: float, hint: float | None) -> tuple[float, float]:
    if hint is None:
        return default_min, default_max
    lower = max(default_min, float(hint) * 0.65)
    upper = min(default_max, float(hint) * 1.45)
    if upper <= lower:
        return default_min, default_max
    return lower, upper


def _halton(index: int, base: int) -> float:
    result = 0.0
    fraction = 1.0 / float(base)
    current = max(1, int(index))
    while current > 0:
        result += fraction * (current % base)
        current //= base
        fraction /= float(base)
    return result


def _lerp(min_value: float, max_value: float, unit: float) -> float:
    return float(min_value) + (float(max_value) - float(min_value)) * max(0.0, min(1.0, float(unit)))


def _rendered_views_for_evaluation(layout_path: str, *, limit: int = 3) -> List[Dict[str, str]]:
    layout = Path(str(layout_path or "")).expanduser()
    if not layout.exists():
        return []
    try:
        payload = json.loads(layout.read_text(encoding="utf-8"))
    except Exception:
        return []
    summary = dict(payload.get("summary", {}) or {})
    render_views_3d = list(summary.get("render_views_3d", []) or [])
    views_3d = _encoded_render_views(
        _rank_3d_render_views(render_views_3d),
        limit=limit,
        label_prefix="3D capture",
    )
    if views_3d:
        return views_3d
    render_views = list(summary.get("render_views", []) or [])
    ranked = sorted(render_views, key=lambda item: (
        0 if str(item.get("name", "") or "").startswith("final_") else 1,
        str(item.get("name", "") or ""),
    ))
    return _encoded_render_views(ranked, limit=limit, label_prefix="Rendered view")


def _rank_3d_render_views(render_views: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    selected: List[Mapping[str, Any]] = []
    used_ids: set[int] = set()
    kind_groups = (
        {"pedestrian", "street"},
        {"junction"},
        {"overview"},
    )
    for kinds in kind_groups:
        candidates = [
            (idx, view)
            for idx, view in enumerate(render_views)
            if idx not in used_ids and str(view.get("kind", "") or "").strip().lower() in kinds
        ]
        if not candidates:
            continue
        idx, view = max(
            candidates,
            key=lambda item: (int(item[1].get("priority", 0) or 0), str(item[1].get("view_id", item[1].get("name", "")) or "")),
        )
        used_ids.add(idx)
        selected.append(view)
    remaining = [
        (idx, view)
        for idx, view in enumerate(render_views)
        if idx not in used_ids
    ]
    remaining.sort(
        key=lambda item: (
            -int(item[1].get("priority", 0) or 0),
            str(item[1].get("view_id", item[1].get("name", "")) or ""),
        )
    )
    selected.extend(view for _, view in remaining)
    return selected


def _encoded_render_views(
    ranked: Sequence[Mapping[str, Any]],
    *,
    limit: int,
    label_prefix: str,
) -> List[Dict[str, str]]:
    views: List[Dict[str, str]] = []
    for index, view in enumerate(ranked):
        if len(views) >= max(1, int(limit)):
            break
        path = Path(str(view.get("path", "") or view.get("image_path", "") or "")).expanduser()
        if not path.exists():
            continue
        mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        try:
            image_data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        except Exception:
            continue
        view_id = str(view.get("view_id", "") or view.get("name", "") or f"view_{index + 1}")
        views.append({
            "view_id": view_id,
            "label": str(view.get("label", "") or view.get("title", "") or view.get("name", "") or f"{label_prefix} {index + 1}"),
            "image_data_url": image_data_url,
        })
    return views


def _render_view_paths(layout_path: str) -> List[Path]:
    layout = Path(str(layout_path or "")).expanduser()
    if not layout.exists():
        return []
    try:
        payload = json.loads(layout.read_text(encoding="utf-8"))
    except Exception:
        return []
    summary = dict(payload.get("summary", {}) or {})
    paths: List[Path] = []
    for view in list(summary.get("render_views", []) or []):
        path_text = str(view.get("path", "") or "").strip()
        if path_text:
            paths.append(Path(path_text).expanduser())
    return paths


def _existing_retained_artifact_paths(node: BranchNode, node_dir: Path) -> List[str]:
    paths: List[Path] = []
    if node.scene_glb_path:
        paths.append(Path(node.scene_glb_path).expanduser())
    paths.extend(capture_view_paths(node.scene_layout_path))
    paths.extend(_render_view_paths(node.scene_layout_path))
    view_dir = node_dir / "presentation_views"
    if view_dir.exists():
        paths.append(view_dir)
    existing = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except Exception:
            continue
        if not resolved.exists():
            continue
        text = str(resolved)
        if text in seen:
            continue
        seen.add(text)
        existing.append(text)
    return existing


def _node_has_restorable_glb(node: BranchNode) -> bool:
    if not node.scene_layout_path or not node.scene_glb_path:
        return False
    try:
        return Path(node.scene_layout_path).expanduser().exists() and Path(node.scene_glb_path).expanduser().exists()
    except Exception:
        return False


def _prune_node_artifacts(node: BranchNode, node_dir: Path, run_dir: Path) -> bool:
    changed = False
    candidates: List[Path] = []
    if node.scene_glb_path and not layout_capture_failed(node.scene_layout_path):
        candidates.append(Path(node.scene_glb_path).expanduser())
    candidates.extend(_render_view_paths(node.scene_layout_path))
    candidates.append(node_dir / "presentation_views")
    for path in candidates:
        changed = _safe_delete_path(path, run_dir) or changed
    return changed


def _safe_delete_path(path: Path, root: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
        root_resolved = root.expanduser().resolve()
    except Exception:
        return False
    if resolved == root_resolved or root_resolved not in resolved.parents:
        return False
    if not resolved.exists():
        return False
    if resolved.is_dir():
        shutil.rmtree(resolved)
    else:
        resolved.unlink()
    return True


def _combined_score(evaluation: Mapping[str, Any], weights: Mapping[str, float]) -> float:
    values = {
        "walkability": _nullable_float(evaluation.get("walkability")),
        "safety": _nullable_float(evaluation.get("safety")),
        "beauty": _nullable_float(evaluation.get("beauty")),
    }
    available = {key: value for key, value in values.items() if value is not None}
    if not available:
        return 0.0
    total_weight = sum(float(weights.get(key, 0.0)) for key in available)
    if total_weight <= 0:
        return sum(available.values()) / len(available)
    return sum(float(weights.get(key, 0.0)) * value for key, value in available.items()) / total_weight


def _ensure_branch_evaluation_scores(evaluation: Mapping[str, Any], weights: Mapping[str, float]) -> Dict[str, Any]:
    """Fill branch-analysis score axes when visual LLM scores are unavailable.

    The public unified evaluator intentionally leaves safety/beauty as null
    without rendered visual input. Branch traces still need stable 3D axes, so
    this local fallback uses numeric structural indicators and records provenance.
    """
    result = dict(evaluation or {})
    indicators = dict(result.get("indicators", {}) or {})
    fallback = dict(result.get("branch_score_fallback", {}) or {})

    walkability = _nullable_float(result.get("walkability"))
    if _nullable_float(result.get("safety")) is None:
        safety_value, safety_source = _branch_metric_fallback(
            indicators,
            primary_keys=("safety_lighting", "safety_visibility", "safety_protection", "safety_activation"),
            secondary_keys=("protection", "comfort"),
            walkability=walkability,
        )
        if safety_value is not None:
            result["safety"] = safety_value
            fallback["safety"] = {
                "source": safety_source,
                "reason": "visual safety LLM score unavailable for branch analysis",
                "value": safety_value,
            }

    if _nullable_float(result.get("beauty")) is None:
        beauty_value, beauty_source = _branch_metric_fallback(
            indicators,
            primary_keys=(
                "beauty_coherence",
                "beauty_human_scale",
                "beauty_material_contrast",
                "beauty_visual_interest",
            ),
            secondary_keys=("delight", "comfort"),
            walkability=walkability,
        )
        if beauty_value is not None:
            result["beauty"] = beauty_value
            fallback["beauty"] = {
                "source": beauty_source,
                "reason": "visual beauty LLM score unavailable for branch analysis",
                "value": beauty_value,
            }

    if _nullable_float(result.get("overall")) is None:
        overall = _combined_score(result, weights)
        if overall > 0:
            result["overall"] = round(overall, 3)
            fallback["overall"] = {
                "source": "weighted_branch_scores",
                "reason": "overall recomputed from branch-analysis axes",
                "value": result["overall"],
            }

    if fallback:
        result["branch_score_fallback"] = fallback
    return result


def _branch_metric_fallback(
    indicators: Mapping[str, Any],
    *,
    primary_keys: Sequence[str],
    secondary_keys: Sequence[str],
    walkability: float | None,
) -> tuple[float | None, str]:
    primary = _mean_numeric_indicators(indicators, primary_keys)
    if primary is not None:
        return primary, "visual_subscores"
    secondary = _mean_numeric_indicators(indicators, secondary_keys)
    if secondary is not None:
        return secondary, "structural_walkability_proxy"
    if walkability is not None:
        return _clamp_score(walkability), "walkability_proxy"
    return None, "unavailable"


def _mean_numeric_indicators(indicators: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    values = [_nullable_float(indicators.get(key)) for key in keys]
    numeric = [_clamp_score(value) for value in values if value is not None]
    if not numeric:
        return None
    return round(sum(numeric) / len(numeric), 3)


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _build_branch_node_trace(
    state: _BranchRunState,
    node: BranchNode,
    *,
    artifact_dir: Path | None = None,
) -> Dict[str, Any]:
    evaluation_status = "pending"
    if node.status == "failed":
        evaluation_status = "failed"
    elif node.evaluation:
        evaluation_status = "succeeded"
    parameter_sources = {key: node.candidate_source for key in node.config_patch}
    candidate_stage = "pareto_search" if node.candidate_source == "pareto_search" else "llm_candidate"
    candidate_label = (
        "Pareto parameter sample generated."
        if node.candidate_source == "pareto_search"
        else "LLM candidate generated."
    )
    return dict(make_json_safe({
        "schema_version": "generation_trace_v1",
        "run_id": state.run_id,
        "node_id": node.node_id,
        "status": node.status,
        "created_at": node.created_at,
        "finished_at": node.finished_at,
        "error": node.error,
        "provenance": {
            "rag_evidence": list(node.rag_evidence),
            "rag_queries": [state.prompt],
            "citations_by_field": {},
            "parameter_sources_by_field": parameter_sources,
            "knowledge_source": state.knowledge_source,
            "evidence_count": len(node.rag_evidence),
            "preset_id": state.preset_id,
            "benchmark_id": state.benchmark_id,
            "batch_id": state.batch_id,
        },
        "llm_recommendation": {
            "normalized_scene_query": str(node.config_patch.get("query", state.prompt)),
            "design_summary": node.llm_candidate_reasoning,
            "config_patch": dict(node.config_patch),
            "raw_fields": sorted(node.config_patch),
            "defaulted_fields": [],
            "overridden_fields": [],
            "risk_notes": [],
            "derivation_status": node.candidate_source,
        },
        "influence_rows": list(node.influence_rows),
        "process": {
            "growth_tree_node": {
                "node_id": node.node_id,
                "parent_id": node.parent_id,
                "depth": node.depth,
                "rank": node.rank,
                "status": node.status,
                "score": node.score,
                "artifacts_retained": node.artifacts_retained,
                "artifact_rank": node.artifact_rank,
            },
            "stage_tree": [
                {
                    "id": candidate_stage,
                    "stage": candidate_stage,
                    "label": candidate_label,
                    "status": "completed",
                    "progress": 15,
                    "children": [{"id": f"{node.node_id}:config_patch", "label": "config_patch", "kind": "artifact"}],
                },
                {
                    "id": "scene_generation",
                    "stage": "scene_generation",
                    "label": "Scene generated from branch candidate.",
                    "status": "completed" if node.scene_layout_path else ("failed" if node.status == "failed" else "active"),
                    "progress": 75 if node.scene_layout_path else 45,
                    "children": [{"id": f"{node.node_id}:layout", "label": node.scene_layout_path, "kind": "artifact"}] if node.scene_layout_path else [],
                },
                {
                    "id": "evaluation",
                    "stage": "evaluation",
                    "label": "Unified scene evaluation.",
                    "status": evaluation_status,
                    "progress": 100 if node.evaluation else 80,
                    "children": [{"id": f"{node.node_id}:evaluation", "label": "evaluation", "kind": "artifact"}] if node.evaluation else [],
                },
            ],
            "operations": [
                {"stage": candidate_stage, "progress": 15, "message": node.llm_candidate_reasoning},
                {"stage": "scene_generation", "progress": 75, "message": node.scene_layout_path or node.error},
                {"stage": "evaluation", "progress": 100 if node.evaluation else 80, "message": str(node.evaluation.get("evaluation", "") if node.evaluation else node.error)},
            ],
        },
        "result": {
            "compose_config": dict(node.config_patch),
            "summary": {},
            "scene_layout_path": node.scene_layout_path,
            "scene_glb_path": node.scene_glb_path,
            "preview_path": node.preview_path,
            "can_restore_artifact": node.can_restore_artifact,
            "artifacts_retained": node.artifacts_retained,
            "artifact_rank": node.artifact_rank,
            "artifact_paths": list(node.artifact_paths),
            "artifact_dir": str(artifact_dir or (state.output_dir / node.node_id)),
            "generation_trace_path": str((artifact_dir or (state.output_dir / node.node_id)) / "generation_trace.json"),
            "preset_id": state.preset_id,
            "benchmark_id": state.benchmark_id,
            "batch_id": state.batch_id,
        },
        "evaluation": {"status": evaluation_status, **dict(node.evaluation), **({"error": node.error} if node.error else {})},
    }))


def _normalize_weights(payload: Mapping[str, float] | None) -> Dict[str, float]:
    values = dict(payload or {})
    return {
        "walkability": float(values.get("walkability", 0.4) or 0.4),
        "safety": float(values.get("safety", 0.3) or 0.3),
        "beauty": float(values.get("beauty", 0.3) or 0.3),
    }


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounded_target_samples(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(parsed, 100))


def _normalize_search_mode(value: str | None) -> str:
    normalized = str(value or "llm_branch").strip().lower()
    if normalized in {"pareto", "pareto_search", "traditional", "traditional_search"}:
        return "pareto"
    return "llm_branch"


def _bounded_early_stop_patience(value: int | None, *, default: int | None) -> int | None:
    candidate = default if value is None else value
    if candidate is None:
        return None
    try:
        parsed = int(candidate)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, 100))


def _bounded_retained_artifact_count(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return max(1, min(parsed, 20))


def _completed_sample_count(nodes: Sequence[BranchNode]) -> int:
    return sum(1 for node in nodes if node.status == "succeeded" and bool(node.evaluation))


def _attempted_sample_count(nodes: Sequence[BranchNode]) -> int:
    return sum(1 for node in nodes if node.status in {"running", "succeeded", "failed"})


def _sample_budget_exhausted(state: _BranchRunState) -> bool:
    if state.target_samples is None:
        return False
    completed = _completed_sample_count(state.nodes)
    attempted = _attempted_sample_count(state.nodes)
    return completed >= state.target_samples or attempted >= state.target_samples * 2


def _progress_for_branch_state(state: _BranchRunState, depth: int) -> int:
    if state.target_samples:
        completed = _completed_sample_count(state.nodes)
        attempted = _attempted_sample_count(state.nodes)
        basis = max(completed, attempted * 0.5)
        return min(96, 12 + int((basis / max(state.target_samples, 1)) * 82))
    return _progress_for_depth(depth, state.rounds)


def _find_node(nodes: Sequence[BranchNode], node_id: str | None) -> BranchNode | None:
    if not node_id:
        return None
    return next((node for node in nodes if node.node_id == node_id), None)


def _score_delta(value: float | None, parent_value: float | None) -> float | None:
    if value is None or parent_value is None:
        return None
    return round(value - parent_value, 3)


def _succeeded_scored_nodes(nodes: Sequence[BranchNode]) -> List[BranchNode]:
    return [
        node for node in nodes
        if node.status == "succeeded" and _score_vector(node) is not None
    ]


def _score_vector(node: BranchNode) -> tuple[float, float, float] | None:
    evaluation = node.evaluation or {}
    walkability = _nullable_float(evaluation.get("walkability"))
    safety = _nullable_float(evaluation.get("safety"))
    beauty = _nullable_float(evaluation.get("beauty"))
    if walkability is None or safety is None or beauty is None:
        return None
    return (_clamp_score(walkability), _clamp_score(safety), _clamp_score(beauty))


def _dominates(left: BranchNode, right: BranchNode) -> bool:
    left_scores = _score_vector(left)
    right_scores = _score_vector(right)
    if left_scores is None or right_scores is None:
        return False
    return all(a >= b for a, b in zip(left_scores, right_scores)) and any(a > b for a, b in zip(left_scores, right_scores))


def _is_non_dominated_against(node: BranchNode, others: Sequence[BranchNode]) -> bool:
    return not any(_dominates(other, node) for other in others if other.node_id != node.node_id)


def _pareto_layers(nodes: Sequence[BranchNode]) -> Dict[str, int]:
    remaining = _succeeded_scored_nodes(nodes)
    layers: Dict[str, int] = {}
    rank = 0
    while remaining:
        front = [
            node for node in remaining
            if not any(_dominates(other, node) for other in remaining if other.node_id != node.node_id)
        ]
        if not front:
            break
        for node in front:
            layers[node.node_id] = rank
        front_ids = {node.node_id for node in front}
        remaining = [node for node in remaining if node.node_id not in front_ids]
        rank += 1
    return layers


def _pareto_front_ids(nodes: Sequence[BranchNode]) -> List[str]:
    layers = _pareto_layers(nodes)
    front = [node for node in _succeeded_scored_nodes(nodes) if layers.get(node.node_id) == 0]
    return [node.node_id for node in sorted(front, key=lambda item: item.score, reverse=True)]


def _dominated_by_count(node: BranchNode, nodes: Sequence[BranchNode]) -> int:
    return sum(1 for other in nodes if other.node_id != node.node_id and _dominates(other, node))


def _scatter_points(nodes: Sequence[BranchNode]) -> List[BranchScatterPoint]:
    points: List[BranchScatterPoint] = []
    by_id = {node.node_id: node for node in nodes}
    pareto_ranks = _pareto_layers(nodes)
    for node in nodes:
        evaluation = node.evaluation or {}
        walkability = _nullable_float(evaluation.get("walkability"))
        safety = _nullable_float(evaluation.get("safety"))
        beauty = _nullable_float(evaluation.get("beauty"))
        overall = _nullable_float(evaluation.get("overall")) or node.score or None
        parent = by_id.get(node.parent_id or "")
        parent_evaluation = parent.evaluation if parent else {}
        parent_walkability = _nullable_float(parent_evaluation.get("walkability"))
        parent_safety = _nullable_float(parent_evaluation.get("safety"))
        parent_beauty = _nullable_float(parent_evaluation.get("beauty"))
        parent_overall = (_nullable_float(parent_evaluation.get("overall")) or parent.score or None) if parent else None
        points.append(BranchScatterPoint(
            node_id=node.node_id,
            parent_id=node.parent_id,
            depth=node.depth,
            rank=node.rank,
            x=walkability,
            y=safety,
            z=beauty,
            overall=overall,
            walkability=walkability,
            safety=safety,
            beauty=beauty,
            delta_walkability=_score_delta(walkability, parent_walkability),
            delta_safety=_score_delta(safety, parent_safety),
            delta_beauty=_score_delta(beauty, parent_beauty),
            delta_overall=_score_delta(overall, parent_overall),
            is_pareto_front=pareto_ranks.get(node.node_id) == 0,
            pareto_rank=pareto_ranks.get(node.node_id),
            dominated_by_count=_dominated_by_count(node, nodes),
            label=f"D{node.depth} · #{node.rank}",
            status=node.status,
            influence_summary=_top_influence_summary(node.influence_rows),
        ))
    return points


def _top_influence_summary(rows: Sequence[Mapping[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    ranked = sorted(rows, key=lambda item: (
        0 if item.get("active") else 1,
        -float(item.get("score") or item.get("confidence") or 0),
        str(item.get("label", "")),
    ))
    return [
        {
            "id": str(item.get("id", "")),
            "group": str(item.get("group", "")),
            "source_type": str(item.get("source_type", "")),
            "label": str(item.get("label", "")),
            "active": bool(item.get("active")),
        }
        for item in ranked[: max(1, int(limit))]
    ]


def _build_influence_rows(node: BranchNode, *, parent: BranchNode | None = None) -> List[Dict[str, Any]]:
    patch_keys = {str(key) for key in node.config_patch}
    rows: List[Dict[str, Any]] = []

    evidence_rows = sorted(
        list(node.rag_evidence),
        key=lambda item: float(item.get("score") or 0),
        reverse=True,
    )
    for rank, evidence in enumerate(evidence_rows, start=1):
        chunk_id = str(evidence.get("chunk_id", ""))
        knowledge_source = str(evidence.get("knowledge_source", ""))
        score = _nullable_float(evidence.get("score")) or 0.0
        if _is_scenario_parameter_evidence(evidence):
            parsed = _parse_evidence_text(evidence.get("text"))
            parameter_name = str(parsed.get("parameter_name") or _parameter_name_from_chunk_id(chunk_id) or "")
            confidence = _nullable_float(parsed.get("confidence")) or score
            rows.append({
                "id": f"parameter:{chunk_id or rank}",
                "group": "parameters",
                "source_type": "parameter_triple",
                "label": parameter_name or str(evidence.get("section_title", "Scenario parameter")),
                "detail": str(parsed.get("scenario_label") or evidence.get("section_title") or ""),
                "field": parameter_name,
                "value": parsed.get("normalized_value", parsed.get("raw_value", "")),
                "unit": parsed.get("unit", ""),
                "score": score,
                "confidence": confidence,
                "active": parameter_name in patch_keys if parameter_name else False,
                "chunk_id": chunk_id,
                "source": str(evidence.get("source_path", "")),
                "rank": rank,
            })
            continue

        rows.append({
            "id": f"rag:{chunk_id or rank}",
            "group": "knowledge",
            "source_type": "rag",
            "label": str(evidence.get("section_title") or chunk_id or "Retrieved knowledge"),
            "detail": _truncate_text(evidence.get("text"), 180),
            "score": score,
            "confidence": score,
            "active": True,
            "chunk_id": chunk_id,
            "source": str(evidence.get("source_path", "")),
            "knowledge_source": knowledge_source,
            "rank": rank,
        })

    parent_patch = parent.config_patch if parent else {}
    patch_source_type = "search_patch" if node.candidate_source == "pareto_search" else "llm_patch"
    patch_detail = (
        "Pareto search config patch"
        if node.candidate_source == "pareto_search"
        else "LLM candidate config patch"
    )
    for rank, (field_name, value) in enumerate(sorted(node.config_patch.items()), start=1):
        field = str(field_name)
        old_value = parent_patch.get(field) if isinstance(parent_patch, Mapping) else None
        changed = parent is None or old_value != value
        rows.append({
            "id": f"{patch_source_type}:{field}",
            "group": "llm_constraints",
            "source_type": patch_source_type,
            "label": field,
            "detail": patch_detail,
            "field": field,
            "old_value": old_value,
            "value": value,
            "score": 1.0 if changed else 0.45,
            "confidence": 1.0 if changed else 0.45,
            "active": changed,
            "source": node.candidate_source,
            "rank": rank,
        })

    for rank, directive in enumerate(node.optimization_directives, start=1):
        directive_id = str(directive.get("directive_id") or f"directive-{rank}")
        rows.append({
            "id": f"directive:{directive_id}",
            "group": "llm_constraints",
            "source_type": "directive",
            "label": str(directive.get("target_metric") or directive_id),
            "detail": str(directive.get("problem") or directive.get("direction") or ""),
            "field": ", ".join(str(item) for item in directive.get("allowed_fields", []) or []),
            "value": directive.get("direction", ""),
            "score": 0.8,
            "confidence": 0.8,
            "active": True,
            "source": "rule_based_planner",
            "rank": rank,
        })

    for rank, rejected in enumerate(node.rejected_edits, start=1):
        field = str(rejected.get("field") or f"rejected-{rank}")
        rows.append({
            "id": f"constraint:{field}:{rank}",
            "group": "llm_constraints",
            "source_type": "constraint",
            "label": field,
            "detail": str(rejected.get("reason") or "Rejected by rule-based planner"),
            "field": field,
            "value": rejected.get("value", ""),
            "score": 0.9,
            "confidence": 0.9,
            "active": True,
            "source": "rule_based_planner",
            "rank": rank,
        })

    return _sorted_influence_rows(rows)


def _sorted_influence_rows(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    sorted_rows = sorted(rows, key=lambda item: (
        str(item.get("group", "")),
        0 if item.get("active") else 1,
        -float(item.get("score") or item.get("confidence") or 0),
        int(item.get("rank") or 0),
    ))
    return [dict(make_json_safe(row)) for row in sorted_rows]


def _is_scenario_parameter_evidence(evidence: Mapping[str, Any]) -> bool:
    return (
        str(evidence.get("knowledge_source", "")) == "scenario_parameters"
        or str(evidence.get("doc_id", "")) == "scenario_parameter_triples"
        or str(evidence.get("chunk_id", "")).startswith("scenario_parameters::")
    )


def _parse_evidence_text(value: Any) -> Dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parameter_name_from_chunk_id(chunk_id: str) -> str:
    parts = str(chunk_id or "").split("::")
    return parts[-1] if parts else ""


def _truncate_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}..."


def _merge_rag_evidence(items: Sequence[RagEvidence], *, topk: int) -> List[RagEvidence]:
    merged: Dict[str, RagEvidence] = {}
    for item in items:
        existing = merged.get(item.chunk_id)
        if existing is None or float(item.score) > float(existing.score):
            merged[item.chunk_id] = item
    return sorted(merged.values(), key=lambda item: float(item.score), reverse=True)[: max(1, int(topk))]


def _operation(stage: str, progress: int, message: str, detail: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "timestamp": _utc_now(),
        "stage": stage,
        "progress": int(progress),
        "message": message,
        "detail": dict(detail or {}),
    }


def _write_json(path: Path, payload: Mapping[str, Any] | Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(make_json_safe(payload), ensure_ascii=True, indent=2), encoding="utf-8")


def _weakness_queries(directives: Sequence[Mapping[str, Any]]) -> List[str]:
    queries: List[str] = []
    for directive in directives:
        metric = str(directive.get("target_metric", "") or "").replace("_", " ")
        problem = str(directive.get("problem", "") or "")
        if metric or problem:
            queries.append(f"{metric} {problem} complete streets design guidance")
    return queries[:6]


def _progress_for_depth(depth: int, rounds: int) -> int:
    if rounds <= 1:
        return 80
    return min(90, 20 + int((depth / max(rounds - 1, 1)) * 60))
