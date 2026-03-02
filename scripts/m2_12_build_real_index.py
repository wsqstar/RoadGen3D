#!/usr/bin/env python3
"""Build CLIP+FAISS index for real asset manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.embedder import ClipTextEmbedder, ModelLoadError  # noqa: E402
from roadgen3d.index_store import FaissIndexStore  # noqa: E402


def _resolve_manifest_path(path_text: object, base_dir: Path) -> str:
    path = Path(str(path_text)).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return str(path)


def load_real_manifest(path: Path) -> List[Dict[str, object]]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    rows: List[Dict[str, object]] = []
    base_dir = path.parent.resolve()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        for key in ("asset_id", "text_desc", "latent_path"):
            if key not in payload:
                raise ValueError(f"missing '{key}' in line {line_no} ({path})")
        payload["latent_path"] = _resolve_manifest_path(payload["latent_path"], base_dir)
        if "mesh_path" in payload:
            payload["mesh_path"] = _resolve_manifest_path(payload["mesh_path"], base_dir)
        rows.append(payload)
    return rows


def evaluate_topk_category_hits(predictions: List[Dict[str, object]], topk: int = 3) -> float:
    """
    Evaluate top-k category hit rate.

    Each item format:
    {
      "target_category": "bench",
      "hits": [{"asset_id": "...", "category": "bench", "score": 0.9}, ...]
    }
    """
    if topk <= 0:
        raise ValueError("topk must be >= 1")
    if not predictions:
        return 0.0

    success = 0
    for item in predictions:
        target = str(item.get("target_category", "")).strip().lower()
        hits = item.get("hits", []) or []
        top_hits = hits[:topk]
        matched = any(str(hit.get("category", "")).strip().lower() == target for hit in top_hits)
        if matched:
            success += 1
    return success / len(predictions)


def write_assets_for_pipeline(rows: List[Dict[str, object]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    {
                        "asset_id": str(row["asset_id"]),
                        "description": str(row["text_desc"]),
                        "latent_path": str(row["latent_path"]),
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )
    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build real-asset retrieval index.")
    parser.add_argument("--manifest", type=Path, default=Path("data/real/real_assets_manifest.jsonl"))
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/real"))
    parser.add_argument("--model-name", default="openai/clip-vit-base-patch32")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--device", default="cpu")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rows = load_real_manifest(args.manifest)
        if not rows:
            raise ValueError(
                f"real manifest is empty: {args.manifest}. Add at least one row before building index."
            )
        asset_ids = [str(row["asset_id"]) for row in rows]
        descriptions = [str(row["text_desc"]) for row in rows]

        embedder = ClipTextEmbedder(
            model_name=args.model_name,
            model_dir=args.model_dir,
            local_files_only=args.local_files_only,
            device=args.device,
        )
        embeddings = embedder.encode_texts(descriptions)

        args.artifacts.mkdir(parents=True, exist_ok=True)
        np.save(args.artifacts / "asset_text_embeds.npy", embeddings)
        (args.artifacts / "asset_ids.json").write_text(
            json.dumps(asset_ids, indent=2, ensure_ascii=True), encoding="utf-8"
        )
        (args.artifacts / "embed_meta.json").write_text(
            json.dumps(
                {
                    "num_assets": len(asset_ids),
                    "embedding_dim": int(embeddings.shape[1]),
                    "model_source": embedder.model_source,
                    "projection_dim": int(embedder.projection_dim),
                    "local_files_only": bool(args.local_files_only),
                },
                indent=2,
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )

        store = FaissIndexStore.build(embeddings=embeddings, asset_ids=asset_ids)
        store.save(index_path=args.artifacts / "index_ip.faiss", id_map_path=args.artifacts / "id_map.json")

        assets_pipeline_path = write_assets_for_pipeline(rows=rows, out_path=args.artifacts / "real_assets_for_pipeline.jsonl")
    except ModelLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Build real index failed: {exc}", file=sys.stderr)
        return 1

    print(f"Real index built: {args.artifacts / 'index_ip.faiss'}")
    print(f"Assets for pipeline: {assets_pipeline_path}")
    print(f"Asset count: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
