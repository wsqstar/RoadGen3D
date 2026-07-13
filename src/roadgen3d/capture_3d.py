"""Backend 3D view capture planning and artifact patching."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .json_safe import make_json_safe


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAPTURE_PROFILE = "review_expanded"
DEFAULT_CAPTURE_RESOLUTION = (1280, 720)
CAPTURE_MANIFEST_VERSION = "capture_manifest_v1"
VALID_CAPTURE_PROFILES = frozenset({"quick_12", "review_24", "review_expanded", "exhaustive_keypoints"})
VALID_CAPTURE_FAILURE_POLICIES = frozenset({"warn", "fail"})
VALID_RETAIN_GLB_POLICIES = frozenset({"top_k", "always", "debug_only"})
CAPTURE_PROFILE_BUDGETS = {
    "quick_12": 12,
    "review_24": 24,
    "review_expanded": 40,
    "exhaustive_keypoints": 10_000,
}
CAPTURE_PROFILE_KIND_QUOTAS = {
    "quick_12": (
        ("pedestrian", 1),
        ("overview", 2),
        ("junction_pedestrian", 2),
        ("junction", 2),
        ("street", 2),
        ("bench_eye", 1),
        ("window_view", 1),
        ("rooftop", 1),
    ),
    "review_24": (
        ("pedestrian", 1),
        ("overview", 2),
        ("junction_pedestrian", 4),
        ("junction", 4),
        ("street", 3),
        ("bench_eye", 2),
        ("window_view", 3),
        ("rooftop", 2),
        ("building", 3),
    ),
    "review_expanded": (
        ("pedestrian", 1),
        ("overview", 2),
        ("junction_pedestrian", 8),
        ("junction", 6),
        ("street", 5),
        ("bench_eye", 4),
        ("window_view", 6),
        ("rooftop", 4),
        ("building", 4),
    ),
}


@dataclass(frozen=True)
class Capture3DOptions:
    """Normalized options for backend 3D capture."""

    capture_3d_views: bool = True
    capture_profile: str = DEFAULT_CAPTURE_PROFILE
    capture_resolution: Tuple[int, int] = DEFAULT_CAPTURE_RESOLUTION
    capture_failure_policy: str = "warn"
    retain_glb_policy: str = "top_k"
    capture_timeout_s: float = 180.0
    viewer_url: str = ""
    debug: bool = False
    capture_defer_glb_retention: bool = False
    create_contact_sheet: bool = False


@dataclass(frozen=True)
class Capture3DResult:
    """Result of a capture run."""

    status: str
    layout_path: str
    capture_manifest_path: str = ""
    scene_glb_path: str = ""
    view_count: int = 0
    views: List[Dict[str, Any]] = field(default_factory=list)
    skipped_targets: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    glb_deleted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return dict(make_json_safe(asdict(self)))


def normalize_capture_options(overrides: Mapping[str, Any] | None = None) -> Capture3DOptions:
    """Coerce request or generation option payload into capture options."""

    payload = dict(overrides or {})

    def _bool(name: str, default: bool) -> bool:
        value = payload.get(name, default)
        if value in (None, ""):
            return bool(default)
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)

    def _str_choice(name: str, default: str, choices: frozenset[str]) -> str:
        value = str(payload.get(name, default) or default).strip().lower()
        if value not in choices:
            raise ValueError(f"{name} must be one of: {', '.join(sorted(choices))}")
        return value

    def _resolution() -> Tuple[int, int]:
        value = payload.get("capture_resolution", DEFAULT_CAPTURE_RESOLUTION)
        if isinstance(value, str):
            parts = value.lower().replace("x", ",").split(",")
        elif isinstance(value, Sequence):
            parts = list(value)
        else:
            parts = list(DEFAULT_CAPTURE_RESOLUTION)
        if len(parts) < 2:
            return DEFAULT_CAPTURE_RESOLUTION
        try:
            width = max(64, min(4096, int(float(parts[0]))))
            height = max(64, min(4096, int(float(parts[1]))))
        except (TypeError, ValueError):
            return DEFAULT_CAPTURE_RESOLUTION
        return (width, height)

    try:
        timeout_s = float(payload.get("capture_timeout_s", 180.0) or 180.0)
    except (TypeError, ValueError):
        timeout_s = 180.0

    return Capture3DOptions(
        capture_3d_views=_bool("capture_3d_views", True),
        capture_profile=_str_choice("capture_profile", DEFAULT_CAPTURE_PROFILE, VALID_CAPTURE_PROFILES),
        capture_resolution=_resolution(),
        capture_failure_policy=_str_choice("capture_failure_policy", "warn", VALID_CAPTURE_FAILURE_POLICIES),
        retain_glb_policy=_str_choice("retain_glb_policy", "top_k", VALID_RETAIN_GLB_POLICIES),
        capture_timeout_s=max(5.0, timeout_s),
        viewer_url=str(payload.get("viewer_url", payload.get("capture_viewer_url", "")) or "").strip(),
        debug=_bool("debug", False),
        capture_defer_glb_retention=_bool("capture_defer_glb_retention", False),
        create_contact_sheet=_bool("create_contact_sheet", False),
    )


def plan_capture_targets(
    layout_payload: Mapping[str, Any],
    *,
    profile: str = DEFAULT_CAPTURE_PROFILE,
) -> Dict[str, Any]:
    """Build deterministic camera targets for a scene layout."""

    profile_key = str(profile or DEFAULT_CAPTURE_PROFILE).strip().lower()
    if profile_key not in VALID_CAPTURE_PROFILES:
        raise ValueError(f"capture profile must be one of: {', '.join(sorted(VALID_CAPTURE_PROFILES))}")
    budget = CAPTURE_PROFILE_BUDGETS[profile_key]

    bounds = _layout_bounds(layout_payload)
    center_x, center_z = bounds["center_xz"]
    min_x, max_x, min_z, max_z = bounds["bbox_xz"]
    extent = max(bounds["extent"], 20.0)
    axis_is_x = _bounds_axis_is_x(bounds)
    road_half_width = max(2.0, _float_at_path(layout_payload, ("summary", "spatial_context", "road_half_width_m"), 4.0))

    candidates: List[Dict[str, Any]] = []
    seq = 0

    def add_target(
        target_id: str,
        kind: str,
        label: str,
        camera: Sequence[float],
        target: Sequence[float],
        *,
        priority: int,
        fov: float = 58.0,
        source: str = "",
    ) -> None:
        nonlocal seq
        seq += 1
        candidates.append({
            "target_id": _stable_id(target_id),
            "kind": str(kind),
            "label": str(label),
            "camera": [_round_float(value) for value in camera],
            "target": [_round_float(value) for value in target],
            "priority": int(priority),
            "fov": float(fov),
            "source": str(source),
            "_sequence": seq,
        })

    overview_height = max(28.0, extent * 0.95)
    add_target(
        "overview_top",
        "overview",
        "Overview top",
        (center_x, overview_height, center_z + 0.01),
        (center_x, 0.0, center_z),
        priority=82,
        fov=52.0,
        source="global",
    )
    add_target(
        "overview_oblique_45",
        "overview",
        "Overview 45 degree",
        (center_x - extent * 0.72, max(16.0, extent * 0.42), center_z - extent * 0.72),
        (center_x, 2.0, center_z),
        priority=86,
        fov=54.0,
        source="global",
    )
    add_target(
        "side_left",
        "side",
        "Left side elevation",
        (center_x, max(10.0, extent * 0.28), min_z - extent * 0.48),
        (center_x, 2.0, center_z),
        priority=62,
        fov=56.0,
        source="global",
    )
    add_target(
        "side_right",
        "side",
        "Right side elevation",
        (center_x, max(10.0, extent * 0.28), max_z + extent * 0.48),
        (center_x, 2.0, center_z),
        priority=61,
        fov=56.0,
        source="global",
    )

    entrances = _spatial_points(layout_payload, "entrance_points_xz")
    entrance_points = entrances[:]
    if not entrance_points:
        entrance_points = [(min_x, center_z)]
    for idx, (x, z) in enumerate(entrance_points[: (1 if profile_key != "exhaustive_keypoints" else len(entrance_points))]):
        forward_sign = 1.0 if x <= center_x else -1.0
        add_target(
            f"entrance_{idx + 1}",
            "pedestrian",
            f"Entrance street view {idx + 1}",
            (x - forward_sign * 8.0, 1.62, z - road_half_width - 1.5),
            (x + forward_sign * 10.0, 1.48, z),
            priority=96 - idx,
            fov=66.0,
            source="entrance",
        )

    junction_points = _spatial_points(layout_payload, "junction_points_xz")
    for idx, (x, z) in enumerate(junction_points):
        add_target(
            f"junction_{idx + 1}",
            "junction",
            f"Junction {idx + 1}",
            (x - 12.0, 8.5, z - 12.0),
            (x, 1.0, z),
            priority=82 - min(idx, 20),
            fov=60.0,
            source="junction",
        )

    pedestrian_junction_limits = {"quick_12": 2, "review_24": 4, "review_expanded": 8}
    pedestrian_junction_limit = (
        len(junction_points)
        if profile_key == "exhaustive_keypoints"
        else pedestrian_junction_limits.get(profile_key, 4)
    )
    sampled_pedestrian_junctions = _spatially_diverse_points(junction_points, limit=pedestrian_junction_limit)
    for idx, (x, z) in enumerate(sampled_pedestrian_junctions):
        corner_sign = -1.0 if idx % 2 == 0 else 1.0
        if axis_is_x:
            camera = (x - 5.0, 1.62, z + corner_sign * (road_half_width + 1.1))
            target = (x + 7.0, 1.42, z - corner_sign * min(road_half_width * 0.4, 2.2))
        else:
            camera = (x + corner_sign * (road_half_width + 1.1), 1.62, z - 5.0)
            target = (x - corner_sign * min(road_half_width * 0.4, 2.2), 1.42, z + 7.0)
        add_target(
            f"junction_pedestrian_{idx + 1}",
            "junction_pedestrian",
            f"Pedestrian junction view {idx + 1}",
            camera,
            target,
            priority=92 - min(idx, 20),
            fov=68.0,
            source="junction_pedestrian",
        )

    street_samples = 2 if profile_key == "quick_12" else 5
    if profile_key == "exhaustive_keypoints":
        street_samples = 8
    for idx, (x, z, tx, tz) in enumerate(_street_sample_points(bounds, street_samples, road_half_width)):
        add_target(
            f"street_{idx + 1}",
            "street",
            f"Street eye view {idx + 1}",
            (x, 1.62, z),
            (tx, 1.45, tz),
            priority=74 - idx,
            fov=68.0,
            source="street_sample",
        )

    bench_targets = _bench_targets(layout_payload)
    bench_limits = {"quick_12": 1, "review_24": 2, "review_expanded": 4}
    sampled_benches = _spatially_diverse_records(
        bench_targets,
        limit=len(bench_targets) if profile_key == "exhaustive_keypoints" else bench_limits.get(profile_key, 2),
    )
    for idx, item in enumerate(sampled_benches):
        x, z = item["center_xz"]
        if axis_is_x:
            away_sign = 1.0 if z >= center_z else -1.0
            along_sign = 1.0 if x <= center_x else -1.0
            camera = (x, 1.35, z + away_sign * 0.75)
            target = (x + along_sign * 8.0, 1.32, center_z)
        else:
            away_sign = 1.0 if x >= center_x else -1.0
            along_sign = 1.0 if z <= center_z else -1.0
            camera = (x + away_sign * 0.75, 1.35, z)
            target = (center_x, 1.32, z + along_sign * 8.0)
        add_target(
            f"bench_eye_{item['id']}",
            "bench_eye",
            f"Bench eye view {idx + 1}",
            camera,
            target,
            priority=76 - min(idx, 20),
            fov=66.0,
            source="bench",
        )

    building_targets = _building_targets(layout_payload)
    building_limit = len(building_targets) if profile_key == "exhaustive_keypoints" else max(0, budget - 10)
    sampled_buildings = _spatially_diverse_records(building_targets, limit=building_limit)
    for idx, item in enumerate(sampled_buildings):
        x, z = item["center_xz"]
        side = str(item.get("side") or ("right" if z >= center_z else "left")).lower()
        side_sign = 1.0 if side == "right" or z >= center_z else -1.0
        height = max(4.0, min(18.0, float(item.get("height_m", 8.0) or 8.0)))
        add_target(
            f"building_{item['id']}",
            "building",
            f"Building {idx + 1}",
            (x - 8.0, max(3.2, height * 0.58), z + side_sign * 15.0),
            (x, max(1.8, height * 0.38), z),
            priority=58 - min(idx, 25),
            fov=54.0,
            source=str(item.get("source") or "building"),
        )

    building_view_limits = {"quick_12": 2, "review_24": 5, "review_expanded": 10}
    sampled_building_views = _spatially_diverse_records(
        building_targets,
        limit=len(building_targets) if profile_key == "exhaustive_keypoints" else building_view_limits.get(profile_key, 5),
    )
    for idx, item in enumerate(sampled_building_views):
        x, z = item["center_xz"]
        height = max(6.0, float(item.get("height_m", 10.0) or 10.0))
        bbox = _coerce_bbox_xz(item.get("bbox_xz"))
        if axis_is_x:
            along_sign = 1.0 if x <= center_x else -1.0
            road_target = (x + along_sign * 10.0, 1.4, center_z)
            roof_target = (x + along_sign * 12.0, 0.3, center_z)
            if bbox is not None:
                front_z = bbox[2] if z >= center_z else bbox[3]
            else:
                front_z = z
            toward_road = -1.0 if z >= center_z else 1.0
            window_camera = (x, min(max(3.0, height * 0.45), height - 0.6), front_z + toward_road * 0.35)
        else:
            along_sign = 1.0 if z <= center_z else -1.0
            road_target = (center_x, 1.4, z + along_sign * 10.0)
            roof_target = (center_x, 0.3, z + along_sign * 12.0)
            if bbox is not None:
                front_x = bbox[0] if x >= center_x else bbox[1]
            else:
                front_x = x
            toward_road = -1.0 if x >= center_x else 1.0
            window_camera = (front_x + toward_road * 0.35, min(max(3.0, height * 0.45), height - 0.6), z)
        add_target(
            f"window_view_{item['id']}",
            "window_view",
            f"Window street view {idx + 1}",
            window_camera,
            road_target,
            priority=68 - min(idx, 20),
            fov=60.0,
            source="building_window",
        )
        add_target(
            f"rooftop_{item['id']}",
            "rooftop",
            f"Rooftop view {idx + 1}",
            (x, height + 2.4, z),
            roof_target,
            priority=66 - min(idx, 20),
            fov=58.0,
            source="building_rooftop",
        )

    selected, skipped = _select_targets(candidates, budget, profile=profile_key)
    for target in selected:
        target.pop("_sequence", None)
    for target in skipped:
        target.pop("_sequence", None)
    return {
        "profile": profile_key,
        "budget": budget if budget < 10_000 else None,
        "targets": selected,
        "skipped_targets": skipped,
    }


def capture_views_for_layout(
    *,
    layout_path: str | Path,
    scene_glb_path: str | Path | None = None,
    options: Capture3DOptions | Mapping[str, Any] | None = None,
    manifest_path: str | Path | None = None,
) -> Capture3DResult:
    """Capture a gallery for a scene layout and patch the layout payload."""

    capture_options = options if isinstance(options, Capture3DOptions) else normalize_capture_options(options)
    layout = Path(layout_path).expanduser().resolve()
    if not layout.exists():
        raise FileNotFoundError(f"Layout file not found: {layout}")
    if not capture_options.capture_3d_views:
        return Capture3DResult(status="skipped", layout_path=str(layout))

    try:
        payload = json.loads(layout.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid scene_layout.json: {exc}") from exc

    view_dir = (layout.parent / "view_captures").resolve()
    view_dir.mkdir(parents=True, exist_ok=True)
    _clear_previous_capture_artifacts(view_dir)
    target_plan = plan_capture_targets(payload, profile=capture_options.capture_profile)
    targets = list(target_plan.get("targets", []) or [])
    skipped_targets = list(target_plan.get("skipped_targets", []) or [])

    glb_path = _resolve_scene_glb_path(
        layout,
        payload,
        scene_glb_path=scene_glb_path,
        manifest_path=manifest_path,
    )
    _patch_layout_scene_glb(layout, glb_path)

    manifest_file = (view_dir / "capture_manifest.json").resolve()
    target_file = (view_dir / "capture_targets.json").resolve()
    target_file.write_text(
        json.dumps(make_json_safe({"targets": targets}), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    try:
        node_payload = _run_playwright_capture(
            layout_path=layout,
            scene_glb_path=glb_path,
            out_dir=view_dir,
            target_file=target_file,
            width=capture_options.capture_resolution[0],
            height=capture_options.capture_resolution[1],
            timeout_s=capture_options.capture_timeout_s,
            viewer_url=capture_options.viewer_url,
        )
        raw_views = list(node_payload.get("views", []) or [])
        views = _normalize_captured_views(
            raw_views,
            targets=targets,
            resolution=capture_options.capture_resolution,
        )
        status = "succeeded"
        error = ""
    except Exception as exc:
        views = []
        status = "failed"
        error = str(exc)

    manifest = _build_capture_manifest(
        status=status,
        layout_path=layout,
        scene_glb_path=glb_path,
        manifest_path=manifest_file,
        options=capture_options,
        views=views,
        skipped_targets=skipped_targets,
        error=error,
    )
    manifest_file.write_text(json.dumps(make_json_safe(manifest), ensure_ascii=True, indent=2), encoding="utf-8")

    retain_glb = should_retain_scene_glb(capture_options, capture_status=status)
    glb_deleted = False
    final_glb_path = str(glb_path)
    if status == "succeeded" and not retain_glb:
        glb_deleted = _safe_delete_generated_glb(glb_path, layout.parent)
        if glb_deleted:
            final_glb_path = ""

    patch_layout_with_capture_manifest(
        layout,
        manifest,
        scene_glb_path=final_glb_path,
    )

    result = Capture3DResult(
        status=status,
        layout_path=str(layout),
        capture_manifest_path=str(manifest_file),
        scene_glb_path=final_glb_path,
        view_count=len(views),
        views=views,
        skipped_targets=skipped_targets,
        error=error,
        glb_deleted=glb_deleted,
    )
    if status == "failed" and capture_options.capture_failure_policy == "fail":
        raise RuntimeError(error or "3D capture failed")
    return result


def patch_layout_with_capture_manifest(
    layout_path: str | Path,
    capture_manifest: Mapping[str, Any],
    *,
    scene_glb_path: str | Path | None = None,
) -> None:
    """Write capture outputs back into scene_layout.json."""

    layout = Path(layout_path).expanduser().resolve()
    payload = json.loads(layout.read_text(encoding="utf-8"))
    summary = dict(payload.get("summary", {}) or {})
    outputs = dict(payload.get("outputs", {}) or {})
    manifest_path = str(capture_manifest.get("manifest_path", "") or capture_manifest.get("capture_manifest_path", "") or "")
    views = list(capture_manifest.get("views", []) or [])

    summary["render_views_3d"] = views
    summary["capture_3d"] = {
        "status": str(capture_manifest.get("status", "")),
        "profile": str(capture_manifest.get("profile", "")),
        "resolution": list(capture_manifest.get("resolution", []) or []),
        "view_count": int(capture_manifest.get("view_count", len(views)) or 0),
        "manifest_path": manifest_path,
        "error": str(capture_manifest.get("error", "") or ""),
    }
    if capture_manifest.get("skipped_targets"):
        summary["capture_3d"]["skipped_target_count"] = len(list(capture_manifest.get("skipped_targets", []) or []))
    if manifest_path:
        outputs["capture_manifest"] = manifest_path
    if scene_glb_path is not None:
        outputs["scene_glb"] = str(scene_glb_path)
    payload["summary"] = summary
    payload["outputs"] = outputs
    layout.write_text(json.dumps(make_json_safe(payload), ensure_ascii=True, indent=2), encoding="utf-8")


def should_retain_scene_glb(options: Capture3DOptions, *, capture_status: str = "succeeded") -> bool:
    """Return whether capture should leave the generated GLB in place."""

    if str(capture_status) != "succeeded":
        return True
    if options.capture_defer_glb_retention:
        return True
    if options.retain_glb_policy == "always":
        return True
    if options.retain_glb_policy == "debug_only" and options.debug:
        return True
    return False


def layout_capture_failed(layout_path: str | Path) -> bool:
    """Check whether a layout records a failed 3D capture."""

    path = Path(str(layout_path or "")).expanduser()
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    capture = dict((payload.get("summary", {}) or {}).get("capture_3d", {}) or {})
    return str(capture.get("status", "") or "").lower() == "failed"


def capture_view_paths(layout_path: str | Path) -> List[Path]:
    """Return capture artifact paths referenced by a scene layout."""

    path = Path(str(layout_path or "")).expanduser()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    summary = dict(payload.get("summary", {}) or {})
    outputs = dict(payload.get("outputs", {}) or {})
    paths: List[Path] = []
    manifest = str(outputs.get("capture_manifest", "") or "").strip()
    if manifest:
        paths.append(Path(manifest).expanduser())
    for view in list(summary.get("render_views_3d", []) or []):
        image_path = str(view.get("path", "") or view.get("image_path", "") or "").strip()
        if image_path:
            paths.append(Path(image_path).expanduser())
    if paths:
        paths.append(path.parent / "view_captures")
    return paths


def _clear_previous_capture_artifacts(view_dir: Path) -> None:
    for pattern in ("*.png", "*.jpg", "*.jpeg"):
        for image_path in view_dir.glob(pattern):
            if image_path.is_file():
                image_path.unlink()


def _run_playwright_capture(
    *,
    layout_path: Path,
    scene_glb_path: Path,
    out_dir: Path,
    target_file: Path,
    width: int,
    height: int,
    timeout_s: float,
    viewer_url: str = "",
) -> Dict[str, Any]:
    script = ROOT / "web" / "viewer" / "scripts" / "capture-gallery.mjs"
    if not script.exists():
        raise FileNotFoundError(f"Capture script not found: {script}")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for 3D capture but was not found on PATH")
    if not (ROOT / "web" / "viewer" / "node_modules" / "playwright").exists():
        raise RuntimeError("Playwright is not installed for web/viewer; skipping backend 3D capture")
    command = [
        node,
        str(script),
        "--layout",
        str(layout_path),
        "--glb",
        str(scene_glb_path),
        "--targets",
        str(target_file),
        "--out",
        str(out_dir),
        "--width",
        str(width),
        "--height",
        str(height),
    ]
    if viewer_url:
        command.extend(["--viewer-url", viewer_url])
    completed = subprocess.run(
        command,
        cwd=str(ROOT / "web" / "viewer"),
        capture_output=True,
        text=True,
        timeout=max(5.0, timeout_s),
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(f"Playwright capture failed: {detail}")
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Capture script returned invalid JSON: {completed.stdout[:500]}") from exc


def _resolve_scene_glb_path(
    layout_path: Path,
    layout_payload: Mapping[str, Any],
    *,
    scene_glb_path: str | Path | None,
    manifest_path: str | Path | None,
) -> Path:
    explicit = _resolve_layout_referenced_path(scene_glb_path, layout_path)
    if explicit is not None and explicit.exists():
        return explicit
    outputs = dict(layout_payload.get("outputs", {}) or {})
    referenced = _resolve_layout_referenced_path(outputs.get("scene_glb"), layout_path)
    if referenced is not None and referenced.exists():
        return referenced
    from .street_layout import rebuild_glb_from_layout

    resolved_manifest = (
        Path(manifest_path).expanduser().resolve()
        if manifest_path
        else (ROOT / "data" / "street_furniture" / "street_furniture_manifest.jsonl").resolve()
    )
    rebuild_outputs = rebuild_glb_from_layout(
        layout_path=layout_path,
        manifest_path=resolved_manifest,
        out_dir=layout_path.parent / "capture_rebuild",
    )
    rebuilt = Path(str(rebuild_outputs.get("scene_glb", "") or "")).expanduser().resolve()
    if not rebuilt.exists():
        raise RuntimeError("Temporary GLB rebuild did not create a scene_glb output")
    return rebuilt


def _patch_layout_scene_glb(layout_path: Path, scene_glb_path: Path) -> None:
    payload = json.loads(layout_path.read_text(encoding="utf-8"))
    outputs = dict(payload.get("outputs", {}) or {})
    outputs["scene_glb"] = str(scene_glb_path)
    outputs["scene_layout"] = str(layout_path)
    payload["outputs"] = outputs
    layout_path.write_text(json.dumps(make_json_safe(payload), ensure_ascii=True, indent=2), encoding="utf-8")


def _resolve_layout_referenced_path(value: object, layout_path: Path) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = layout_path.parent / candidate
    return candidate.resolve()


def _build_capture_manifest(
    *,
    status: str,
    layout_path: Path,
    scene_glb_path: Path,
    manifest_path: Path,
    options: Capture3DOptions,
    views: Sequence[Mapping[str, Any]],
    skipped_targets: Sequence[Mapping[str, Any]],
    error: str,
) -> Dict[str, Any]:
    return {
        "version": CAPTURE_MANIFEST_VERSION,
        "status": str(status),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "layout_path": str(layout_path),
        "scene_glb_path": str(scene_glb_path),
        "manifest_path": str(manifest_path),
        "profile": options.capture_profile,
        "resolution": list(options.capture_resolution),
        "view_count": len(list(views)),
        "views": list(views),
        "skipped_targets": list(skipped_targets),
        "error": str(error or ""),
    }


def _normalize_captured_views(
    raw_views: Sequence[Mapping[str, Any]],
    *,
    targets: Sequence[Mapping[str, Any]],
    resolution: Tuple[int, int],
) -> List[Dict[str, Any]]:
    target_by_id = {str(item.get("target_id") or ""): item for item in targets}
    normalized: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_views):
        target_id = str(raw.get("target_id") or raw.get("view_id") or f"view_{index + 1}")
        target = dict(target_by_id.get(target_id, {}) or {})
        path = str(raw.get("path", "") or raw.get("image_path", "") or "").strip()
        if not path:
            continue
        normalized.append({
            "view_id": target_id,
            "name": target_id,
            "label": str(target.get("label") or raw.get("label") or target_id),
            "kind": str(target.get("kind") or raw.get("kind") or "view"),
            "path": path,
            "camera": list(target.get("camera", raw.get("camera", [])) or []),
            "target": list(target.get("target", raw.get("target", [])) or []),
            "priority": int(target.get("priority", raw.get("priority", 0)) or 0),
            "width": int(raw.get("width", resolution[0]) or resolution[0]),
            "height": int(raw.get("height", resolution[1]) or resolution[1]),
            "source": str(target.get("source") or raw.get("source") or "roadgen3d_capture_3d"),
            "projection": str(target.get("projection") or raw.get("projection") or "perspective"),
            "vertical_fov_deg": _float_or_none(target.get("fov", raw.get("fov"))),
            "content_origin": "roadgen3d_synthetic_render",
        })
    return normalized


def _safe_delete_generated_glb(path: Path, root: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
        root_resolved = root.expanduser().resolve()
    except Exception:
        return False
    if resolved == root_resolved or root_resolved not in resolved.parents:
        return False
    if not resolved.exists() or not resolved.is_file() or resolved.suffix.lower() != ".glb":
        return False
    resolved.unlink()
    return True


def _layout_bounds(layout_payload: Mapping[str, Any]) -> Dict[str, Any]:
    xs: List[float] = []
    zs: List[float] = []

    def add_xz(x: Any, z: Any) -> None:
        try:
            fx = float(x)
            fz = float(z)
        except (TypeError, ValueError):
            return
        if not (fx == fx and fz == fz):
            return
        xs.append(fx)
        zs.append(fz)

    for point in _spatial_points(layout_payload, "junction_points_xz"):
        add_xz(point[0], point[1])
    for point in _spatial_points(layout_payload, "entrance_points_xz"):
        add_xz(point[0], point[1])
    for placement in _records(layout_payload.get("placements")):
        position = placement.get("position_xyz") or ()
        if isinstance(position, Sequence) and len(position) >= 3:
            add_xz(position[0], position[2])
        bbox = placement.get("bbox_xz") or ()
        if isinstance(bbox, Sequence) and len(bbox) >= 4:
            add_xz(bbox[0], bbox[2])
            add_xz(bbox[1], bbox[3])
    for item in _records(layout_payload.get("building_footprints")) + _records(layout_payload.get("generated_lots")):
        for point in _records(item.get("polygon_xz")):
            if isinstance(point, Sequence) and len(point) >= 2:
                add_xz(point[0], point[1])
        center = item.get("centroid_xz") or item.get("center_xz") or item.get("placement_xz")
        if isinstance(center, Sequence) and len(center) >= 2:
            add_xz(center[0], center[1])

    length = _float_at_path(layout_payload, ("config", "length_m"), 80.0)
    road_half = _float_at_path(layout_payload, ("summary", "spatial_context", "road_half_width_m"), 4.0)
    if not xs or not zs:
        xs.extend([-length / 2.0, length / 2.0])
        zs.extend([-road_half, road_half])
    min_x = min(xs)
    max_x = max(xs)
    min_z = min(zs)
    max_z = max(zs)
    if max_x - min_x < 1.0:
        min_x -= length / 2.0
        max_x += length / 2.0
    if max_z - min_z < 1.0:
        min_z -= max(road_half, 4.0)
        max_z += max(road_half, 4.0)
    center_x = (min_x + max_x) / 2.0
    center_z = (min_z + max_z) / 2.0
    extent = max(max_x - min_x, max_z - min_z, 20.0)
    return {
        "bbox_xz": (min_x, max_x, min_z, max_z),
        "center_xz": (center_x, center_z),
        "extent": extent,
    }


def _spatial_points(layout_payload: Mapping[str, Any], field_name: str) -> List[Tuple[float, float]]:
    spatial = dict((layout_payload.get("summary", {}) or {}).get("spatial_context", {}) or {})
    points: List[Tuple[float, float]] = []
    for point in list(spatial.get(field_name, []) or []):
        if not isinstance(point, Sequence) or len(point) < 2:
            continue
        try:
            points.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            continue
    return sorted(points, key=lambda item: (round(item[0], 6), round(item[1], 6)))


def _street_sample_points(
    bounds: Mapping[str, Any],
    count: int,
    road_half_width: float,
) -> List[Tuple[float, float, float, float]]:
    min_x, max_x, min_z, max_z = bounds["bbox_xz"]
    center_x, center_z = bounds["center_xz"]
    axis_is_x = (max_x - min_x) >= (max_z - min_z)
    samples: List[Tuple[float, float, float, float]] = []
    safe_count = max(1, int(count))
    for idx in range(safe_count):
        unit = (idx + 1) / float(safe_count + 1)
        if axis_is_x:
            x = min_x + (max_x - min_x) * unit
            z = center_z - road_half_width * 0.35
            tx = min(max_x, x + max(8.0, (max_x - min_x) * 0.16))
            tz = center_z
        else:
            z = min_z + (max_z - min_z) * unit
            x = center_x - road_half_width * 0.35
            tx = center_x
            tz = min(max_z, z + max(8.0, (max_z - min_z) * 0.16))
        samples.append((x, z, tx, tz))
    return samples


def _bounds_axis_is_x(bounds: Mapping[str, Any]) -> bool:
    min_x, max_x, min_z, max_z = bounds["bbox_xz"]
    return (max_x - min_x) >= (max_z - min_z)


def _bench_targets(layout_payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(_records(layout_payload.get("placements"))):
        category = str(item.get("category", "") or "").strip().lower()
        asset_id = str(item.get("asset_id", "") or "").strip().lower()
        if category != "bench" and "bench" not in asset_id:
            continue
        point = _coerce_xyz_to_xz(item.get("position_xyz")) or _bbox_center(item.get("bbox_xz"))
        if point is None:
            continue
        result.append({
            "id": str(item.get("instance_id") or item.get("slot_id") or f"bench_{index + 1}"),
            "center_xz": point,
            "bbox_xz": _coerce_bbox_xz(item.get("bbox_xz")),
            "yaw_deg": _float_or_none(item.get("yaw_deg")),
            "source": "placement",
        })
    return sorted(result, key=lambda item: (round(item["center_xz"][0], 6), round(item["center_xz"][1], 6), item["id"]))


def _building_targets(layout_payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    footprints = _records(layout_payload.get("building_footprints"))
    region_footprints = [
        item for item in footprints
        if str(item.get("source", "") or "").strip().lower() in {"building_region", "annotation", "reference_annotation"}
    ]
    source_records = region_footprints or footprints
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(source_records):
        center = item.get("centroid_xz") or item.get("placement_xz") or item.get("center_xz")
        point = _coerce_xz(center)
        if point is None:
            point = _polygon_center(item.get("polygon_xz"))
        if point is None:
            continue
        result.append({
            "id": str(item.get("footprint_id") or item.get("lot_id") or f"footprint_{index + 1}"),
            "center_xz": point,
            "side": str(item.get("side", "") or ""),
            "height_m": _record_height_m(item, fallback=8.0),
            "bbox_xz": _polygon_bbox(item.get("polygon_xz")),
            "street_edge_xz": _coerce_xz(item.get("street_edge_xz")),
            "source": str(item.get("source", "building_footprint") or "building_footprint"),
        })
    if result:
        return sorted(result, key=lambda item: (round(item["center_xz"][0], 6), round(item["center_xz"][1], 6), item["id"]))

    for index, item in enumerate(_records(layout_payload.get("building_placements"))):
        point = _coerce_xyz_to_xz(item.get("position_xyz")) or _bbox_center(item.get("bbox_xz"))
        if point is None:
            continue
        result.append({
            "id": str(item.get("instance_id") or item.get("footprint_id") or f"building_{index + 1}"),
            "center_xz": point,
            "side": str(item.get("side", "") or ""),
            "height_m": _record_height_m(item, fallback=8.0),
            "bbox_xz": _coerce_bbox_xz(item.get("bbox_xz")),
            "street_edge_xz": _coerce_xz(item.get("street_edge_xz")),
            "source": "building_placement",
        })
    if result:
        return sorted(result, key=lambda item: (round(item["center_xz"][0], 6), round(item["center_xz"][1], 6), item["id"]))

    for index, item in enumerate(_records(layout_payload.get("generated_lots"))):
        point = _coerce_xz(item.get("placement_xz")) or _coerce_xz(item.get("center_xz")) or _polygon_center(item.get("polygon_xz"))
        if point is None:
            continue
        result.append({
            "id": str(item.get("lot_id") or f"lot_{index + 1}"),
            "center_xz": point,
            "side": str(item.get("side", "") or ""),
            "height_m": _record_height_m(item, fallback=8.0),
            "bbox_xz": _polygon_bbox(item.get("polygon_xz")),
            "street_edge_xz": _coerce_xz(item.get("street_edge_xz")),
            "source": str(item.get("source", "generated_lot") or "generated_lot"),
        })
    return sorted(result, key=lambda item: (round(item["center_xz"][0], 6), round(item["center_xz"][1], 6), item["id"]))


def _spatially_diverse_records(records: Sequence[Mapping[str, Any]], *, limit: int) -> List[Mapping[str, Any]]:
    if limit <= 0:
        return []
    if len(records) <= limit:
        return list(records)
    selected: List[Mapping[str, Any]] = []
    remaining = list(records)
    selected.append(remaining.pop(0))
    while remaining and len(selected) < limit:
        def score(item: Mapping[str, Any]) -> Tuple[float, str]:
            x, z = item["center_xz"]
            min_dist = min(
                (x - other["center_xz"][0]) ** 2 + (z - other["center_xz"][1]) ** 2
                for other in selected
            )
            return (float(min_dist), str(item.get("id", "")))

        best = max(remaining, key=score)
        remaining.remove(best)
        selected.append(best)
    return sorted(selected, key=lambda item: (round(item["center_xz"][0], 6), round(item["center_xz"][1], 6), str(item.get("id", ""))))


def _spatially_diverse_points(points: Sequence[Tuple[float, float]], *, limit: int) -> List[Tuple[float, float]]:
    if limit <= 0:
        return []
    records = [
        {"id": f"point_{index + 1}", "center_xz": point}
        for index, point in enumerate(points)
    ]
    return [item["center_xz"] for item in _spatially_diverse_records(records, limit=limit)]


def _select_targets(
    candidates: Sequence[Mapping[str, Any]],
    budget: int,
    *,
    profile: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    ordered = sorted(
        [dict(item) for item in candidates],
        key=lambda item: (-int(item.get("priority", 0)), int(item.get("_sequence", 0)), str(item.get("target_id", ""))),
    )
    selected: List[Dict[str, Any]] = []
    selected_ids: set[str] = set()
    profile_key = str(profile or "").strip().lower()
    for kind, quota in CAPTURE_PROFILE_KIND_QUOTAS.get(profile_key, ()):
        for item in ordered:
            if len([entry for entry in selected if entry.get("kind") == kind]) >= quota:
                break
            target_id = str(item.get("target_id") or "")
            if target_id in selected_ids or str(item.get("kind") or "") != kind:
                continue
            selected.append(item)
            selected_ids.add(target_id)
    for item in ordered:
        if len(selected) >= max(1, int(budget)):
            break
        target_id = str(item.get("target_id") or "")
        if target_id in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(target_id)
    skipped = [item for item in ordered if str(item.get("target_id") or "") not in selected_ids]
    return selected, skipped


def _records(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _coerce_xz(value: Any) -> Tuple[float, float] | None:
    if not isinstance(value, Sequence) or len(value) < 2:
        return None
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return None


def _coerce_xyz_to_xz(value: Any) -> Tuple[float, float] | None:
    if not isinstance(value, Sequence) or len(value) < 3:
        return None
    try:
        return (float(value[0]), float(value[2]))
    except (TypeError, ValueError):
        return None


def _coerce_bbox_xz(value: Any) -> Tuple[float, float, float, float] | None:
    if not isinstance(value, Sequence) or len(value) < 4:
        return None
    try:
        min_x = float(value[0])
        max_x = float(value[1])
        min_z = float(value[2])
        max_z = float(value[3])
    except (TypeError, ValueError):
        return None
    if not all(component == component for component in (min_x, max_x, min_z, max_z)):
        return None
    return (min(min_x, max_x), max(min_x, max_x), min(min_z, max_z), max(min_z, max_z))


def _polygon_center(value: Any) -> Tuple[float, float] | None:
    points = [_coerce_xz(point) for point in _records(value)]
    valid = [point for point in points if point is not None]
    if not valid:
        return None
    return (
        sum(point[0] for point in valid) / len(valid),
        sum(point[1] for point in valid) / len(valid),
    )


def _polygon_bbox(value: Any) -> Tuple[float, float, float, float] | None:
    points = [_coerce_xz(point) for point in _records(value)]
    valid = [point for point in points if point is not None]
    if not valid:
        return None
    xs = [point[0] for point in valid]
    zs = [point[1] for point in valid]
    return (min(xs), max(xs), min(zs), max(zs))


def _bbox_center(value: Any) -> Tuple[float, float] | None:
    if not isinstance(value, Sequence) or len(value) < 4:
        return None
    try:
        return ((float(value[0]) + float(value[1])) / 2.0, (float(value[2]) + float(value[3])) / 2.0)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _record_height_m(record: Mapping[str, Any], *, fallback: float) -> float:
    for key in ("target_height_m", "height_m"):
        value = _float_or_none(record.get(key))
        if value is not None and value > 0:
            return value
    for key in ("final_size_m", "native_size_m", "raw_size_m", "canonical_target"):
        nested = record.get(key)
        if not isinstance(nested, Mapping):
            continue
        value = _float_or_none(nested.get("height_m"))
        if value is not None and value > 0:
            return value
    return float(fallback)


def _float_at_path(payload: Mapping[str, Any], keys: Iterable[str], fallback: float) -> float:
    current: Any = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return fallback
        current = current.get(key)
    try:
        return float(current)
    except (TypeError, ValueError):
        return fallback


def _round_float(value: Any) -> float:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return 0.0


def _stable_id(value: str) -> str:
    text = str(value or "view").strip().lower()
    safe = "".join(ch if ch.isalnum() else "_" for ch in text)
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_") or "view"
