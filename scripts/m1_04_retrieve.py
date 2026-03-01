#!/usr/bin/env python3
"""Run text query retrieval on persisted FAISS artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.embedder import ClipTextEmbedder, ModelLoadError  # noqa: E402
from roadgen3d.index_store import FaissIndexStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retrieve top-k assets for one text query.")
    parser.add_argument("--query", required=True, help="Text query.")
    parser.add_argument("--topk", type=int, default=3, help="Top-k retrieval.")
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/m1"), help="Artifacts directory.")
    parser.add_argument("--model-name", default="openai/clip-vit-base-patch32", help="HF model id.")
    parser.add_argument("--model-dir", type=Path, default=None, help="Local model directory override.")
    parser.add_argument("--local-files-only", action="store_true", help="Force offline local model loading.")
    parser.add_argument("--device", default="cpu", help="Torch device, default cpu.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        store = FaissIndexStore.load(
            index_path=args.artifacts / "index_ip.faiss",
            id_map_path=args.artifacts / "id_map.json",
        )
        embedder = ClipTextEmbedder(
            model_name=args.model_name,
            model_dir=args.model_dir,
            local_files_only=args.local_files_only,
            device=args.device,
        )
        query_vec = embedder.encode_texts([args.query])
        hits = store.search(query_vec, topk=args.topk)[0]
        output = {
            "query": args.query,
            "topk": args.topk,
            "hits": [{"asset_id": hit.asset_id, "score": hit.score} for hit in hits],
        }
        out_path = args.artifacts / "last_retrieval.json"
        out_path.write_text(json.dumps(output, indent=2, ensure_ascii=True), encoding="utf-8")
    except ModelLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Retrieval failed: {exc}", file=sys.stderr)
        return 1

    print(f"Query: {args.query}")
    for idx, hit in enumerate(output["hits"], start=1):
        print(f"{idx}. {hit['asset_id']} (score={hit['score']:.4f})")
    print(f"Saved retrieval report to: {args.artifacts / 'last_retrieval.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

