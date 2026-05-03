"""Helpers for serving and linking to the local web viewer inside Gradio."""

from __future__ import annotations

import json
import mimetypes
import os
import shlex
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import quote

from .json_safe import make_json_safe

ROOT = Path(__file__).resolve().parents[2]
VIEWER_DIR = (ROOT / "web" / "viewer").resolve()
VIEWER_DIST_DIR = (VIEWER_DIR / "dist").resolve()
VIEWER_LAYOUTS_DIR = (ROOT / "artifacts" / "web_viewer_layouts").resolve()
RECENT_LAYOUT_LIMIT = 20
IGNORED_DISCOVERY_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "dist",
}


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
    resolved = Path(layout_path).expanduser().resolve()
    if resolved.is_dir():
        candidate = (resolved / "scene_layout.json").resolve()
        if candidate.exists():
            return candidate
    if not resolved.exists():
        raise WebViewerError(f"Scene layout does not exist: {resolved}")
    return resolved


def is_repo_local_path(path_text: str | Path) -> bool:
    try:
        resolve_repo_path(path_text)
        return True
    except WebViewerError:
        return False


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
    return {
        "spawn_point": [0.0, 1.65, 0.0],
        "forward_vector": [1.0, 0.0, 0.0],
    }


def _iter_scene_layout_paths(search_roots: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for root in search_roots:
        resolved_root = Path(root).expanduser().resolve()
        if not resolved_root.exists() or not resolved_root.is_dir():
            continue
        for current_root, dirnames, filenames in os.walk(resolved_root):
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if dirname not in IGNORED_DISCOVERY_DIRS
                and not dirname.startswith(".mypy_cache")
            ]
            if "scene_layout.json" not in filenames:
                continue
            candidate = (Path(current_root) / "scene_layout.json").resolve()
            if candidate in seen:
                continue
            seen.add(candidate)
            yield candidate


def _display_path_for(candidate: Path, roots: Iterable[Path]) -> str:
    for root in roots:
        resolved_root = Path(root).expanduser().resolve()
        try:
            return str(candidate.relative_to(resolved_root))
        except ValueError:
            continue
    return str(candidate.name)


def _recent_layout_entry(candidate: Path, roots: Iterable[Path]) -> Dict[str, Any]:
    stats = candidate.stat()
    updated_at = datetime.fromtimestamp(stats.st_mtime, tz=timezone.utc).astimezone()
    relative_path = _display_path_for(candidate, roots)
    label = f"{candidate.parent.name} · {relative_path}"
    return {
        "layout_path": str(candidate),
        "label": label,
        "relative_path": relative_path,
        "updated_at": updated_at.isoformat(timespec="seconds"),
        "mtime_ms": int(stats.st_mtime * 1000),
    }


def discover_recent_scene_layouts(
    search_roots: Iterable[str | Path] | None = None,
    *,
    limit: int = RECENT_LAYOUT_LIMIT,
) -> List[Dict[str, Any]]:
    roots = [
        Path(root).expanduser().resolve()
        for root in (search_roots or [ROOT])
    ]
    entries = []
    for candidate in _iter_scene_layout_paths(roots):
        try:
            entries.append(_recent_layout_entry(candidate, roots))
        except Exception:
            continue
    entries.sort(key=lambda item: int(item.get("mtime_ms", 0)), reverse=True)
    safe_limit = max(1, int(limit or RECENT_LAYOUT_LIMIT))
    return entries[:safe_limit]


def build_recent_layouts_payload(
    search_roots: Iterable[str | Path] | None = None,
    *,
    limit: int = RECENT_LAYOUT_LIMIT,
) -> Dict[str, Any]:
    return {
        "results": discover_recent_scene_layouts(search_roots, limit=limit),
    }


def _stable_layout_cache_dir(source_layout: Path) -> Path:
    source_id = source_layout.parent.name or source_layout.stem or "scene"
    source_hash = sha1(str(source_layout).encode("utf-8")).hexdigest()[:10]
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in source_id).strip("_") or "scene"
    return (VIEWER_LAYOUTS_DIR / f"{safe_id}_{source_hash}").resolve()


def cache_scene_layout_for_viewer(
    layout_path: str | Path,
    layout_json_text: str | None = None,
) -> Path:
    resolved_layout = resolve_scene_layout_path(layout_path)

    payload_text = str(layout_json_text or "").strip()
    if payload_text:
        try:
            payload = json.loads(payload_text)
        except Exception:
            payload = json.loads(resolved_layout.read_text(encoding="utf-8"))
    else:
        payload = json.loads(resolved_layout.read_text(encoding="utf-8"))

    try:
        resolved_layout.relative_to(VIEWER_LAYOUTS_DIR)
        cached_layout = resolved_layout
        cached_layout.parent.mkdir(parents=True, exist_ok=True)
    except ValueError:
        cache_dir = _stable_layout_cache_dir(resolved_layout)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_layout = (cache_dir / "scene_layout.json").resolve()
    cached_payload = json.dumps(make_json_safe(payload), indent=2, ensure_ascii=True, allow_nan=False)
    cached_layout.write_text(cached_payload, encoding="utf-8")
    return cached_layout


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
        "lighting_preset": outputs.get("lighting_preset", "bright_day"),
        "lighting_params": outputs.get("lighting_params"),
    }


def build_web_viewer_url(layout_path: str | Path) -> str:
    resolved_layout = resolve_scene_layout_path(layout_path)
    host = str(os.environ.get("ROADGEN_VIEWER_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.environ.get("ROADGEN_VIEWER_PORT") or 4173)
    except (TypeError, ValueError):
        port = 4173
    return f"http://{host}:{port}/?layout={quote(str(resolved_layout), safe='')}"


def build_web_viewer_dev_url(
    layout_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 4173,
) -> str:
    resolved_layout = resolve_scene_layout_path(layout_path)
    return f"http://{host}:{int(port)}/?layout={quote(str(resolved_layout), safe='')}"


def build_web_viewer_dev_command(
    layout_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 4173,
) -> str:
    resolved_layout = resolve_scene_layout_path(layout_path)
    allowed_roots = [str(ROOT.resolve()), str(resolved_layout.parent.resolve())]
    env_value = os.pathsep.join(dict.fromkeys(allowed_roots))
    open_target = f"/?layout={quote(str(resolved_layout), safe='')}"
    return " ".join(
        [
            f"ROADGEN_VIEWER_ALLOWED_ROOTS={shlex.quote(env_value)}",
            "npm",
            "--prefix",
            shlex.quote(str(VIEWER_DIR)),
            "run",
            "dev",
            "--",
            "--host",
            shlex.quote(str(host)),
            "--port",
            shlex.quote(str(int(port))),
            "--strictPort",
            "--open",
            shlex.quote(open_target),
        ]
    )
