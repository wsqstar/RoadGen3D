"""Design draft, generation, and matrix API routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from roadgen3d.json_safe import make_json_safe
from roadgen3d.llm import LLMConfigurationError, LLMResponseError
from roadgen3d.template_patch import TemplatePatchError
from roadgen3d.services.street_design_parameters import (
    ParameterSpecError,
    compile_street_design_parameter_spec,
    list_parameter_profiles,
    parameter_control_registry,
)
from roadgen3d.services.parameter_proposals import ParameterProposalError
from web.api.route_utils import dump_model, model_payload, prepare_scene_generation_request
from web.api.schemas import (
    DesignMatrixGenerateRequestModel,
    DesignMatrixInventoryRequestModel,
    DraftRequestModel,
    GenerateRequestModel,
    SceneJobCreateRequestModel,
    StreetDesignParameterCompileRequestModel,
    StreetDesignParameterProposalRequestModel,
)

router = APIRouter(tags=["design"])


@router.get("/api/design/parameter-profiles")
def design_parameter_profiles() -> Dict[str, Any]:
    return {
        "schema_version": "roadgen3d.street-design-parameter-registry.v1",
        "generation_mode": "parametric",
        "deprecated": True,
        "replacement": "/api/design/parameter-controls",
        "profiles": list_parameter_profiles(),
    }


@router.get("/api/design/parameter-controls")
def design_parameter_controls() -> Dict[str, Any]:
    return parameter_control_registry()


@router.post("/api/design/parameter-specs/compile")
def compile_design_parameter_spec(
    request_body: StreetDesignParameterCompileRequestModel,
) -> Dict[str, Any]:
    try:
        result = compile_street_design_parameter_spec(
            request_body.spec,
            field_sources=request_body.field_sources,
        )
    except ParameterSpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return make_json_safe(result.to_dict())


@router.post("/api/design/parameter-proposals")
def propose_design_parameters(
    request_body: StreetDesignParameterProposalRequestModel,
    request: Request,
) -> Dict[str, Any]:
    try:
        result = request.app.state.design_service.propose_street_design_parameters(
            current_spec=request_body.current_spec,
            design_goals=request_body.design_goals,
            structured_weaknesses=request_body.structured_weaknesses,
            scene_summary=request_body.scene_summary,
        )
    except (LLMConfigurationError, LLMResponseError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ParameterProposalError, ParameterSpecError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return make_json_safe(result)


@router.post("/api/design/draft")
def design_draft(request_body: DraftRequestModel, request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    try:
        result = service.draft_design(
            messages=[dump_model(item) for item in request_body.messages],
            user_input=request_body.user_input,
            current_patch=request_body.current_patch,
            topk=int(request_body.topk),
            knowledge_source=request_body.knowledge_source,
            force=request_body.force,
        )
    except (LLMConfigurationError, LLMResponseError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(result.to_dict())


@router.post("/api/design/generate")
def design_generate(request_body: GenerateRequestModel, request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    try:
        draft, scene_context, patch_overrides, generation_options = prepare_scene_generation_request(
            request_body,
            scenario_design_service=request.app.state.scenario_design_service,
        )
        result = service.generate_scene(
            draft=draft,
            scene_context=scene_context,
            patch_overrides=patch_overrides,
            generation_options=generation_options,
        )
    except (RuntimeError, ValueError, TemplatePatchError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe(result)


@router.post("/api/design/matrix/inventory")
def design_matrix_inventory(request_body: DesignMatrixInventoryRequestModel, request: Request) -> Dict[str, Any]:
    try:
        return make_json_safe(request.app.state.design_matrix_service.inventory(model_payload(request_body)))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/design/matrix/cells/generate")
def generate_design_matrix_cell(request_body: DesignMatrixGenerateRequestModel, request: Request) -> Dict[str, Any]:
    try:
        prepared = request.app.state.design_matrix_service.prepare_generate(model_payload(request_body))
        if prepared.get("mode") == "materialized":
            return make_json_safe(prepared)
        scene_job_request = SceneJobCreateRequestModel(**dict(prepared.get("scene_job_request") or {}))
        draft, scene_context, patch_overrides, generation_options = prepare_scene_generation_request(
            scene_job_request,
            scenario_design_service=request.app.state.scenario_design_service,
        )
        result = request.app.state.design_service.create_scene_job(
            draft=draft,
            scene_context=scene_context,
            patch_overrides=patch_overrides,
            generation_options=generation_options,
        )
    except (RuntimeError, ValueError, TemplatePatchError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = dict(prepared)
    payload.pop("scene_job_request", None)
    payload.update(result.to_dict())
    return make_json_safe(payload)
