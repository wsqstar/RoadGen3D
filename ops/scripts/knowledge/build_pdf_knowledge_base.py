#!/usr/bin/env python3
"""Build a generic FAISS-backed PDF knowledge base for RoadGen3D."""

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

from roadgen3d.knowledge import ClipTextEmbedderAdapter, SentenceTransformerEmbedder, build_pdf_knowledge_base  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a PDF-backed knowledge base for the design assistant.")
    parser.add_argument(
        "--pdf-path",
        type=Path,
        default=Path("knowledge/book/Complete streets design guide.pdf"),
        help="Source PDF to index.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("knowledge/complete_streets"),
        help="Output directory for chunks, metadata, embeddings, and FAISS index.",
    )
    parser.add_argument(
        "--embedder-backend",
        choices=["auto", "sentence_transformers", "clip"],
        default="auto",
        help="Embedding backend for document retrieval.",
    )
    parser.add_argument(
        "--clip-model-dir",
        type=Path,
        default=Path("models/clip-vit-base-patch32"),
        help="Local CLIP model directory used for offline fallback.",
    )
    parser.add_argument("--target-chars", type=int, default=900, help="Approximate chunk size.")
    parser.add_argument("--overlap-chars", type=int, default=160, help="Approximate chunk overlap.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    embedder = None
    if args.embedder_backend == "sentence_transformers":
        embedder = SentenceTransformerEmbedder()
    elif args.embedder_backend == "clip":
        embedder = ClipTextEmbedderAdapter(model_dir=args.clip_model_dir, local_files_only=True, device="cpu")
    if args.embedder_backend == "auto":
        try:
            embedder = SentenceTransformerEmbedder()
            artifacts = build_pdf_knowledge_base(
                pdf_path=args.pdf_path,
                output_dir=args.out_dir,
                embedder=embedder,
                target_chars=int(args.target_chars),
                overlap_chars=int(args.overlap_chars),
            )
        except Exception:
            embedder = ClipTextEmbedderAdapter(model_dir=args.clip_model_dir, local_files_only=True, device="cpu")
            artifacts = build_pdf_knowledge_base(
                pdf_path=args.pdf_path,
                output_dir=args.out_dir,
                embedder=embedder,
                target_chars=int(args.target_chars),
                overlap_chars=int(args.overlap_chars),
            )
    else:
        artifacts = build_pdf_knowledge_base(
            pdf_path=args.pdf_path,
            output_dir=args.out_dir,
            embedder=embedder,
            target_chars=int(args.target_chars),
            overlap_chars=int(args.overlap_chars),
        )
    print(json.dumps(artifacts.to_dict(), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
