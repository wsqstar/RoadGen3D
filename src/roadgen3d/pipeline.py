"""Composable end-to-end pipeline for milestone-1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Sequence, Tuple

import numpy as np

from .types import PipelineResult, RetrievalHit


class M1Pipeline:
    """Orchestrates query embedding, FAISS retrieval, and latent decoding."""

    def __init__(self, embedder, index_store, latent_store, decoder):
        self.embedder = embedder
        self.index_store = index_store
        self.latent_store = latent_store
        self.decoder = decoder

    def run(self, query: str, topk: int = 1, output_dir: Path = Path("artifacts/m1")) -> Tuple[PipelineResult, List[RetrievalHit]]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        if topk <= 0:
            raise ValueError("topk must be >= 1")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        query_embedding = self.embedder.encode_texts([query])
        hits_per_query = self.index_store.search(query_embedding, topk=topk)
        hits = hits_per_query[0] if hits_per_query else []
        if not hits:
            raise RuntimeError("No retrieval hits returned from FAISS index.")

        top_hit = hits[0]
        latent = self.latent_store.load(top_hit.asset_id)
        voxel_prob, voxel_bin = self.decoder.decode(latent)

        voxel_prob_path = output_dir / "voxel_prob.npy"
        voxel_bin_path = output_dir / "voxel_bin.npy"
        np.save(voxel_prob_path, voxel_prob)
        np.save(voxel_bin_path, voxel_bin)

        latent_shape = list(np.asarray(latent).shape)
        result = PipelineResult(
            query=query,
            top_hit=top_hit,
            latent_shape=latent_shape,
            voxel_shape=list(voxel_bin.shape),
            occupied_voxels=int(voxel_bin.sum()),
            outputs={
                "voxel_prob": str(voxel_prob_path),
                "voxel_bin": str(voxel_bin_path),
            },
        )
        return result, hits

    @staticmethod
    def save_result_json(result: PipelineResult, hits: Sequence[RetrievalHit], out_path: Path) -> None:
        payload = result.to_dict()
        payload["hits"] = [{"asset_id": hit.asset_id, "score": hit.score} for hit in hits]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

