"""Scenario design API routes."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from roadgen3d.json_safe import make_json_safe
from roadgen3d.semantic_scenario_edits import draft_semantic_scenario_variant
from roadgen3d.template_patch import TemplatePatchError
from web.api.schemas import (
    ScenarioDesignDraftVariantRequestModel,
    ScenarioDesignRunCreateRequestModel,
)

router = APIRouter(prefix="/api/scenario-designs", tags=["scenario-designs"])


def _build_semantic_scenario_edit_messages(
    *,
    prompt: str,
    graph_template_id: str,
    base_scenario_id: Optional[str],
    citations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    evidence = []
    for index, item in enumerate(citations[:6], start=1):
        evidence.append({
            "rank": index,
            "title": str(item.get("title") or item.get("source_id") or item.get("chunk_id") or ""),
            "text": str(item.get("text") or item.get("excerpt") or item.get("content") or "")[:900],
            "score": item.get("score"),
            "knowledge_source": str(item.get("knowledge_source") or ""),
        })
    schema = {
        "schema_version": "roadgen3d_semantic_scenario_edit_v1",
        "edits": [{
            "action": "add",
            "feature": "bus_stop | colored_pavement | bike_lane | bus_lane | safety_island | median_green | transit_pad",
            "road_selector": {"kind": "primary"},
            "longitudinal": {
                "anchor": "start | middle | end",
                "center_fraction": 0.5,
                "span_fraction": 0.12,
                "near": "entrance | school | junction | crossing",
            },
            "lateral": {
                "anchor": "center | median | left_curbside | right_curbside | left_sidewalk | right_sidewalk | lane_index_from_center:0",
                "width_m": 3.2,
            },
            "style": {"pavement_color": "green"},
        }],
    }
    return [
        {
            "role": "system",
            "content": (
                "You convert natural-language street design requests into strict "
                "roadgen3d_semantic_scenario_edit_v1 JSON. Return JSON only. "
                "Do not produce low-level template_patch operations, station meter values, "
                "or lateral meter spans. Use normalized positions and semantic anchors. "
                "If the user omits dimensions, omit the field so the deterministic compiler "
                "can apply defaults. Prefer center_fraction over exact meter positions."
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "prompt": prompt,
                "graph_template_id": graph_template_id,
                "base_scenario_id": base_scenario_id or "",
                "allowed_features": [
                    "bus_stop",
                    "colored_pavement",
                    "bike_lane",
                    "bus_lane",
                    "safety_island",
                    "median_green",
                    "transit_pad",
                ],
                "position_policy": {
                    "middle": {"center_fraction": 0.5},
                    "start_or_front": {"center_fraction": 0.15},
                    "end_or_back": {"center_fraction": 0.85},
                    "right_side": {"lateral_anchor": "right_curbside"},
                    "left_side": {"lateral_anchor": "left_curbside"},
                    "road_middle": {"lateral_anchor": "median"},
                },
                "rag_evidence": evidence,
                "required_output_shape": schema,
            }, ensure_ascii=False),
        },
    ]


@router.get("")
def list_scenario_designs(request: Request) -> Dict[str, Any]:
    try:
        return make_json_safe(request.app.state.scenario_design_service.list_scenarios())
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/draft-variant")
def draft_scenario_design_variant(
    request_body: ScenarioDesignDraftVariantRequestModel,
    request: Request,
) -> Dict[str, Any]:
    citations: List[Dict[str, Any]] = []
    prompt = str(request_body.prompt or "").strip()
    if prompt:
        try:
            citations = [
                item.to_dict() if hasattr(item, "to_dict") else dict(item)
                for item in request.app.state.design_service.search_knowledge(
                    query=prompt,
                    topk=4,
                    knowledge_source="scenario_parameters",
                )
            ]
        except Exception:
            citations = []
    semantic_payload = request_body.semantic_payload
    provided_semantic_payload = semantic_payload is not None
    llm_used = False
    fallback_reason = ""
    if request_body.use_llm and semantic_payload is None:
        try:
            llm = request.app.state.design_service._get_llm_client()
            semantic_payload = llm.chat_json(
                _build_semantic_scenario_edit_messages(
                    prompt=prompt,
                    graph_template_id=request_body.graph_template_id,
                    base_scenario_id=request_body.base_scenario_id,
                    citations=citations,
                ),
                temperature=0.1,
            )
            draft_semantic_scenario_variant(
                prompt=prompt,
                graph_template_id=request_body.graph_template_id,
                semantic_payload=semantic_payload,
                citations=citations,
            )
            llm_used = True
        except Exception as exc:
            semantic_payload = None
            fallback_reason = f"{type(exc).__name__}: {exc}"
    try:
        payload = draft_semantic_scenario_variant(
            prompt=prompt,
            graph_template_id=request_body.graph_template_id,
            semantic_payload=semantic_payload,
            citations=citations,
        )
        payload["llm_requested"] = bool(request_body.use_llm)
        payload["llm_used"] = bool(llm_used)
        payload["fallback_reason"] = "" if llm_used else (
            fallback_reason
            or ("semantic_payload provided" if provided_semantic_payload else ("use_llm=false" if not request_body.use_llm else "deterministic parser fallback"))
        )
        payload["semantic_parse_method"] = "llm" if llm_used else ("provided_semantic_payload" if provided_semantic_payload else "deterministic")
        return make_json_safe(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TemplatePatchError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{scenario_id}/reference-annotation")
def get_scenario_design_reference_annotation(
    scenario_id: str,
    request: Request,
    graph_template_id: str = Query(default="hkust_gz_gate"),
) -> Dict[str, Any]:
    try:
        payload = request.app.state.scenario_design_service.reference_annotation_for_scenario(
            scenario_id,
            graph_template_id=graph_template_id,
        )
        return make_json_safe(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs")
def create_scenario_design_run(
    request_body: ScenarioDesignRunCreateRequestModel,
    request: Request,
) -> Dict[str, Any]:
    try:
        return make_json_safe(request.app.state.scenario_design_service.submit_run(
            scenario_ids=request_body.scenario_ids,
            samples_per_scenario=request_body.samples_per_scenario,
            base_seed=request_body.base_seed,
            graph_template_id=request_body.graph_template_id,
            generation_options=request_body.generation_options,
        ))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
def get_scenario_design_run(run_id: str, request: Request) -> Dict[str, Any]:
    result = request.app.state.scenario_design_service.get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Scenario design run not found: {run_id}")
    return make_json_safe(result)


@router.get("/runs/{run_id}/report")
def get_scenario_design_run_report(run_id: str, request: Request) -> Dict[str, Any]:
    result = request.app.state.scenario_design_service.get_report(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Scenario design run not found: {run_id}")
    return make_json_safe(result)

