"""Shared helpers for RoadGen3D API routers."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel

from roadgen3d.llm.design_workflow import parse_design_draft
from roadgen3d.services.design_types import sanitize_compose_config_patch, sanitize_scene_context
from web.api.schemas import GenerateRequestModel, SceneJobCreateRequestModel


def dump_model(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def model_payload(model: BaseModel) -> Dict[str, Any]:
    return dump_model(model)


def parse_draft_payload(payload: Dict[str, Any]) -> Any:
    return parse_design_draft(
        payload,
        evidence=(),
        fallback_query=str(payload.get("normalized_scene_query", "") or ""),
        current_patch=payload.get("compose_config_patch", {}) or {},
    )


def resolve_layout_referenced_path(value: str, layout_path: Path) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = layout_path.parent / candidate
    return candidate.resolve()


def infer_layout_preset_id(layout_path: str) -> str:
    try:
        payload = json.loads(Path(layout_path).expanduser().read_text(encoding="utf-8"))
    except Exception:
        return "custom"
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    config = payload.get("config", {}) if isinstance(payload, dict) else {}
    for source in (summary, config):
        if isinstance(source, dict):
            preset_id = str(source.get("preset_id") or source.get("benchmark_preset_id") or "").strip()
            if preset_id:
                return preset_id
    return "custom"


def prepare_scene_generation_request(
    request_body: GenerateRequestModel | SceneJobCreateRequestModel,
    *,
    scenario_design_service: Any,
) -> tuple[Any, Any, Dict[str, Any], Dict[str, Any]]:
    draft = parse_draft_payload(request_body.draft)
    scene_context_payload = dict(request_body.scene_context or {})
    patch_overrides = dict(request_body.patch_overrides or {})
    generation_options = dict(request_body.generation_options or {})
    scenario_id = str(
        scene_context_payload.get("scenario_id")
        or generation_options.get("scenario_id")
        or ""
    ).strip()
    if not scenario_id:
        return draft, sanitize_scene_context(scene_context_payload), patch_overrides, generation_options

    graph_template_id = str(
        scene_context_payload.get("graph_template_id")
        or generation_options.get("graph_template_id")
        or "hkust_gz_gate"
    ).strip() or "hkust_gz_gate"
    inline_template_patch = scene_context_payload.get("template_patch")
    if isinstance(inline_template_patch, dict) and inline_template_patch.get("operations") is not None:
        scenario_summary = dict(scene_context_payload.get("scenario_design_variant") or {})
        scenario_title = str(
            scene_context_payload.get("scenario_title")
            or scenario_summary.get("title_zh")
            or scenario_id
        )
        draft = replace(draft, template_patch=dict(inline_template_patch))
        scene_context_payload.update({
            "layout_mode": "graph_template",
            "graph_template_id": graph_template_id,
            "template_patch": dict(inline_template_patch),
            "scenario_id": scenario_id,
            "scenario_title": scenario_title,
            "scenario_design_variant": scenario_summary,
        })
        generation_options["scenario_id"] = scenario_id
        generation_options["scenario_title"] = scenario_title
        return draft, sanitize_scene_context(scene_context_payload), patch_overrides, generation_options

    scenario_inputs = scenario_design_service.generation_inputs_for_scenario(
        scenario_id,
        graph_template_id=graph_template_id,
        validate=True,
    )
    scenario_summary = dict(scenario_inputs.get("scenario") or {})
    template_patch = dict(scenario_inputs.get("template_patch") or {})
    scenario_compose_patch = sanitize_compose_config_patch(
        scenario_inputs.get("compose_config_patch") or {}
    )
    scenario_compose_already_applied = bool(generation_options.get("scenario_compose_patch_applied"))
    if not scenario_compose_already_applied:
        draft = replace(
            draft,
            compose_config_patch={
                **sanitize_compose_config_patch(draft.compose_config_patch),
                **scenario_compose_patch,
            },
        )
    draft = replace(draft, template_patch=template_patch)
    scene_context_payload.update({
        "layout_mode": "graph_template",
        "graph_template_id": graph_template_id,
        "template_patch": template_patch,
        "scenario_id": scenario_id,
        "scenario_title": str(scenario_summary.get("title_zh") or scenario_id),
        "scenario_design_variant": scenario_summary,
    })
    generation_options["scenario_id"] = scenario_id
    generation_options["scenario_title"] = str(scenario_summary.get("title_zh") or scenario_id)
    return draft, sanitize_scene_context(scene_context_payload), patch_overrides, generation_options

