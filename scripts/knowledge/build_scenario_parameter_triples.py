#!/usr/bin/env python3
"""Build the scenario-parameter triples JSONL artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.knowledge.scenario_parameters import (  # noqa: E402
    build_preset_triples,
    parse_suitability_matrix_triples,
    stable_sort_triples,
    write_triples_jsonl,
    write_triples_metadata,
)
from roadgen3d.presets import SCENE_PRESETS  # noqa: E402


DEFAULT_MATRIX_PATH = (
    ROOT
    / "knowledge"
    / "graphRAG"
    / "graphrag_quickstart"
    / "input"
    / "003_1.1.2_DESIGN_TREATMENT_SUITABILITY_MATRIX.txt"
)
DEFAULT_OUT_PATH = ROOT / "knowledge" / "scenario_parameter_triples.jsonl"
DEFAULT_METADATA_PATH = ROOT / "knowledge" / "scenario_parameter_triples.metadata.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build RoadGen3D scenario-parameter triple artifacts.")
    parser.add_argument("--matrix-path", type=Path, default=DEFAULT_MATRIX_PATH)
    parser.add_argument("--out-path", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--metadata-path", type=Path, default=DEFAULT_METADATA_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    matrix_path = args.matrix_path.expanduser().resolve()
    if not matrix_path.exists():
        raise RuntimeError(f"Matrix source not found: {matrix_path}")

    matrix_triples = parse_suitability_matrix_triples(
        matrix_path.read_text(encoding="utf-8"),
        source_path=matrix_path,
    )
    preset_triples = build_preset_triples(SCENE_PRESETS)
    triples = stable_sort_triples([*matrix_triples, *preset_triples])
    summary = write_triples_jsonl(args.out_path, triples)
    metadata = write_triples_metadata(
        args.metadata_path,
        triples_path=args.out_path,
        triples=triples,
        extra={
            "matrix_path": str(matrix_path),
            "matrix_triple_count": len(matrix_triples),
            "preset_triple_count": len(preset_triples),
            "generated_by": "scripts/knowledge/build_scenario_parameter_triples.py",
        },
    )
    print(json.dumps({"summary": summary, "metadata": metadata}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
