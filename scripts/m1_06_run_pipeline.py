#!/usr/bin/env python3
"""Run milestone-1 end-to-end pipeline in one command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.decoder import PlaceholderVoxelDecoder  # noqa: E402
from roadgen3d.decoder_shapee import ShapeEDecoder  # noqa: E402
from roadgen3d.embedder import ClipTextEmbedder, ModelLoadError  # noqa: E402
from roadgen3d.index_store import FaissIndexStore  # noqa: E402
from roadgen3d.latent_store import LatentStore  # noqa: E402
from roadgen3d.pipeline import M1Pipeline  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full text->FAISS->latent->voxel pipeline.")
    parser.add_argument("--query", required=True, help="Input text query.")
    parser.add_argument("--topk", type=int, default=1, help="Top-k retrieval results.")
    parser.add_argument("--data-dir", type=Path, default=Path("data/m1"), help="Data directory.")
    parser.add_argument("--assets", type=Path, default=None, help="Asset metadata path.")
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/m1"), help="Artifacts directory.")
    parser.add_argument("--model-name", default="openai/clip-vit-base-patch32", help="HF model id.")
    parser.add_argument("--model-dir", type=Path, default=None, help="Local model directory override.")
    parser.add_argument("--local-files-only", action="store_true", help="Force offline local model loading.")
    parser.add_argument("--device", default="cpu", help="Torch device.")
    parser.add_argument("--resolution", type=int, default=64, help="Output voxel resolution.")
    parser.add_argument("--threshold", type=float, default=0.5, help="Binarization threshold.")
    parser.add_argument("--decoder", choices=["placeholder", "shapee"], default="placeholder")
    parser.add_argument("--shapee-model-dir", type=Path, default=None, help="Optional Shape-E local model dir.")
    parser.add_argument("--shapee-strict", action="store_true", help="Disable fallback when Shape-E fails.")
    parser.add_argument("--voxel-size", type=float, default=0.1, help="Voxel edge size for mesh export.")
    parser.add_argument("--export-method", choices=["marching_cubes", "cubes"], default="marching_cubes")
    parser.add_argument("--export-format", choices=["glb", "ply", "both"], default="both")
    return parser.parse_args()


def build_decoder(args: argparse.Namespace):
    placeholder = PlaceholderVoxelDecoder(resolution=args.resolution, threshold=args.threshold)
    if args.decoder == "placeholder":
        return placeholder
    return ShapeEDecoder(
        resolution=args.resolution,
        threshold=args.threshold,
        device=args.device,
        model_dir=args.shapee_model_dir,
        strict=args.shapee_strict,
        fallback_decoder=None if args.shapee_strict else placeholder,
    )


def main() -> int:
    args = parse_args()
    assets_path = args.assets or (args.data_dir / "assets.jsonl")
    try:
        embedder = ClipTextEmbedder(
            model_name=args.model_name,
            model_dir=args.model_dir,
            local_files_only=args.local_files_only,
            device=args.device,
        )
        index_store = FaissIndexStore.load(
            index_path=args.artifacts / "index_ip.faiss",
            id_map_path=args.artifacts / "id_map.json",
        )
        latent_store = LatentStore(assets_jsonl_path=assets_path)
        decoder = build_decoder(args)

        pipeline = M1Pipeline(embedder=embedder, index_store=index_store, latent_store=latent_store, decoder=decoder)
        result, hits = pipeline.run(
            query=args.query,
            topk=args.topk,
            output_dir=args.artifacts,
            voxel_size=args.voxel_size,
            export_method=args.export_method,
            export_format=args.export_format,
        )
        result_path = args.artifacts / "pipeline_result.json"
        pipeline.save_result_json(result=result, hits=hits, out_path=result_path)
    except ModelLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1

    print(f"Top-1 asset: {result.top_hit.asset_id} (score={result.top_hit.score:.4f})")
    print(f"Occupied voxels: {result.occupied_voxels}")
    if result.outputs.get("mesh_glb"):
        print(f"Mesh GLB: {result.outputs['mesh_glb']}")
    if result.outputs.get("mesh_ply"):
        print(f"Mesh PLY: {result.outputs['mesh_ply']}")
    print(f"Saved result to: {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
