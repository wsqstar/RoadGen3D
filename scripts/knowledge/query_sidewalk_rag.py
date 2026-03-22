#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quick CLI to query the Sidewalk Area RAG index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

import faiss
import numpy as np
import sys
from sentence_transformers import SentenceTransformer


def load_chunks(chunks_path: Path) -> List[dict]:
    chunks: List[dict] = []
    with chunks_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                chunks.append(json.loads(line))
    return chunks


def load_index(index_path: Path) -> faiss.Index:
    return faiss.read_index(str(index_path))


def search(query: str, model_name: str, chunks: List[dict], index: faiss.Index, topk: int) -> List[dict]:
    model = SentenceTransformer(model_name)
    vec = model.encode([query], convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)
    scores, idxs = index.search(vec, topk)
    results: List[dict] = []
    for score, idx in zip(scores[0], idxs[0]):
        if idx < 0 or idx >= len(chunks):
            continue
        payload = dict(chunks[idx])
        payload["score"] = float(score)
        results.append(payload)
    return results


def parse_args() -> argparse.Namespace:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Query the Sidewalk Area RAG index.")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--artifact-dir", type=Path, default=Path("knowledge/sidewalk_area"))
    parser.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--topk", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    chunks = load_chunks(args.artifact_dir / "chunks.jsonl")
    index = load_index(args.artifact_dir / "index.faiss")
    results = search(args.query, args.model, chunks, index, args.topk)
    for item in results:
        pages = f"pages {item['page_start']}-{item['page_end']}"
        zones = ",".join(item.get("zones") or []) or "(zones unspecified)"
        print(f"[{item['score']:.3f}] {item['heading']} ({pages}; zones: {zones})")
        print(item["text"])
        print("-" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
