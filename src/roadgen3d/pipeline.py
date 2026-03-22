"""Composable end-to-end pipeline for milestone-1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np

from .types import PipelineResult, RetrievalHit
from .voxel_export import export_voxel_meshes


def _normalize_decode_output(result):
    if not isinstance(result, tuple):
        raise TypeError("decoder output must be a tuple")
    if len(result) == 3:
        voxel_prob, voxel_bin, meta = result
    elif len(result) == 2:
        voxel_prob, voxel_bin = result
        meta = {}
    else:
        raise TypeError("decoder output tuple must have length 2 or 3")
    if not isinstance(meta, dict):
        meta = {"meta": str(meta)}
    return np.asarray(voxel_prob), np.asarray(voxel_bin), meta


class M1Pipeline:
    """Orchestrates query embedding, FAISS retrieval, and latent decoding."""

    def __init__(self, embedder, index_store, latent_store, decoder):
        self.embedder = embedder
        self.index_store = index_store
        self.latent_store = latent_store
        self.decoder = decoder

    def run(
        self,
        query: str,
        topk: int = 1,
        output_dir: Path = Path("artifacts/m1"),
        voxel_size: float = 0.1,
        export_method: str = "marching_cubes",
        export_format: str = "both",
    ) -> Tuple[PipelineResult, List[RetrievalHit]]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        if topk <= 0:
            raise ValueError("topk must be >= 1")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        ntotal_raw = getattr(self.index_store, "ntotal", None)
        if ntotal_raw is not None:
            ntotal = int(ntotal_raw)
            if ntotal <= 0:
                raise RuntimeError(
                    "FAISS index is empty. Build index with non-empty assets before running pipeline."
                )

        query_embedding = self.embedder.encode_texts([query])
        hits_per_query = self.index_store.search(query_embedding, topk=topk)
        hits = hits_per_query[0] if hits_per_query else []
        if not hits:
            raise RuntimeError("No retrieval hits returned from FAISS index.")

        top_hit = hits[0]
        latent = self.latent_store.load(top_hit.asset_id)
        voxel_prob, voxel_bin, decoder_meta = _normalize_decode_output(self.decoder.decode(latent))

        voxel_prob_path = output_dir / "voxel_prob.npy"
        voxel_bin_path = output_dir / "voxel_bin.npy"
        np.save(voxel_prob_path, voxel_prob)
        np.save(voxel_bin_path, voxel_bin)

        mesh_info: Dict[str, str] = {
            "mesh_glb": "",
            "mesh_ply": "",
            "mesh_method": "",
        }
        try:
            mesh_info = export_voxel_meshes(
                voxel_bin=voxel_bin,
                out_dir=output_dir,
                stem=f"{top_hit.asset_id}_voxel",
                voxel_size=voxel_size,
                method=export_method,
                export_format=export_format,
                mesh_override=decoder_meta.get("mesh"),
            )
        except Exception as exc:
            decoder_meta["mesh_export_error"] = str(exc)

        latent_shape = list(np.asarray(latent).shape)
        outputs: Dict[str, str] = {
            "voxel_prob": str(voxel_prob_path.resolve()),
            "voxel_bin": str(voxel_bin_path.resolve()),
            "mesh_glb": mesh_info.get("mesh_glb", ""),
            "mesh_ply": mesh_info.get("mesh_ply", ""),
        }
        decoder_name = str(decoder_meta.get("decoder", "")).strip()
        if decoder_name:
            outputs["decoder_used"] = decoder_name
        shapee_error = str(decoder_meta.get("shapee_error", "")).strip()
        if shapee_error:
            outputs["shapee_error"] = shapee_error
        mesh_method = str(mesh_info.get("mesh_method", "")).strip()
        if mesh_method:
            outputs["mesh_method"] = mesh_method
        mesh_export_error = str(decoder_meta.get("mesh_export_error", "")).strip()
        if mesh_export_error:
            outputs["mesh_export_error"] = mesh_export_error

        result = PipelineResult(
            query=query,
            top_hit=top_hit,
            latent_shape=latent_shape,
            voxel_shape=list(voxel_bin.shape),
            occupied_voxels=int(voxel_bin.sum()),
            outputs=outputs,
        )
        return result, hits

    @staticmethod
    def save_result_json(result: PipelineResult, hits: Sequence[RetrievalHit], out_path: Path) -> None:
        payload = result.to_dict()
        payload["hits"] = [{"asset_id": hit.asset_id, "score": hit.score} for hit in hits]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
