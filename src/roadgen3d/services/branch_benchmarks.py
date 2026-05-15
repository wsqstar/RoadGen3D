"""Persistent benchmark samples for branch and evaluated scene scores."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock, Thread
from time import sleep
from typing import Any, Dict, List, Mapping, Sequence
from uuid import uuid4

from ..json_safe import make_json_safe
from ..presets import SCENE_PRESETS
from .generation_method import infer_generation_method


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BENCHMARK_DIR = (ROOT / "artifacts" / "branch_benchmarks").resolve()
DEFAULT_BRANCH_RUN_DIR = (ROOT / "artifacts" / "branch_runs").resolve()
OUTCOME_KEYS = ("walkability", "safety", "beauty", "overall")
SUMMARY_FEATURE_KEYS = (
    "road_width_m",
    "sidewalk_width_m",
    "left_clear_path_width_m",
    "right_clear_path_width_m",
    "left_furnishing_width_m",
    "right_furnishing_width_m",
    "row_width_m",
    "carriageway_width_m",
    "lane_count",
    "length_m",
    "density",
    "building_density",
    "building_max_per_100m",
    "building_footprint_count",
    "building_region_count",
    "building_target_lot_count",
    "unique_asset_count",
    "dropped_slots",
    "dropped_slot_rate",
    "diversity_ratio",
    "overlap_rate",
    "retrieval_top3_category_hit",
    "compliance_rate_total",
    "violations_total",
    "avg_constraint_penalty",
    "avg_feasibility_score",
    "spacing_uniformity",
    "style_consistency",
    "balance_score",
    "rule_satisfaction_rate",
    "topology_validity",
    "cross_section_feasibility",
    "editability",
    "conflict_explainability",
    "mean_entrance_openness",
    "mean_noise_shielding",
    "entrances_below_openness_threshold",
    "min_entrance_openness",
    "entrance_count",
    "selected_road_effective_poi_count",
    "selected_road_effective_poi_score",
    "selected_road_core_poi_count",
    "required_slot_realization_rate",
    "unplaced_required_slot_count",
    "street_furniture_balance_ok",
    "poi_fit_feasible",
    "width_expanded",
)
CONFIG_FEATURE_KEYS = (
    "road_width_m",
    "sidewalk_width_m",
    "lane_count",
    "length_m",
    "density",
    "building_density",
    "building_max_per_100m",
    "ped_demand_level",
    "bike_demand_level",
    "transit_demand_level",
    "vehicle_demand_level",
    "target_street_type",
    "objective_profile",
    "style_preset",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def preset_by_id(preset_id: str | None) -> Dict[str, Any] | None:
    normalized = str(preset_id or "").strip()
    return next((dict(item) for item in SCENE_PRESETS if str(item.get("id")) == normalized), None)


def preset_meta(preset_id: str | None) -> Dict[str, Any]:
    preset = preset_by_id(preset_id)
    if preset:
        return {
            "preset_id": str(preset.get("id", "")),
            "preset_name": str(preset.get("nameEn") or preset.get("name") or preset.get("id") or ""),
            "preset_label": str(preset.get("name") or preset.get("nameEn") or preset.get("id") or ""),
            "preset_color": str(preset.get("color") or "#64748b"),
            "preset_prompt": str(preset.get("prompt") or ""),
        }
    normalized = str(preset_id or "custom_legacy").strip() or "custom_legacy"
    return {
        "preset_id": normalized,
        "preset_name": "Custom Legacy" if normalized == "custom_legacy" else normalized,
        "preset_label": "历史自定义" if normalized == "custom_legacy" else normalized,
        "preset_color": "#64748b",
        "preset_prompt": "",
    }


class BranchBenchmarkStore:
    """Append-friendly JSONL store with idempotent upserts."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or DEFAULT_BENCHMARK_DIR).expanduser().resolve()
        self.samples_path = self.root / "samples.jsonl"
        self.summary_path = self.root / "summary.json"
        self._lock = Lock()
        self._samples_cache_signature: tuple[int, int] | None = None
        self._samples_cache_rows: Dict[str, Dict[str, Any]] | None = None
        self._query_cache: Dict[
            tuple[str, str, str, str, int],
            tuple[tuple[int, int], Dict[str, Any]],
        ] = {}

    def import_branch_manifests(self, branch_root: str | Path | None = None) -> Dict[str, Any]:
        branch_dir = Path(branch_root or DEFAULT_BRANCH_RUN_DIR).expanduser().resolve()
        manifests: List[Mapping[str, Any]] = []
        for manifest_path in sorted(branch_dir.glob("*/manifest.json")):
            try:
                manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
            except Exception:
                continue

        imported = 0
        changed = False
        with self._lock:
            by_id = self._read_samples_by_id_locked()
            for payload in manifests:
                for sample in self._samples_from_branch_run(
                    payload,
                    default_preset_id=str(payload.get("preset_id") or "custom_legacy"),
                ):
                    sample_id = str(sample.get("sample_id", "") or "").strip()
                    if not sample_id:
                        continue
                    safe_sample = dict(make_json_safe(sample))
                    if sample_id not in by_id:
                        imported += 1
                    if by_id.get(sample_id) != safe_sample:
                        by_id[sample_id] = safe_sample
                        changed = True
            if changed:
                self.root.mkdir(parents=True, exist_ok=True)
                self._write_samples_locked(by_id)
                self._write_summary_locked(by_id)
                self._store_samples_cache_locked(by_id)
                self._clear_query_cache_locked()
        return {"imported_samples": imported, "branch_root": str(branch_dir)}

    def upsert_branch_run(self, run_payload: Mapping[str, Any], *, default_preset_id: str = "custom_legacy") -> None:
        self.upsert_samples(self._samples_from_branch_run(run_payload, default_preset_id=default_preset_id))

    def upsert_branch_node(self, run_payload: Mapping[str, Any], node: Mapping[str, Any]) -> None:
        node_id = str(node.get("node_id", "") or "").strip()
        if not node_id:
            return
        point = _point_from_node(node, run_payload)
        if point is None:
            return
        self.upsert_samples([self._sample_from_branch_node(
            run_payload,
            point,
            node,
            default_preset_id=str(run_payload.get("preset_id") or "custom_legacy"),
        )])

    def upsert_evaluation(
        self,
        *,
        layout_path: str,
        evaluation: Mapping[str, Any],
        preset_id: str = "custom",
        source: str = "manual_evaluation",
    ) -> Dict[str, Any] | None:
        walkability = _score(evaluation, "walkability")
        safety = _score(evaluation, "safety")
        beauty = _score(evaluation, "beauty")
        if walkability is None or safety is None or beauty is None:
            return None
        sample_id = f"eval:{sha256(str(layout_path).encode('utf-8')).hexdigest()[:24]}"
        meta = preset_meta(preset_id)
        sample = {
            "sample_id": sample_id,
            "source": source,
            "run_id": "",
            "node_id": sample_id,
            "parent_id": "",
            "benchmark_id": "manual_evaluations",
            "batch_id": "",
            **meta,
            "label": Path(layout_path).parent.name or "manual evaluation",
            "created_at": utc_now(),
            "status": "succeeded",
            "scene_layout_path": str(layout_path),
            "scene_glb_path": "",
            "walkability": walkability,
            "safety": safety,
            "beauty": beauty,
            "overall": _score(evaluation, "overall") or round((walkability + safety + beauty) / 3, 3),
            "x": walkability,
            "y": safety,
            "z": beauty,
            "is_pareto_front": False,
            "pareto_rank": None,
            "dominated_by_count": 0,
            "evaluation": dict(evaluation),
            "influence_rows": [],
            "config_patch": {},
            "analysis_features": _analysis_features_for_sample({
                "scene_layout_path": str(layout_path),
                "config_patch": {},
                "influence_rows": [],
                **meta,
            }),
            "artifacts_retained": False,
            "artifact_paths": [],
        }
        self.upsert_samples([sample])
        return sample

    def upsert_samples(self, samples: Sequence[Mapping[str, Any]]) -> None:
        if not samples:
            return
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            by_id = self._read_samples_by_id_locked()
            for sample in samples:
                sample_id = str(sample.get("sample_id", "") or "").strip()
                if sample_id:
                    by_id[sample_id] = dict(make_json_safe(sample))
            self._write_samples_locked(by_id)
            self._write_summary_locked(by_id)
            self._store_samples_cache_locked(by_id)
            self._clear_query_cache_locked()

    def query_samples(
        self,
        *,
        preset_id: str | None = None,
        batch_id: str | None = None,
        run_id: str | None = None,
        generation_method: str | None = None,
        limit: int = 5000,
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 5000), 10000))
        cache_key = (
            str(preset_id or ""),
            str(batch_id or ""),
            str(run_id or ""),
            str(generation_method or ""),
            safe_limit,
        )
        with self._lock:
            signature = self._samples_file_signature_locked()
            cached = self._query_cache.get(cache_key)
            if cached and cached[0] == signature:
                return _copy_payload(cached[1])
            samples = list(self._read_samples_by_id_locked().values())

        if preset_id:
            samples = [item for item in samples if str(item.get("preset_id")) == str(preset_id)]
        if batch_id:
            samples = [item for item in samples if str(item.get("batch_id")) == str(batch_id)]
        if run_id:
            samples = [item for item in samples if str(item.get("run_id")) == str(run_id)]
        if generation_method:
            samples = [item for item in samples if str(item.get("generation_method") or "unknown_legacy") == str(generation_method)]
        samples.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        samples = samples[:safe_limit]
        samples = _annotate_pareto(samples)
        payload = {
            "items": samples,
            "summaries": _summaries_by_preset(samples),
            "total": len(samples),
            "updated_at": utc_now(),
        }
        with self._lock:
            if self._samples_file_signature_locked() == signature:
                self._query_cache[cache_key] = (signature, _copy_payload(payload))
        return payload

    def query_analysis(
        self,
        *,
        preset_id: str | None = None,
        batch_id: str | None = None,
        run_id: str | None = None,
        generation_method: str | None = None,
        limit: int = 5000,
    ) -> Dict[str, Any]:
        payload = self.query_samples(
            preset_id=preset_id,
            batch_id=batch_id,
            run_id=run_id,
            generation_method=generation_method,
            limit=limit,
        )
        warnings: List[str] = []
        raw_samples = [dict(item) for item in _records(payload.get("items"))]
        enriched_samples = [_sample_with_analysis_features(sample, warnings=warnings) for sample in raw_samples]
        analysis_samples = [_analysis_sample(sample) for sample in enriched_samples]
        correlations = _correlation_rows(analysis_samples, warnings=warnings)
        categorical_effects = _categorical_effect_rows(analysis_samples, warnings=warnings)
        feature_importance = _feature_importance_rows(analysis_samples, warnings=warnings)
        return {
            "samples": analysis_samples,
            "correlations": correlations,
            "categorical_effects": categorical_effects,
            "feature_importance": feature_importance,
            "summaries": payload.get("summaries", []),
            "total": len(analysis_samples),
            "updated_at": utc_now(),
            "warnings": sorted(set(warnings)),
        }

    def _sample_from_branch_node(
        self,
        run_payload: Mapping[str, Any],
        point: Mapping[str, Any],
        node: Mapping[str, Any],
        *,
        default_preset_id: str,
    ) -> Dict[str, Any]:
        run_id = str(run_payload.get("run_id") or "")
        node_id = str(point.get("node_id") or node.get("node_id") or "")
        preset_id = str(run_payload.get("preset_id") or node.get("preset_id") or default_preset_id or "custom_legacy")
        meta = preset_meta(preset_id)
        return {
            "sample_id": f"{run_id}:{node_id}",
            "source": "branch_run",
            "run_id": run_id,
            "node_id": node_id,
            "parent_id": str(point.get("parent_id") or node.get("parent_id") or ""),
            "benchmark_id": str(run_payload.get("benchmark_id") or ""),
            "batch_id": str(run_payload.get("batch_id") or ""),
            **meta,
            "label": str(point.get("label") or f"D{node.get('depth', 0)} · #{node.get('rank', 0)}"),
            "prompt": str(run_payload.get("prompt") or ""),
            "graph_template_id": str(run_payload.get("graph_template_id") or ""),
            "knowledge_source": str(run_payload.get("knowledge_source") or ""),
            "generation_method": _sample_generation_method(run_payload, point, node),
            "created_at": str(node.get("finished_at") or node.get("created_at") or run_payload.get("created_at") or utc_now()),
            "status": str(point.get("status") or node.get("status") or "succeeded"),
            "depth": int(point.get("depth") or node.get("depth") or 0),
            "rank": int(point.get("rank") or node.get("rank") or 0),
            "scene_layout_path": str(node.get("scene_layout_path") or ""),
            "scene_glb_path": str(node.get("scene_glb_path") or ""),
            "can_restore_artifact": _can_restore_sample_artifact(node),
            "walkability": _score(point, "walkability"),
            "safety": _score(point, "safety"),
            "beauty": _score(point, "beauty"),
            "overall": _score(point, "overall"),
            "x": _score(point, "walkability"),
            "y": _score(point, "safety"),
            "z": _score(point, "beauty"),
            "delta_walkability": _score(point, "delta_walkability"),
            "delta_safety": _score(point, "delta_safety"),
            "delta_beauty": _score(point, "delta_beauty"),
            "delta_overall": _score(point, "delta_overall"),
            "is_pareto_front": bool(point.get("is_pareto_front")),
            "pareto_rank": point.get("pareto_rank"),
            "dominated_by_count": int(point.get("dominated_by_count") or 0),
            "early_stop_triggered": bool(run_payload.get("early_stop_triggered")),
            "early_stop_reason": str(run_payload.get("early_stop_reason") or ""),
            "evaluation": dict(node.get("evaluation") or {}),
            "influence_rows": list(node.get("influence_rows") or point.get("influence_summary") or []),
            "config_patch": dict(node.get("config_patch") or {}),
            "analysis_features": _analysis_features_for_sample({
                "scene_layout_path": str(node.get("scene_layout_path") or ""),
                "config_patch": dict(node.get("config_patch") or {}),
                "influence_rows": list(node.get("influence_rows") or point.get("influence_summary") or []),
                "knowledge_source": str(run_payload.get("knowledge_source") or ""),
                "generation_method": _sample_generation_method(run_payload, point, node),
                **meta,
            }),
            "artifacts_retained": bool(node.get("artifacts_retained")),
            "artifact_rank": node.get("artifact_rank"),
            "artifact_paths": list(node.get("artifact_paths") or []),
        }

    def _samples_from_branch_run(
        self,
        run_payload: Mapping[str, Any],
        *,
        default_preset_id: str = "custom_legacy",
    ) -> List[Dict[str, Any]]:
        run_id = str(run_payload.get("run_id", "") or "").strip()
        if not run_id:
            return []
        nodes = {
            str(node.get("node_id", "")): dict(node)
            for node in _records(run_payload.get("nodes"))
            if str(node.get("node_id", "")).strip()
        }
        samples = []
        for point in _records(run_payload.get("scatter_points")):
            node_id = str(point.get("node_id", "") or "").strip()
            if not node_id:
                continue
            node = nodes.get(node_id, {})
            if str(point.get("status") or node.get("status") or "") != "succeeded":
                continue
            if _score(point, "walkability") is None or _score(point, "safety") is None or _score(point, "beauty") is None:
                continue
            samples.append(self._sample_from_branch_node(
                run_payload,
                point,
                node,
                default_preset_id=default_preset_id,
            ))
        return samples

    def _read_samples_by_id(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return self._read_samples_by_id_locked()

    def _read_samples_by_id_locked(self) -> Dict[str, Dict[str, Any]]:
        signature = self._samples_file_signature_locked()
        if self._samples_cache_signature == signature and self._samples_cache_rows is not None:
            return _copy_rows_by_id(self._samples_cache_rows)
        if not self.samples_path.exists():
            self._store_samples_cache_locked({})
            return {}
        rows: Dict[str, Dict[str, Any]] = {}
        for line in self.samples_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            sample_id = str(item.get("sample_id", "") or "").strip()
            if sample_id:
                item.setdefault("generation_method", infer_generation_method(
                    explicit=str(item.get("generation_method") or ""),
                    knowledge_source=str(item.get("knowledge_source") or ""),
                    influence_rows=_records(item.get("influence_rows")),
                    rag_evidence=_records(item.get("rag_evidence")),
                ))
                rows[sample_id] = item
        self._store_samples_cache_locked(rows)
        return rows

    def _write_samples_locked(self, rows: Mapping[str, Mapping[str, Any]]) -> None:
        ordered = sorted(rows.values(), key=lambda item: (str(item.get("preset_id", "")), str(item.get("sample_id", ""))))
        self.samples_path.write_text(
            "".join(f"{json.dumps(make_json_safe(item), ensure_ascii=False, sort_keys=True)}\n" for item in ordered),
            encoding="utf-8",
        )

    def _write_summary_locked(self, rows: Mapping[str, Mapping[str, Any]]) -> None:
        samples = list(rows.values())
        self.summary_path.write_text(
            json.dumps(make_json_safe({
                "updated_at": utc_now(),
                "sample_count": len(samples),
                "summaries": _summaries_by_preset(samples),
            }), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _samples_file_signature_locked(self) -> tuple[int, int]:
        try:
            stat = self.samples_path.stat()
            return (int(stat.st_mtime_ns), int(stat.st_size))
        except FileNotFoundError:
            return (0, 0)

    def _store_samples_cache_locked(self, rows: Mapping[str, Mapping[str, Any]]) -> None:
        self._samples_cache_signature = self._samples_file_signature_locked()
        self._samples_cache_rows = _copy_rows_by_id(rows)

    def _clear_query_cache_locked(self) -> None:
        self._query_cache.clear()


@dataclass
class _BatchChild:
    preset_id: str
    run_id: str = ""
    status: str = "queued"
    completed_samples: int = 0
    attempted_samples: int = 0
    early_stop_triggered: bool = False
    early_stop_reason: str = ""
    error: str = ""


@dataclass
class _BatchState:
    batch_id: str
    benchmark_id: str
    status: str = "queued"
    created_at: str = field(default_factory=utc_now)
    started_at: str = ""
    finished_at: str = ""
    progress: int = 0
    current_preset_id: str = ""
    error: str = ""
    children: List[_BatchChild] = field(default_factory=list)


class BranchBenchmarkBatchService:
    """Sequentially runs preset benchmark branch runs."""

    def __init__(self, *, branch_run_service: Any, benchmark_store: BranchBenchmarkStore) -> None:
        self.branch_run_service = branch_run_service
        self.benchmark_store = benchmark_store
        self._lock = Lock()
        self._batches: Dict[str, _BatchState] = {}

    def submit_batch(
        self,
        *,
        preset_ids: Sequence[str] | None = None,
        target_samples: int = 100,
        graph_template_id: str = "hkust_gz_gate",
        knowledge_source: str = "graph_rag",
        early_stop_patience: int = 20,
        retain_topk_artifacts: int = 10,
        score_with_rendered_views: bool = True,
    ) -> Dict[str, Any]:
        batch_id = uuid4().hex
        selected_ids = [str(item) for item in (preset_ids or [preset.get("id") for preset in SCENE_PRESETS]) if str(item or "").strip()]
        state = _BatchState(
            batch_id=batch_id,
            benchmark_id=batch_id,
            children=[_BatchChild(preset_id=preset_id) for preset_id in selected_ids],
        )
        with self._lock:
            self._batches[batch_id] = state
        thread = Thread(
            target=self._run_batch,
            args=(batch_id, target_samples, graph_template_id, knowledge_source, early_stop_patience, retain_topk_artifacts, score_with_rendered_views),
            name=f"roadgen3d-benchmark-batch-{batch_id[:8]}",
            daemon=True,
        )
        thread.start()
        return self.get_batch(batch_id) or {"batch_id": batch_id, "status": "queued"}

    def get_batch(self, batch_id: str) -> Dict[str, Any] | None:
        with self._lock:
            state = self._batches.get(str(batch_id))
            if state is None:
                return None
            return self._payload_locked(state)

    def _run_batch(
        self,
        batch_id: str,
        target_samples: int,
        graph_template_id: str,
        knowledge_source: str,
        early_stop_patience: int,
        retain_topk_artifacts: int,
        score_with_rendered_views: bool,
    ) -> None:
        with self._lock:
            state = self._batches[batch_id]
            state.status = "running"
            state.started_at = utc_now()
        for child in list(state.children):
            preset = preset_by_id(child.preset_id)
            if not preset:
                child.status = "failed"
                child.error = f"Unknown preset: {child.preset_id}"
                continue
            with self._lock:
                state.current_preset_id = child.preset_id
                child.status = "submitting"
                state.progress = self._batch_progress(state)
            created = self.branch_run_service.submit_run(
                prompt=str(preset.get("prompt") or preset.get("nameEn") or child.preset_id),
                topk=5,
                rounds=5,
                target_samples=target_samples,
                search_mode="pareto",
                early_stop_patience=early_stop_patience,
                retain_topk_artifacts=retain_topk_artifacts,
                score_with_rendered_views=score_with_rendered_views,
                graph_template_id=graph_template_id,
                knowledge_source=knowledge_source,
                preset_id=child.preset_id,
                preset_config_patch=dict(preset.get("configPatch") or {}),
                benchmark_id=batch_id,
                batch_id=batch_id,
                persist_to_benchmark=True,
            )
            child.run_id = str(created.get("run_id") or "")
            child.status = "running"
            while True:
                payload = self.branch_run_service.get_run(child.run_id) or {}
                child.status = str(payload.get("status") or child.status)
                child.completed_samples = int(payload.get("completed_samples") or 0)
                child.attempted_samples = int(payload.get("attempted_samples") or 0)
                child.early_stop_triggered = bool(payload.get("early_stop_triggered"))
                child.early_stop_reason = str(payload.get("early_stop_reason") or "")
                child.error = str(payload.get("error") or "")
                with self._lock:
                    state.progress = self._batch_progress(state)
                if child.status in {"succeeded", "failed"}:
                    break
                sleep(2.0)
        with self._lock:
            state = self._batches[batch_id]
            state.finished_at = utc_now()
            state.current_preset_id = ""
            state.progress = 100
            state.status = "failed" if any(child.status == "failed" for child in state.children) else "succeeded"
            state.error = "; ".join(child.error for child in state.children if child.error)

    def _batch_progress(self, state: _BatchState) -> int:
        if not state.children:
            return 0
        total = len(state.children) * 100
        completed = sum(min(100, child.completed_samples) for child in state.children)
        return max(1, min(99, int((completed / total) * 100)))

    def _payload_locked(self, state: _BatchState) -> Dict[str, Any]:
        children = []
        for child in state.children:
            meta = preset_meta(child.preset_id)
            children.append({
                **meta,
                "run_id": child.run_id,
                "status": child.status,
                "completed_samples": child.completed_samples,
                "attempted_samples": child.attempted_samples,
                "early_stop_triggered": child.early_stop_triggered,
                "early_stop_reason": child.early_stop_reason,
                "error": child.error,
            })
        return dict(make_json_safe({
            "batch_id": state.batch_id,
            "benchmark_id": state.benchmark_id,
            "status": state.status,
            "created_at": state.created_at,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "progress": state.progress,
            "current_preset_id": state.current_preset_id,
            "error": state.error,
            "children": children,
            "completed_presets": sum(1 for child in state.children if child.status == "succeeded"),
            "failed_presets": sum(1 for child in state.children if child.status == "failed"),
        }))


def _records(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _copy_rows_by_id(rows: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(key): dict(make_json_safe(value)) for key, value in rows.items()}


def _copy_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(make_json_safe(payload))


def _score(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _sample_generation_method(
    run_payload: Mapping[str, Any],
    point: Mapping[str, Any],
    node: Mapping[str, Any],
) -> str:
    explicit = node.get("generation_method") or point.get("generation_method") or run_payload.get("generation_method")
    return infer_generation_method(
        explicit=str(explicit or ""),
        candidate_source=str(node.get("candidate_source") or ""),
        knowledge_source=str(run_payload.get("knowledge_source") or ""),
        influence_rows=_records(node.get("influence_rows") or point.get("influence_summary")),
        rag_evidence=_records(node.get("rag_evidence")),
        parameter_sources_by_field=_mapping(_mapping(node.get("trace")).get("provenance", {})).get("parameter_sources_by_field", {}),
    )


def _can_restore_sample_artifact(node: Mapping[str, Any]) -> bool:
    if "can_restore_artifact" in node:
        return bool(node.get("can_restore_artifact"))
    layout_path = str(node.get("scene_layout_path") or "").strip()
    glb_path = str(node.get("scene_glb_path") or "").strip()
    if not layout_path or not glb_path:
        return False
    try:
        return Path(layout_path).expanduser().exists() and Path(glb_path).expanduser().exists()
    except Exception:
        return False


def _sample_with_analysis_features(sample: Mapping[str, Any], *, warnings: List[str]) -> Dict[str, Any]:
    item = dict(sample)
    existing = item.get("analysis_features")
    if isinstance(existing, Mapping) and isinstance(existing.get("input"), Mapping) and isinstance(existing.get("scene"), Mapping):
        return item
    features = _analysis_features_for_sample(item)
    if not features.get("layout_available"):
        layout_path = str(item.get("scene_layout_path") or "").strip()
        if layout_path:
            warnings.append(f"Layout unavailable for sample {item.get('sample_id')}: {features.get('layout_error') or layout_path}")
    item["analysis_features"] = features
    return item


def _analysis_features_for_sample(sample: Mapping[str, Any]) -> Dict[str, Any]:
    input_features = _input_features_for_sample(sample)
    scene_features: Dict[str, Any] = {}
    derived_features: Dict[str, Any] = {}
    layout_available = False
    layout_error = ""
    layout_path = str(sample.get("scene_layout_path") or "").strip()
    if layout_path:
        try:
            path = Path(layout_path).expanduser().resolve()
            if not path.exists():
                raise FileNotFoundError(str(path))
            payload = json.loads(path.read_text(encoding="utf-8"))
            scene_features, derived_features = _scene_features_from_layout(payload)
            layout_available = True
        except Exception as exc:
            layout_error = str(exc)
    else:
        layout_error = "scene_layout_path is empty"
    return {
        "input": input_features,
        "scene": scene_features,
        "derived": derived_features,
        "layout_available": layout_available,
        "layout_error": layout_error,
    }


def _input_features_for_sample(sample: Mapping[str, Any]) -> Dict[str, Any]:
    features: Dict[str, Any] = {}
    for key in ("preset_id", "preset_name", "graph_template_id", "knowledge_source", "generation_method"):
        value = sample.get(key)
        if _is_scalar(value):
            features[key] = value
    patch = sample.get("config_patch")
    if isinstance(patch, Mapping):
        for key, value in patch.items():
            if _is_scalar(value):
                features[str(key)] = value
    source_counts: Counter[str] = Counter()
    active_source_counts: Counter[str] = Counter()
    active_fields: set[str] = set()
    for row in _records(sample.get("influence_rows")):
        source_type = str(row.get("source_type") or "unknown")
        source_counts[source_type] += 1
        if row.get("active"):
            active_source_counts[source_type] += 1
            field = str(row.get("field") or row.get("label") or "").strip()
            if field:
                active_fields.add(field)
            if source_type in {"parameter_triple", "llm_patch", "search_patch"}:
                key = str(row.get("field") or row.get("label") or "").strip()
                value = row.get("value")
                if key and _is_scalar(value) and key not in features:
                    features[key] = value
    for source_type, count in source_counts.items():
        features[f"{source_type}_row_count"] = int(count)
    for source_type, count in active_source_counts.items():
        features[f"active_{source_type}_count"] = int(count)
    features["active_influence_field_count"] = int(len(active_fields))
    return features


def _scene_features_from_layout(payload: Mapping[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    summary = _mapping(payload.get("summary"))
    config = _mapping(payload.get("config"))
    scene: Dict[str, Any] = {}
    for key in SUMMARY_FEATURE_KEYS:
        value = summary.get(key)
        if _is_scalar(value):
            scene[key] = value
    for key in CONFIG_FEATURE_KEYS:
        value = config.get(key)
        if _is_scalar(value):
            scene.setdefault(key, value)
            scene[f"config_{key}"] = value

    placements = _records(payload.get("placements"))
    categories = Counter(_normalize_category(item.get("category")) for item in placements)
    asset_ids = {str(item.get("asset_id") or "").strip() for item in placements if str(item.get("asset_id") or "").strip()}
    scene["placement_count"] = len(placements)
    scene["unique_placed_asset_count"] = len(asset_ids)
    scene["tree_count"] = _category_count(categories, ("tree", "plant", "green"))
    scene["bench_count"] = _category_count(categories, ("bench", "seat", "seating"))
    scene["lamp_count"] = _category_count(categories, ("lamp", "light", "street_light"))
    scene["bollard_count"] = _category_count(categories, ("bollard",))
    scene["poi_count"] = _category_count(categories, ("poi", "shop", "retail", "cafe", "restaurant"))
    scene["building_instance_count"] = _category_count(categories, ("building",))
    scene["building_placement_count"] = len(_records(payload.get("building_placements")))
    scene["building_footprint_count"] = scene.get("building_footprint_count", len(_records(payload.get("building_footprints"))))
    for category, count in categories.most_common(12):
        if category:
            scene[f"category_{category}_count"] = int(count)

    length_m = _numeric_feature(scene.get("length_m")) or _numeric_feature(config.get("length_m")) or 0.0
    derived: Dict[str, Any] = {}
    road_width = _numeric_feature(scene.get("road_width_m"))
    sidewalk_width = _numeric_feature(scene.get("sidewalk_width_m"))
    left_clear = _numeric_feature(scene.get("left_clear_path_width_m"))
    right_clear = _numeric_feature(scene.get("right_clear_path_width_m"))
    unique_assets = _numeric_feature(scene.get("unique_placed_asset_count"))
    placement_count = _numeric_feature(scene.get("placement_count"))
    if road_width and sidewalk_width is not None:
        derived["sidewalk_to_road_ratio"] = round(float(sidewalk_width) / float(road_width), 5)
    if road_width and left_clear is not None and right_clear is not None:
        derived["clear_path_to_road_ratio"] = round((float(left_clear) + float(right_clear)) / float(road_width), 5)
    if length_m > 0:
        for key in ("placement_count", "tree_count", "bench_count", "lamp_count", "bollard_count", "poi_count"):
            value = _numeric_feature(scene.get(key))
            if value is not None:
                derived[f"{key}_per_100m"] = round(float(value) / length_m * 100.0, 5)
    if placement_count and unique_assets is not None:
        derived["asset_diversity_per_placement"] = round(float(unique_assets) / float(placement_count), 5)
    return scene, derived


def _analysis_sample(sample: Mapping[str, Any]) -> Dict[str, Any]:
    features = _mapping(sample.get("analysis_features"))
    return {
        "sample_id": str(sample.get("sample_id") or ""),
        "run_id": str(sample.get("run_id") or ""),
        "node_id": str(sample.get("node_id") or ""),
        "parent_id": str(sample.get("parent_id") or ""),
        "preset_id": str(sample.get("preset_id") or ""),
        "preset_name": str(sample.get("preset_name") or ""),
        "preset_label": str(sample.get("preset_label") or ""),
        "preset_color": str(sample.get("preset_color") or ""),
        "label": str(sample.get("label") or ""),
        "scene_layout_path": str(sample.get("scene_layout_path") or ""),
        "input_features": dict(_mapping(features.get("input"))),
        "scene_features": dict(_mapping(features.get("scene"))),
        "derived_features": dict(_mapping(features.get("derived"))),
        "layout_available": bool(features.get("layout_available")),
        "layout_error": str(features.get("layout_error") or ""),
        "outcome": {key: _score(sample, key) for key in OUTCOME_KEYS},
        "delta_outcome": {key: _score(sample, f"delta_{key}") for key in OUTCOME_KEYS},
        "meta": {
            "source": str(sample.get("source") or ""),
            "depth": sample.get("depth"),
            "rank": sample.get("rank"),
            "is_pareto_front": bool(sample.get("is_pareto_front")),
            "pareto_rank": sample.get("pareto_rank"),
            "dominated_by_count": int(sample.get("dominated_by_count") or 0),
            "created_at": str(sample.get("created_at") or ""),
        },
    }


def _correlation_rows(samples: Sequence[Mapping[str, Any]], *, warnings: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    rows.extend(_pooled_correlations(samples, mode="pooled"))
    by_preset: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for sample in samples:
        by_preset[str(sample.get("preset_id") or "custom")].append(sample)
    for preset_id, preset_samples in by_preset.items():
        rows.extend(_pooled_correlations(preset_samples, mode="within_preset", preset_id=preset_id))
    rows.extend(_residual_correlations(samples))
    rows.extend(_delta_correlations(samples))
    if not rows:
        warnings.append("No numeric feature correlations could be computed.")
    rows.sort(key=lambda item: (str(item.get("mode")), -abs(float(item.get("rho") or 0.0)), str(item.get("feature"))))
    return rows


def _pooled_correlations(
    samples: Sequence[Mapping[str, Any]],
    *,
    mode: str,
    preset_id: str | None = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    features = sorted(_numeric_feature_names(samples))
    for feature in features:
        for outcome in OUTCOME_KEYS:
            pairs = _numeric_pairs(samples, feature, outcome)
            stat = _spearman(pairs)
            if stat is None:
                continue
            rows.append({
                "mode": mode,
                "preset_id": preset_id,
                "feature": feature,
                "outcome": outcome,
                **stat,
            })
    return rows


def _residual_correlations(samples: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    features = sorted(_numeric_feature_names(samples))
    for feature in features:
        for outcome in OUTCOME_KEYS:
            grouped: Dict[str, List[tuple[float, float]]] = defaultdict(list)
            for sample in samples:
                x = _feature_value(sample, feature)
                y = _outcome_value(sample, outcome)
                if x is None or y is None:
                    continue
                grouped[str(sample.get("preset_id") or "custom")].append((x, y))
            residual_pairs: List[tuple[float, float]] = []
            for pairs in grouped.values():
                if len(pairs) < 2:
                    continue
                mean_x = sum(x for x, _ in pairs) / len(pairs)
                mean_y = sum(y for _, y in pairs) / len(pairs)
                residual_pairs.extend((x - mean_x, y - mean_y) for x, y in pairs)
            stat = _spearman(residual_pairs)
            if stat is not None:
                rows.append({
                    "mode": "preset_residual",
                    "preset_id": None,
                    "feature": feature,
                    "outcome": outcome,
                    **stat,
                })
    return rows


def _delta_correlations(samples: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    by_run_node = {
        (str(sample.get("run_id") or ""), str(sample.get("node_id") or "")): sample
        for sample in samples
        if str(sample.get("run_id") or "") and str(sample.get("node_id") or "")
    }
    features = sorted(_numeric_feature_names(samples))
    rows: List[Dict[str, Any]] = []
    for feature in features:
        for outcome in OUTCOME_KEYS:
            pairs: List[tuple[float, float]] = []
            for sample in samples:
                parent_id = str(sample.get("parent_id") or "").strip()
                if not parent_id:
                    continue
                parent = by_run_node.get((str(sample.get("run_id") or ""), parent_id))
                if parent is None:
                    continue
                child_x = _feature_value(sample, feature)
                parent_x = _feature_value(parent, feature)
                if child_x is None or parent_x is None:
                    continue
                delta_y = _mapping(sample.get("delta_outcome")).get(outcome)
                dy = _numeric_feature(delta_y)
                if dy is None:
                    child_y = _outcome_value(sample, outcome)
                    parent_y = _outcome_value(parent, outcome)
                    if child_y is None or parent_y is None:
                        continue
                    dy = child_y - parent_y
                pairs.append((child_x - parent_x, dy))
            stat = _spearman(pairs)
            if stat is not None:
                rows.append({
                    "mode": "delta",
                    "preset_id": None,
                    "feature": feature,
                    "outcome": outcome,
                    **stat,
                })
    return rows


def _categorical_effect_rows(samples: Sequence[Mapping[str, Any]], *, warnings: List[str]) -> List[Dict[str, Any]]:
    try:
        from scipy import stats  # type: ignore
    except Exception:
        warnings.append("scipy unavailable; categorical effects skipped.")
        return []
    feature_names = sorted(_categorical_feature_names(samples) | {"meta.preset_id"})
    rows: List[Dict[str, Any]] = []
    for feature in feature_names:
        for outcome in OUTCOME_KEYS:
            grouped: Dict[str, List[float]] = defaultdict(list)
            for sample in samples:
                category = str(sample.get("preset_id") if feature == "meta.preset_id" else _raw_feature_value(sample, feature) or "").strip()
                y = _outcome_value(sample, outcome)
                if category and y is not None:
                    grouped[category].append(y)
            groups = [values for values in grouped.values() if len(values) >= 2]
            if len(groups) < 2:
                continue
            try:
                result = stats.kruskal(*groups)
            except Exception:
                continue
            rows.append({
                "feature": feature,
                "outcome": outcome,
                "test": "kruskal",
                "statistic": _finite_or_none(float(result.statistic)),
                "p_value": _finite_or_none(float(result.pvalue)),
                "n": int(sum(len(values) for values in groups)),
                "category_count": len(groups),
                "group_means": {
                    key: round(sum(values) / len(values), 4)
                    for key, values in sorted(grouped.items())
                    if len(values) >= 2
                },
            })
    rows.sort(key=lambda item: (float(item.get("p_value") if item.get("p_value") is not None else 1.0), str(item.get("feature"))))
    return rows


def _feature_importance_rows(samples: Sequence[Mapping[str, Any]], *, warnings: List[str]) -> List[Dict[str, Any]]:
    numeric_features = sorted(_numeric_feature_names(samples))
    max_model_samples = 320
    max_model_features = 56
    if len(samples) < 30:
        warnings.append("Feature importance skipped: fewer than 30 samples.")
        return []
    if len(numeric_features) < 2:
        warnings.append("Feature importance skipped: fewer than two numeric features.")
        return []
    try:
        import numpy as np  # type: ignore
        from sklearn.ensemble import RandomForestRegressor  # type: ignore
        from sklearn.inspection import permutation_importance  # type: ignore
    except Exception:
        warnings.append("sklearn unavailable; feature importance skipped.")
        return []

    rows: List[Dict[str, Any]] = []
    sampled_for_latency = False
    capped_features_for_latency = False
    for outcome in OUTCOME_KEYS:
        valid_samples = [sample for sample in samples if _outcome_value(sample, outcome) is not None]
        if len(valid_samples) < 30:
            warnings.append(f"Feature importance skipped for {outcome}: fewer than 30 scored samples.")
            continue
        sampled_from = len(valid_samples)
        if sampled_from > max_model_samples:
            step = sampled_from / max_model_samples
            valid_samples = [valid_samples[int(index * step)] for index in range(max_model_samples)]
            sampled_for_latency = True
        matrix: List[List[float]] = []
        usable_features: List[str] = []
        columns: Dict[str, List[float | None]] = {
            feature: [_feature_value(sample, feature) for sample in valid_samples]
            for feature in numeric_features
        }
        for feature, values in columns.items():
            numeric = [float(value) for value in values if value is not None and math.isfinite(float(value))]
            if len(numeric) < max(6, int(len(valid_samples) * 0.35)) or len(set(round(value, 8) for value in numeric)) < 2:
                continue
            usable_features.append(feature)
        if len(usable_features) < 2:
            warnings.append(f"Feature importance skipped for {outcome}: insufficient varying features.")
            continue
        pooled_by_feature = {
            row["feature"]: row
            for row in _pooled_correlations(valid_samples, mode="pooled")
            if row.get("outcome") == outcome
        }
        if len(usable_features) > max_model_features:
            usable_features.sort(
                key=lambda feature: abs(float(pooled_by_feature.get(feature, {}).get("rho") or 0.0)),
                reverse=True,
            )
            usable_features = sorted(usable_features[:max_model_features])
            capped_features_for_latency = True
        medians = {
            feature: _median([float(value) for value in columns[feature] if value is not None])
            for feature in usable_features
        }
        for sample in valid_samples:
            matrix.append([
                float(_feature_value(sample, feature) if _feature_value(sample, feature) is not None else medians[feature])
                for feature in usable_features
            ])
        target = [float(_outcome_value(sample, outcome) or 0.0) for sample in valid_samples]
        model = RandomForestRegressor(n_estimators=48, random_state=37, min_samples_leaf=2, n_jobs=1)
        try:
            model.fit(np.array(matrix, dtype=float), np.array(target, dtype=float))
            importance = permutation_importance(
                model,
                np.array(matrix, dtype=float),
                np.array(target, dtype=float),
                n_repeats=3,
                random_state=37,
            )
        except Exception as exc:
            warnings.append(f"Feature importance failed for {outcome}: {exc}")
            continue
        order = sorted(range(len(usable_features)), key=lambda index: float(importance.importances_mean[index]), reverse=True)[:20]
        for rank, index in enumerate(order, start=1):
            feature = usable_features[index]
            corr = pooled_by_feature.get(feature, {})
            rows.append({
                "outcome": outcome,
                "feature": feature,
                "importance": _finite_or_none(float(importance.importances_mean[index])),
                "std": _finite_or_none(float(importance.importances_std[index])),
                "rank": rank,
                "n": len(valid_samples),
                "sampled_from": sampled_from,
                "direction": _finite_or_none(float(corr.get("rho"))) if corr.get("rho") is not None else None,
            })
    if sampled_for_latency:
        warnings.append(f"Feature importance sampled at most {max_model_samples} scored samples per outcome for latency.")
    if capped_features_for_latency:
        warnings.append(f"Feature importance used at most {max_model_features} numeric features per outcome by Spearman relevance.")
    return rows


def _point_from_node(node: Mapping[str, Any], run_payload: Mapping[str, Any]) -> Dict[str, Any] | None:
    evaluation = dict(node.get("evaluation") or {})
    walkability = _score(evaluation, "walkability")
    safety = _score(evaluation, "safety")
    beauty = _score(evaluation, "beauty")
    if walkability is None or safety is None or beauty is None:
        return None
    return {
        "node_id": node.get("node_id"),
        "parent_id": node.get("parent_id"),
        "depth": node.get("depth", 0),
        "rank": node.get("rank", 0),
        "status": node.get("status", "succeeded"),
        "walkability": walkability,
        "safety": safety,
        "beauty": beauty,
        "overall": _score(evaluation, "overall") or _score(node, "score"),
        "is_pareto_front": str(node.get("node_id")) in set(run_payload.get("pareto_front") or []),
        "pareto_rank": None,
        "dominated_by_count": 0,
        "label": f"D{node.get('depth', 0)} · #{node.get('rank', 0)}",
    }


def _summaries_by_preset(samples: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    by_preset: Dict[str, List[Mapping[str, Any]]] = {}
    for sample in samples:
        by_preset.setdefault(str(sample.get("preset_id") or "custom"), []).append(sample)
    summaries = []
    for preset_id, rows in sorted(by_preset.items()):
        meta = preset_meta(preset_id)
        summaries.append({
            **meta,
            "sample_count": len(rows),
            "centroid": {
                "walkability": _mean(rows, "walkability"),
                "safety": _mean(rows, "safety"),
                "beauty": _mean(rows, "beauty"),
                "overall": _mean(rows, "overall"),
            },
            "ranges": {
                "walkability": _range(rows, "walkability"),
                "safety": _range(rows, "safety"),
                "beauty": _range(rows, "beauty"),
                "overall": _range(rows, "overall"),
            },
            "top_overall": max((_score(row, "overall") or 0.0 for row in rows), default=0.0),
            "pareto_front_count": sum(1 for row in rows if row.get("is_pareto_front")),
            "early_stop_count": len({str(row.get("run_id")) for row in rows if row.get("early_stop_triggered")}),
        })
    return summaries


def _annotate_pareto(samples: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    rows = [dict(item) for item in samples]
    scored = [item for item in rows if all(_score(item, key) is not None for key in ("walkability", "safety", "beauty"))]
    for item in rows:
        item["is_pareto_front"] = False
        item["pareto_rank"] = None
        item["dominated_by_count"] = 0
    remaining = list(scored)
    rank = 0
    while remaining:
        front = [
            item for item in remaining
            if not any(_dominates_sample(other, item) for other in remaining if other.get("sample_id") != item.get("sample_id"))
        ]
        if not front:
            break
        front_ids = {str(item.get("sample_id")) for item in front}
        for item in rows:
            if str(item.get("sample_id")) in front_ids:
                item["pareto_rank"] = rank
                item["is_pareto_front"] = rank == 0
        remaining = [item for item in remaining if str(item.get("sample_id")) not in front_ids]
        rank += 1
    for item in rows:
        item["dominated_by_count"] = sum(
            1 for other in scored
            if other.get("sample_id") != item.get("sample_id") and _dominates_sample(other, item)
        )
    return rows


def _dominates_sample(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    keys = ("walkability", "safety", "beauty")
    left_values = [_score(left, key) for key in keys]
    right_values = [_score(right, key) for key in keys]
    if any(value is None for value in left_values + right_values):
        return False
    return all(float(a) >= float(b) for a, b in zip(left_values, right_values)) and any(
        float(a) > float(b) for a, b in zip(left_values, right_values)
    )


def _mean(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [_score(row, key) for row in rows]
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return round(sum(numeric) / len(numeric), 3)


def _range(rows: Sequence[Mapping[str, Any]], key: str) -> Dict[str, float | None]:
    values = [_score(row, key) for row in rows]
    numeric = [value for value in values if value is not None]
    if not numeric:
        return {"min": None, "max": None}
    return {"min": round(min(numeric), 3), "max": round(max(numeric), 3)}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and value is not None and not (
        isinstance(value, float) and not math.isfinite(value)
    )


def _numeric_feature(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    return None


def _normalize_category(value: Any) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return raw or "unknown"


def _category_count(categories: Counter[str], needles: Sequence[str]) -> int:
    return int(sum(count for category, count in categories.items() if any(needle in category for needle in needles)))


def _flat_features(sample: Mapping[str, Any]) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for prefix, key in (
        ("input", "input_features"),
        ("scene", "scene_features"),
        ("derived", "derived_features"),
    ):
        for name, value in _mapping(sample.get(key)).items():
            flat[f"{prefix}.{name}"] = value
    return flat


def _raw_feature_value(sample: Mapping[str, Any], feature: str) -> Any:
    return _flat_features(sample).get(feature)


def _feature_value(sample: Mapping[str, Any], feature: str) -> float | None:
    return _numeric_feature(_raw_feature_value(sample, feature))


def _outcome_value(sample: Mapping[str, Any], outcome: str) -> float | None:
    return _numeric_feature(_mapping(sample.get("outcome")).get(outcome))


def _numeric_feature_names(samples: Sequence[Mapping[str, Any]]) -> set[str]:
    names: set[str] = set()
    for sample in samples:
        for key, value in _flat_features(sample).items():
            if _numeric_feature(value) is not None:
                names.add(key)
    return names


def _categorical_feature_names(samples: Sequence[Mapping[str, Any]]) -> set[str]:
    names: set[str] = set()
    for sample in samples:
        for key, value in _flat_features(sample).items():
            if isinstance(value, str) and value.strip():
                names.add(key)
            elif isinstance(value, bool):
                names.add(key)
    return names


def _numeric_pairs(samples: Sequence[Mapping[str, Any]], feature: str, outcome: str) -> List[tuple[float, float]]:
    pairs: List[tuple[float, float]] = []
    for sample in samples:
        x = _feature_value(sample, feature)
        y = _outcome_value(sample, outcome)
        if x is not None and y is not None:
            pairs.append((x, y))
    return pairs


def _spearman(pairs: Sequence[tuple[float, float]]) -> Dict[str, Any] | None:
    clean = [(float(x), float(y)) for x, y in pairs if math.isfinite(float(x)) and math.isfinite(float(y))]
    if len(clean) < 4:
        return None
    if len({round(x, 10) for x, _ in clean}) < 2 or len({round(y, 10) for _, y in clean}) < 2:
        return None
    try:
        from scipy import stats  # type: ignore
        result = stats.spearmanr([x for x, _ in clean], [y for _, y in clean])
        rho = _finite_or_none(float(result.statistic))
        p_value = _finite_or_none(float(result.pvalue))
    except Exception:
        rho = _rank_pearson([x for x, _ in clean], [y for _, y in clean])
        p_value = None
    if rho is None:
        return None
    return {
        "rho": round(float(rho), 5),
        "p_value": p_value,
        "n": len(clean),
    }


def _rank_pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    rx = _ranks(xs)
    ry = _ranks(ys)
    mean_x = sum(rx) / len(rx)
    mean_y = sum(ry) / len(ry)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(rx, ry))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in rx))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ry))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _ranks(values: Sequence[float]) -> List[float]:
    ordered = sorted((value, index) for index, value in enumerate(values))
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        rank = (index + end + 1) / 2
        for _, original_index in ordered[index:end]:
            ranks[original_index] = rank
        index = end
    return ranks


def _finite_or_none(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _median(values: Sequence[float]) -> float:
    numeric = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not numeric:
        return 0.0
    mid = len(numeric) // 2
    if len(numeric) % 2:
        return numeric[mid]
    return (numeric[mid - 1] + numeric[mid]) / 2.0


__all__ = [
    "BranchBenchmarkBatchService",
    "BranchBenchmarkStore",
    "DEFAULT_BENCHMARK_DIR",
    "preset_by_id",
    "preset_meta",
]
