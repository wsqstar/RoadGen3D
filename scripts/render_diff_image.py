#!/usr/bin/env python3
"""CLI for rendering 2D diff images between two scene layouts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.diff_render import render_delta_map, render_diff_overlay


def main() -> None:
    p = argparse.ArgumentParser(description="Render a 2D diff image for two layouts.")
    p.add_argument("--mode", choices=["overlay", "delta"], required=True)
    p.add_argument("--layout-a", required=True, help="Path to scene_layout A")
    p.add_argument("--layout-b", required=True, help="Path to scene_layout B")
    p.add_argument("--out", required=True, help="Output PNG path")
    args = p.parse_args()

    if args.mode == "overlay":
        render_diff_overlay(args.layout_a, args.layout_b, args.out)
    else:
        render_delta_map(args.layout_a, args.layout_b, args.out)

    print(json.dumps({"ok": True, "path": args.out}))


if __name__ == "__main__":
    main()
