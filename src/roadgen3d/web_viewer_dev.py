"""Helpers for serving and linking to the local web viewer inside Gradio."""

from __future__ import annotations

import json
import mimetypes
import os
import shlex
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence
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
    scene_graph = layout_payload.get("scene_graph") or {}
    nodes = scene_graph.get("nodes") if isinstance(scene_graph, Mapping) else None
    road_nodes: list[tuple[float, float]] = []
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            if str(node.get("node_type") or "").strip().lower() != "road_segment":
                continue
            x = _as_number(node.get("x"))
            z = _as_number(node.get("z"))
            if x is None or z is None:
                continue
            road_nodes.append((x, z))
            if len(road_nodes) >= 2:
                break

    if road_nodes:
        spawn_x, spawn_z = road_nodes[0]
        forward_x, forward_z = 1.0, 0.0
        if len(road_nodes) > 1:
            delta_x = road_nodes[1][0] - spawn_x
            delta_z = road_nodes[1][1] - spawn_z
            length = (delta_x * delta_x + delta_z * delta_z) ** 0.5
            if length > 1e-6:
                forward_x = delta_x / length
                forward_z = delta_z / length
        return {
            "spawn_point": [round(spawn_x, 6), 1.65, round(spawn_z, 6)],
            "forward_vector": [round(forward_x, 6), 0.0, round(forward_z, 6)],
        }

    return {
        "spawn_point": [0.0, 1.65, 0.0],
        "forward_vector": [1.0, 0.0, 0.0],
    }


def _as_number(value: Any, fallback: float | None = None) -> float | None:
    if isinstance(value, bool):
        return fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number and abs(number) != float("inf") else fallback


