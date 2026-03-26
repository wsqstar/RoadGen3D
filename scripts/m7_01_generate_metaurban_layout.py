#!/usr/bin/env python3
"""Generate a RoadGen3D-side MetaUrban procedural road-layout payload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.metaurban_procedural import (  # noqa: E402
    MetaUrbanProceduralConfig,
    write_metaurban_layout_payload,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a MetaUrban-style procedural road graph for RoadGen3D.")
    parser.add_argument("--out", type=Path, default=Path("artifacts/metaurban_procedural/layout.json"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--block-count", type=int, default=6)
    parser.add_argument("--sequence", type=str, default="")
    parser.add_argument("--lane-count", type=int, default=2)
    parser.add_argument("--lane-width-m", type=float, default=3.5)
    parser.add_argument("--sidewalk-width-m", type=float, default=2.5)
    parser.add_argument("--segment-length-m", type=float, default=12.0)
    parser.add_argument("--entrance-length-m", type=float, default=10.0)
    parser.add_argument("--straight-length-m", type=float, default=28.0)
    parser.add_argument("--curve-radius-m", type=float, default=16.0)
    parser.add_argument("--curve-angle-deg", type=float, default=60.0)
    parser.add_argument("--intersection-span-m", type=float, default=18.0)
    parser.add_argument("--branch-length-m", type=float, default=24.0)
    parser.add_argument("--start-heading-deg", type=float, default=0.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = MetaUrbanProceduralConfig(
        seed=int(args.seed),
        block_count=int(args.block_count),
        block_sequence=str(args.sequence or ""),
        lane_count=int(args.lane_count),
        lane_width_m=float(args.lane_width_m),
        sidewalk_width_m=float(args.sidewalk_width_m),
        segment_length_m=float(args.segment_length_m),
        entrance_length_m=float(args.entrance_length_m),
        straight_length_m=float(args.straight_length_m),
        curve_radius_m=float(args.curve_radius_m),
        curve_angle_deg=float(args.curve_angle_deg),
        intersection_span_m=float(args.intersection_span_m),
        branch_length_m=float(args.branch_length_m),
        start_heading_deg=float(args.start_heading_deg),
    )
    payload = write_metaurban_layout_payload(args.out, config)
    print(json.dumps(payload["summary"], indent=2, ensure_ascii=True))
    print(f"Layout JSON: {Path(args.out).expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
