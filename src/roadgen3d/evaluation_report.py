"""Batch reporting for layered RoadGen3D generation quality."""

from __future__ import annotations

import csv
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


QUALITY_LAYERS = (
    "layout_semantic",
    "json_glb_consistency",
    "geometry_validity",
    "visual_perception",
)


def discover_scene_layouts(root: str | Path, *, limit: int | None = None) -> List[Path]:
    """Find ``scene_layout.json`` files under a batch artifact directory."""

    root_path = Path(root).expanduser().resolve()
    if root_path.is_file():
        return [root_path]
    paths = sorted(path.resolve() for path in root_path.rglob("scene_layout.json"))
    if limit is not None:
        return paths[: max(0, int(limit))]
    return paths


def evaluate_quality_batch(
    scene_layout_paths: Sequence[str | Path],
    *,
    include_llm_visual: bool = False,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Evaluate layered quality over many generated scenes.

    The default keeps the report deterministic and low-cost by disabling LLM
    visual evaluation. Existing captured-view metadata is still surfaced as
    artifact coverage through layer availability and image counts.
    """

    _ensure_eval_engine_path()
    from road_metrics import EvalConfig, EvalEngine

    engine = EvalEngine(EvalConfig(enable_llm_eval=bool(include_llm_visual), enable_audio_profile=False))
    rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []
    for raw_path in scene_layout_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            failures.append({"scene_layout_path": str(path), "error": "missing scene_layout.json"})
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            result = engine.evaluate(payload)
        except Exception as exc:
            failures.append({"scene_layout_path": str(path), "error": str(exc)})
            continue
        rows.append(_scene_quality_row(path, payload, result))

    report = _aggregate_quality_rows(rows, failures)
    return report, rows


def write_quality_report(
    report: Mapping[str, Any],
    per_scene_rows: Sequence[Mapping[str, Any]],
    out_dir: str | Path,
) -> Dict[str, str]:
    """Write ``quality_report.json`` and ``quality_per_scene.csv``."""

    output_dir = Path(out_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "quality_report.json"
    csv_path = output_dir / "quality_per_scene.csv"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    fieldnames = _csv_fieldnames(per_scene_rows)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in per_scene_rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    return {"quality_report": str(report_path), "quality_per_scene_csv": str(csv_path)}


def build_quality_report(
    search_root_or_paths: str | Path | Sequence[str | Path],
    *,
    out_dir: str | Path | None = None,
    include_llm_visual: bool = False,
    limit: int | None = None,
) -> Dict[str, Any]:
    """Discover/evaluate/write a batch quality report in one call."""

    if isinstance(search_root_or_paths, (str, Path)):
        paths = discover_scene_layouts(search_root_or_paths, limit=limit)
    else:
        paths = [Path(path).expanduser().resolve() for path in search_root_or_paths]
        if limit is not None:
            paths = paths[: max(0, int(limit))]
    report, rows = evaluate_quality_batch(paths, include_llm_visual=include_llm_visual)
    outputs: Dict[str, str] = {}
    if out_dir is not None:
        outputs = write_quality_report(report, rows, out_dir)
    return {"report": report, "rows": rows, "outputs": outputs}


def _ensure_eval_engine_path() -> None:
    mpl_dir = Path(tempfile.gettempdir()) / "roadgen3d_matplotlib"
    mpl_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir))
    submodule_path = Path(__file__).resolve().parent / "eval_engine_ext"
    if str(submodule_path) not in sys.path:
        sys.path.insert(0, str(submodule_path))


def _scene_quality_row(path: Path, payload: Mapping[str, Any], result: Any) -> Dict[str, Any]:
    summary = dict(payload.get("summary", {}) or {})
    layers = dict(getattr(result, "quality_layers", {}) or {})
    row: Dict[str, Any] = {
        "scene_layout_path": str(path),
        "scene_glb_path": str((payload.get("outputs", {}) or {}).get("scene_glb", "") or ""),
        "instance_count": int(summary.get("instance_count", len(payload.get("placements", []) or [])) or 0),
        "generation_quality_score": getattr(result, "generation_quality_score", None),
        "overall_score": getattr(result, "overall", None),
        "walkability_score": getattr(result, "walkability", None),
        "safety_score": getattr(result, "safety", None),
        "beauty_score": getattr(result, "beauty", None),
        "capture_3d_view_count": len(summary.get("render_views_3d", []) or []),
    }
    for layer_name in QUALITY_LAYERS:
        layer = dict(layers.get(layer_name, {}) or {})
        row[f"{layer_name}_available"] = bool(layer.get("available"))
        row[f"{layer_name}_score"] = layer.get("score")
    geometry = dict(layers.get("geometry_validity", {}) or {})
    consistency = dict(layers.get("json_glb_consistency", {}) or {})
    topology = dict(geometry.get("topology_continuity", {}) or {})
    row.update(
        {
            "object_recall": consistency.get("object_recall"),
            "object_precision": consistency.get("object_precision"),
            "category_accuracy": consistency.get("category_accuracy"),
            "mean_position_error_m": consistency.get("mean_position_error_m"),
            "blocked_clear_path_ratio": geometry.get("blocked_clear_path_ratio"),
            "road_conflict_count": geometry.get("road_conflict_count"),
            "mesh_aabb_collision_count": geometry.get("mesh_aabb_collision_count"),
            "road_graph_continuity_score": topology.get("road_graph_continuity_score"),
            "junction_correctness_score": topology.get("junction_correctness_score"),
            "sidewalk_corner_continuity_score": topology.get("sidewalk_corner_continuity_score"),
            "lane_width_consistency_score": topology.get("lane_width_consistency_score"),
            "turning_smoothness_score": topology.get("turning_smoothness_score"),
            "warning_count": len(geometry.get("warnings", []) or []) + len(consistency.get("warnings", []) or []),
        }
    )
    return row


def _aggregate_quality_rows(rows: Sequence[Mapping[str, Any]], failures: Sequence[Mapping[str, str]]) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "scene_count": len(rows),
        "failure_count": len(failures),
        "failures": list(failures)[:20],
        "layer_availability": {},
        "score_distributions": {},
        "worst_cases": {},
    }
    for layer_name in QUALITY_LAYERS:
        available_key = f"{layer_name}_available"
        score_key = f"{layer_name}_score"
        available_count = sum(1 for row in rows if bool(row.get(available_key)))
        report["layer_availability"][layer_name] = {
            "available_count": available_count,
            "availability_rate": _round(available_count / max(len(rows), 1)),
        }
        report["score_distributions"][layer_name] = _distribution(row.get(score_key) for row in rows)
        report["worst_cases"][layer_name] = _worst_cases(rows, score_key)
    report["score_distributions"]["generation_quality_score"] = _distribution(
        row.get("generation_quality_score") for row in rows
    )
    report["score_distributions"]["mesh_aabb_collision_count"] = _distribution(
        row.get("mesh_aabb_collision_count") for row in rows
    )
    report["score_distributions"]["road_conflict_count"] = _distribution(row.get("road_conflict_count") for row in rows)
    return report


def _distribution(values: Iterable[Any]) -> Dict[str, Any]:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return {"count": 0, "mean": None, "min": None, "max": None, "median": None}
    return {
        "count": len(numeric),
        "mean": _round(statistics.fmean(numeric)),
        "min": _round(min(numeric)),
        "max": _round(max(numeric)),
        "median": _round(statistics.median(numeric)),
    }


def _worst_cases(rows: Sequence[Mapping[str, Any]], score_key: str, *, limit: int = 5) -> List[Dict[str, Any]]:
    scored = [
        row
        for row in rows
        if isinstance(row.get(score_key), (int, float))
    ]
    scored.sort(key=lambda row: float(row.get(score_key, 0.0)))
    return [
        {
            "scene_layout_path": str(row.get("scene_layout_path", "")),
            "score": row.get(score_key),
            "warning_count": row.get("warning_count"),
        }
        for row in scored[:limit]
    ]


def _csv_fieldnames(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    preferred = [
        "scene_layout_path",
        "scene_glb_path",
        "instance_count",
        "generation_quality_score",
        "layout_semantic_score",
        "json_glb_consistency_score",
        "geometry_validity_score",
        "visual_perception_score",
        "object_recall",
        "object_precision",
        "category_accuracy",
        "mean_position_error_m",
        "blocked_clear_path_ratio",
        "road_conflict_count",
        "mesh_aabb_collision_count",
        "road_graph_continuity_score",
        "junction_correctness_score",
        "sidewalk_corner_continuity_score",
        "lane_width_consistency_score",
        "turning_smoothness_score",
    ]
    seen = set(preferred)
    extra = sorted({key for row in rows for key in row.keys() if key not in seen})
    return preferred + extra


def _round(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None
