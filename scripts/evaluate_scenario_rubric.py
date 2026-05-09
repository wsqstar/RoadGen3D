#!/usr/bin/env python3
"""Evaluate scenario-design layouts with the deterministic Scenario Rubric v1."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
_MPL_DIR = Path(tempfile.gettempdir()) / "roadgen3d_matplotlib"
_CACHE_DIR = Path(tempfile.gettempdir()) / "roadgen3d_cache"
_MPL_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_MPL_DIR))
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE_DIR))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.json_safe import make_json_safe  # noqa: E402
from roadgen3d.scenario_rubric import (  # noqa: E402
    DEFAULT_SCENARIO_RUBRIC_PATH,
    RUBRIC_BATCH_SCHEMA_VERSION,
    ScenarioRubricEvaluator,
    build_calibration_table,
    missing_layout_evaluation,
    summarize_scenario_evaluations,
    write_evaluations_csv,
    write_expert_scoring_template,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--layout", type=Path, help="Single scene_layout.json path.")
    input_group.add_argument("--run", type=Path, help="Scenario run directory or manifest.json path.")
    input_group.add_argument("--runs-root", type=Path, help="Directory containing scenario_design_runs/*/manifest.json.")
    parser.add_argument("--scenario-id", default="", help="Required with --layout.")
    parser.add_argument("--rubric", type=Path, default=DEFAULT_SCENARIO_RUBRIC_PATH, help="Rubric JSON path.")
    parser.add_argument("--out", type=Path, default=None, help="Write JSON result to this path.")
    parser.add_argument("--csv", type=Path, default=None, help="Write item-level evaluation CSV.")
    parser.add_argument("--calibration-csv", type=Path, default=None, help="Write scenario-level calibration CSV.")
    parser.add_argument("--expert-template-csv", type=Path, default=None, help="Write blank expert scoring template CSV.")
    parser.add_argument("--force-disabled", action="store_true", help="Evaluate disabled/future-ready scenarios instead of NotApplicable.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    evaluator = ScenarioRubricEvaluator(rubric_path=args.rubric)
    records: list[Dict[str, Any]]
    mode: str

    if args.layout:
        if not args.scenario_id:
            print("--scenario-id is required with --layout", file=sys.stderr)
            return 2
        result = evaluator.evaluate_layout_path(args.layout, args.scenario_id, force_disabled=args.force_disabled)
        records = [{"scenario_id": args.scenario_id, "scene_layout_path": str(args.layout), "scenario_evaluation": result}]
        mode = "layout"
    elif args.run:
        records = evaluate_run(args.run, evaluator=evaluator, force_disabled=args.force_disabled)
        mode = "run"
    else:
        records = []
        for manifest_path in sorted(Path(args.runs_root).expanduser().glob("*/manifest.json")):
            records.extend(evaluate_run(manifest_path, evaluator=evaluator, force_disabled=args.force_disabled))
        mode = "runs_root"

    payload = make_json_safe({
        "schema_version": RUBRIC_BATCH_SCHEMA_VERSION,
        "mode": mode,
        "record_count": len(records),
        "evaluation_summary": summarize_scenario_evaluations(records),
        "calibration": build_calibration_table(records),
        "records": records,
    })

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.csv:
        write_evaluations_csv(records, args.csv)
    if args.expert_template_csv:
        write_expert_scoring_template(records, args.expert_template_csv)
    if args.calibration_csv:
        write_calibration_csv(payload["calibration"], args.calibration_csv)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def evaluate_run(
    run_path: Path,
    *,
    evaluator: ScenarioRubricEvaluator,
    force_disabled: bool = False,
) -> list[Dict[str, Any]]:
    manifest_path = _manifest_path(run_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_id = str(manifest.get("run_id") or manifest_path.parent.name)
    records: list[Dict[str, Any]] = []
    for item in manifest.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        scenario_id = str(item.get("scenario_id") or "")
        layout_path = str(item.get("scene_layout_path") or "")
        record = {**dict(item), "run_id": run_id}
        if str(item.get("status") or "") != "succeeded":
            continue
        if not scenario_id:
            continue
        try:
            result = evaluator.evaluate_layout_path(layout_path, scenario_id, force_disabled=force_disabled)
        except Exception as exc:
            result = missing_layout_evaluation(scenario_id, layout_path, str(exc))
        record["scenario_evaluation"] = result
        records.append(record)
    return records


def write_calibration_csv(calibration: Mapping[str, Any], path: Path) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "scenario_id",
        "n",
        "pass",
        "review",
        "fail",
        "not_applicable",
        "mean_total_score",
        "min_total_score",
        "max_total_score",
        "gate_failure_rate",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in calibration.get("rows") or []:
            if not isinstance(row, Mapping):
                continue
            counts = dict(row.get("status_counts") or {})
            writer.writerow({
                "scenario_id": row.get("scenario_id", ""),
                "n": row.get("n", 0),
                "pass": counts.get("Pass", 0),
                "review": counts.get("Review", 0),
                "fail": counts.get("Fail", 0),
                "not_applicable": counts.get("NotApplicable", 0),
                "mean_total_score": row.get("mean_total_score", ""),
                "min_total_score": row.get("min_total_score", ""),
                "max_total_score": row.get("max_total_score", ""),
                "gate_failure_rate": row.get("gate_failure_rate", ""),
            })


def _manifest_path(path: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_dir():
        candidate = candidate / "manifest.json"
    if not candidate.exists():
        raise FileNotFoundError(f"Scenario run manifest not found: {candidate}")
    return candidate


if __name__ == "__main__":
    raise SystemExit(main())
