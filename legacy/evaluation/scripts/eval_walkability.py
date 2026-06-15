#!/usr/bin/env python3
"""Standalone helper to compute walkability indicators for a scene layout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.eval_quality import compute_walkability_indicators, write_walkability_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute walkability indicators for a scene layout JSON.")
    parser.add_argument("--layout", type=Path, required=True, help="Path to scene_layout.json")
    parser.add_argument("--out", type=Path, default=None, help="Output JSON path (default: alongside layout)")
    return parser.parse_args()


def _load_layout(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    layout_path = Path(args.layout).resolve()
    if not layout_path.exists():
        print(f"Layout file not found: {layout_path}", file=sys.stderr)
        return 1
    payload = _load_layout(layout_path)
    result = compute_walkability_indicators(payload)
    out_path = Path(args.out).resolve() if args.out else layout_path.with_name("walkability.json")
    write_walkability_report(result, out_path)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=True))
    print(f"Walkability report written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
