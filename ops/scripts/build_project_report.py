#!/usr/bin/env python3
"""Build a source-backed RoadGen3D project report with figures.

The report is intentionally conservative: every number is read from existing
artifacts or computed by the repository's evaluation code. Missing data is
written as N/A instead of being inferred.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
N_A = "N/A"
DEFAULT_SCENARIO_ID = "scenario_04_child_friendly_school_corridor"

_MPL_DIR = Path(tempfile.gettempdir()) / "roadgen3d_matplotlib"
_CACHE_DIR = Path(tempfile.gettempdir()) / "roadgen3d_cache"
_MPL_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_MPL_DIR))
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE_DIR))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory. Defaults to artifacts/project_report/<timestamp>.",
    )
    parser.add_argument(
        "--benchmark-samples",
        type=Path,
        default=ROOT / "artifacts" / "branch_benchmarks" / "samples.jsonl",
        help="Branch benchmark samples.jsonl used for 3D evaluation-coordinate plots.",
    )
    parser.add_argument(
        "--scenario-catalog",
        type=Path,
        default=ROOT / "data" / "scenario_designs" / "hkust_gz_gate_scenarios.json",
        help="Scenario design catalog.",
    )
    parser.add_argument(
        "--scenario-rubric",
        type=Path,
        default=ROOT / "data" / "scenario_designs" / "hkust_gz_gate_evaluation_rubric.json",
        help="Scenario rubric JSON for the case study.",
    )
    parser.add_argument(
        "--design-matrix-root",
        type=Path,
        default=ROOT / "artifacts" / "design_matrix",
        help="Design-matrix artifact root.",
    )
    parser.add_argument(
        "--case-scenario-id",
        default=DEFAULT_SCENARIO_ID,
        help="Scenario id used for the detailed case study.",
    )
    parser.add_argument(
        "--capture-3d",
        choices=["auto", "never", "force"],
        default="auto",
        help="Whether to capture real 3D screenshots for the case-study layout.",
    )
    parser.add_argument(
        "--capture-profile",
        default="quick_12",
        help="3D capture profile passed to roadgen3d.capture_3d.",
    )
    parser.add_argument(
        "--max-gallery-scenarios",
        type=int,
        default=8,
        help="Maximum number of scenario gallery rows to include.",
    )
    parser.add_argument(
        "--benchmark-limit",
        type=int,
        default=5000,
        help="Maximum benchmark samples to load.",
    )
    parser.add_argument(
        "--skip-case-eval",
        action="store_true",
        help="Skip repository evaluation for the case-study layout.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = (args.out_dir or ROOT / "artifacts" / "project_report" / timestamp).expanduser().resolve()
    figures_dir = out_dir / "figures"
    tables_dir = out_dir / "tables"
    case_dir = out_dir / "case_study"
    gallery_dir = out_dir / "scenario_gallery"
    for directory in (out_dir, figures_dir, tables_dir, case_dir, gallery_dir):
        directory.mkdir(parents=True, exist_ok=True)

    catalog = load_scenario_catalog(args.scenario_catalog)
    scenarios = list(catalog.get("scenarios", []) or [])
    case_scenario = find_scenario(scenarios, args.case_scenario_id)
    case_layout_source = resolve_layout_path(case_scenario.get("preview_layout_path") if case_scenario else "")

    benchmark_rows = load_benchmark_samples(args.benchmark_samples, limit=args.benchmark_limit)
    write_csv(tables_dir / "evaluation_samples.csv", benchmark_rows, evaluation_sample_fields())

    plot_3d_scatter(
        benchmark_rows,
        group_key="preset_id",
        out_path=figures_dir / "evaluation_3d_all.png",
        title="All Evaluation Samples: Walkability / Safety / Beauty",
    )
    plot_3d_scatter(
        benchmark_rows,
        group_key="skeleton_group",
        out_path=figures_dir / "evaluation_3d_by_skeleton.png",
        title="Evaluation Coordinates by Road Skeleton Design",
    )
    plot_3d_scatter(
        benchmark_rows,
        group_key="furniture_group",
        out_path=figures_dir / "evaluation_3d_by_furniture.png",
        title="Evaluation Coordinates by Street Furniture Design",
    )
    plot_group_means(
        benchmark_rows,
        group_key="skeleton_group",
        out_path=figures_dir / "mean_scores_by_skeleton.png",
        title="Mean Scores by Road Skeleton Design",
    )
    plot_group_means(
        benchmark_rows,
        group_key="furniture_group",
        out_path=figures_dir / "mean_scores_by_furniture.png",
        title="Mean Scores by Street Furniture Design",
    )

    design_matrix_rows = collect_design_matrix_layouts(args.design_matrix_root)
    write_csv(tables_dir / "design_matrix_layouts.csv", design_matrix_rows, design_matrix_fields())
    plot_design_matrix_coverage(
        design_matrix_rows,
        out_path=figures_dir / "design_matrix_coverage.png",
    )

    gallery_items = build_scenario_gallery(
        scenarios,
        gallery_dir=gallery_dir,
        max_count=max(0, int(args.max_gallery_scenarios or 0)),
    )

    case_payload: dict[str, Any] | None = None
    case_outputs: dict[str, Any] = {
        "scenario": case_scenario or {},
        "layout_source": str(case_layout_source) if case_layout_source else "",
        "layout_path": "",
        "capture_result": None,
        "presentation_views": [],
        "evaluation": None,
        "rubric": None,
        "metrics_rows": [],
        "errors": [],
    }
    if case_layout_source and case_layout_source.exists():
        case_payload = json_load(case_layout_source)
        case_layout = copy_case_layout(case_layout_source, case_dir)
        case_outputs["layout_path"] = str(case_layout)
        case_outputs["presentation_views"] = render_case_presentation_views(case_payload, case_dir)
        capture_result = maybe_capture_case_3d(case_layout, case_payload, args.capture_3d, args.capture_profile)
        case_outputs["capture_result"] = capture_result
        if not args.skip_case_eval:
            evaluation, evaluation_error = evaluate_case_layout(case_payload)
            if evaluation_error:
                case_outputs["errors"].append(evaluation_error)
            case_outputs["evaluation"] = evaluation
            rubric, rubric_error = evaluate_case_rubric(case_payload, args.case_scenario_id, args.scenario_rubric)
            if rubric_error:
                case_outputs["errors"].append(rubric_error)
            case_outputs["rubric"] = rubric
            case_outputs["metrics_rows"] = flatten_case_metrics(evaluation, rubric)
    elif case_layout_source:
        case_outputs["errors"].append(f"Case-study layout path does not exist: {case_layout_source}")
    else:
        case_outputs["errors"].append(f"Case-study scenario not found or has no preview layout: {args.case_scenario_id}")

    write_json(tables_dir / "case_study_evaluation.json", case_outputs)
    write_csv(tables_dir / "case_study_metrics.csv", case_outputs["metrics_rows"], case_metric_fields())

    report_path = out_dir / "RoadGen3D_project_report.md"
    report_path.write_text(
        build_markdown_report(
            out_dir=out_dir,
            generated_at=timestamp,
            benchmark_rows=benchmark_rows,
            design_matrix_rows=design_matrix_rows,
            scenarios=scenarios,
            gallery_items=gallery_items,
            case_outputs=case_outputs,
            source_paths={
                "benchmark_samples": args.benchmark_samples,
                "scenario_catalog": args.scenario_catalog,
                "scenario_rubric": args.scenario_rubric,
                "design_matrix_root": args.design_matrix_root,
            },
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "roadgen3d_project_report_manifest_v1",
        "generated_at": timestamp,
        "report_path": str(report_path),
        "out_dir": str(out_dir),
        "figures": sorted(str(path) for path in figures_dir.glob("*.png")),
        "tables": sorted(str(path) for path in tables_dir.glob("*")),
        "case_study": case_outputs,
    }
    write_json(out_dir / "manifest.json", manifest)
    print(json.dumps({"report_path": str(report_path), "out_dir": str(out_dir)}, ensure_ascii=False, indent=2))
    return 0


def load_scenario_catalog(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"scenarios": [], "error": f"Missing scenario catalog: {path}"}
    return json_load(path)


def find_scenario(scenarios: Sequence[Mapping[str, Any]], scenario_id: str) -> dict[str, Any] | None:
    for item in scenarios:
        if str(item.get("scenario_id") or "") == scenario_id:
            return dict(item)
    return None


def resolve_layout_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_benchmark_samples(path: Path, *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        row = benchmark_row(item)
        if row:
            rows.append(row)
        if len(rows) >= limit:
            break
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return rows


def benchmark_row(item: Mapping[str, Any]) -> dict[str, Any] | None:
    walkability = coerce_float(first_present(item.get("x"), item.get("walkability")))
    safety = coerce_float(first_present(item.get("y"), item.get("safety")))
    beauty = coerce_float(first_present(item.get("z"), item.get("beauty")))
    if walkability is None or safety is None or beauty is None:
        return None
    config = dict(item.get("config_patch") or {})
    features = dict(item.get("analysis_features") or {})
    feature_input = dict(features.get("input") or {})
    feature_scene = dict(features.get("scene") or {})
    skeleton = first_text(
        config.get("skeleton_design_profile"),
        feature_input.get("skeleton_design_profile"),
        config.get("target_street_type"),
        feature_input.get("target_street_type"),
        item.get("graph_template_id"),
        "unknown_skeleton",
    )
    lane_count = first_present(config.get("lane_count"), feature_input.get("lane_count"), feature_scene.get("lane_count"))
    if skeleton in {"", "unknown_skeleton"} and lane_count not in (None, ""):
        skeleton = f"lane_count_{lane_count}"
    furniture = first_text(
        config.get("street_furniture_profile"),
        feature_input.get("street_furniture_profile"),
        item.get("preset_id"),
        feature_input.get("preset_id"),
        config.get("style_preset"),
        "unknown_furniture",
    )
    overall = coerce_float(item.get("overall"))
    return {
        "sample_id": str(item.get("sample_id") or ""),
        "created_at": str(item.get("created_at") or ""),
        "source": str(item.get("source") or ""),
        "run_id": str(item.get("run_id") or ""),
        "node_id": str(item.get("node_id") or ""),
        "status": str(item.get("status") or ""),
        "preset_id": str(item.get("preset_id") or ""),
        "preset_label": str(item.get("preset_label") or item.get("preset_name") or item.get("preset_id") or N_A),
        "generation_method": str(item.get("generation_method") or N_A),
        "skeleton_group": skeleton or N_A,
        "furniture_group": furniture or N_A,
        "walkability": walkability,
        "safety": safety,
        "beauty": beauty,
        "overall": overall if overall is not None else N_A,
        "is_pareto_front": bool(item.get("is_pareto_front")),
        "pareto_rank": item.get("pareto_rank") if item.get("pareto_rank") is not None else N_A,
        "dominated_by_count": item.get("dominated_by_count") if item.get("dominated_by_count") is not None else N_A,
        "scene_layout_path": str(item.get("scene_layout_path") or ""),
        "scene_glb_path": str(item.get("scene_glb_path") or ""),
    }


def collect_design_matrix_layouts(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for layout_path in sorted(root.rglob("scene_layout.json")):
        try:
            payload = json_load(layout_path)
        except Exception:
            continue
        summary = dict(payload.get("summary") or {})
        config = dict(payload.get("config") or {})
        metadata = dict(summary.get("design_matrix_cell") or {})
        outputs = dict(payload.get("outputs") or {})
        glb_path = resolve_output_path(outputs.get("scene_glb"), layout_path)
        skeleton = first_text(
            metadata.get("skeleton_design_profile"),
            config.get("skeleton_design_profile"),
            summary.get("skeleton_design_profile"),
            metadata.get("structure_key"),
            config.get("target_street_type"),
            N_A,
        )
        furniture = first_text(
            metadata.get("street_furniture_profile"),
            config.get("street_furniture_profile"),
            summary.get("street_furniture_profile"),
            metadata.get("furniture_key"),
            N_A,
        )
        rows.append({
            "layout_path": str(layout_path.resolve()),
            "cell_key": str(metadata.get("cell_key") or N_A),
            "cell_hash": str(metadata.get("cell_hash") or layout_path.parents[2].name if len(layout_path.parents) > 2 else N_A),
            "structure_key": str(metadata.get("structure_key") or N_A),
            "furniture_key": str(metadata.get("furniture_key") or N_A),
            "skeleton_design_profile": skeleton or N_A,
            "street_furniture_profile": furniture or N_A,
            "profile_pair": first_text(summary.get("profile_pair"), f"{skeleton}+{furniture}" if skeleton and furniture else N_A),
            "scene_glb_path": str(glb_path) if glb_path else "",
            "scene_glb_exists": bool(glb_path and glb_path.exists()),
            "placement_count": int(summary.get("instance_count") or len(payload.get("placements") or []) or 0),
            "walkability": N_A,
            "safety": N_A,
            "beauty": N_A,
            "overall": N_A,
            "evaluation_source": "N/A",
        })
    return rows


def resolve_output_path(value: object, layout_path: Path) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = layout_path.parent / path
    return path.resolve()


def build_scenario_gallery(
    scenarios: Sequence[Mapping[str, Any]],
    *,
    gallery_dir: Path,
    max_count: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for scenario in scenarios[:max_count]:
        scenario_id = str(scenario.get("scenario_id") or "")
        layout_path = resolve_layout_path(scenario.get("preview_layout_path"))
        image_paths: list[Path] = []
        image_source = N_A
        if layout_path and layout_path.exists():
            captures = sorted((layout_path.parent / "view_captures").glob("*.png"))
            if captures:
                image_paths = captures[:2]
                image_source = "existing_3d_capture"
            else:
                try:
                    payload = json_load(layout_path)
                    rendered = render_layout_presentation_views(payload, gallery_dir / scenario_id)
                    image_paths = [Path(str(item.get("path"))) for item in rendered[:2] if item.get("path")]
                    image_source = "presentation_render" if image_paths else N_A
                except Exception:
                    image_paths = []
        copied = []
        for index, source in enumerate(image_paths, start=1):
            if not source.exists():
                continue
            suffix = source.suffix or ".png"
            dest = gallery_dir / scenario_id / f"gallery_{index:02d}{suffix}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != dest.resolve():
                shutil.copyfile(source, dest)
            copied.append(str(dest))
        items.append({
            "scenario_id": scenario_id,
            "title_zh": str(scenario.get("title_zh") or scenario_id),
            "query": str(scenario.get("query") or ""),
            "layout_path": str(layout_path) if layout_path else "",
            "image_source": image_source,
            "images": copied,
        })
    return items


def copy_case_layout(source: Path, case_dir: Path) -> Path:
    dest = case_dir / "scene_layout.json"
    shutil.copyfile(source, dest)
    return dest


def render_case_presentation_views(payload: Mapping[str, Any], case_dir: Path) -> list[dict[str, str]]:
    try:
        return render_layout_presentation_views(payload, case_dir / "presentation")
    except Exception as exc:
        return [{"name": "N/A", "title": "presentation_render_failed", "path": "", "error": str(exc)}]


def render_layout_presentation_views(payload: Mapping[str, Any], out_dir: Path) -> list[dict[str, str]]:
    from roadgen3d.beauty import render_presentation_views
    from roadgen3d.types import StreetComposeConfig

    config_payload = dict(payload.get("config") or {})
    allowed = {field.name for field in fields(StreetComposeConfig)}
    filtered = {key: value for key, value in config_payload.items() if key in allowed}
    required_defaults = {
        "query": str(payload.get("query") or config_payload.get("query") or ""),
        "length_m": 80.0,
        "road_width_m": 8.0,
        "sidewalk_width_m": 2.5,
        "lane_count": 2,
        "density": 1.0,
        "seed": 42,
        "topk_per_category": 20,
        "max_trials_per_slot": 30,
    }
    for key, value in required_defaults.items():
        filtered.setdefault(key, value)
    config = StreetComposeConfig(**filtered)
    return list(render_presentation_views(payload, out_dir=out_dir, config=config))


def maybe_capture_case_3d(
    layout_path: Path,
    payload: Mapping[str, Any],
    mode: str,
    profile: str,
) -> dict[str, Any] | None:
    if mode == "never":
        return {"status": "skipped", "reason": "capture_3d=never"}
    existing = sorted((layout_path.parent / "view_captures").glob("*.png"))
    if existing and mode != "force":
        return {
            "status": "succeeded",
            "source": "existing_3d_capture",
            "view_count": len(existing),
            "views": [{"path": str(path), "label": path.stem, "kind": "existing"} for path in existing],
        }
    glb_path = resolve_output_path((payload.get("outputs") or {}).get("scene_glb"), layout_path)
    if glb_path is None or not glb_path.exists():
        return {"status": "failed", "error": "scene_glb missing; cannot capture real 3D views"}
    try:
        from roadgen3d.capture_3d import capture_views_for_layout

        result = capture_views_for_layout(
            layout_path=layout_path,
            scene_glb_path=glb_path,
            options={
                "capture_3d_views": True,
                "capture_profile": profile,
                "capture_failure_policy": "warn",
                "retain_glb_policy": "always",
                "capture_resolution": [1280, 720],
                "capture_timeout_s": 240,
            },
        )
        return result.to_dict()
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def evaluate_case_layout(payload: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        from roadgen3d.eval_engine_ext.road_metrics.core.config import EvalConfig
        from roadgen3d.eval_engine_ext.road_metrics.core.engine import EvalEngine

        config = EvalConfig.default()
        config.enable_llm_eval = False
        config.enable_audio_profile = False
        result = EvalEngine(config).evaluate(payload)
        return result.to_dict(), None
    except Exception as exc:
        return None, f"case evaluation failed: {exc}"


def evaluate_case_rubric(
    payload: Mapping[str, Any],
    scenario_id: str,
    rubric_path: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        from roadgen3d.scenario_rubric import ScenarioRubricEvaluator

        evaluator = ScenarioRubricEvaluator(rubric_path=rubric_path)
        return evaluator.evaluate_layout(payload, scenario_id), None
    except Exception as exc:
        return None, f"scenario rubric evaluation failed: {exc}"


def flatten_case_metrics(evaluation: Mapping[str, Any] | None, rubric: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if evaluation:
        walk = dict(evaluation.get("walkability") or {})
        for metric, value in dict(walk.get("indicators") or {}).items():
            rows.append({"section": "walkability_indicator", "metric": metric, "value": value, "source": "EvalEngine"})
        for metric, value in dict(walk.get("pillar_scores") or {}).items():
            rows.append({"section": "walkability_pillar", "metric": metric, "value": value, "source": "EvalEngine"})
        rows.append({
            "section": "score",
            "metric": "walkability_index",
            "value": walk.get("walkability_index", N_A),
            "source": "EvalEngine",
        })
        for section in ("safety", "beauty"):
            report = dict(evaluation.get(section) or {})
            rows.append({
                "section": section,
                "metric": "structural_score",
                "value": report.get("structural_score", N_A),
                "source": "EvalEngine",
            })
            rows.append({
                "section": section,
                "metric": "final_score",
                "value": report.get("final_score", N_A),
                "source": "EvalEngine",
            })
            for metric, value in dict(report.get("features") or {}).items():
                rows.append({"section": f"{section}_feature", "metric": metric, "value": value, "source": "EvalEngine"})
        rows.append({
            "section": "score",
            "metric": "evaluation_score",
            "value": evaluation.get("evaluation_score", N_A),
            "source": "EvalEngine",
        })
        rows.append({
            "section": "score",
            "metric": "generation_quality_score",
            "value": evaluation.get("generation_quality_score", N_A),
            "source": "EvalEngine",
        })
    if rubric:
        rows.append({
            "section": "scenario_rubric",
            "metric": "status",
            "value": rubric.get("status", N_A),
            "source": "ScenarioRubricEvaluator",
        })
        rows.append({
            "section": "scenario_rubric",
            "metric": "total_score",
            "value": rubric.get("total_score", N_A),
            "source": "ScenarioRubricEvaluator",
        })
        for metric, value in dict(rubric.get("dimension_scores") or {}).items():
            rows.append({"section": "scenario_rubric_dimension", "metric": metric, "value": value, "source": "ScenarioRubricEvaluator"})
        for gate in rubric.get("semantic_gates") or []:
            if not isinstance(gate, Mapping):
                continue
            rows.append({
                "section": "scenario_semantic_gate",
                "metric": str(gate.get("gate_id") or gate.get("description") or N_A),
                "value": str(gate.get("status") or gate.get("passed") or N_A),
                "source": "ScenarioRubricEvaluator",
            })
    return rows


def plot_3d_scatter(rows: Sequence[Mapping[str, Any]], *, group_key: str, out_path: Path, title: str) -> None:
    plt = require_matplotlib()
    numeric = [row for row in rows if all(coerce_float(row.get(key)) is not None for key in ("walkability", "safety", "beauty"))]
    if not numeric:
        save_na_figure(out_path, title, "No numeric evaluation coordinates available.")
        return
    groups = sorted({str(row.get(group_key) or N_A) for row in numeric})
    color_map = color_map_for_groups(groups)
    fig = plt.figure(figsize=(9.0, 7.0))
    ax = fig.add_subplot(111, projection="3d")
    for group in groups:
        subset = [row for row in numeric if str(row.get(group_key) or N_A) == group]
        xs = [float(row["walkability"]) for row in subset]
        ys = [float(row["safety"]) for row in subset]
        zs = [float(row["beauty"]) for row in subset]
        ax.scatter(xs, ys, zs, s=34, alpha=0.78, color=color_map[group], label=truncate_label(group, 28))
    pareto = [row for row in numeric if row.get("is_pareto_front")]
    if pareto:
        ax.scatter(
            [float(row["walkability"]) for row in pareto],
            [float(row["safety"]) for row in pareto],
            [float(row["beauty"]) for row in pareto],
            s=78,
            marker="*",
            color="#111827",
            label="Pareto front",
        )
    ax.set_xlabel("Walkability (x)")
    ax.set_ylabel("Safety (y)")
    ax.set_zlabel("Beauty (z)")
    ax.set_title(f"{title}\nN={len(numeric)}")
    ax.view_init(elev=24, azim=-48)
    handles, labels = ax.get_legend_handles_labels()
    if len(handles) <= 14:
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), fontsize=8)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_group_means(rows: Sequence[Mapping[str, Any]], *, group_key: str, out_path: Path, title: str) -> None:
    plt = require_matplotlib()
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if coerce_float(row.get("overall")) is None:
            continue
        grouped.setdefault(str(row.get(group_key) or N_A), []).append(row)
    if not grouped:
        save_na_figure(out_path, title, "No numeric overall score available.")
        return
    stats = []
    for group, items in grouped.items():
        stats.append((group, mean([float(item["overall"]) for item in items]), len(items)))
    stats.sort(key=lambda item: item[1], reverse=True)
    labels = [truncate_label(item[0], 24) for item in stats]
    values = [item[1] for item in stats]
    counts = [item[2] for item in stats]
    fig, ax = plt.subplots(figsize=(9.2, max(4.8, len(stats) * 0.42)))
    bars = ax.barh(labels, values, color="#2563eb", alpha=0.82)
    ax.invert_yaxis()
    ax.set_xlabel("Mean overall score")
    ax.set_title(title)
    for bar, count in zip(bars, counts):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2, f"n={count}", va="center", fontsize=8)
    ax.grid(axis="x", alpha=0.22)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_design_matrix_coverage(rows: Sequence[Mapping[str, Any]], *, out_path: Path) -> None:
    plt = require_matplotlib()
    if not rows:
        save_na_figure(out_path, "Design Matrix Coverage", "No design-matrix layouts found.")
        return
    skeletons = sorted({str(row.get("skeleton_design_profile") or N_A) for row in rows})
    furniture = sorted({str(row.get("street_furniture_profile") or N_A) for row in rows})
    counts = [[0 for _ in furniture] for _ in skeletons]
    for row in rows:
        y = skeletons.index(str(row.get("skeleton_design_profile") or N_A))
        x = furniture.index(str(row.get("street_furniture_profile") or N_A))
        counts[y][x] += 1
    fig, ax = plt.subplots(figsize=(max(7.0, len(furniture) * 0.8), max(4.5, len(skeletons) * 0.45)))
    image = ax.imshow(counts, cmap="Blues")
    ax.set_xticks(range(len(furniture)), [truncate_label(item, 18) for item in furniture], rotation=35, ha="right")
    ax.set_yticks(range(len(skeletons)), [truncate_label(item, 22) for item in skeletons])
    ax.set_title("Design Matrix Layout Coverage\ncell value = existing scene_layout.json count")
    for y, row_values in enumerate(counts):
        for x, value in enumerate(row_values):
            ax.text(x, y, str(value), ha="center", va="center", color="#111827", fontsize=8)
    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def require_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def save_na_figure(out_path: Path, title: str, message: str) -> None:
    plt = require_matplotlib()
    fig, ax = plt.subplots(figsize=(8.0, 4.8))
    ax.axis("off")
    ax.text(0.5, 0.62, title, ha="center", va="center", fontsize=16, weight="bold")
    ax.text(0.5, 0.42, f"{N_A}: {message}", ha="center", va="center", fontsize=11)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def color_map_for_groups(groups: Sequence[str]) -> dict[str, Any]:
    plt = require_matplotlib()
    cmap = plt.get_cmap("tab20")
    return {group: cmap(index % 20) for index, group in enumerate(groups)}


def build_markdown_report(
    *,
    out_dir: Path,
    generated_at: str,
    benchmark_rows: Sequence[Mapping[str, Any]],
    design_matrix_rows: Sequence[Mapping[str, Any]],
    scenarios: Sequence[Mapping[str, Any]],
    gallery_items: Sequence[Mapping[str, Any]],
    case_outputs: Mapping[str, Any],
    source_paths: Mapping[str, Path],
) -> str:
    case_scenario = dict(case_outputs.get("scenario") or {})
    evaluation = dict(case_outputs.get("evaluation") or {})
    rubric = dict(case_outputs.get("rubric") or {})
    lines: list[str] = []
    lines.append("# RoadGen3D 项目报告自动导出")
    lines.append("")
    lines.append(f"- 生成时间: `{generated_at}`")
    lines.append("- 数据原则: 所有数值来自现有 artifact 或当前代码评估；缺失项统一写为 `N/A`。")
    lines.append("- 评价坐标约定: `x=walkability`, `y=safety`, `z=beauty`。")
    lines.append("")
    lines.append("## 数据来源")
    for name, path in source_paths.items():
        lines.append(f"- {name}: `{path}`")
    lines.append("")
    lines.append("## 全量评价坐标")
    lines.append("")
    lines.append(f"- 可用评价样本数: `{len(benchmark_rows)}`")
    lines.append(f"- Pareto front 样本数: `{sum(1 for row in benchmark_rows if row.get('is_pareto_front'))}`")
    lines.append(f"- 导出表: `{rel(out_dir / 'tables' / 'evaluation_samples.csv', out_dir)}`")
    lines.append("")
    for image in (
        "evaluation_3d_all.png",
        "evaluation_3d_by_skeleton.png",
        "evaluation_3d_by_furniture.png",
        "mean_scores_by_skeleton.png",
        "mean_scores_by_furniture.png",
    ):
        lines.append(f"![{image}]({rel(out_dir / 'figures' / image, out_dir)})")
        lines.append("")
    lines.append("## 道路骨架与街道家具设计矩阵")
    lines.append("")
    lines.append(f"- 已发现 design matrix layout 数: `{len(design_matrix_rows)}`")
    lines.append(f"- 导出表: `{rel(out_dir / 'tables' / 'design_matrix_layouts.csv', out_dir)}`")
    lines.append("- 注意: design matrix layout 本身若没有现成评价分数，表中评价字段保持 `N/A`。")
    lines.append("")
    lines.append(f"![design_matrix_coverage]({rel(out_dir / 'figures' / 'design_matrix_coverage.png', out_dir)})")
    lines.append("")
    lines.append("## 场景截图索引")
    lines.append("")
    if gallery_items:
        for item in gallery_items:
            lines.append(f"### {safe_text(item.get('title_zh'))}")
            lines.append(f"- scenario_id: `{safe_text(item.get('scenario_id'))}`")
            lines.append(f"- image_source: `{safe_text(item.get('image_source'))}`")
            for image_path in item.get("images") or []:
                lines.append(f"![{Path(image_path).name}]({rel(Path(image_path), out_dir)})")
            if not item.get("images"):
                lines.append("- 截图: `N/A`")
            lines.append("")
    else:
        lines.append("N/A")
        lines.append("")
    lines.append("## Case Study: 方案 4 儿童友好型学校走廊")
    lines.append("")
    lines.append(f"- scenario_id: `{safe_text(case_scenario.get('scenario_id', N_A))}`")
    lines.append(f"- 标题: {safe_text(case_scenario.get('title_zh', N_A))}")
    lines.append(f"- 设计意图: {safe_text(case_scenario.get('intent_zh', N_A))}")
    lines.append(f"- 布局来源: `{safe_text(case_outputs.get('layout_source', N_A))}`")
    lines.append(f"- 工作副本: `{safe_text(case_outputs.get('layout_path', N_A))}`")
    lines.append("")
    lines.append("### Case 截图")
    lines.append("")
    capture = dict(case_outputs.get("capture_result") or {})
    capture_views = [view for view in capture.get("views") or [] if isinstance(view, Mapping) and view.get("path")]
    if capture_views:
        lines.append(f"- 3D capture status: `{safe_text(capture.get('status'))}`")
        for view in capture_views[:6]:
            lines.append(f"![{safe_text(view.get('label') or view.get('view_id'))}]({rel(Path(str(view.get('path'))), out_dir)})")
    else:
        lines.append(f"- 3D capture status: `{safe_text(capture.get('status', N_A))}`")
        if capture.get("error"):
            lines.append(f"- 3D capture error: `{safe_text(capture.get('error'))}`")
    presentation_views = [view for view in case_outputs.get("presentation_views") or [] if isinstance(view, Mapping) and view.get("path")]
    if presentation_views:
        lines.append("")
        lines.append("补充 presentation views:")
        for view in presentation_views[:4]:
            lines.append(f"![{safe_text(view.get('title') or view.get('name'))}]({rel(Path(str(view.get('path'))), out_dir)})")
    if not capture_views and not presentation_views:
        lines.append("N/A")
    lines.append("")
    lines.append("### 详细评价")
    lines.append("")
    score_rows = case_score_rows(evaluation, rubric)
    lines.extend(markdown_table(["Metric", "Value", "Source"], score_rows))
    lines.append("")
    lines.append(f"- JSON: `{rel(out_dir / 'tables' / 'case_study_evaluation.json', out_dir)}`")
    lines.append(f"- CSV: `{rel(out_dir / 'tables' / 'case_study_metrics.csv', out_dir)}`")
    lines.append("")
    lines.append("### Walkability 指标")
    lines.extend(metric_table_from_mapping(dict((evaluation.get("walkability") or {}).get("indicators") or {})))
    lines.append("")
    lines.append("### Safety 指标")
    lines.extend(metric_table_from_mapping(dict((evaluation.get("safety") or {}).get("features") or {})))
    lines.append("")
    lines.append("### Beauty 指标")
    lines.extend(metric_table_from_mapping(dict((evaluation.get("beauty") or {}).get("features") or {})))
    lines.append("")
    lines.append("### Scenario Rubric")
    if rubric:
        lines.extend(markdown_table(
            ["Item", "Value"],
            [
                ["status", fmt(rubric.get("status"))],
                ["total_score", fmt(rubric.get("total_score"))],
                ["profile_pair", fmt(rubric.get("profile_pair"))],
                ["missing_metrics", fmt(", ".join(rubric.get("missing_metrics") or []) if rubric.get("missing_metrics") else "")],
            ],
        ))
        gate_rows = []
        for gate in rubric.get("semantic_gates") or []:
            if isinstance(gate, Mapping):
                gate_rows.append([
                    safe_text(gate.get("gate_id") or gate.get("description") or N_A),
                    fmt(gate.get("status", gate.get("passed", N_A))),
                    safe_text(gate.get("description", "")),
                ])
        if gate_rows:
            lines.append("")
            lines.extend(markdown_table(["Gate", "Status", "Description"], gate_rows))
    else:
        lines.append("N/A")
    if case_outputs.get("errors"):
        lines.append("")
        lines.append("### 运行警告")
        for error in case_outputs.get("errors") or []:
            lines.append(f"- {safe_text(error)}")
    lines.append("")
    lines.append("## N/A 说明")
    lines.append("")
    lines.append("`N/A` 表示对应 artifact 中没有该字段、该图片不存在、或当前评估代码无法完成该项计算。脚本不会用均值、经验值或文字推测补齐缺失数据。")
    lines.append("")
    return "\n".join(lines)


def case_score_rows(evaluation: Mapping[str, Any], rubric: Mapping[str, Any]) -> list[list[str]]:
    if not evaluation and not rubric:
        return [["N/A", "N/A", "N/A"]]
    walk = dict(evaluation.get("walkability") or {})
    safety = dict(evaluation.get("safety") or {})
    beauty = dict(evaluation.get("beauty") or {})
    return [
        ["walkability_index", fmt(walk.get("walkability_index")), "EvalEngine"],
        ["safety_structural_score", fmt(safety.get("structural_score")), "EvalEngine"],
        ["safety_final_score", fmt(safety.get("final_score")), "EvalEngine"],
        ["beauty_structural_score", fmt(beauty.get("structural_score")), "EvalEngine"],
        ["beauty_final_score", fmt(beauty.get("final_score")), "EvalEngine"],
        ["evaluation_score", fmt(evaluation.get("evaluation_score")), "EvalEngine"],
        ["generation_quality_score", fmt(evaluation.get("generation_quality_score")), "EvalEngine"],
        ["rubric_status", fmt(rubric.get("status")), "ScenarioRubricEvaluator"],
        ["rubric_total_score", fmt(rubric.get("total_score")), "ScenarioRubricEvaluator"],
    ]


def metric_table_from_mapping(values: Mapping[str, Any]) -> list[str]:
    if not values:
        return ["N/A"]
    rows = [[safe_text(key), fmt(value)] for key, value in sorted(values.items())]
    return markdown_table(["Metric", "Value"], rows)


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        safe_row = [safe_text(cell) for cell in row]
        if len(safe_row) < len(headers):
            safe_row.extend([N_A] * (len(headers) - len(safe_row)))
        lines.append("| " + " | ".join(safe_row[: len(headers)]) + " |")
    return lines


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field, N_A)) for field in fieldnames})


def evaluation_sample_fields() -> list[str]:
    return [
        "sample_id",
        "created_at",
        "source",
        "run_id",
        "node_id",
        "status",
        "preset_id",
        "preset_label",
        "generation_method",
        "skeleton_group",
        "furniture_group",
        "walkability",
        "safety",
        "beauty",
        "overall",
        "is_pareto_front",
        "pareto_rank",
        "dominated_by_count",
        "scene_layout_path",
        "scene_glb_path",
    ]


def design_matrix_fields() -> list[str]:
    return [
        "layout_path",
        "cell_key",
        "cell_hash",
        "structure_key",
        "furniture_key",
        "skeleton_design_profile",
        "street_furniture_profile",
        "profile_pair",
        "scene_glb_path",
        "scene_glb_exists",
        "placement_count",
        "walkability",
        "safety",
        "beauty",
        "overall",
        "evaluation_source",
    ]


def case_metric_fields() -> list[str]:
    return ["section", "metric", "value", "source"]


def csv_value(value: Any) -> str:
    if value is None or value == "":
        return N_A
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except Exception:
        return str(path)


def fmt(value: Any) -> str:
    if value is None or value == "":
        return N_A
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple, dict)):
        if not value:
            return N_A
        return safe_text(json.dumps(value, ensure_ascii=False))
    return safe_text(value)


def safe_text(value: Any) -> str:
    text = str(value if value is not None else N_A)
    text = text.replace("\n", " ").replace("|", "\\|")
    return text if text else N_A


def coerce_float(value: Any) -> float | None:
    if value in (None, "", N_A):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def first_text(*values: Any) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return ""


def truncate_label(label: str, max_len: int) -> str:
    text = str(label or N_A)
    if len(text) <= max_len:
        return text
    return text[: max(1, max_len - 1)] + "..."


def mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
