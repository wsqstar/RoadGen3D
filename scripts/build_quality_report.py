#!/usr/bin/env python3
"""Build a layered generation-quality report for RoadGen3D scene batches."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
_MPL_DIR = Path(tempfile.gettempdir()) / "roadgen3d_matplotlib"
_MPL_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_MPL_DIR))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.evaluation_report import build_quality_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("search_root", help="Batch artifact directory or a scene_layout.json path.")
    parser.add_argument("--out-dir", default="", help="Directory for quality_report.json and quality_per_scene.csv.")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of scene_layout.json files.")
    parser.add_argument(
        "--include-llm-visual",
        action="store_true",
        help="Run LLM visual evaluators when configured; off by default for deterministic batch reports.",
    )
    args = parser.parse_args()

    result = build_quality_report(
        args.search_root,
        out_dir=args.out_dir or None,
        include_llm_visual=bool(args.include_llm_visual),
        limit=args.limit,
    )
    print(json.dumps(result["report"], indent=2, ensure_ascii=True))
    if result.get("outputs"):
        print(json.dumps(result["outputs"], indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
