#!/usr/bin/env python3
"""Export voxel_bin.npy to mesh files (.glb/.ply)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.voxel_export import export_voxel_meshes  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export voxel bin occupancy to GLB/PLY mesh.")
    parser.add_argument("--voxel-bin", type=Path, required=True, help="Path to voxel_bin.npy.")
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/m1"), help="Output directory.")
    parser.add_argument("--stem", default="voxel_export", help="Output filename stem.")
    parser.add_argument("--voxel-size", type=float, default=0.1, help="Voxel edge size in world units.")
    parser.add_argument("--method", choices=["marching_cubes", "cubes"], default="marching_cubes")
    parser.add_argument("--export-format", choices=["glb", "ply", "both"], default="both")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if not args.voxel_bin.exists():
            raise FileNotFoundError(f"voxel_bin file not found: {args.voxel_bin}")
        voxel_bin = np.load(args.voxel_bin)
        info = export_voxel_meshes(
            voxel_bin=voxel_bin,
            out_dir=args.out_dir,
            stem=args.stem,
            voxel_size=args.voxel_size,
            method=args.method,
            export_format=args.export_format,
        )
        meta_path = args.out_dir / f"{args.stem}_mesh_export.json"
        meta_path.write_text(json.dumps(info, indent=2, ensure_ascii=True), encoding="utf-8")
    except Exception as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1

    print(f"Mesh export complete: {meta_path}")
    if info.get("mesh_glb"):
        print(f"GLB: {info['mesh_glb']}")
    if info.get("mesh_ply"):
        print(f"PLY: {info['mesh_ply']}")
    print(f"Method: {info.get('mesh_method', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

