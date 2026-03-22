"""Compliance evaluation for M5 POI-constrained scene layouts."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)


def compute_compliance(placements: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute compliance metrics from a single scene's placement list.

    Parameters
    ----------
    placements : list of placement dicts (from scene_layout.json ``placements``).

    Returns
    -------
    Dict with compliance statistics.
    """
    if not placements:
        return {
            "compliance_rate_total": 0.0,
            "violations_total": 0,
            "rule_violation_counts": {},
            "avg_constraint_penalty": 0.0,
            "avg_feasibility_score": 1.0,
        }

    violations_total = 0
    total_penalty = 0.0
    total_feasibility = 0.0
    rule_counts: Dict[str, int] = {}

    for p in placements:
        violated = p.get("violated_rules", []) or []
        if violated:
            violations_total += 1
        for rule_name in violated:
            rule_counts[rule_name] = rule_counts.get(rule_name, 0) + 1
        total_penalty += float(p.get("constraint_penalty", 0.0))
        total_feasibility += float(p.get("feasibility_score", 1.0))

    n = len(placements)
    return {
        "compliance_rate_total": 1.0 - (violations_total / n),
        "violations_total": violations_total,
        "rule_violation_counts": rule_counts,
        "avg_constraint_penalty": total_penalty / n,
        "avg_feasibility_score": total_feasibility / n,
    }


def evaluate_compliance_batch(
    scene_layout_paths: Sequence[Path],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Evaluate compliance across multiple scene_layout.json files.

    Returns
    -------
    (aggregated_report, per_scene_rows) where *per_scene_rows* is suitable
    for writing to CSV.
    """
    per_scene_rows: List[Dict[str, Any]] = []
    total_compliance = 0.0
    total_penalty = 0.0
    total_feasibility = 0.0
    total_violations = 0
    total_instances = 0
    agg_rule_counts: Dict[str, int] = {}

    for path in scene_layout_paths:
        path = Path(path)
        if not path.exists():
            logger.warning("Scene layout not found, skipping: %s", path)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        placements = data.get("placements", [])
        summary = data.get("summary", {})

        comp = compute_compliance(placements)
        n = len(placements)

        row = {
            "scene_path": str(path),
            "instance_count": n,
            "compliance_rate_total": comp["compliance_rate_total"],
            "violations_total": comp["violations_total"],
            "avg_constraint_penalty": comp["avg_constraint_penalty"],
            "avg_feasibility_score": comp["avg_feasibility_score"],
            # carry forward engineering metrics if present
            "overlap_rate": float(summary.get("overlap_rate", 0.0)),
            "dropped_slot_rate": float(summary.get("dropped_slot_rate", 0.0)),
            "diversity_ratio": float(summary.get("diversity_ratio", 0.0)),
            "layout_mode": str(summary.get("layout_mode", "")),
            "constraint_mode": str(summary.get("constraint_mode", "")),
        }
        per_scene_rows.append(row)

        total_compliance += comp["compliance_rate_total"]
        total_penalty += comp["avg_constraint_penalty"]
        total_feasibility += comp["avg_feasibility_score"]
        total_violations += comp["violations_total"]
        total_instances += n
        for rname, cnt in comp["rule_violation_counts"].items():
            agg_rule_counts[rname] = agg_rule_counts.get(rname, 0) + cnt

    scene_count = len(per_scene_rows)
    report: Dict[str, Any] = {
        "scene_count": scene_count,
        "total_instances": total_instances,
        "compliance_rate_total": total_compliance / scene_count if scene_count else 0.0,
        "violations_total": total_violations,
        "rule_violation_counts": agg_rule_counts,
        "avg_constraint_penalty": total_penalty / scene_count if scene_count else 0.0,
        "avg_feasibility_score": total_feasibility / scene_count if scene_count else 1.0,
    }
    return report, per_scene_rows


def write_compliance_report(
    report: Dict[str, Any],
    per_scene_rows: List[Dict[str, Any]],
    out_dir: Path,
) -> Tuple[Path, Path]:
    """Write compliance_report.json and compliance_per_scene.csv."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = out_dir / "compliance_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    csv_path = out_dir / "compliance_per_scene.csv"
    if per_scene_rows:
        fieldnames = list(per_scene_rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(per_scene_rows)
    else:
        csv_path.write_text("", encoding="utf-8")

    logger.info("Compliance report: %s", report_path)
    logger.info("Per-scene CSV: %s", csv_path)
    return report_path, csv_path


def merge_compliance_engineering(
    compliance: Dict[str, Any],
    engineering: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge compliance and engineering metric dicts into one report."""
    merged = dict(engineering)
    merged.update(compliance)
    return merged
