"""Street-structure by furniture-goal preview matrix support."""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from ..json_safe import make_json_safe
from ..semantic_design_layers import street_furniture_profile_config_patch
from ..web_viewer_dev import cache_scene_layout_for_viewer
from .design_types import sanitize_compose_config_patch


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GRAPH_TEMPLATE_ID = "hkust_gz_gate"
DEFAULT_MATRIX_ARTIFACT_ROOT = ROOT / "artifacts" / "design_matrix"
DEFAULT_RECENT_ROOTS = (ROOT / "artifacts",)
MATRIX_SCHEMA_VERSION = "design_matrix_cell_v1"

FURNITURE_PRESETS: Sequence[Mapping[str, str]] = (
    {"id": "balanced_complete", "label": "平衡完整 / Balanced Complete"},
    {"id": "pedestrian_friendly", "label": "步行友好 / Pedestrian Friendly"},
    {"id": "commercial_vitality", "label": "商业活力 / Commercial Vitality"},
    {"id": "transit_priority", "label": "公交优先 / Transit Priority"},
    {"id": "park_landscape", "label": "公园景观 / Park Landscape"},
    {"id": "quiet_residential", "label": "安静居住 / Quiet Residential"},
)


@dataclass(frozen=True)
class MatrixOption:
    key: str
    label: str
    enabled: bool = True
    reason: str = ""
    scenario_id: str = ""
    preview_layout_path: str = ""
    compose_config_patch: Mapping[str, Any] | None = None
    template_patch: Mapping[str, Any] | None = None
    prompt: str = ""
    kind: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "enabled": bool(self.enabled),
            "reason": self.reason,
            "scenario_id": self.scenario_id or None,
            "preview_layout_path": self.preview_layout_path,
            "compose_config_patch": dict(self.compose_config_patch or {}),
            "template_patch": dict(self.template_patch or {}) if isinstance(self.template_patch, Mapping) else None,
            "prompt": self.prompt,
            "kind": self.kind,
        }


