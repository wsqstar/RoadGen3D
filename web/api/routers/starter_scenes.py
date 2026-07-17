"""Bundled starter-scene routes for the professional workbench."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from roadgen3d.json_safe import make_json_safe
from roadgen3d.services.starter_scenes import (
    DEFAULT_STARTER_SCENE_ID,
    StarterSceneError,
    load_starter_scene,
    materialize_starter_scene,
    starter_scene_file,
    starter_scene_manifest,
)

router = APIRouter(prefix="/api/starter-scenes", tags=["starter-scenes"])


@router.get("/default")
def get_default_starter_scene():
    try:
        return make_json_safe(load_starter_scene(DEFAULT_STARTER_SCENE_ID))
    except StarterSceneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{scene_id}/manifest")
def get_starter_scene_manifest(scene_id: str):
    try:
        return starter_scene_manifest(scene_id)
    except StarterSceneError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{scene_id}/files/{filename}")
def get_starter_scene_file(scene_id: str, filename: str):
    try:
        path = starter_scene_file(scene_id, filename)
    except StarterSceneError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media_type = "model/gltf-binary" if path.suffix.lower() == ".glb" else "application/json"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.post("/{scene_id}/materialize")
def materialize_starter(scene_id: str):
    try:
        return make_json_safe(materialize_starter_scene(scene_id))
    except StarterSceneError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
