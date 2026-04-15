#!/usr/bin/env python3
"""Embed asset descriptions using CLIP text projection features."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.embedder import ClipTextEmbedder, ModelLoadError  # noqa: E402
from roadgen3d.latent_store import load_asset_records  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embed asset text descriptions.")
    parser.add_argument("--assets", type=Path, default=Path("data/m1/assets.jsonl"), help="Assets metadata path.")
    parser.add_argument("--out", type=Path, default=Path("artifacts/m1"), help="Output directory.")
    parser.add_argument("--model-name", default="openai/clip-vit-base-patch32", help="HF model id.")
    parser.add_argument("--model-dir", type=Path, default=None, help="Local model directory override.")
    parser.add_argument("--local-files-only", action="store_true", help="Force offline local model loading.")
    parser.add_argument("--device", default="cpu", help="Torch device, default cpu.")
    return parser.parse_args()


def run(args: argparse.Namespace) -> Dict[str, object]:
    records = load_asset_records(args.assets)
    descriptions = [record.description for record in records]
    asset_ids = [record.asset_id for record in records]

    embedder = ClipTextEmbedder(
        model_name=args.model_name,
        model_dir=args.model_dir,
        local_files_only=args.local_files_only,
        device=args.device,
    )
    embeddings = embedder.encode_texts(descriptions)
    if embeddings.shape[0] != len(asset_ids):
        raise RuntimeError("Embedding row count does not match asset count.")

    args.out.mkdir(parents=True, exist_ok=True)
    embeds_path = args.out / "asset_text_embeds.npy"
    ids_path = args.out / "asset_ids.json"
    meta_path = args.out / "embed_meta.json"
    np.save(embeds_path, embeddings)
    ids_path.write_text(json.dumps(asset_ids, indent=2, ensure_ascii=True), encoding="utf-8")
    meta = {
        "num_assets": len(asset_ids),
        "embedding_dim": int(embeddings.shape[1]) if embeddings.ndim == 2 else 0,
        "model_source": embedder.model_source,
        "projection_dim": int(embedder.projection_dim),
        "local_files_only": bool(args.local_files_only),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8")
    return meta


def main() -> int:
    args = parse_args()
    try:
        meta = run(args)
    except ModelLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Embedding failed: {exc}", file=sys.stderr)
        return 1

    print(
        "Saved embeddings to artifacts: "
        f"{args.out / 'asset_text_embeds.npy'} (N={meta['num_assets']}, D={meta['embedding_dim']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