class DesignMatrixService:
    """Build and generate the 9 x 8 design preview matrix."""

    def __init__(
        self,
        *,
        design_service: Any,
        scenario_design_service: Any,
        artifact_root: str | Path | None = None,
        recent_roots: Iterable[str | Path] | None = None,
        cache_for_viewer: bool = True,
    ) -> None:
        self.design_service = design_service
        self.scenario_design_service = scenario_design_service
        self.artifact_root = Path(artifact_root or DEFAULT_MATRIX_ARTIFACT_ROOT).expanduser().resolve()
        self.recent_roots = tuple(Path(root).expanduser().resolve() for root in (recent_roots or DEFAULT_RECENT_ROOTS))
        self.cache_for_viewer = bool(cache_for_viewer)

    def inventory(self, request: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        payload = dict(request or {})
        graph_template_id = _clean_id(payload.get("graph_template_id")) or DEFAULT_GRAPH_TEMPLATE_ID
        rows = self.structure_options(graph_template_id, payload.get("custom_structure"))
        columns = self.furniture_options(payload.get("custom_furniture"))
        cell_records = [
            self._build_cell(row, column, graph_template_id=graph_template_id)
            for row in rows
            for column in columns
        ]
        ready_by_cell = self._recent_ready_by_cell(
            {str(cell["cell_key"]) for cell in cell_records},
            graph_template_id=graph_template_id,
            limit=int(payload.get("recent_limit") or 500),
        )
        cells: list[Dict[str, Any]] = []
        for cell in cell_records:
            ready = ready_by_cell.get(str(cell["cell_key"]))
            if ready and cell["status"] != "disabled":
                cell.update({
                    "status": "ready",
                    "layout_path": ready["layout_path"],
                    "scene_glb_path": ready["scene_glb_path"],
                    "updated_at": ready["updated_at"],
                })
            cells.append(cell)
        return make_json_safe({
            "schema_version": "design_matrix_inventory_v1",
            "graph_template_id": graph_template_id,
            "rows": [row.to_dict() for row in rows],
            "columns": [column.to_dict() for column in columns],
            "cells": cells,
            "generated_at": _utc_now(),
        })

    def prepare_generate(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        payload = dict(request or {})
        graph_template_id = _clean_id(payload.get("graph_template_id")) or DEFAULT_GRAPH_TEMPLATE_ID
        rows = {row.key: row for row in self.structure_options(graph_template_id, payload.get("custom_structure"))}
        columns = {column.key: column for column in self.furniture_options(payload.get("custom_furniture"))}
        structure_key = _clean_key(payload.get("structure_key"))
        furniture_key = _clean_key(payload.get("furniture_key"))
        structure = rows.get(structure_key)
        furniture = columns.get(furniture_key)
        if structure is None:
            raise RuntimeError(f"Unknown matrix structure key: {structure_key}")
        if furniture is None:
            raise RuntimeError(f"Unknown matrix furniture key: {furniture_key}")
        if not structure.enabled:
            raise RuntimeError(structure.reason or f"Structure is disabled: {structure.label}")
        if not furniture.enabled:
            raise RuntimeError(furniture.reason or f"Furniture target is disabled: {furniture.label}")

        cell = self._build_cell(structure, furniture, graph_template_id=graph_template_id)
        metadata = dict(cell["metadata"])
        if furniture.key == "none":
            return self._materialize_no_furniture_cell(
                structure=structure,
                metadata=metadata,
                source_layout_path=str(payload.get("source_layout_path") or ""),
            )

        scene_job_request = self._scene_job_request(
            structure=structure,
            furniture=furniture,
            graph_template_id=graph_template_id,
            metadata=metadata,
        )
        return make_json_safe({
            "mode": "job",
            "cell": cell,
            "scene_job_request": scene_job_request,
        })

    def structure_options(self, graph_template_id: str, custom_structure: object = None) -> list[MatrixOption]:
        rows = [
            MatrixOption(
                key="base",
                label="Base Template / 基础模板",
                kind="base",
            )
        ]
        try:
            catalog = self.scenario_design_service.list_scenarios()
            items = list(catalog.get("items") or [])
        except Exception:
            items = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            scenario_id = _clean_id(item.get("scenario_id"))
            if not scenario_id:
                continue
            rows.append(
                MatrixOption(
                    key=f"scenario:{scenario_id}",
                    label=str(item.get("title_zh") or scenario_id),
                    enabled=bool(item.get("enabled", True)),
                    reason=str(item.get("excluded_reason_zh") or ""),
                    scenario_id=scenario_id,
                    preview_layout_path=str(item.get("preview_layout_path") or ""),
                    compose_config_patch=sanitize_compose_config_patch(item.get("compose_config_patch")),
                    prompt=str(item.get("query") or item.get("intent_zh") or scenario_id),
                    kind="scenario",
                )
            )
        custom = self._custom_structure_option(custom_structure)
        if custom is None:
            custom = MatrixOption(
                key="custom:empty",
                label="Custom Structure / 自定义结构",
                enabled=False,
                reason="No draft structure is available.",
                kind="custom",
            )
        rows.append(custom)
        return rows

    def furniture_options(self, custom_furniture: object = None) -> list[MatrixOption]:
        columns = [
            MatrixOption(
                key="none",
                label="No Furniture / 无家具",
                kind="none",
            )
        ]
        for preset in FURNITURE_PRESETS:
            preset_id = str(preset["id"])
            patch = street_furniture_profile_config_patch(preset_id)
            patch.update({
                "street_furniture_profile_source": "manual",
                "street_furniture_profile_confidence": 1.0,
                "street_furniture_profile_reasons": ("matrix:preset",),
            })
            columns.append(
                MatrixOption(
                    key=f"preset:{preset_id}",
                    label=str(preset["label"]),
                    compose_config_patch=sanitize_compose_config_patch(patch),
                    prompt=str(preset["label"]),
                    kind="preset",
                )
            )
        custom = self._custom_furniture_option(custom_furniture)
        if custom is None:
            custom = MatrixOption(
                key="custom:empty",
                label="Custom Furniture / 自定义家具",
                enabled=False,
                reason="No custom furniture prompt is available.",
                kind="custom",
            )
        columns.append(custom)
        return columns

    def _custom_structure_option(self, custom_structure: object) -> MatrixOption | None:
        if not isinstance(custom_structure, Mapping):
            return None
        payload = dict(custom_structure)
        scenario_id = _clean_id(payload.get("scenario_id")) or f"custom_{_stable_hash(payload)[:10]}"
        key = f"custom:{_stable_hash(payload)}"
        return MatrixOption(
            key=key,
            label=str(payload.get("title_zh") or payload.get("label") or "Custom Structure / 自定义结构"),
            enabled=bool(payload.get("enabled", True)),
            reason=str(payload.get("reason") or payload.get("excluded_reason_zh") or ""),
            scenario_id=scenario_id,
            preview_layout_path=str(payload.get("preview_layout_path") or ""),
            compose_config_patch=sanitize_compose_config_patch(payload.get("compose_config_patch")),
            template_patch=dict(payload.get("template_patch")) if isinstance(payload.get("template_patch"), Mapping) else None,
            prompt=str(payload.get("query") or payload.get("intent_zh") or payload.get("prompt") or scenario_id),
            kind="custom",
        )

    def _custom_furniture_option(self, custom_furniture: object) -> MatrixOption | None:
        if not isinstance(custom_furniture, Mapping):
            return None
        payload = dict(custom_furniture)
        prompt = str(payload.get("prompt") or "").strip()
        patch = sanitize_compose_config_patch(payload.get("compose_config_patch"))
        if not prompt and not patch:
            return None
        label = str(payload.get("label") or "Custom Furniture / 自定义家具")
        key = f"custom:{_stable_hash({'prompt': prompt, 'compose_config_patch': patch})}"
        patch = dict(patch)
        patch.setdefault("query", prompt)
        return MatrixOption(
            key=key,
            label=label,
            compose_config_patch=sanitize_compose_config_patch(patch),
            prompt=prompt,
            kind="custom",
        )

    def _build_cell(self, structure: MatrixOption, furniture: MatrixOption, *, graph_template_id: str) -> Dict[str, Any]:
        metadata = {
            "schema_version": MATRIX_SCHEMA_VERSION,
            "graph_template_id": graph_template_id,
            "structure_key": structure.key,
            "structure_label": structure.label,
            "structure_scenario_id": structure.scenario_id or None,
            "furniture_key": furniture.key,
            "furniture_label": furniture.label,
            "furniture_kind": furniture.kind,
        }
        cell_hash = _stable_hash(metadata)
        metadata.update({
            "cell_hash": cell_hash,
            "cell_key": f"dm:{graph_template_id}:{cell_hash}",
        })
        disabled_reason = structure.reason if not structure.enabled else furniture.reason if not furniture.enabled else ""
        return {
            "cell_key": metadata["cell_key"],
            "cell_hash": cell_hash,
            "structure_key": structure.key,
            "furniture_key": furniture.key,
            "status": "disabled" if disabled_reason else "missing",
            "reason": disabled_reason,
            "layout_path": "",
            "scene_glb_path": "",
            "updated_at": "",
            "metadata": metadata,
        }

    def _scene_job_request(
        self,
        *,
        structure: MatrixOption,
        furniture: MatrixOption,
        graph_template_id: str,
        metadata: Mapping[str, Any],
    ) -> Dict[str, Any]:
        structure_patch = sanitize_compose_config_patch(structure.compose_config_patch)
        furniture_patch = sanitize_compose_config_patch(furniture.compose_config_patch)
        prompt = " ".join(
            part for part in (structure.prompt, furniture.prompt, f"{structure.label} x {furniture.label}") if part
        ).strip()
        compose_patch = {
            **structure_patch,
            **furniture_patch,
        }
        compose_patch["query"] = prompt or f"{structure.label} x {furniture.label}"
        seed = _seed_from_hash(str(metadata.get("cell_hash") or ""))
        scenario_context: Dict[str, Any] = {}
        generation_options: Dict[str, Any] = {
            "preset_id": str(metadata.get("furniture_key") or "matrix"),
            "random_seed": seed,
            "design_variant_id": "design_matrix_cell",
            "design_variant_name": f"{structure.label} x {furniture.label}",
            "build_production_artifacts": False,
            "render_presentation_artifacts": False,
            "capture_3d_views": False,
            "export_format": "glb",
            "retain_glb_policy": "always",
            "design_matrix_cell": {
                **dict(metadata),
                "generated_at": _utc_now(),
            },
            "out_dir": str((self.artifact_root / str(metadata["cell_hash"])).resolve()),
        }
        if structure.kind in {"scenario", "custom"} and structure.scenario_id:
            scenario_context.update({
                "scenario_id": structure.scenario_id,
                "scenario_title": structure.label,
                "scenario_design_variant": {
                    "scenario_id": structure.scenario_id,
                    "title_zh": structure.label,
                    "compose_config_patch": structure_patch,
                    "preview_layout_path": structure.preview_layout_path,
                },
            })
            generation_options["scenario_id"] = structure.scenario_id
            generation_options["scenario_title"] = structure.label
            generation_options["scenario_compose_patch_applied"] = True
            if structure.template_patch:
                scenario_context["template_patch"] = dict(structure.template_patch)
        return {
            "draft": {
                "normalized_scene_query": prompt,
                "compose_config_patch": compose_patch,
                "citations_by_field": {},
                "design_summary": prompt,
                "risk_notes": ["Design matrix cell generation; no production-step GLBs are retained."],
                "parameter_sources_by_field": {
                    "structure_key": "design_matrix",
                    "furniture_key": "design_matrix",
                },
            },
            "scene_context": {
                "layout_mode": "graph_template",
                "aoi_bbox": None,
                "city_name_en": None,
                "reference_plan_id": None,
                "graph_template_id": graph_template_id,
                **scenario_context,
            },
            "patch_overrides": {},
            "generation_options": generation_options,
        }

    def _materialize_no_furniture_cell(
        self,
        *,
        structure: MatrixOption,
        metadata: Mapping[str, Any],
        source_layout_path: str,
    ) -> Dict[str, Any]:
        layout_path = _resolve_existing_layout(structure.preview_layout_path) or _resolve_existing_layout(source_layout_path)
        if layout_path is None:
            raise RuntimeError(f"No structure preview layout is available for {structure.label}.")
        payload = json.loads(layout_path.read_text(encoding="utf-8"))
        source_glb = _find_step_glb(payload, layout_path, ("buildings", "land_use_zoning", "road_base"))
        if source_glb is None:
            source_glb = _resolve_layout_path((payload.get("outputs") or {}).get("scene_glb"), layout_path)
        if source_glb is None or not source_glb.exists():
            raise RuntimeError(f"No buildings-step GLB is available for {structure.label}.")

        cell_hash = str(metadata["cell_hash"])
        out_dir = (self.artifact_root / cell_hash / "no_furniture").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        scene_glb = out_dir / "scene.glb"
        shutil.copyfile(source_glb, scene_glb)

        cell_payload = copy.deepcopy(payload)
        outputs = dict(cell_payload.get("outputs") or {})
        outputs["scene_glb"] = str(scene_glb)
        outputs["scene_ply"] = ""
        outputs.pop("production_steps_dir", None)
        outputs.pop("production_steps_manifest", None)
        cell_payload["outputs"] = outputs
        cell_payload["production_steps"] = []
        summary = dict(cell_payload.get("summary") or {})
        summary.update({
            "design_matrix_cell": {
                **dict(metadata),
                "generated_at": _utc_now(),
                "materialized_from_layout": str(layout_path),
                "materialized_from_step": "buildings",
            },
            "preset_id": "no_furniture",
            "design_variant_id": "design_matrix_cell",
            "design_variant_name": f"{structure.label} x No Furniture / 无家具",
            "street_furniture_profile": "none",
            "instance_count": 0,
            "asset_library_scene_instances": 0,
            "production_step_count": 0,
            "production_step_ids": [],
            "final_production_step_id": "",
        })
        cell_payload["summary"] = make_json_safe(summary)
        layout_out = out_dir / "scene_layout.json"
        layout_out.write_text(json.dumps(make_json_safe(cell_payload), indent=2, ensure_ascii=True), encoding="utf-8")
        cached_layout = cache_scene_layout_for_viewer(layout_out) if self.cache_for_viewer else layout_out
        return make_json_safe({
            "mode": "materialized",
            "cell": {
                "cell_key": metadata["cell_key"],
                "cell_hash": cell_hash,
                "structure_key": metadata["structure_key"],
                "furniture_key": metadata["furniture_key"],
                "status": "ready",
                "layout_path": str(cached_layout),
                "scene_glb_path": str(scene_glb),
                "updated_at": _utc_now(),
                "metadata": dict(metadata),
            },
            "layout_path": str(cached_layout),
            "scene_glb_path": str(scene_glb),
        })

    def _recent_ready_by_cell(
        self,
        cell_keys: set[str],
        *,
        graph_template_id: str,
        limit: int,
    ) -> Dict[str, Dict[str, str]]:
        if not cell_keys:
            return {}
        matches: Dict[str, Dict[str, str]] = {}
        for layout_path in _iter_recent_layouts(self.recent_roots, limit=max(1, limit)):
            try:
                payload = json.loads(layout_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
            metadata = summary.get("design_matrix_cell") if isinstance(summary, Mapping) else None
            if not isinstance(metadata, Mapping):
                continue
            if str(metadata.get("graph_template_id") or "") != graph_template_id:
                continue
            cell_key = str(metadata.get("cell_key") or "")
            if cell_key not in cell_keys or cell_key in matches:
                continue
            scene_glb = _resolve_layout_path((payload.get("outputs") or {}).get("scene_glb"), layout_path)
            if scene_glb is None or not scene_glb.exists():
                continue
            stat = layout_path.stat()
            matches[cell_key] = {
                "layout_path": str(layout_path),
                "scene_glb_path": str(scene_glb),
                "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
            if len(matches) >= len(cell_keys):
                break
        return matches


def _iter_recent_layouts(roots: Sequence[Path], *, limit: int) -> list[Path]:
    candidates: list[tuple[float, Path]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("scene_layout.json"):
            try:
                candidates.append((path.stat().st_mtime, path.resolve()))
            except OSError:
                continue
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in candidates[:limit]]


def _find_step_glb(payload: Mapping[str, Any], layout_path: Path, step_ids: Sequence[str]) -> Path | None:
    by_id = {
        str(step.get("step_id") or ""): step
        for step in payload.get("production_steps", []) or []
        if isinstance(step, Mapping)
    }
    for step_id in step_ids:
        step = by_id.get(step_id)
        if not step:
            continue
        glb = _resolve_layout_path(step.get("glb_path"), layout_path)
        if glb and glb.exists():
            return glb
    return None


def _resolve_existing_layout(value: object) -> Path | None:
    path = _resolve_layout_path(value, None)
    if path and path.exists() and path.is_file():
        return path
    return None


def _resolve_layout_path(value: object, layout_path: Path | None) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute() and layout_path is not None:
        candidate = layout_path.parent / candidate
    return candidate.resolve()


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(make_json_safe(payload), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _seed_from_hash(value: str) -> int:
    raw = value[:8] or "20260511"
    try:
        return int(raw, 16) % 2_147_483_647
    except ValueError:
        return 20260511


def _clean_id(value: object) -> str:
    return str(value or "").strip()


def _clean_key(value: object) -> str:
    return str(value or "").strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
