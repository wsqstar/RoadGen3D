#!/usr/bin/env python3
"""Compose a real street scene with multi-asset placement (M3 MVP)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.embedder import ModelLoadError  # noqa: E402
from roadgen3d.street_layout import compose_street_scene  # noqa: E402
from roadgen3d.types import StreetComposeConfig  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compose a street scene from real assets.")
    parser.add_argument("--query", required=True, help="Scene-level text query.")
    parser.add_argument("--manifest", type=Path, default=Path("data/real/real_assets_manifest.jsonl"))
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/real"))
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/real"))
    parser.add_argument("--model-name", default="openai/clip-vit-base-patch32")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--length-m", type=float, default=80.0)
    parser.add_argument("--road-width-m", type=float, default=8.0)
    parser.add_argument("--sidewalk-width-m", type=float, default=2.5)
    parser.add_argument("--lane-count", type=int, default=2)
    parser.add_argument("--density", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--topk-per-category", type=int, default=20)
    parser.add_argument("--max-trials-per-slot", type=int, default=30)
    parser.add_argument("--export-format", choices=["glb", "ply", "both"], default="both")
    parser.add_argument("--placement-policy", choices=["rule", "learned"], default="rule")
    parser.add_argument("--policy-ckpt", type=Path, default=None)
    parser.add_argument("--policy-temperature", type=float, default=0.12)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = StreetComposeConfig(
        query=args.query,
        length_m=float(args.length_m),
        road_width_m=float(args.road_width_m),
        sidewalk_width_m=float(args.sidewalk_width_m),
        lane_count=int(args.lane_count),
        density=float(args.density),
        seed=int(args.seed),
        topk_per_category=int(args.topk_per_category),
        max_trials_per_slot=int(args.max_trials_per_slot),
    )
    try:
        result = compose_street_scene(
            config=config,
            manifest_path=args.manifest,
            artifacts_dir=args.artifacts,
            model_name=args.model_name,
            model_dir=args.model_dir,
            local_files_only=bool(args.local_files_only),
            device=args.device,
            export_format=args.export_format,
            out_dir=args.out_dir,
            placement_policy=args.placement_policy,
            policy_ckpt=args.policy_ckpt,
            policy_temperature=float(args.policy_temperature),
        )
    except ModelLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Street compose failed: {exc}", file=sys.stderr)
        return 1

    layout_path = result.outputs.get("scene_layout", "")
    if layout_path:
        try:
            payload = json.loads(Path(layout_path).read_text(encoding="utf-8"))
            print(json.dumps(payload["summary"], indent=2, ensure_ascii=True))
        except Exception:
            pass
    print(f"Instances: {result.instance_count}")
    print(f"Dropped slots: {result.dropped_slots}")
    if result.outputs.get("scene_glb"):
        print(f"Scene GLB: {result.outputs['scene_glb']}")
    if result.outputs.get("scene_ply"):
        print(f"Scene PLY: {result.outputs['scene_ply']}")
    if result.outputs.get("scene_layout"):
        print(f"Scene Layout: {result.outputs['scene_layout']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
