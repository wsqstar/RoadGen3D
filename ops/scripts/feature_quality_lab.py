#!/usr/bin/env python3
"""Create a deterministic RoadGen3D single-feature experiment manifest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.feature_quality_lab import (  # noqa: E402
    FEATURE_TARGETS,
    build_feature_experiment,
    write_experiment_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(FEATURE_TARGETS), default="curb_ramp")
    parser.add_argument("--experiment-id", default="curb_ramp_baseline_v1")
    parser.add_argument("--brief", default="A clean independent road-to-sidewalk ramp with no gaps or overlap.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/feature_quality/experiment.json"))
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = FEATURE_TARGETS[args.target]
    baseline = {field: value for field, value in _baseline_patch(args.target).items() if field in target.allowed_fields}
    experiment = build_feature_experiment(
        experiment_id=args.experiment_id,
        target_id=args.target,
        brief=args.brief,
        fixed_patch={"length_m": 20.0, "street_furniture_profile": "none"},
        variants=[{"variant_id": "baseline", "label": "Baseline", "patch": baseline}],
        seed=args.seed,
    )
    output = write_experiment_manifest(experiment, ROOT / args.output)
    print(output)
    return 0


def _baseline_patch(target_id: str) -> dict[str, object]:
    return {
        "curb_ramp_enabled": True,
        "curb_ramp_side": "right",
        "curb_ramp_position_ratio": 0.5,
        "bus_stop_enabled": True,
        "bus_stop_placement": "curbside",
        "building_density": 0.55,
        "building_max_per_100m": 10.0,
        "building_representation": "asset",
        "surrounding_building_mode": "grid_growth",
        "infill_policy": "balanced",
        "building_height_mode": "theme_random",
        "style_preset": "civic_clean_v1",
        "scene_texture_mode": "topdown_tiles_v1",
        "furniture_style": "civic_clean",
    }


if __name__ == "__main__":
    raise SystemExit(main())
