"""Asynchronous beam-search branch runs for design evolution."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Condition, Lock, Thread
from typing import Any, Dict, List, Mapping, Sequence
from uuid import uuid4

from ..graph_templates import get_graph_template
from ..json_safe import make_json_safe
from ..llm.design_workflow import DesignAssistantService
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
    overall: float | None
    walkability: float | None
    safety: float | None
    beauty: float | None
    label: str
    status: str

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
    directive_ids: List[str] = field(default_factory=list)
    rejected_edits: List[Dict[str, Any]] = field(default_factory=list)
    scene_layout_path: str = ""
    scene_glb_path: str = ""
    preview_path: str = ""
    evaluation: Dict[str, Any] = field(default_factory=dict)
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
    ) -> None:
        self.design_service = design_service or DesignAssistantService()
        self.output_root = Path(output_root or DEFAULT_BRANCH_RUN_DIR).expanduser().resolve()
        self.planner = planner or RuleBasedOptimizationPlanner()
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
    ) -> Dict[str, Any]:
        self._ensure_worker()
        run_id = uuid4().hex
        bounded_topk = max(1, min(int(topk or 3), 5))
        bounded_rounds = max(1, min(int(rounds or 2), 3))
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
        )
        with self._condition:
            self._runs[run_id] = state
            self._queue.put(run_id)
            self._condition.notify_all()
        return {"run_id": run_id, "status": state.status, "created_at": state.created_at}

    def get_run(self, run_id: str) -> Dict[str, Any] | None:
        with self._lock:
            state = self._runs.get(str(run_id))
            if state is None:
                return None
            return self._to_payload(state)

    def list_runs(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            ordered = sorted(self._runs.values(), key=lambda item: item.created_at, reverse=True)
            return [self._to_payload(item, include_nodes=False) for item in ordered[: max(1, int(limit))]]

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
        self._update_state(state.run_id, "llm_candidates", 10, "Requesting initial top-k LLM candidates.")
        candidates = self.design_service.generate_initial_config_candidates_from_graph(
            graph_summary=graph_summary,
            user_prompt=state.prompt,
            current_patch={"query": state.prompt},
            evidence=evidence,
            topk=state.topk,
        )

        current_level: List[BranchNode] = []
        for index, candidate in enumerate(candidates[: state.topk]):
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

        for depth in range(1, state.rounds):
            frontier = self._rank_frontier(current_level, state.topk)
            self._set_frontier(state.run_id, [node.node_id for node in frontier])
            next_level: List[BranchNode] = []
            for parent in frontier:
                if parent.status != "succeeded":
                    continue
                self._update_state(
                    state.run_id,
                    "llm_improvement",
                    _progress_for_depth(depth, state.rounds),
                    f"Requesting improvement candidates for {parent.node_id}.",
                )
                child_candidates = self.design_service.propose_improvement_candidates(
                    current_evaluation=str(parent.evaluation.get("evaluation", "")),
                    comparison={},
                    current_patch=parent.config_patch,
                    optimization_directives=parent.optimization_directives,
                    topk=state.topk,
                    weakness_queries=_weakness_queries(parent.optimization_directives),
                    knowledge_source=state.knowledge_source,
                )
                for index, candidate in enumerate(child_candidates[: state.topk]):
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
                        evidence=[],
                    )
                    self._run_node(state, child)
                    next_level.append(child)
            current_level = next_level
            if not current_level:
                break

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
        return BranchNode(
            node_id=node_id,
            parent_id=parent.node_id if parent else None,
            depth=depth,
            rank=rank,
            prompt=state.prompt,
            config_patch=patch,
            rag_evidence=[item.to_dict() for item in evidence],
            llm_candidate_reasoning=str(candidate.get("reasoning", "") or candidate.get("design_summary", "") or ""),
            directive_ids=[str(item) for item in candidate.get("directive_ids", []) or []],
            rejected_edits=list(candidate.get("rejected_edits", []) or []),
        )

    def _run_node(self, state: _BranchRunState, node: BranchNode) -> None:
        node_dir = state.output_dir / node.node_id
        node_dir.mkdir(parents=True, exist_ok=True)
        node.status = "running"
        self._append_node(state.run_id, node)
        _write_json(node_dir / "config_patch.json", node.config_patch)
        try:
            draft = DesignDraft(
                normalized_scene_query=str(node.config_patch.get("query", state.prompt)),
                compose_config_patch=node.config_patch,
                citations_by_field={},
                design_summary=node.llm_candidate_reasoning,
                parameter_sources_by_field={key: "branch_llm_candidate" for key in node.config_patch},
            )
            generation_options = {
                **state.generation_options,
                "out_dir": str(node_dir),
                "preset_id": "branch_run",
                "random_seed": int(1000 + node.depth * 100 + node.rank),
            }
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
            evaluation = self.design_service.evaluate_scene_unified(layout_path=node.scene_layout_path)
            node.evaluation = dict(evaluation)
            node.score = _combined_score(evaluation, state.evaluation_weights)
            directives = self.planner.plan(
                evaluation=evaluation,
                current_patch=node.config_patch,
                generation_diagnostics=getattr(result, "summary", {}) if not isinstance(result, Mapping) else result.get("summary", {}),
            )
            node.optimization_directives = [directive.to_dict() for directive in directives]
            node.status = "succeeded"
            node.finished_at = _utc_now()
            _write_json(node_dir / "evaluation.json", node.evaluation)
            _write_json(node_dir / "optimization_directives.json", node.optimization_directives)
        except Exception as exc:
            node.status = "failed"
            node.error = str(exc)
            node.blocker_details = {"error": str(exc), "stage": "generate_or_evaluate"}
            node.finished_at = _utc_now()
        finally:
            _write_json(node_dir / "node.json", node.to_dict())
            self._replace_node(state.run_id, node)

    def _retrieve_initial_evidence(self, state: _BranchRunState) -> List[RagEvidence]:
        try:
            return list(self.design_service.search_knowledge(
                query=state.prompt,
                topk=state.topk,
                knowledge_source=state.knowledge_source,
            ))
        except Exception:
            return []

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
            if node.status == "succeeded":
                best = self._best_node(state.nodes)
                state.best_node_id = best.node_id if best else ""
            state.operations.append(_operation(f"node_{node.status}", state.progress, f"{node.node_id} {node.status}.", {"node_id": node.node_id, "score": node.score, "error": node.error}))
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
            "graph_template_id": state.graph_template_id,
            "knowledge_source": state.knowledge_source,
            "best_node_id": state.best_node_id,
            "frontier": list(state.frontier),
            "nodes": [node.to_dict() for node in nodes] if include_nodes else [],
            "scatter_points": [point.to_dict() for point in _scatter_points(nodes)],
            "operations": list(state.operations[-100:]),
            "artifact_dir": str(state.output_dir),
        }))

    def _write_manifest(self, state: _BranchRunState) -> None:
        state.output_dir.mkdir(parents=True, exist_ok=True)
        _write_json(state.output_dir / "manifest.json", self._to_payload(state))

    @staticmethod
    def _rank_frontier(nodes: Sequence[BranchNode], topk: int) -> List[BranchNode]:
        succeeded = [node for node in nodes if node.status == "succeeded"]
        return sorted(succeeded, key=lambda item: item.score, reverse=True)[: max(1, int(topk))]

    @staticmethod
    def _best_node(nodes: Sequence[BranchNode]) -> BranchNode | None:
        ranked = BranchRunService._rank_frontier(nodes, 1)
        return ranked[0] if ranked else None


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


def _scatter_points(nodes: Sequence[BranchNode]) -> List[BranchScatterPoint]:
    points: List[BranchScatterPoint] = []
    for node in nodes:
        evaluation = node.evaluation or {}
        walkability = _nullable_float(evaluation.get("walkability"))
        safety = _nullable_float(evaluation.get("safety"))
        beauty = _nullable_float(evaluation.get("beauty"))
        overall = _nullable_float(evaluation.get("overall")) or node.score or None
        points.append(BranchScatterPoint(
            node_id=node.node_id,
            parent_id=node.parent_id,
            depth=node.depth,
            rank=node.rank,
            x=walkability,
            y=overall or beauty,
            overall=overall,
            walkability=walkability,
            safety=safety,
            beauty=beauty,
            label=f"D{node.depth} · #{node.rank}",
            status=node.status,
        ))
    return points


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

