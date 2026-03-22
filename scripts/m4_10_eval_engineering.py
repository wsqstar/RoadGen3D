#!/usr/bin/env python3
"""Run M4 engineering evaluation for street composition policies."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.eval_metrics import (  # noqa: E402
    aggregate_scene_rows,
    compare_mode_reports,
    compute_balance_score,
    compute_spacing_uniformity,
    compute_style_consistency,
)
from roadgen3d.street_layout import compose_street_scene  # noqa: E402
from roadgen3d.types import StreetComposeConfig  # noqa: E402

DEFAULT_QUERIES = [
    "modern clean urban street",
    "tree-lined residential street",
    "dense downtown avenue with street furniture",
    "quiet neighborhood road with benches",
    "functional industrial roadside",
    "pedestrian-friendly boulevard",
    "compact city block street",
    "orderly transit corridor",
    "minimalist urban street",
    "high-utility municipal road",
    "mixed-use street with bus facilities",
    "wide arterial road with sparse furniture",
    "cozy community street",
    "street with frequent bollards and lamps",
    "green urban corridor",
    "commercial street with mailbox and trash",
    "modern tactical city street",
    "nordic-style clean roadway",
    "high-density downtown street",
    "balanced street with diverse assets",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate engineering metrics for street layout policies.")
    parser.add_argument("--queries", type=Path, default=Path("data/eval/queries_m4.txt"))
    parser.add_argument("--manifest", type=Path, default=Path("data/real/real_assets_manifest.jsonl"))
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/real"))
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/m4"))
    parser.add_argument("--model-name", default="openai/clip-vit-base-patch32")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--placement-policy", choices=["rule", "learned"], default="rule")
    parser.add_argument("--policy-ckpt", type=Path, default=None)
    parser.add_argument("--policy-temperature", type=float, default=0.12)
    parser.add_argument("--program-generator", choices=["heuristic_v1", "learned_v1"], default="learned_v1")
    parser.add_argument("--program-ckpt", type=Path, default=None)
    parser.add_argument("--layout-solver", choices=["hybrid_milp_v1", "milp_template_v1", "banded"], default="hybrid_milp_v1")
    parser.add_argument("--no-solver-fallback", action="store_true")
    parser.add_argument("--segment-length-m", type=float, default=12.0)
    parser.add_argument("--compare-rule", action="store_true")
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--seed-end", type=int, default=4)
    parser.add_argument("--length-m", type=float, default=80.0)
    parser.add_argument("--road-width-m", type=float, default=8.0)
    parser.add_argument("--sidewalk-width-m", type=float, default=2.5)
    parser.add_argument("--lane-count", type=int, default=2)
    parser.add_argument("--density", type=float, default=1.0)
    parser.add_argument("--topk-per-category", type=int, default=20)
    parser.add_argument("--max-trials-per-slot", type=int, default=30)
    parser.add_argument("--export-format", choices=["glb", "ply", "both"], default="glb")
    parser.add_argument(
        "--design-rule-profile",
        choices=["balanced_complete_street_v1", "pedestrian_priority_v1", "transit_priority_v1"],
        default="balanced_complete_street_v1",
    )
    parser.add_argument("--city-context", type=str, default="generic_city")
    parser.add_argument("--target-street-type", type=str, default="mixed_use")
    return parser.parse_args()


def _load_queries(path: Path) -> List[str]:
    if not path.exists():
        return list(DEFAULT_QUERIES)
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines or list(DEFAULT_QUERIES)


def _safe_float(payload: Dict[str, object], key: str) -> float:
    try:
        return float(payload.get(key, 0.0))
    except Exception:
        return 0.0


def _safe_int(payload: Dict[str, object], key: str) -> int:
    try:
        return int(payload.get(key, 0))
    except Exception:
        return 0


def _run_mode(
    *,
    mode: str,
    queries: Sequence[str],
    seed_start: int,
    seed_end: int,
    manifest: Path,
    artifacts: Path,
    model_name: str,
    model_dir: Path | None,
    local_files_only: bool,
    device: str,
    length_m: float,
    road_width_m: float,
    sidewalk_width_m: float,
    lane_count: int,
    density: float,
    topk_per_category: int,
    max_trials_per_slot: int,
    export_format: str,
    design_rule_profile: str,
    city_context: str,
    target_street_type: str,
    policy_ckpt: Path | None,
    program_generator: str,
    program_ckpt: Path | None,
    layout_solver: str,
    allow_solver_fallback: bool,
    segment_length_m: float,
    policy_temperature: float,
    out_root: Path,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    scene_index = 0
    for query in queries:
        for seed in range(int(seed_start), int(seed_end) + 1):
            config = StreetComposeConfig(
                query=query,
                length_m=float(length_m),
                road_width_m=float(road_width_m),
                sidewalk_width_m=float(sidewalk_width_m),
                lane_count=int(lane_count),
                density=float(density),
                seed=int(seed),
                topk_per_category=int(topk_per_category),
                max_trials_per_slot=int(max_trials_per_slot),
                design_rule_profile=str(design_rule_profile),
                city_context=str(city_context),
                target_street_type=str(target_street_type),
                program_generator=str(program_generator),
                layout_solver=str(layout_solver),
                allow_solver_fallback=bool(allow_solver_fallback),
                segment_length_m=float(segment_length_m),
            )
            scene_id = f"{mode}_q{scene_index:03d}_s{seed:04d}"
            scene_index += 1
            scene_out = out_root / mode / scene_id
            result = compose_street_scene(
                config=config,
                manifest_path=manifest,
                artifacts_dir=artifacts,
                model_name=model_name,
                model_dir=model_dir,
                local_files_only=bool(local_files_only),
                device=device,
                export_format=export_format,
                out_dir=scene_out,
                placement_policy=mode,
                policy_ckpt=policy_ckpt,
                program_ckpt=program_ckpt,
                policy_temperature=float(policy_temperature),
            )

            layout_path = Path(result.outputs.get("scene_layout", "")).resolve()
            summary = {}
            if layout_path.exists():
                payload = json.loads(layout_path.read_text(encoding="utf-8"))
                summary = payload.get("summary", {}) or {}

            row = {
                "scene_id": scene_id,
                "query": query,
                "seed": int(seed),
                "policy_used": str(summary.get("policy_used", result.outputs.get("policy_used", mode))),
                "instance_count": _safe_int(summary, "instance_count") or int(result.instance_count),
                "dropped_slots": _safe_int(summary, "dropped_slots") or int(result.dropped_slots),
                "dropped_slot_rate": _safe_float(summary, "dropped_slot_rate"),
                "overlap_rate": _safe_float(summary, "overlap_rate"),
                "diversity_ratio": _safe_float(summary, "diversity_ratio"),
                "retrieval_top3_category_hit": _safe_float(summary, "retrieval_top3_category_hit"),
                "latency_ms_total": _safe_float(summary, "latency_ms_total"),
                "latency_ms_per_instance": _safe_float(summary, "latency_ms_per_instance"),
                "spacing_uniformity": _safe_float(summary, "spacing_uniformity"),
                "style_consistency": _safe_float(summary, "style_consistency"),
                "balance_score": _safe_float(summary, "balance_score"),
                "rule_satisfaction_rate": _safe_float(summary, "rule_satisfaction_rate"),
                "topology_validity": _safe_float(summary, "topology_validity"),
                "cross_section_feasibility": _safe_float(summary, "cross_section_feasibility"),
                "editability": _safe_float(summary, "editability"),
                "conflict_explainability": _safe_float(summary, "conflict_explainability"),
                "program_generator_used": str(summary.get("program_generator_used", "")),
                "layout_solver_used": str(summary.get("layout_solver_used", "")),
                "program_fallback_reason": str(summary.get("program_fallback_reason", "")),
                "solver_fallback_reason": str(summary.get("solver_fallback_reason", "")),
                "scene_layout_path": str(layout_path),
                "scene_glb": str(result.outputs.get("scene_glb", "")),
                "scene_ply": str(result.outputs.get("scene_ply", "")),
            }
            rows.append(row)
    return rows


def run_eval(args: argparse.Namespace) -> Dict[str, object]:
    queries = _load_queries(args.queries)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    design_rule_profile = str(getattr(args, "design_rule_profile", "balanced_complete_street_v1"))
    city_context = str(getattr(args, "city_context", "generic_city"))
    target_street_type = str(getattr(args, "target_street_type", "mixed_use"))

    policy_mode = str(args.placement_policy).strip().lower()
    compare_rule = bool(args.compare_rule) or policy_mode == "learned"

    learned_rows = _run_mode(
        mode=policy_mode,
        queries=queries,
        seed_start=args.seed_start,
        seed_end=args.seed_end,
        manifest=Path(args.manifest).resolve(),
        artifacts=Path(args.artifacts).resolve(),
        model_name=args.model_name,
        model_dir=Path(args.model_dir).resolve() if args.model_dir else None,
        local_files_only=bool(args.local_files_only),
        device=args.device,
        length_m=float(args.length_m),
        road_width_m=float(args.road_width_m),
        sidewalk_width_m=float(args.sidewalk_width_m),
        lane_count=int(args.lane_count),
        density=float(args.density),
        topk_per_category=int(args.topk_per_category),
        max_trials_per_slot=int(args.max_trials_per_slot),
        export_format=args.export_format,
        design_rule_profile=design_rule_profile,
        city_context=city_context,
        target_street_type=target_street_type,
        policy_ckpt=Path(args.policy_ckpt).resolve() if args.policy_ckpt else None,
        program_generator=str(getattr(args, "program_generator", "learned_v1")),
        program_ckpt=Path(str(getattr(args, "program_ckpt"))).resolve() if getattr(args, "program_ckpt", None) else None,
        layout_solver=str(getattr(args, "layout_solver", "milp_template_v1")),
        allow_solver_fallback=not bool(getattr(args, "no_solver_fallback", False)),
        segment_length_m=float(getattr(args, "segment_length_m", 12.0)),
        policy_temperature=float(args.policy_temperature),
        out_root=out_dir / "eval_scenes",
    )
    learned_summary = aggregate_scene_rows(learned_rows)

    comparison = {}
    rule_summary = None
    if compare_rule and policy_mode == "learned":
        rule_rows = _run_mode(
            mode="rule",
            queries=queries,
            seed_start=args.seed_start,
            seed_end=args.seed_end,
            manifest=Path(args.manifest).resolve(),
            artifacts=Path(args.artifacts).resolve(),
            model_name=args.model_name,
            model_dir=Path(args.model_dir).resolve() if args.model_dir else None,
            local_files_only=bool(args.local_files_only),
            device=args.device,
            length_m=float(args.length_m),
            road_width_m=float(args.road_width_m),
            sidewalk_width_m=float(args.sidewalk_width_m),
            lane_count=int(args.lane_count),
            density=float(args.density),
            topk_per_category=int(args.topk_per_category),
            max_trials_per_slot=int(args.max_trials_per_slot),
            export_format=args.export_format,
            design_rule_profile=design_rule_profile,
            city_context=city_context,
            target_street_type=target_street_type,
            policy_ckpt=None,
            program_generator=str(getattr(args, "program_generator", "learned_v1")),
            program_ckpt=Path(str(getattr(args, "program_ckpt"))).resolve() if getattr(args, "program_ckpt", None) else None,
            layout_solver=str(getattr(args, "layout_solver", "milp_template_v1")),
            allow_solver_fallback=not bool(getattr(args, "no_solver_fallback", False)),
            segment_length_m=float(getattr(args, "segment_length_m", 12.0)),
            policy_temperature=float(args.policy_temperature),
            out_root=out_dir / "eval_scenes",
        )
        rule_summary = aggregate_scene_rows(rule_rows)
        comparison = compare_mode_reports(rule_summary=rule_summary, learned_summary=learned_summary)

    csv_path = out_dir / "eval_per_scene.csv"
    if learned_rows:
        fieldnames = list(learned_rows[0].keys())
    else:
        fieldnames = [
            "scene_id",
            "query",
            "seed",
            "policy_used",
            "instance_count",
            "dropped_slots",
            "dropped_slot_rate",
            "overlap_rate",
            "diversity_ratio",
            "retrieval_top3_category_hit",
            "latency_ms_total",
            "latency_ms_per_instance",
            "scene_layout_path",
            "scene_glb",
            "scene_ply",
        ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in learned_rows:
            writer.writerow(row)

    report = {
        "mode": policy_mode,
        "summary": learned_summary,
        "rule_summary": rule_summary,
        "comparison_vs_rule": comparison,
        "scene_count": len(learned_rows),
        "queries_count": len(queries),
        "seed_range": [int(args.seed_start), int(args.seed_end)],
        "outputs": {
            "eval_per_scene": str(csv_path.resolve()),
            "eval_report": str((out_dir / "eval_report.json").resolve()),
        },
    }
    report_path = out_dir / "eval_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    return report


def main() -> int:
    args = parse_args()
    try:
        report = run_eval(args)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(f"Engineering eval failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
