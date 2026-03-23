"""Generic PDF -> chunk -> embedding -> FAISS knowledge pipeline."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Protocol, Sequence

import numpy as np


class TextEmbedder(Protocol):
    """Minimal embedder protocol used by the knowledge pipeline."""

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        """Encode a sequence of texts into float32 embeddings."""


def _require_faiss():
    try:
        import faiss  # type: ignore
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError("faiss is required to build or query the PDF knowledge base.") from exc
    return faiss


def _load_pdf_reader():
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError("Install `pypdf` or `PyPDF2` to parse design-guide PDFs.") from exc
    return PdfReader


class SentenceTransformerEmbedder:
    """Lazy sentence-transformers wrapper used for document retrieval."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model_name = str(model_name)
        self._model = None
        self.backend_name = "sentence_transformers"

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore
            except ImportError as exc:  # pragma: no cover - import guard
                raise RuntimeError(
                    "sentence-transformers is required for PDF knowledge embeddings."
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        model = self._load_model()
        vectors = model.encode(
            list(texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)


class ClipTextEmbedderAdapter:
    """Fallback local embedder that reuses the existing CLIP text encoder."""

    def __init__(
        self,
        *,
        model_name: str = "openai/clip-vit-base-patch32",
        model_dir: str | Path | None = None,
        local_files_only: bool = True,
        device: str = "cpu",
    ) -> None:
        self.model_name = str(model_name)
        self.model_dir = Path(model_dir).expanduser().resolve() if model_dir is not None else None
        self.local_files_only = bool(local_files_only)
        self.device = str(device)
        self._embedder = None
        self.backend_name = "clip"

    def _load_model(self):
        if self._embedder is None:
            from ..embedder import ClipTextEmbedder

            self._embedder = ClipTextEmbedder(
                model_name=self.model_name,
                model_dir=self.model_dir,
                local_files_only=self.local_files_only,
                device=self.device,
            )
        return self._embedder

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray(self._load_model().encode_texts(list(texts)), dtype=np.float32)


HEADING_RE = re.compile(
    r"^(?:chapter\s+\d+|appendix\s+[a-z]|[0-9]+(?:\.[0-9]+){0,3})\s+.+$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    doc_id: str
    page_start: int
    page_end: int
    section_title: str
    text: str
    source_path: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KnowledgeBuildArtifacts:
    doc_id: str
    source_path: str
    chunk_count: int
    embedding_dim: int
    output_dir: str
    metadata_path: str
    chunks_path: str
    index_path: str
    embeddings_path: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KnowledgeSearchHit:
    chunk: KnowledgeChunk
    score: float

    def to_dict(self) -> Dict[str, Any]:
        payload = self.chunk.to_dict()
        payload["score"] = float(self.score)
        return payload


def normalize_pdf_text(raw: str) -> str:
    cleaned = str(raw or "")
    cleaned = cleaned.replace("\x00", " ").replace("\uFFFD", " ").replace("\uf0b7", " ")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\s+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def infer_doc_id(pdf_path: Path) -> str:
    stem = pdf_path.stem.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    return normalized or "design_guide"


def infer_section_title(paragraphs: Sequence[str], fallback: str) -> str:
    for paragraph in paragraphs:
        first_line = paragraph.splitlines()[0].strip()
        if HEADING_RE.match(first_line):
            return first_line[:160]
    return fallback


def split_into_paragraphs(text: str) -> List[str]:
    paragraphs = []
    for block in re.split(r"\n\s*\n", text):
        cleaned = normalize_pdf_text(block)
        if cleaned:
            paragraphs.append(cleaned)
    return paragraphs


def paragraph_chunks(paragraphs: Sequence[str], target_chars: int = 900, overlap_chars: int = 160) -> List[str]:
    chunks: List[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if current and len(candidate) > int(target_chars):
            chunks.append(current.strip())
            if overlap_chars > 0:
                tail = current[-int(overlap_chars) :].strip()
                current = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
            else:
                current = paragraph
        else:
            current = candidate
    if current.strip():
        chunks.append(current.strip())
    deduped: List[str] = []
    seen = set()
    for chunk in chunks:
        key = chunk[:240]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


def read_pdf_pages(pdf_path: Path) -> List[ExtractedPage]:
    PdfReader = _load_pdf_reader()
    reader = PdfReader(str(pdf_path))
    pages: List[ExtractedPage] = []
    for index, page in enumerate(reader.pages, start=1):
        text = normalize_pdf_text(page.extract_text() or "")
        if text:
            pages.append(ExtractedPage(page_number=index, text=text))
    return pages


def build_chunks_from_pages(
    pages: Sequence[ExtractedPage],
    *,
    doc_id: str,
    source_path: str,
    target_chars: int = 900,
    overlap_chars: int = 160,
) -> List[KnowledgeChunk]:
    chunks: List[KnowledgeChunk] = []
    chunk_counter = 1
    for page in pages:
        paragraphs = split_into_paragraphs(page.text)
        if not paragraphs:
            continue
        section_title = infer_section_title(paragraphs, fallback=f"Page {page.page_number}")
        for text_chunk in paragraph_chunks(paragraphs, target_chars=target_chars, overlap_chars=overlap_chars):
            chunks.append(
                KnowledgeChunk(
                    chunk_id=f"{doc_id}_{chunk_counter:04d}",
                    doc_id=doc_id,
                    page_start=int(page.page_number),
                    page_end=int(page.page_number),
                    section_title=section_title,
                    text=text_chunk,
                    source_path=source_path,
                )
            )
            chunk_counter += 1
    return chunks


class PdfKnowledgeBaseBuilder:
    """Build a local FAISS-backed knowledge base from a PDF."""

    def __init__(
        self,
        *,
        embedder: TextEmbedder | None = None,
        target_chars: int = 900,
        overlap_chars: int = 160,
    ) -> None:
        self.embedder = embedder or SentenceTransformerEmbedder()
        self.target_chars = int(target_chars)
        self.overlap_chars = int(overlap_chars)

    def extract_pages(self, pdf_path: Path) -> List[ExtractedPage]:
        return read_pdf_pages(pdf_path)

    def build(
        self,
        pdf_path: str | Path,
        output_dir: str | Path,
        *,
        doc_id: str | None = None,
    ) -> KnowledgeBuildArtifacts:
        pdf_file = Path(pdf_path).expanduser().resolve()
        out_dir = Path(output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        resolved_doc_id = _clean_doc_id(doc_id or infer_doc_id(pdf_file))
        pages = self.extract_pages(pdf_file)
        chunks = build_chunks_from_pages(
            pages,
            doc_id=resolved_doc_id,
            source_path=str(pdf_file),
            target_chars=self.target_chars,
            overlap_chars=self.overlap_chars,
        )
        if not chunks:
            raise RuntimeError(f"No chunks were extracted from PDF: {pdf_file}")
        embeddings = np.asarray(self.embedder.encode_texts([chunk.text for chunk in chunks]), dtype=np.float32)
        if embeddings.ndim != 2 or embeddings.shape[0] != len(chunks):
            raise RuntimeError("Knowledge embedder returned an unexpected embedding matrix shape.")

        faiss = _require_faiss()
        index = faiss.IndexFlatIP(int(embeddings.shape[1]))
        index.add(embeddings)

        chunks_path = out_dir / "chunks.jsonl"
        embeddings_path = out_dir / "embeddings.npy"
        index_path = out_dir / "index.faiss"
        metadata_path = out_dir / "metadata.json"

        with chunks_path.open("w", encoding="utf-8") as handle:
            for chunk in chunks:
                handle.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
        np.save(embeddings_path, embeddings)
        faiss.write_index(index, str(index_path))

        metadata = {
            "doc_id": resolved_doc_id,
            "source_path": str(pdf_file),
            "chunk_count": len(chunks),
            "embedding_dim": int(embeddings.shape[1]),
            "embedding_backend": str(getattr(self.embedder, "backend_name", "unknown")),
            "clip_model_dir": (
                str(getattr(self.embedder, "model_dir", "") or "")
                if getattr(self.embedder, "backend_name", "") == "clip"
                else ""
            ),
            "chunks_path": chunks_path.as_posix(),
            "embeddings_path": embeddings_path.as_posix(),
            "index_path": index_path.as_posix(),
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return KnowledgeBuildArtifacts(
            doc_id=resolved_doc_id,
            source_path=str(pdf_file),
            chunk_count=len(chunks),
            embedding_dim=int(embeddings.shape[1]),
            output_dir=str(out_dir),
            metadata_path=str(metadata_path),
            chunks_path=str(chunks_path),
            index_path=str(index_path),
            embeddings_path=str(embeddings_path),
        )


class PdfKnowledgeBaseRetriever:
    """Search a previously built FAISS-backed document knowledge base."""

    def __init__(
        self,
        *,
        artifact_dir: str | Path,
        embedder: TextEmbedder | None = None,
    ) -> None:
        self.artifact_dir = Path(artifact_dir).expanduser().resolve()
        self.embedder = embedder
        self._chunks: List[KnowledgeChunk] | None = None
        self._index = None
        self._metadata: Dict[str, Any] | None = None

    def _load_chunks(self) -> List[KnowledgeChunk]:
        if self._chunks is None:
            chunks_path = self.artifact_dir / "chunks.jsonl"
            rows: List[KnowledgeChunk] = []
            with chunks_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    rows.append(
                        KnowledgeChunk(
                            chunk_id=str(payload["chunk_id"]),
                            doc_id=str(payload["doc_id"]),
                            page_start=int(payload["page_start"]),
                            page_end=int(payload["page_end"]),
                            section_title=str(payload.get("section_title", "")),
                            text=str(payload["text"]),
                            source_path=str(payload.get("source_path", "")),
                        )
                    )
            self._chunks = rows
        return self._chunks

    def _load_index(self):
        if self._index is None:
            faiss = _require_faiss()
            self._index = faiss.read_index(str(self.artifact_dir / "index.faiss"))
        return self._index

    def _load_metadata(self) -> Dict[str, Any]:
        if self._metadata is None:
            metadata_path = self.artifact_dir / "metadata.json"
            if metadata_path.exists():
                self._metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            else:
                self._metadata = {}
        return self._metadata

    def _resolve_embedder(self) -> TextEmbedder:
        if self.embedder is not None:
            return self.embedder
        metadata = self._load_metadata()
        backend_name = str(metadata.get("embedding_backend", "sentence_transformers"))
        if backend_name == "clip":
            model_dir = metadata.get("clip_model_dir")
            self.embedder = ClipTextEmbedderAdapter(model_dir=model_dir, local_files_only=True)
        else:
            self.embedder = SentenceTransformerEmbedder()
        return self.embedder

    def search(self, query: str, *, topk: int = 5) -> List[KnowledgeSearchHit]:
        text = str(query or "").strip()
        if not text:
            return []
        chunks = self._load_chunks()
        index = self._load_index()
        vector = np.asarray(self._resolve_embedder().encode_texts([text]), dtype=np.float32)
        scores, indices = index.search(vector, max(1, int(topk)))
        results: List[KnowledgeSearchHit] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(chunks):
                continue
            results.append(KnowledgeSearchHit(chunk=chunks[int(idx)], score=float(score)))
        return results


def build_pdf_knowledge_base(
    pdf_path: str | Path,
    output_dir: str | Path,
    *,
    embedder: TextEmbedder | None = None,
    target_chars: int = 900,
    overlap_chars: int = 160,
) -> KnowledgeBuildArtifacts:
    """Convenience wrapper used by scripts and the API."""

    builder = PdfKnowledgeBaseBuilder(
        embedder=embedder,
        target_chars=target_chars,
        overlap_chars=overlap_chars,
    )
    return builder.build(pdf_path, output_dir)


def _clean_doc_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return normalized or "design_guide"
