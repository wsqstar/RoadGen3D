"""Scenario design catalog and batch scene generation service."""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence
from uuid import uuid4

from ..graph_templates import load_graph_template_annotation_payload
from ..json_safe import make_json_safe
from ..template_patch import TEMPLATE_PATCH_SCHEMA_VERSION, TemplatePatchError, apply_template_patch
from .design_types import DesignDraft, SceneJobStatusResponse, sanitize_compose_config_patch


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCENARIO_CATALOG_PATH = ROOT / "data" / "scenario_designs" / "hkust_gz_gate_scenarios.json"
DEFAULT_SCENARIO_RUN_ROOT = ROOT / "artifacts" / "scenario_design_runs"
DEFAULT_GRAPH_TEMPLATE_ID = "hkust_gz_gate"


class ScenarioDesignService:
    """Expose curated scenario designs as scene-job batch runs."""

    def __init__(
        self,
        *,
        design_service: Any,
        catalog_path: str | Path | None = None,
        run_root: str | Path | None = None,
    ) -> None:
        self.design_service = design_service
        self.catalog_path = Path(catalog_path or DEFAULT_SCENARIO_CATALOG_PATH).expanduser().resolve()
        self.run_root = Path(run_root or DEFAULT_SCENARIO_RUN_ROOT).expanduser().resolve()
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._primary_centerline_cache: Dict[str, str] = {}

    def list_scenarios(self) -> Dict[str, Any]:
        catalog = self._load_catalog()
        items = [self._scenario_summary(item) for item in catalog["scenarios"]]
        return make_json_safe({
            "schema_version": catalog.get("schema_version", "roadgen3d_scenario_design_catalog_v1"),
            "graph_template_id": catalog.get("graph_template_id", DEFAULT_GRAPH_TEMPLATE_ID),
            "catalog_path": str(self.catalog_path),
            "items": items,
            "runs": self._list_run_summaries(),
        })

    def submit_run(
        self,
        *,
        scenario_ids: Sequence[str] | None = None,
        samples_per_scenario: int = 3,
        base_seed: int = 20260506,
        graph_template_id: str = DEFAULT_GRAPH_TEMPLATE_ID,
        generation_options: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if samples_per_scenario < 1 or samples_per_scenario > 10:
            raise RuntimeError("samples_per_scenario must be between 1 and 10.")
        catalog = self._load_catalog()
        selected = self._select_scenarios(catalog["scenarios"], scenario_ids)
        run_id = f"scenario_run_{_utc_compact()}_{uuid4().hex[:8]}"
        run_dir = (self.run_root / run_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)

        items: list[Dict[str, Any]] = []
        for scenario_index, scenario in enumerate(selected):
            template_patch = self.scenario_to_template_patch(
                scenario,
                graph_template_id=graph_template_id,
                validate=True,
            )
            for sample_index in range(1, samples_per_scenario + 1):
                seed = int(base_seed) + scenario_index * 1000 + sample_index - 1
                item = self._submit_scenario_sample(
                    scenario=scenario,
                    template_patch=template_patch,
                    sample_index=sample_index,
                    seed=seed,
                    run_dir=run_dir,
                    graph_template_id=graph_template_id,
                    generation_options=dict(generation_options or {}),
                )
                items.append(item)

        now = _utc_now()
        run = {
            "run_id": run_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "finished_at": "",
            "graph_template_id": graph_template_id,
            "samples_per_scenario": samples_per_scenario,
            "base_seed": int(base_seed),
            "scenario_count": len(selected),
            "total_jobs": len(items),
            "completed_jobs": 0,
            "failed_jobs": sum(1 for item in items if item["status"] == "failed"),
            "run_dir": str(run_dir),
            "manifest_path": str(run_dir / "manifest.json"),
            "report_path": str(run_dir / "SCENARIO_GENERATION_REPORT.md"),
            "items": items,
            "scenarios": [self._scenario_summary(item) for item in selected],
        }
        self._runs[run_id] = run
        self._refresh_run_status(run)
        return make_json_safe(run)

    def get_run(self, run_id: str) -> Dict[str, Any] | None:
        run = self._runs.get(run_id) or self._load_run_manifest(run_id)
        if run is None:
            return None
        self._runs[run_id] = run
        self._refresh_run_status(run)
        return make_json_safe(run)

    def get_report(self, run_id: str) -> Dict[str, Any] | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        report_path = Path(str(run.get("report_path") or ""))
        content = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        return make_json_safe({
            "run_id": run_id,
            "status": run.get("status", ""),
            "report_path": str(report_path),
            "content": content,
            "content_summary": _summarize_text(content),
        })

    def scenario_to_template_patch(
        self,
        scenario: Mapping[str, Any],
        *,
        graph_template_id: str = DEFAULT_GRAPH_TEMPLATE_ID,
        validate: bool = False,
    ) -> Dict[str, Any]:
        scenario_id = _required_text(scenario.get("scenario_id"), "scenario_id")
        centerline_id = self._primary_centerline_id(graph_template_id)
        operations: list[Dict[str, Any]] = []
        for zone in _records(scenario.get("functional_zones")):
            operations.append({
                "op": "upsert_functional_zone",
                "zone": copy.deepcopy(zone),
            })
        for surface in _records(scenario.get("surface_annotations")):
            normalized_surface = copy.deepcopy(surface)
            normalized_surface["centerline_id"] = centerline_id
            operations.append({
                "op": "upsert_surface_annotation",
                "surface": normalized_surface,
            })
        patch = {
            "schema_version": TEMPLATE_PATCH_SCHEMA_VERSION,
            "variant_id": scenario_id,
            "description": str(scenario.get("intent_zh") or scenario.get("query") or scenario_id),
            "operations": operations,
        }
        if validate:
            try:
                base_annotation = load_graph_template_annotation_payload(graph_template_id)
                apply_template_patch(base_annotation, patch)
            except (KeyError, TemplatePatchError, ValueError) as exc:
                raise RuntimeError(f"Scenario {scenario_id} is not valid for template {graph_template_id}: {exc}") from exc
        return patch

    def _submit_scenario_sample(
        self,
        *,
        scenario: Mapping[str, Any],
        template_patch: Mapping[str, Any],
        sample_index: int,
        seed: int,
        run_dir: Path,
        graph_template_id: str,
        generation_options: Dict[str, Any],
    ) -> Dict[str, Any]:
        scenario_id = _required_text(scenario.get("scenario_id"), "scenario_id")
        compose_patch = sanitize_compose_config_patch(scenario.get("compose_config_patch"))
        compose_patch["query"] = str(scenario.get("query") or scenario_id)
        compose_patch["seed"] = int(seed)
        scenario_out_dir = run_dir / scenario_id / f"sample_{sample_index:02d}"
        sample_generation_options = dict(generation_options)
        sample_generation_options["out_dir"] = str(scenario_out_dir)
        sample_generation_options["random_seed"] = int(seed)
        sample_generation_options.setdefault("preset_id", "skip_llm")
        sample_generation_options.setdefault("retain_glb_policy", "always")
        sample_generation_options.setdefault("capture_failure_policy", "warn")
        draft = DesignDraft(
            normalized_scene_query=str(scenario.get("query") or scenario_id),
            compose_config_patch=compose_patch,
            citations_by_field={},
            design_summary=str(scenario.get("intent_zh") or scenario.get("title_zh") or scenario_id),
            risk_notes=(
                "Scenario design catalog entry; generated without LLM re-drafting.",
            ),
            parameter_sources_by_field={
                "scenario_id": "scenario_design_catalog",
                "template_patch": "scenario_design_catalog",
                "seed": "scenario_design_batch",
            },
            template_patch=dict(template_patch),
        )
        item = {
            "scenario_id": scenario_id,
            "scenario_type": str(scenario.get("scenario_type") or ""),
            "title_zh": str(scenario.get("title_zh") or scenario_id),
            "sample_index": int(sample_index),
            "seed": int(seed),
            "job_id": "",
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "scene_layout_path": "",
            "scene_glb_path": "",
            "viewer_url": "",
            "summary": {},
            "error": "",
        }
        try:
            response = self.design_service.create_scene_job(
                draft=draft,
                scene_context={
                    "layout_mode": "graph_template",
                    "graph_template_id": graph_template_id,
                    "template_patch": dict(template_patch),
                },
                patch_overrides={},
                generation_options=sample_generation_options,
            )
            payload = response.to_dict() if hasattr(response, "to_dict") else dict(response)
            item["job_id"] = str(payload.get("job_id") or "")
            item["status"] = str(payload.get("status") or "queued")
        except Exception as exc:  # pragma: no cover - exercised through API aggregation.
            item["status"] = "failed"
            item["stage"] = "submission_failed"
            item["progress"] = 100
            item["error"] = str(exc)
        return item

    def _refresh_run_status(self, run: Dict[str, Any]) -> None:
        for item in run.get("items", []):
            if not isinstance(item, dict) or not item.get("job_id") or item.get("status") == "failed" and item.get("stage") == "submission_failed":
                continue
            status = self.design_service.get_scene_job(str(item["job_id"]))
            if status is None:
                continue
            self._apply_job_status(item, status)
        total = len([item for item in run.get("items", []) if isinstance(item, dict)])
        completed = sum(1 for item in run.get("items", []) if isinstance(item, dict) and item.get("status") == "succeeded")
        failed = sum(1 for item in run.get("items", []) if isinstance(item, dict) and item.get("status") == "failed")
        terminal = completed + failed
        run["total_jobs"] = total
        run["completed_jobs"] = completed
        run["failed_jobs"] = failed
        run["updated_at"] = _utc_now()
        if total == 0:
            run["status"] = "empty"
        elif terminal >= total:
            run["status"] = "succeeded" if failed == 0 else ("failed" if completed == 0 else "partial")
            run["finished_at"] = run.get("finished_at") or _utc_now()
        else:
            run["status"] = "running" if terminal > 0 or any(item.get("status") == "running" for item in run.get("items", []) if isinstance(item, dict)) else "queued"
            run["finished_at"] = ""
        self._persist_run(run)
        self._write_report(run)

    def _apply_job_status(self, item: Dict[str, Any], status: SceneJobStatusResponse | Mapping[str, Any]) -> None:
        payload = status.to_dict() if hasattr(status, "to_dict") else dict(status)
        item["status"] = str(payload.get("status") or item.get("status") or "queued")
        item["stage"] = str(payload.get("stage") or item.get("stage") or item["status"])
        item["progress"] = int(payload.get("progress") or item.get("progress") or 0)
        item["error"] = str(payload.get("error") or item.get("error") or "")
        result = payload.get("result")
        if isinstance(result, Mapping):
            item["scene_layout_path"] = str(result.get("scene_layout_path") or result.get("layout_path") or item.get("scene_layout_path") or "")
            item["scene_glb_path"] = str(result.get("scene_glb_path") or item.get("scene_glb_path") or "")
            item["viewer_url"] = str(result.get("viewer_url") or item.get("viewer_url") or "")
            summary = result.get("summary")
            item["summary"] = dict(summary) if isinstance(summary, Mapping) else item.get("summary", {})

    def _load_catalog(self) -> Dict[str, Any]:
        if not self.catalog_path.exists():
            raise RuntimeError(f"Scenario design catalog not found: {self.catalog_path}")
        try:
            catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid scenario design catalog JSON: {exc}") from exc
        scenarios = catalog.get("scenarios") or []
        if not isinstance(scenarios, Sequence) or isinstance(scenarios, (str, bytes)):
            raise RuntimeError("Scenario design catalog.scenarios must be an array.")
        normalized = [dict(item) for item in scenarios if isinstance(item, Mapping)]
        ids = [_required_text(item.get("scenario_id"), "scenario_id") for item in normalized]
        if len(ids) != len(set(ids)):
            raise RuntimeError("Scenario design catalog contains duplicate scenario_id values.")
        return {**dict(catalog), "scenarios": normalized}

    def _select_scenarios(
        self,
        scenarios: Sequence[Mapping[str, Any]],
        scenario_ids: Sequence[str] | None,
    ) -> list[Mapping[str, Any]]:
        if not scenario_ids:
            return list(scenarios)
        wanted = [str(item).strip() for item in scenario_ids if str(item).strip()]
        by_id = {str(item.get("scenario_id")): item for item in scenarios}
        missing = [item for item in wanted if item not in by_id]
        if missing:
            raise RuntimeError(f"Unknown scenario design id(s): {', '.join(missing)}")
        return [by_id[item] for item in wanted]

    def _scenario_summary(self, scenario: Mapping[str, Any]) -> Dict[str, Any]:
        preview_path = self._resolve_catalog_path(str(scenario.get("preview_layout_path") or ""))
        surfaces = _records(scenario.get("surface_annotations"))
        surface_roles: Dict[str, int] = {}
        for surface in surfaces:
            role = str(surface.get("surface_role") or "").strip() or "unknown"
            surface_roles[role] = surface_roles.get(role, 0) + 1
        return {
            "scenario_id": str(scenario.get("scenario_id") or ""),
            "title_zh": str(scenario.get("title_zh") or ""),
            "scenario_type": str(scenario.get("scenario_type") or ""),
            "query": str(scenario.get("query") or ""),
            "intent_zh": str(scenario.get("intent_zh") or ""),
            "road_section": dict(scenario.get("road_section") or {}) if isinstance(scenario.get("road_section"), Mapping) else {},
            "edge_context": dict(scenario.get("edge_context") or {}) if isinstance(scenario.get("edge_context"), Mapping) else {},
            "functional_zone_count": len(_records(scenario.get("functional_zones"))),
            "surface_annotation_count": len(surfaces),
            "surface_role_counts": surface_roles,
            "preview_layout_path": str(preview_path) if preview_path is not None else "",
            "preview_layout_exists": bool(preview_path and preview_path.exists()),
        }

    def _resolve_catalog_path(self, raw_path: str) -> Path | None:
        if not raw_path.strip():
            return None
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        return candidate.resolve()

    def _primary_centerline_id(self, graph_template_id: str) -> str:
        template_id = str(graph_template_id or DEFAULT_GRAPH_TEMPLATE_ID).strip().lower()
        cached = self._primary_centerline_cache.get(template_id)
        if cached:
            return cached
        payload = load_graph_template_annotation_payload(template_id)
        best_id = ""
        best_length = -1.0
        for centerline in _records(payload.get("centerlines")):
            centerline_id = str(centerline.get("id") or centerline.get("feature_id") or "").strip()
            length = _polyline_length_px(centerline.get("points"))
            if centerline_id and length > best_length:
                best_id = centerline_id
                best_length = length
        if not best_id:
            raise RuntimeError(f"Graph template {template_id} does not contain a usable centerline.")
        self._primary_centerline_cache[template_id] = best_id
        return best_id

    def _persist_run(self, run: Mapping[str, Any]) -> None:
        manifest_path = Path(str(run.get("manifest_path") or ""))
        if not manifest_path:
            return
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(make_json_safe(run), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_run_manifest(self, run_id: str) -> Dict[str, Any] | None:
        run_path = self.run_root / run_id / "manifest.json"
        if not run_path.exists():
            return None
        try:
            payload = json.loads(run_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return dict(payload) if isinstance(payload, Mapping) else None

    def _list_run_summaries(self) -> list[Dict[str, Any]]:
        summaries: list[Dict[str, Any]] = []
        if not self.run_root.exists():
            return summaries
        for manifest_path in sorted(self.run_root.glob("*/manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, Mapping):
                continue
            summaries.append({
                "run_id": str(payload.get("run_id") or manifest_path.parent.name),
                "status": str(payload.get("status") or ""),
                "created_at": str(payload.get("created_at") or ""),
                "updated_at": str(payload.get("updated_at") or ""),
                "total_jobs": int(payload.get("total_jobs") or 0),
                "completed_jobs": int(payload.get("completed_jobs") or 0),
                "failed_jobs": int(payload.get("failed_jobs") or 0),
                "report_path": str(payload.get("report_path") or manifest_path.parent / "SCENARIO_GENERATION_REPORT.md"),
            })
        return summaries[:12]

    def _write_report(self, run: Mapping[str, Any]) -> None:
        report_path = Path(str(run.get("report_path") or ""))
        if not report_path:
            return
        report_path.parent.mkdir(parents=True, exist_ok=True)
        lines = _render_report(run)
        report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _render_report(run: Mapping[str, Any]) -> list[str]:
    lines = [
        "# Scenario Generation Report",
        "",
        f"- Run ID: `{run.get('run_id', '')}`",
        f"- Status: `{run.get('status', '')}`",
        f"- Graph template: `{run.get('graph_template_id', '')}`",
        f"- Jobs: {run.get('completed_jobs', 0)} succeeded, {run.get('failed_jobs', 0)} failed, {run.get('total_jobs', 0)} total",
        f"- Updated: {run.get('updated_at', '')}",
        "",
        "## Scenario Coverage",
        "",
        "| Scenario | Zones | Surfaces | Surface roles |",
        "| --- | ---: | ---: | --- |",
    ]
    for scenario in run.get("scenarios", []):
        if not isinstance(scenario, Mapping):
            continue
        roles = scenario.get("surface_role_counts")
        role_text = ", ".join(f"{key} x{value}" for key, value in sorted(dict(roles or {}).items())) or "-"
        lines.append(
            "| "
            + " | ".join([
                _md_text(str(scenario.get("title_zh") or scenario.get("scenario_id") or "")),
                str(scenario.get("functional_zone_count") or 0),
                str(scenario.get("surface_annotation_count") or 0),
                _md_text(role_text),
            ])
            + " |"
        )
    lines.extend([
        "",
        "## Results",
        "",
        "| Scenario | Sample | Seed | Status | Layout | GLB | Error |",
        "| --- | ---: | ---: | --- | --- | --- | --- |",
    ])
    for item in run.get("items", []):
        if not isinstance(item, Mapping):
            continue
        layout = _md_path(str(item.get("scene_layout_path") or ""))
        glb = _md_path(str(item.get("scene_glb_path") or ""))
        lines.append(
            "| "
            + " | ".join([
                _md_text(str(item.get("title_zh") or item.get("scenario_id") or "")),
                str(item.get("sample_index") or ""),
                str(item.get("seed") or ""),
                f"`{_md_text(str(item.get('status') or ''))}`",
                layout,
                glb,
                _md_text(str(item.get("error") or "")) or "-",
            ])
            + " |"
        )
    failures = [
        item
        for item in run.get("items", [])
        if isinstance(item, Mapping) and str(item.get("status") or "") == "failed"
    ]
    lines.extend(["", "## Failures", ""])
    if failures:
        for item in failures:
            lines.append(
                f"- `{item.get('scenario_id', '')}` sample {item.get('sample_index', '')}: "
                f"{item.get('error', '') or item.get('stage', 'failed')}"
            )
    else:
        lines.append("- No failed jobs recorded.")
    lines.extend([
        "",
        "## Recommended Viewing Order",
        "",
    ])
    for scenario in run.get("scenarios", []):
        if isinstance(scenario, Mapping):
            lines.append(f"- {scenario.get('title_zh') or scenario.get('scenario_id')}")
    return lines


def _records(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _polyline_length_px(points: Any) -> float:
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
        return 0.0
    total = 0.0
    last: tuple[float, float] | None = None
    for point in points:
        if not isinstance(point, Mapping):
            continue
        try:
            current = (float(point.get("x")), float(point.get("y")))
        except (TypeError, ValueError):
            continue
        if last is not None:
            total += math.hypot(current[0] - last[0], current[1] - last[1])
        last = current
    return total


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"{label} is required.")
    return text


def _summarize_text(value: str, limit: int = 360) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _md_text(value: str) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _md_path(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    return f"`{_md_text(text)}`"


__all__ = [
    "DEFAULT_SCENARIO_CATALOG_PATH",
    "DEFAULT_SCENARIO_RUN_ROOT",
    "ScenarioDesignService",
]
