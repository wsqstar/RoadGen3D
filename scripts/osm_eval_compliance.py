#!/usr/bin/env python3
"""M5 Step 10: Evaluate compliance across generated scene layouts."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from roadgen3d.compliance_eval import evaluate_compliance_batch, write_compliance_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate M5 POI compliance across scene layouts.")
    parser.add_argument(
        "--scene-dir",
        type=str,
        required=True,
        help="Directory containing scene_layout.json files (searched recursively).",
    )
    parser.add_argument("--out-dir", type=str, default="artifacts/m5", help="Output directory for reports.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    scene_dir = Path(args.scene_dir)
    if not scene_dir.is_dir():
        print(f"ERROR: --scene-dir not found: {scene_dir}", file=sys.stderr)
        sys.exit(1)

    layout_paths = sorted(scene_dir.rglob("scene_layout.json"))
    if not layout_paths:
        print(f"WARNING: no scene_layout.json found under {scene_dir}", file=sys.stderr)
        sys.exit(0)

    print(f"Found {len(layout_paths)} scene_layout.json files.")

    report, per_scene = evaluate_compliance_batch(layout_paths)
    report_path, csv_path = write_compliance_report(report, per_scene, Path(args.out_dir))

    print(f"\n--- Compliance Report ---")
    print(f"scenes             : {report['scene_count']}")
    print(f"total instances    : {report['total_instances']}")
    print(f"compliance rate    : {report['compliance_rate_total']:.4f}")
    print(f"violations total   : {report['violations_total']}")
    print(f"avg penalty        : {report['avg_constraint_penalty']:.4f}")
    print(f"avg feasibility    : {report['avg_feasibility_score']:.4f}")
    if report.get("rule_violation_counts"):
        print(f"rule violations    :")
        for rule_name, count in sorted(report["rule_violation_counts"].items()):
            print(f"  {rule_name}: {count}")
    print(f"\nReport saved: {report_path}")
    print(f"CSV saved   : {csv_path}")


if __name__ == "__main__":
    main()
