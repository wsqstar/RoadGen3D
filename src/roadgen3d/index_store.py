"""FAISS index management for text-to-asset retrieval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Sequence

import numpy as np

from .types import RetrievalHit


class FaissUnavailableError(RuntimeError):
    """Raised when FAISS is unavailable."""


def _import_faiss():
    try:
        import faiss  # type: ignore
    except ImportError as exc:
        raise FaissUnavailableError("`faiss` is not installed. Install requirements-m1.txt first.") from exc
    return faiss


class FaissIndexStore:
    """Thin wrapper around `faiss.IndexFlatIP` with id map persistence."""

    def __init__(self, index, asset_ids: Sequence[str]):
        self.index = index
        self.asset_ids = list(asset_ids)

    @classmethod
    def build(cls, embeddings: np.ndarray, asset_ids: Sequence[str]) -> "FaissIndexStore":
        faiss = _import_faiss()
        matrix = np.asarray(embeddings, dtype=np.float32)
        if matrix.ndim != 2:
            raise ValueError(f"Embeddings must be rank-2, got shape {matrix.shape}.")
        if matrix.shape[0] != len(asset_ids):
            raise ValueError(
                f"Embedding row count ({matrix.shape[0]}) does not match id count ({len(asset_ids)})."
            )
        index = faiss.IndexFlatIP(matrix.shape[1])
        index.add(matrix)
        return cls(index=index, asset_ids=asset_ids)

    @classmethod
    def load(cls, index_path: Path, id_map_path: Path) -> "FaissIndexStore":
        faiss = _import_faiss()
        if not index_path.exists():
            raise FileNotFoundError(f"FAISS index not found: {index_path}")
        if not id_map_path.exists():
            raise FileNotFoundError(f"ID map not found: {id_map_path}")

        index = faiss.read_index(str(index_path))
        asset_ids = json.loads(id_map_path.read_text(encoding="utf-8"))
        if not isinstance(asset_ids, list) or any(not isinstance(item, str) for item in asset_ids):
            raise ValueError(f"Invalid id map format in {id_map_path}")
        return cls(index=index, asset_ids=asset_ids)

    def save(self, index_path: Path, id_map_path: Path) -> None:
        faiss = _import_faiss()
        index_path.parent.mkdir(parents=True, exist_ok=True)
        id_map_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(index_path))
        id_map_path.write_text(json.dumps(self.asset_ids, indent=2, ensure_ascii=True), encoding="utf-8")

    @property
    def ntotal(self) -> int:
        return int(self.index.ntotal)

    def search(self, query_embeddings: np.ndarray, topk: int = 1) -> List[List[RetrievalHit]]:
        if topk <= 0:
            raise ValueError("topk must be >= 1")
        queries = np.asarray(query_embeddings, dtype=np.float32)
        if queries.ndim != 2:
            raise ValueError(f"Query embeddings must be rank-2, got shape {queries.shape}.")

        scores, indices = self.index.search(queries, topk)
        all_hits: List[List[RetrievalHit]] = []
        for row_scores, row_indices in zip(scores, indices):
            hits: List[RetrievalHit] = []
            for score, idx in zip(row_scores, row_indices):
                if idx < 0 or idx >= len(self.asset_ids):
                    continue
                hits.append(RetrievalHit(asset_id=self.asset_ids[int(idx)], score=float(score)))
            all_hits.append(hits)
        return all_hits

