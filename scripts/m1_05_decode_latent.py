#!/usr/bin/env python3
"""Decode one latent tensor into voxel probability/binary volumes."""

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

from roadgen3d.decoder import PlaceholderVoxelDecoder  # noqa: E402
from roadgen3d.latent_store import LatentStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Decode one latent by asset ID.")
    parser.add_argument("--asset-id", required=True, help="Asset ID to decode.")
    parser.add_argument("--data-dir", type=Path, default=Path("data/m1"), help="Data directory.")
    parser.add_argument("--assets", type=Path, default=None, help="Asset metadata path.")
    parser.add_argument("--out", type=Path, default=Path("artifacts/m1"), help="Output directory.")
    parser.add_argument("--resolution", type=int, default=64, help="Output voxel resolution.")
    parser.add_argument("--threshold", type=float, default=0.5, help="Binarization threshold.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    assets_path = args.assets or (args.data_dir / "assets.jsonl")
    try:
        store = LatentStore(assets_jsonl_path=assets_path)
        latent = store.load(args.asset_id)
        decoder = PlaceholderVoxelDecoder(resolution=args.resolution, threshold=args.threshold)
        decoded = decoder.decode(latent)
        if len(decoded) == 3:
            voxel_prob, voxel_bin, _ = decoded
        else:
            voxel_prob, voxel_bin = decoded

        args.out.mkdir(parents=True, exist_ok=True)
        prob_path = args.out / "voxel_prob.npy"
        bin_path = args.out / "voxel_bin.npy"
        meta_path = args.out / "decode_meta.json"
        np.save(prob_path, voxel_prob)
        np.save(bin_path, voxel_bin)
        meta = {
            "asset_id": args.asset_id,
            "latent_shape": list(np.asarray(latent).shape),
            "voxel_shape": list(voxel_bin.shape),
            "occupied_voxels": int(voxel_bin.sum()),
            "threshold": float(args.threshold),
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8")
    except Exception as exc:
        print(f"Decoding failed: {exc}", file=sys.stderr)
        return 1

    print(f"Decoded asset: {args.asset_id}")
    print(f"Saved voxel probabilities: {prob_path}")
    print(f"Saved binary voxels: {bin_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
