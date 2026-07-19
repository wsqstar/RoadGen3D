"""Immutable bundled starter scenes for the professional workbench."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from roadgen3d.json_safe import make_json_safe
from roadgen3d.scene_layout_edits import scene_revision_for_layout

ROOT = Path(__file__).resolve().parents[3]
STARTER_ROOT = (ROOT / "assets" / "starter_scenes").resolve()
MATERIALIZED_ROOT = (ROOT / "artifacts" / "starter_scenes").resolve()
DEFAULT_STARTER_SCENE_ID = "guangzhou_complete_intersection_v6"
REGISTERED_STARTER_SCENE_IDS = frozenset({
    "guangzhou_road_skeleton_v1",
    "guangzhou_road_skeleton_v2",
    "guangzhou_complete_intersection_v3",
    "guangzhou_complete_intersection_v4",
    "guangzhou_complete_intersection_v5",
    "guangzhou_complete_intersection_v6",
})


class StarterSceneError(RuntimeError):
    pass


def _registered_dir(scene_id: str) -> Path:
    clean_id = str(scene_id or "").strip()
    if clean_id not in REGISTERED_STARTER_SCENE_IDS:
        raise StarterSceneError(f"Unknown starter scene: {clean_id}")
    directory = (STARTER_ROOT / clean_id).resolve()
    try:
        directory.relative_to(STARTER_ROOT)
    except ValueError as exc:
        raise StarterSceneError("Starter scene path escapes its registered root.") from exc
    return directory


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StarterSceneError(f"Starter scene file is unavailable or invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise StarterSceneError(f"Starter scene file must contain an object: {path.name}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_starter_scene(scene_id: str = DEFAULT_STARTER_SCENE_ID) -> dict[str, Any]:
    directory = _registered_dir(scene_id)
    package = _json(directory / "package.json")
    normalized = _json(directory / "normalized_source.json")
    scene_file = str(package.get("scene_file") or "road_base.glb")
    scene_path = directory / scene_file
    source_path = directory / "normalized_source.json"
    if not scene_path.is_file() or scene_path.stat().st_size <= 0:
        raise StarterSceneError(f"Starter scene asset is missing or empty: {scene_file}")
    source_fingerprint = _sha256(source_path)
    scene_fingerprint = _sha256(scene_path)
    expected_source = str(package.get("source_fingerprint") or "")
    expected_scene = str(package.get("scene_fingerprint") or "")
    if expected_source and expected_source != source_fingerprint:
        raise StarterSceneError("Starter source fingerprint does not match package.json.")
    if expected_scene and expected_scene != scene_fingerprint:
        raise StarterSceneError("Starter scene fingerprint does not match package.json.")
    return {
        "id": scene_id,
        "version": str(package.get("version") or "1"),
        "label": str(package.get("label") or "广州道路骨架"),
        "scene_file": scene_file,
        "source_fingerprint": source_fingerprint,
        "scene_fingerprint": scene_fingerprint,
        "retrieval_bbox": list(package.get("retrieval_bbox") or []),
        "focus_xz": list(package.get("focus_xz") or []),
        "focus_extent_m": float(package.get("focus_extent_m") or 0.0),
        "category_counts": dict(package.get("category_counts") or {}),
        "normalized_source": normalized,
        "viewer_manifest_url": f"/api/starter-scenes/{scene_id}/manifest",
    }


def starter_scene_manifest(scene_id: str) -> dict[str, Any]:
    directory = _registered_dir(scene_id)
    package = _json(directory / "package.json")
    scene_file = str(package.get("scene_file") or "road_base.glb")
    label = str(package.get("label") or "广州道路骨架")
    manifest = _json(directory / "viewer_manifest.json")
    manifest["layout_path"] = f"/api/starter-scenes/{scene_id}/manifest"
    manifest["final_scene"] = {
        "label": label,
        "glb_url": f"/api/starter-scenes/{scene_id}/files/{scene_file}",
    }
    is_complete_scene = scene_file == "complete_scene.glb"
    manifest["production_steps"] = [{
        "step_id": "complete_scene" if is_complete_scene else "road_base",
        "title": "Complete Intersection / 完整十字路口" if is_complete_scene else "Road Base / 道路骨架",
        "glb_url": f"/api/starter-scenes/{scene_id}/files/{scene_file}",
    }]
    manifest["default_selection"] = "final_scene"
    manifest["starter_focus"] = {
        "center_xz": list(package.get("focus_xz") or []),
        "extent_m": float(package.get("focus_extent_m") or 0.0),
    }
    return make_json_safe(manifest)


def starter_scene_file(scene_id: str, filename: str) -> Path:
    directory = _registered_dir(scene_id)
    allowed = {
        "road_base.glb",
        "complete_scene.glb",
        "osm_snapshot.json",
        "osm_snapshot.geojson",
        "normalized_source.json",
    }
    if filename not in allowed:
        raise StarterSceneError(f"Starter scene file is not public: {filename}")
    path = (directory / filename).resolve()
    if not path.is_file():
        raise StarterSceneError(f"Starter scene file not found: {filename}")
    return path


def materialize_starter_scene(scene_id: str) -> dict[str, Any]:
    package = load_starter_scene(scene_id)
    source_dir = _registered_dir(scene_id)
    materialization_fingerprint = hashlib.sha256(
        f"{package['source_fingerprint']}:{package['scene_fingerprint']}".encode("utf-8")
    ).hexdigest()
    target_dir = (MATERIALIZED_ROOT / scene_id / materialization_fingerprint[:16]).resolve()
    try:
        target_dir.relative_to(MATERIALIZED_ROOT)
    except ValueError as exc:
        raise StarterSceneError("Materialized starter scene escapes the artifacts root.") from exc
    target_dir.mkdir(parents=True, exist_ok=True)
    target_glb = target_dir / "scene.glb"
    target_layout = target_dir / "scene_layout.json"
    if not target_glb.is_file() or _sha256(target_glb) != package["scene_fingerprint"]:
        shutil.copyfile(source_dir / str(package.get("scene_file") or "road_base.glb"), target_glb)
    template = _json(source_dir / "scene_layout.json")
    outputs = dict(template.get("outputs") or {})
    outputs.update({"scene_glb": str(target_glb), "scene_layout": str(target_layout)})
    template["outputs"] = outputs
    steps = []
    for step in template.get("production_steps") or []:
        if isinstance(step, Mapping) and str(step.get("step_id") or "") in {"road_base", "complete_scene"}:
            steps.append({**dict(step), "glb_path": str(target_glb), "companion_path": ""})
    template["production_steps"] = steps
    template["scene_edit"] = {
        "schema_version": "roadgen3d.scene_edit.v1",
        "lineage_id": f"starter-{scene_id}-{package['scene_fingerprint'][:12]}",
        "revision": 0,
        "parent_revision": None,
        "starter_scene_id": scene_id,
    }
    encoded = json.dumps(make_json_safe(template), ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if not target_layout.is_file() or target_layout.read_bytes() != encoded:
        target_layout.write_bytes(encoded)
    return {
        **package,
        "materialization_fingerprint": materialization_fingerprint,
        "layout_path": str(target_layout),
        "scene_revision": scene_revision_for_layout(target_layout),
        "materialized": True,
    }


__all__ = [
    "DEFAULT_STARTER_SCENE_ID",
    "REGISTERED_STARTER_SCENE_IDS",
    "StarterSceneError",
    "load_starter_scene",
    "materialize_starter_scene",
    "starter_scene_file",
    "starter_scene_manifest",
]
