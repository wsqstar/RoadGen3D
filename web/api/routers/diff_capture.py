"""Layout rebuild, view capture, and scene diff API routes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from roadgen3d.capture_3d import capture_views_for_layout
from roadgen3d.diff_engine import compute_scene_diff
from roadgen3d.diff_render import render_delta_map, render_diff_overlay
from roadgen3d.json_safe import make_json_safe
from roadgen3d.street_layout import rebuild_glb_from_layout
from web.api.route_utils import resolve_layout_referenced_path
from web.api.schemas import CaptureViewsRequestModel, RebuildLayoutGlbRequestModel, SceneDiffRequestModel

ROOT = Path(__file__).resolve().parents[3]

router = APIRouter(tags=["diff-capture"])


def _existing_production_scene_preview(payload: Dict[str, Any], layout_path: Path) -> Path | None:
    """Find the authoritative full-scene preview recorded by the pipeline."""

    for expected_step_id in ("scene_preview", "complete_scene"):
        for step in payload.get("production_steps", []) or []:
            if str((step or {}).get("step_id", "") or "") != expected_step_id:
                continue
            candidate = resolve_layout_referenced_path(
                str((step or {}).get("glb_path", "") or ""), layout_path
            )
            if candidate is not None and candidate.exists():
                return candidate
    return None


@router.post("/api/design/rebuild-layout-glb")
def rebuild_layout_glb(request_body: RebuildLayoutGlbRequestModel) -> Dict[str, Any]:
    raw_layout_path = request_body.layout_path.strip()
    if not raw_layout_path:
        raise HTTPException(status_code=400, detail="layout_path is required")
    layout_path = Path(raw_layout_path).expanduser().resolve()
    if not layout_path.exists() or not layout_path.is_file():
        raise HTTPException(status_code=404, detail=f"Layout file not found: {layout_path}")

    manifest_path = (
        Path(request_body.manifest_path).expanduser().resolve()
        if request_body.manifest_path
        else (ROOT / "data" / "street_furniture" / "street_furniture_manifest.jsonl").resolve()
    )
    if not manifest_path.exists() or not manifest_path.is_file():
        raise HTTPException(status_code=404, detail=f"Asset manifest not found: {manifest_path}")

    try:
        payload = json.loads(layout_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid scene_layout.json: {exc}") from exc

    outputs = dict(payload.get("outputs", {}) or {})
    production_preview_path = _existing_production_scene_preview(payload, layout_path)
    if production_preview_path is not None and not request_body.force:
        # Never replace a full pipeline scene with the intentionally minimal
        # layout reconstruction.  This path used to make the viewer appear to
        # fall back to a tiny, 2D-inconsistent road fragment.
        return make_json_safe({
            "layout_path": str(layout_path),
            "scene_glb_path": str(production_preview_path),
            "manifest_path": str(manifest_path),
            "rebuilt": False,
            "scene_source": "production_scene_preview",
        })
    existing_glb_path = resolve_layout_referenced_path(str(outputs.get("scene_glb", "") or ""), layout_path)
    if existing_glb_path is not None and existing_glb_path.exists() and not request_body.force:
        return make_json_safe({
            "layout_path": str(layout_path),
            "scene_glb_path": str(existing_glb_path),
            "manifest_path": str(manifest_path),
            "rebuilt": False,
        })

    try:
        rebuild_outputs = rebuild_glb_from_layout(
            layout_path=layout_path,
            manifest_path=manifest_path,
            out_dir=layout_path.parent / "rebuild",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to rebuild GLB from layout: {exc}") from exc

    scene_glb_path = Path(str(rebuild_outputs.get("scene_glb", "") or "")).expanduser().resolve()
    if not scene_glb_path.exists():
        raise HTTPException(status_code=500, detail="GLB rebuild did not create scene_glb output")

    payload = json.loads(layout_path.read_text(encoding="utf-8"))
    outputs = dict(payload.get("outputs", {}) or {})
    outputs["scene_glb"] = str(scene_glb_path)
    outputs["scene_layout"] = str(layout_path)
    payload["outputs"] = outputs
    summary = dict(payload.get("summary", {}) or {})
    summary["scene_glb_rebuilt_from_layout"] = True
    summary["scene_glb_rebuilt_at"] = datetime.now(timezone.utc).isoformat()
    summary["scene_glb_rebuild_manifest_path"] = str(manifest_path)
    payload["summary"] = summary
    layout_path.write_text(
        json.dumps(make_json_safe(payload), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    return make_json_safe({
        "layout_path": str(layout_path),
        "scene_glb_path": str(scene_glb_path),
        "manifest_path": str(manifest_path),
        "rebuilt": True,
    })


@router.post("/api/design/capture-views")
def capture_design_views(request_body: CaptureViewsRequestModel) -> Dict[str, Any]:
    raw_layout_path = request_body.layout_path.strip()
    if not raw_layout_path:
        raise HTTPException(status_code=400, detail="layout_path is required")
    layout_path = Path(raw_layout_path).expanduser().resolve()
    if not layout_path.exists() or not layout_path.is_file():
        raise HTTPException(status_code=404, detail=f"Layout file not found: {layout_path}")

    manifest_path = (
        Path(request_body.manifest_path).expanduser().resolve()
        if request_body.manifest_path
        else (ROOT / "data" / "street_furniture" / "street_furniture_manifest.jsonl").resolve()
    )
    if not manifest_path.exists() or not manifest_path.is_file():
        raise HTTPException(status_code=404, detail=f"Asset manifest not found: {manifest_path}")

    try:
        capture_result = capture_views_for_layout(
            layout_path=layout_path,
            scene_glb_path=request_body.scene_glb_path,
            manifest_path=manifest_path,
            options={
                "capture_3d_views": request_body.capture_3d_views,
                "capture_profile": request_body.capture_profile,
                "capture_resolution": request_body.capture_resolution,
                "capture_failure_policy": request_body.capture_failure_policy,
                "retain_glb_policy": request_body.retain_glb_policy,
                "viewer_url": request_body.viewer_url,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to capture 3D views: {exc}") from exc

    return make_json_safe(capture_result.to_dict())


@router.post("/api/scenes/diff")
def scene_diff(request_body: SceneDiffRequestModel) -> Dict[str, Any]:
    layout_a = Path(request_body.layout_a).expanduser().resolve()
    layout_b = Path(request_body.layout_b).expanduser().resolve()
    if not layout_a.exists() or not layout_b.exists():
        raise HTTPException(status_code=404, detail="One or both layout files not found.")
    try:
        payload_a = json.loads(layout_a.read_text(encoding="utf-8"))
        payload_b = json.loads(layout_b.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse layout: {exc}") from exc
    return make_json_safe(compute_scene_diff(payload_a, payload_b))


@router.get("/api/scenes/diff/image")
def scene_diff_image(
    layout_a: str = Query(...),
    layout_b: str = Query(...),
    mode: str = Query(default="overlay"),
) -> FileResponse:
    layout_a_path = Path(layout_a).expanduser().resolve()
    layout_b_path = Path(layout_b).expanduser().resolve()
    if not layout_a_path.exists() or not layout_b_path.exists():
        raise HTTPException(status_code=404, detail="One or both layout files not found.")
    if mode not in ("overlay", "delta"):
        raise HTTPException(status_code=400, detail="Invalid mode. Use overlay or delta.")

    stat_a = layout_a_path.stat()
    stat_b = layout_b_path.stat()
    cache_key = sha256(
        f"{layout_a_path}:{stat_a.st_mtime}:{stat_a.st_size}|"
        f"{layout_b_path}:{stat_b.st_mtime}:{stat_b.st_size}|"
        f"{mode}".encode("utf-8")
    ).hexdigest()[:16]
    cache_dir = ROOT / "artifacts" / "diff_images"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{cache_key}_{mode}.png"

    if cache_path.exists():
        return FileResponse(cache_path, media_type="image/png")

    try:
        if mode == "overlay":
            render_diff_overlay(layout_a_path, layout_b_path, cache_path)
        else:
            render_delta_map(layout_a_path, layout_b_path, cache_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Diff rendering failed: {exc}") from exc

    if not cache_path.exists():
        raise HTTPException(status_code=500, detail="Diff rendering produced no output.")
    return FileResponse(cache_path, media_type="image/png")
