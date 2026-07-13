"""Immutable placement-edit API for generated scene layouts."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from roadgen3d.json_safe import make_json_safe
from roadgen3d.scene_layout_edits import SceneLayoutEditError, apply_scene_layout_edits
from web.api.schemas import SceneLayoutEditRequestModel

router = APIRouter(prefix="/api/design", tags=["scene-layout-edits"])


@router.post("/scene-layout-edits")
def edit_scene_layout(request: SceneLayoutEditRequestModel) -> Dict[str, Any]:
    commands = [
        item.model_dump() if hasattr(item, "model_dump") else item.dict()
        for item in request.commands
    ]
    try:
        result = apply_scene_layout_edits(
            layout_path=request.layout_path,
            base_revision=request.base.revision,
            base_sha256=request.base.sha256,
            commands=commands,
        )
    except SceneLayoutEditError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc
    return make_json_safe(result)
