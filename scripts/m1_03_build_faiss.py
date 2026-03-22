#!/usr/bin/env python3
"""Build a FAISS IndexFlatIP from precomputed text embeddings."""

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

from roadgen3d.index_store import FaissIndexStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and persist FAISS retrieval index.")
    parser.add_argument("--embeds", type=Path, default=Path("artifacts/m1/asset_text_embeds.npy"))
    parser.add_argument("--asset-ids", type=Path, default=Path("artifacts/m1/asset_ids.json"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/m1"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        embeddings = np.load(args.embeds).astype(np.float32)
        asset_ids = json.loads(args.asset_ids.read_text(encoding="utf-8"))
        store = FaissIndexStore.build(embeddings=embeddings, asset_ids=asset_ids)
        index_path = args.out / "index_ip.faiss"
        id_map_path = args.out / "id_map.json"
        store.save(index_path=index_path, id_map_path=id_map_path)
    except Exception as exc:
        print(f"Failed to build FAISS index: {exc}", file=sys.stderr)
        return 1

    print(f"Saved index to: {args.out / 'index_ip.faiss'}")
    print(f"Indexed assets: {store.ntotal}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