def _as_triplet(value: Any) -> List[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    triplet = [_as_number(item) for item in value[:3]]
    if any(item is None for item in triplet):
        return None
    return [float(item) for item in triplet if item is not None]


def _as_pair(value: Any) -> List[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    pair = [_as_number(item) for item in value[:2]]
    if any(item is None for item in pair):
        return None
    return [float(item) for item in pair if item is not None]


def _as_quad(value: Any) -> List[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    quad = [_as_number(item) for item in value[:4]]
    if any(item is None for item in quad):
        return None
    return [float(item) for item in quad if item is not None]


def _as_bbox_xz(value: Any, position_xyz: List[float] | None = None) -> List[float] | None:
    """Normalize bbox_xz to [min_x, max_x, min_z, max_z].

    Older payloads have appeared in both [min_x, max_x, min_z, max_z] and
    [min_x, min_z, max_x, max_z] order. Prefer the ordering whose center is
    closest to the instance position, falling back to the smaller footprint.
    """
    quad = _as_quad(value)
    if not quad:
        return None
    a, b, c, d = quad
    current_order = [min(a, b), max(a, b), min(c, d), max(c, d)]
    legacy_order = [min(a, c), max(a, c), min(b, d), max(b, d)]
    if position_xyz:
        px, pz = position_xyz[0], position_xyz[2]
        current_distance = abs((current_order[0] + current_order[1]) * 0.5 - px) + abs(
            (current_order[2] + current_order[3]) * 0.5 - pz
        )
        legacy_distance = abs((legacy_order[0] + legacy_order[1]) * 0.5 - px) + abs(
            (legacy_order[2] + legacy_order[3]) * 0.5 - pz
        )
        return legacy_order if legacy_distance + 0.01 < current_distance else current_order
    current_area = max(0.0, current_order[1] - current_order[0]) * max(0.0, current_order[3] - current_order[2])
    legacy_area = max(0.0, legacy_order[1] - legacy_order[0]) * max(0.0, legacy_order[3] - legacy_order[2])
    return legacy_order if legacy_area + 1e-6 < current_area else current_order


def _build_instance_payloads(layout_payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    instances: Dict[str, Dict[str, Any]] = {}
    placements = layout_payload.get("placements", [])
    if not isinstance(placements, list):
        return instances
    for placement in placements:
        if not isinstance(placement, dict):
            continue
        instance_id = str(placement.get("instance_id", "") or "").strip()
        if not instance_id:
            continue
        instances[instance_id] = make_json_safe(
            {
                "instance_id": instance_id,
                "asset_id": str(placement.get("asset_id", "") or "").strip(),
                "category": str(placement.get("category", "") or "").strip(),
                "placement_group": str(placement.get("placement_group", "") or "").strip(),
                "theme_id": str(placement.get("theme_id", "") or "").strip(),
                "selection_source": str(placement.get("selection_source", "") or "").strip(),
                "position_xyz": _as_triplet(placement.get("position_xyz")),
                "bbox_xz": _as_quad(placement.get("bbox_xz")),
                "anchor_poi_type": str(placement.get("anchor_poi_type", "") or "").strip(),
                "anchor_target_xz": _as_pair(placement.get("anchor_target_xz")),
                "anchor_distance_m": _as_number(placement.get("anchor_distance_m")),
                "feasibility_score": _as_number(placement.get("feasibility_score")),
                "constraint_penalty": _as_number(placement.get("constraint_penalty")),
                "dist_to_road_edge_m": _as_number(placement.get("dist_to_road_edge_m")),
                "dist_to_nearest_junction_m": _as_number(placement.get("dist_to_nearest_junction_m")),
                "dist_to_nearest_entrance_m": _as_number(placement.get("dist_to_nearest_entrance_m")),
                "violated_rules": [
                    str(item).strip()
                    for item in (placement.get("violated_rules") or [])
                    if item is not None and str(item).strip()
                ],
            }
        )
    return instances


def _build_scene_bounds(layout_payload: Dict[str, Any]) -> Dict[str, Any]:
    placements = layout_payload.get("placements", [])
    summary = layout_payload.get("summary", {}) if isinstance(layout_payload.get("summary"), dict) else {}
    spatial_context = summary.get("spatial_context", {}) if isinstance(summary.get("spatial_context"), dict) else {}
    min_x = float("inf")
    max_x = float("-inf")
    min_z = float("inf")
    max_z = float("-inf")
    max_y = 0.0

    def include_xz(x: float, z: float, padding: float = 0.0) -> None:
        nonlocal min_x, max_x, min_z, max_z
        min_x = min(min_x, x - padding)
        max_x = max(max_x, x + padding)
        min_z = min(min_z, z - padding)
        max_z = max(max_z, z + padding)

    if isinstance(placements, list):
        for placement in placements:
            if not isinstance(placement, dict):
                continue
            position = _as_triplet(placement.get("position_xyz"))
            bbox = _as_bbox_xz(placement.get("bbox_xz"), position)
            if bbox:
                min_x = min(min_x, bbox[0])
                max_x = max(max_x, bbox[1])
                min_z = min(min_z, bbox[2])
                max_z = max(max_z, bbox[3])
            else:
                if position:
                    include_xz(position[0], position[2], 0.75)
                    max_y = max(max_y, position[1])
            scale = _as_triplet(placement.get("scale_xyz"))
            if scale:
                max_y = max(max_y, scale[1])

    road_half_width = max(3.0, _as_number(spatial_context.get("road_half_width_m"), 6.0) or 6.0)
    length_m = max(
        24.0,
        _as_number(spatial_context.get("length_m"), _as_number(summary.get("length_m"), 80.0)) or 80.0,
    )
    if not all(value not in {float("inf"), float("-inf")} for value in (min_x, max_x, min_z, max_z)):
        min_x = -length_m * 0.5
        max_x = length_m * 0.5
        min_z = -road_half_width * 3.5
        max_z = road_half_width * 3.5

    size_x = max(1.0, max_x - min_x)
    size_z = max(1.0, max_z - min_z)
    size_y = max(12.0, max_y + 10.0)
    road_axis = [1, 0, 0] if size_x >= size_z else [0, 0, 1]
    return make_json_safe(
        {
            "center": [(min_x + max_x) * 0.5, size_y * 0.5, (min_z + max_z) * 0.5],
            "size": [size_x, size_y, size_z],
            "road_axis": road_axis,
        }
    )


def _list_field(layout_payload: Dict[str, Any], key: str) -> List[Any]:
    value = layout_payload.get(key, [])
    return list(value) if isinstance(value, list) else []


def _build_road_centerlines(layout_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build compact road centerlines for direction-aware viewer overlays."""
    scene_graph = layout_payload.get("scene_graph", {})
    if not isinstance(scene_graph, dict):
        return []

    segment_road_ids: Dict[str, Any] = {}
    for profile in _list_field(layout_payload, "segment_semantic_profiles"):
        if not isinstance(profile, dict):
            continue
        segment_id = str(profile.get("segment_id") or "").strip()
        if segment_id:
            segment_road_ids[segment_id] = profile.get("road_id")

    groups: Dict[str, Dict[str, Any]] = {}
    for node in scene_graph.get("nodes", []) or []:
        if not isinstance(node, dict) or str(node.get("node_type") or "") != "road_segment":
            continue
        x = _as_number(node.get("x"))
        z = _as_number(node.get("z"))
        if x is None or z is None:
            continue
        segment_id = str(node.get("segment_id") or "").strip()
        road_id = node.get("road_id")
        if road_id in (None, "") and segment_id:
            road_id = segment_road_ids.get(segment_id)
        group_key = str(road_id if road_id not in (None, "") else node.get("centerline_id") or segment_id).strip()
        if not group_key:
            continue
        group = groups.setdefault(group_key, {"road_id": road_id if road_id not in (None, "") else group_key, "points": []})
        group["points"].append([float(x), float(z)])

    result: List[Dict[str, Any]] = []
    for group in groups.values():
        points = list(group.get("points", []))
        if len(points) < 2:
            continue
        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        min_z = min(point[1] for point in points)
        max_z = max(point[1] for point in points)
        if (max_x - min_x) >= (max_z - min_z):
            points.sort(key=lambda point: (point[0], point[1]))
        else:
            points.sort(key=lambda point: (point[1], point[0]))

        deduped: List[List[float]] = []
        for point in points:
            if deduped and abs(deduped[-1][0] - point[0]) < 0.05 and abs(deduped[-1][1] - point[1]) < 0.05:
                continue
            deduped.append([round(point[0], 3), round(point[1], 3)])
        if len(deduped) >= 2:
            result.append({"road_id": group.get("road_id"), "points_xz": deduped})

    def sort_key(item: Dict[str, Any]) -> tuple[int, float, str]:
        road_id = item.get("road_id")
        try:
            return (0, float(road_id), str(road_id))
        except (TypeError, ValueError):
            return (1, 0.0, str(road_id))

    result.sort(key=sort_key)
    return result


def _build_layout_overlay(layout_payload: Dict[str, Any]) -> Dict[str, Any]:
    street_program = layout_payload.get("street_program", {})
    if not isinstance(street_program, dict):
        street_program = {}
    config = layout_payload.get("config", {})
    if not isinstance(config, dict):
        config = {}
    bands = street_program.get("bands", [])
    return make_json_safe(
        {
            "bands": list(bands) if isinstance(bands, list) else [],
            "road_centerlines": _build_road_centerlines(layout_payload),
            "building_footprints": _list_field(layout_payload, "building_footprints"),
            "generated_lots": _list_field(layout_payload, "generated_lots"),
            "building_regions": _list_field(layout_payload, "building_regions"),
            "regions": _list_field(layout_payload, "regions"),
            "derived_regions": _list_field(layout_payload, "derived_regions"),
            "functional_zones": _list_field(layout_payload, "functional_zones"),
            "surface_annotations": _list_field(layout_payload, "surface_annotations"),
            "length_m": _as_number(config.get("length_m"), 0.0) or 0.0,
            "lane_count": _as_number(street_program.get("lane_count"), 1.0) or 1.0,
            "road_width_m": _as_number(street_program.get("road_width_m"), 0.0) or 0.0,
        }
    )


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _build_comparison_metadata(layout_payload: Dict[str, Any], production_steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = layout_payload.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    config = layout_payload.get("config", {})
    if not isinstance(config, dict):
        config = {}
    scenario_variant = summary.get("scenario_design_variant", {})
    if not isinstance(scenario_variant, dict):
        scenario_variant = {}
    semantic_profile_parts = _clean_string(summary.get("semantic_profile_pair")).split("+")

    return make_json_safe(
        {
            "preset_id": _clean_string(summary.get("preset_id") or summary.get("benchmark_preset_id")),
            "preset_label": _clean_string(summary.get("preset_label") or summary.get("preset_name")),
            "scenario_id": _clean_string(summary.get("scenario_id") or scenario_variant.get("scenario_id")),
            "scenario_title": _clean_string(summary.get("scenario_title") or scenario_variant.get("title_zh")),
            "graph_template_id": _clean_string(
                summary.get("graph_template_id") or summary.get("base_graph_template_id") or summary.get("plan_id")
            ),
            "skeleton_design_profile": _clean_string(
                summary.get("skeleton_design_profile")
                or (semantic_profile_parts[0] if len(semantic_profile_parts) > 0 else "")
            ),
            "street_furniture_profile": _clean_string(
                summary.get("street_furniture_profile")
                or (semantic_profile_parts[1] if len(semantic_profile_parts) > 1 else "")
            ),
            "curated_street_assets_profile": _clean_string(summary.get("curated_street_assets_profile")),
            "furniture_balance_policy": _clean_string(summary.get("furniture_balance_policy")),
            "prompt": _clean_string(config.get("query") or layout_payload.get("query") or summary.get("query")),
            "variant_id": _clean_string(summary.get("design_variant_id") or summary.get("variant_id")),
            "variant_name": _clean_string(summary.get("design_variant_name") or summary.get("variant_name")),
            "random_seed": _as_number(summary.get("random_seed"), _as_number(config.get("seed"))),
            "density": _as_number(config.get("density"), _as_number(summary.get("density"))),
            "road_width_m": _as_number(config.get("road_width_m"), _as_number(summary.get("road_width_m"))),
            "lane_count": _as_number(config.get("lane_count"), _as_number(summary.get("lane_count"))),
            "style_preset": _clean_string(config.get("style_preset") or summary.get("style_preset") or summary.get("visual_style_preset")),
            "instance_count": _as_number(summary.get("instance_count")),
            "production_step_ids": [str(step.get("step_id", "") or "") for step in production_steps if step.get("step_id")],
        }
    )


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


def build_layout_manifest_payload(
    payload: Mapping[str, Any],
    *,
    final_scene: Mapping[str, Any],
    production_steps: Sequence[Mapping[str, Any]] = (),
    layout_identity: str | None = None,
) -> Dict[str, Any]:
    """Build the canonical Viewer manifest without assuming local paths."""

    outputs = payload.get("outputs", {}) or {}
    manifest = {
        "summary": make_json_safe(payload.get("summary") or {}),
        "visual_style": make_json_safe(payload.get("visual_style") or {}),
        "final_scene": make_json_safe(dict(final_scene)),
        "production_steps": make_json_safe([dict(item) for item in production_steps]),
        "default_selection": "final_scene",
        "spawn_point": infer_spawn_payload(payload)["spawn_point"],
        "forward_vector": infer_spawn_payload(payload)["forward_vector"],
        "scene_bounds": _build_scene_bounds(payload),
        "instances": _build_instance_payloads(payload),
        "layout_overlay": _build_layout_overlay(payload),
        "comparison_metadata": _build_comparison_metadata(payload, list(production_steps)),
        "lighting_preset": outputs.get("lighting_preset", "bright_day"),
        "lighting_params": outputs.get("lighting_params"),
        "environment_state": payload.get("environment_state") or outputs.get("environment_state"),
    }
    if layout_identity:
        manifest["layout_path"] = layout_identity
    return manifest


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
    return build_layout_manifest_payload(
        payload,
        layout_identity=str(resolved_layout),
        final_scene={
            "label": "Final Scene",
            "glb_url": f"./api/file?path={quote(str(final_scene_path), safe='')}",
        },
        production_steps=production_steps,
    )


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
