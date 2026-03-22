"""Helpers for serving and linking to the local web viewer inside Gradio."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
VIEWER_DIR = (ROOT / "web" / "viewer").resolve()
VIEWER_DIST_DIR = (VIEWER_DIR / "dist").resolve()


class WebViewerError(RuntimeError):
    """Raised when viewer assets or scene layout are unavailable."""


def _is_within_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def resolve_repo_path(path_text: str | Path) -> Path:
    resolved = Path(path_text).expanduser().resolve()
    if not _is_within_root(resolved):
        raise WebViewerError(f"Path must stay inside repo root: {resolved}")
    return resolved


def resolve_scene_layout_path(layout_path: str | Path) -> Path:
    resolved = resolve_repo_path(layout_path)
    if not resolved.exists():
        raise WebViewerError(f"Scene layout does not exist: {resolved}")
    return resolved


def ensure_web_viewer_assets() -> Path:
    if not VIEWER_DIST_DIR.exists():
        raise WebViewerError(
            "Web Viewer build is missing. Run: npm --prefix web/viewer install && npm --prefix web/viewer run build"
        )
    index_path = (VIEWER_DIST_DIR / "index.html").resolve()
    if not index_path.exists():
        raise WebViewerError(
            "Web Viewer build is incomplete. Run: npm --prefix web/viewer run build"
        )
    return index_path


def viewer_asset_path(relative_path: str) -> Path:
    normalized = str(relative_path or "").lstrip("/")
    candidate = (VIEWER_DIST_DIR / normalized).resolve()
    try:
        candidate.relative_to(VIEWER_DIST_DIR)
    except ValueError as exc:
        raise WebViewerError(f"Viewer asset must stay inside dist: {relative_path}") from exc
    if not candidate.exists():
        raise WebViewerError(f"Viewer asset not found: {candidate}")
    return candidate


def content_type_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed:
        return guessed
    if path.suffix.lower() == ".glb":
        return "model/gltf-binary"
    return "application/octet-stream"


def infer_spawn_payload(layout_payload: Dict[str, Any]) -> Dict[str, Any]:
    summary = layout_payload.get("summary", {}) or {}
    length_m = float(summary.get("length_m", 80.0) or 80.0)
    return {
        "spawn_point": [-(max(24.0, length_m) * 0.35), 1.65, 0.0],
        "forward_vector": [1.0, 0.0, 0.0],
    }


def build_layout_manifest(layout_path: str | Path) -> Dict[str, Any]:
    resolved_layout = resolve_scene_layout_path(layout_path)
    payload = json.loads(resolved_layout.read_text(encoding="utf-8"))
    outputs = payload.get("outputs", {}) or {}
    final_scene_path_raw = str(outputs.get("scene_glb", "") or "").strip()
    if not final_scene_path_raw:
        raise WebViewerError("scene_layout.json does not define outputs.scene_glb")
    final_scene_path = resolve_repo_path(final_scene_path_raw)
    if not final_scene_path.exists():
        raise WebViewerError(f"Final scene GLB does not exist: {final_scene_path}")
    production_steps = []
    for step in payload.get("production_steps", []) or []:
        glb_path_raw = str((step or {}).get("glb_path", "") or "").strip()
        if not glb_path_raw:
            continue
        try:
            glb_path = resolve_repo_path(glb_path_raw)
        except WebViewerError:
            continue
        if not glb_path.exists():
            continue
        production_steps.append(
            {
                "step_id": str(step.get("step_id", "") or ""),
                "title": str(step.get("title", "") or step.get("step_id", "Production Step")),
                "glb_url": f"./api/file?path={quote(str(glb_path), safe='')}",
            }
        )
    spawn_payload = infer_spawn_payload(payload)
    return {
        "layout_path": str(resolved_layout),
        "final_scene": {
            "label": "Final Scene",
            "glb_url": f"./api/file?path={quote(str(final_scene_path), safe='')}",
        },
        "production_steps": production_steps,
        "default_selection": "final_scene",
        "spawn_point": spawn_payload["spawn_point"],
        "forward_vector": spawn_payload["forward_vector"],
    }


def build_web_viewer_url(layout_path: str | Path) -> str:
    resolve_scene_layout_path(layout_path)
    ensure_web_viewer_assets()
    return f"/web-viewer/?layout={quote(str(Path(layout_path).expanduser().resolve()), safe='')}"
