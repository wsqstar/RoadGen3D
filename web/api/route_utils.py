"""Shared helpers for RoadGen3D API routers."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel

from roadgen3d.llm.design_workflow import parse_design_draft
from roadgen3d.services.design_types import sanitize_compose_config_patch, sanitize_scene_context
from roadgen3d.services.street_design_parameters import compile_street_design_parameter_spec
from web.api.schemas import GenerateRequestModel, SceneJobCreateRequestModel

ROOT = Path(__file__).resolve().parents[2]


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
    parameter_spec = generation_options.get("street_design_parameter_spec") or generation_options.get("parameter_spec")
    if isinstance(parameter_spec, dict):
        compiled = compile_street_design_parameter_spec(
            parameter_spec,
            field_sources=generation_options.get("parameter_sources_by_field") or {},
        )
        patch_overrides.update(compiled.compose_config_patch)
        generation_options.update(compiled.generation_options)
    scenario_id = str(
        scene_context_payload.get("scenario_id")
        or generation_options.get("scenario_id")
        or ""
    ).strip()
    if scene_context_payload.get("reference_annotation_path"):
        raise ValueError(
            "reference_annotation_path is not accepted from API clients; "
            "submit scene_context.reference_annotation inline."
        )
    if not scenario_id:
        return draft, sanitize_scene_context(scene_context_payload), patch_overrides, generation_options

    graph_template_id = str(
        scene_context_payload.get("graph_template_id")
        or generation_options.get("graph_template_id")
        or "hkust_gz_gate"
    ).strip() or "hkust_gz_gate"
    inline_reference_annotation = scene_context_payload.get("reference_annotation")
    is_inline_reference_mode = (
        str(scene_context_payload.get("layout_mode") or "").strip().lower()
        == "reference_annotation"
        and isinstance(inline_reference_annotation, dict)
    )
    if is_inline_reference_mode:
        # The approved inline annotation is the source of truth. A selected
        # scenario may contribute parameter patches, but it must never replace
        # the user's OSM/GeoJSON geometry with a catalog annotation or graph
        # template that merely happens to share the same generation dialog.
        scenario_inputs = scenario_design_service.generation_inputs_for_scenario(
            scenario_id,
            graph_template_id=graph_template_id,
            validate=True,
        )
        scenario_summary = dict(
            scene_context_payload.get("scenario_design_variant")
            or scenario_inputs.get("scenario")
            or {}
        )
        scenario_compose_patch = sanitize_compose_config_patch(
            scenario_inputs.get("compose_config_patch") or {}
        )
        if not bool(generation_options.get("scenario_compose_patch_applied")):
            draft = replace(
                draft,
                compose_config_patch={
                    **sanitize_compose_config_patch(draft.compose_config_patch),
                    **scenario_compose_patch,
                },
                template_patch=None,
            )
        scene_context_payload.update({
            "layout_mode": "reference_annotation",
            "reference_annotation": dict(inline_reference_annotation),
            "reference_annotation_path": None,
            "template_patch": None,
            "scenario_id": scenario_id,
            "scenario_title": str(
                scene_context_payload.get("scenario_title")
                or scenario_summary.get("title_zh")
                or scenario_id
            ),
            "scenario_design_variant": scenario_summary,
        })
        generation_options["scenario_id"] = scenario_id
        generation_options["scenario_title"] = str(
            scene_context_payload.get("scenario_title") or scenario_id
        )
        return draft, sanitize_scene_context(scene_context_payload), patch_overrides, generation_options

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
    scenario_annotation_path = str(scenario_inputs.get("reference_annotation_path") or "").strip()
    if scenario_annotation_path:
        candidate = Path(scenario_annotation_path).expanduser()
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(ROOT)
        except ValueError as exc:
            raise ValueError("Scenario reference annotation escaped the trusted repository root.") from exc
        if candidate.suffix.lower() != ".json" or not candidate.is_file():
            raise ValueError("Scenario reference annotation is not a trusted JSON file.")
        if candidate.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Scenario reference annotation exceeds the 10 MiB limit.")
        annotation_payload = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(annotation_payload, dict):
            raise ValueError("Scenario reference annotation must contain a JSON object.")
        scenario_summary = dict(scenario_inputs.get("scenario") or {})
        scenario_compose_patch = sanitize_compose_config_patch(
            scenario_inputs.get("compose_config_patch") or {}
        )
        if not bool(generation_options.get("scenario_compose_patch_applied")):
            draft = replace(
                draft,
                compose_config_patch={
                    **sanitize_compose_config_patch(draft.compose_config_patch),
                    **scenario_compose_patch,
                },
                template_patch=None,
            )
        scene_context_payload.update({
            "layout_mode": "reference_annotation",
            "reference_annotation": annotation_payload,
            "reference_annotation_path": None,
            "template_patch": None,
            "scenario_id": scenario_id,
            "scenario_title": str(scenario_summary.get("title_zh") or scenario_id),
            "scenario_design_variant": scenario_summary,
            "source_context": {
                "source": {
                    "schema_version": "roadgen3d_scene_source_v1",
                    "source_id": scenario_id,
                    "kind": "reference_annotation",
                    "producer": "catalog",
                },
                "aligned_buildings": [],
                "source_alignment": {
                    "schema_version": "roadgen3d.source_alignment.v1",
                    "status": "n/a",
                    "reason": "catalog_annotation_has_no_geographic_alignment",
                },
            },
        })
        generation_options["scenario_id"] = scenario_id
        generation_options["scenario_title"] = str(scenario_summary.get("title_zh") or scenario_id)
        return draft, sanitize_scene_context(scene_context_payload), patch_overrides, generation_options
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
