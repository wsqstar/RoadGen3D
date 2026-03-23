#!/usr/bin/env python3
"""
Build a RAG-ready knowledge base from the "Sidewalk Area" PDF.

Steps:
1. Parse the PDF into cleaned text per page.
2. Split text into sections (e.g., "4.1 Building Entries") and chunk it.
3. Infer lightweight metadata (zones mentioned, heading, pages).
4. Embed each chunk and persist both the JSONL knowledge file and FAISS index.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Sequence

import faiss
import numpy as np
from PyPDF2 import PdfReader
from sentence_transformers import SentenceTransformer


SECTION_RE = re.compile(r"^(4\.(\d+(?:\.\d+)*))\s+(.+)$")
ZONE_KEYWORDS = {
    "frontage": ("frontage zone",),
    "pedestrian": ("pedestrian zone", "walkway zone"),
    "amenity": ("amenity zone",),
    "flex": ("flex zone", "parking lane", "curb extension", "parklet"),
}


@dataclass
class PageText:
    page: int
    text: str


@dataclass
class Section:
    section_id: str
    heading: str
    start_page: int
    end_page: int
    paragraphs: List[str] = field(default_factory=list)

    def to_text(self) -> str:
        return "\n".join(self.paragraphs).strip()


@dataclass
class Chunk:
    chunk_id: str
    section_id: str
    heading: str
    text: str
    page_start: int
    page_end: int
    zones: List[str]


def normalize_text(raw: str) -> str:
    cleaned = raw.replace("\uFFFD", " ").replace("\uf0b7", "?").replace("锟?", " ")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\s+\n", "\n", cleaned)
    return cleaned.strip()


def extract_pages(pdf_path: Path) -> List[PageText]:
    reader = PdfReader(str(pdf_path))
    pages: List[PageText] = []
    for idx, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(PageText(page=idx, text=normalize_text(text)))
    return pages


def iter_lines_with_page(pages: Sequence[PageText]) -> Iterable[tuple[int, str]]:
    for page in pages:
        for line in page.text.splitlines():
            yield page.page, line.strip()


def build_sections(pages: Sequence[PageText]) -> List[Section]:
    if not pages:
        return []
    sections: List[Section] = []
    current = Section(section_id="4", heading="Sidewalk Area Overview", start_page=pages[0].page, end_page=pages[0].page)
    buffer: List[str] = []
    last_page = pages[0].page
    for page_num, line in iter_lines_with_page(pages):
        last_page = page_num
        if not line:
            continue
        match = SECTION_RE.match(line)
        if match:
            if buffer:
                current.paragraphs.append("\n".join(buffer))
                buffer.clear()
            if current.paragraphs:
                current.end_page = page_num
                sections.append(current)
            section_id, heading = match.group(1), match.group(3).strip()
            current = Section(section_id=section_id, heading=heading, start_page=page_num, end_page=page_num)
            continue
        buffer.append(line)
    if buffer:
        current.paragraphs.append("\n".join(buffer))
    current.end_page = last_page
    if current.paragraphs:
        sections.append(current)
    return sections


def paragraph_chunks(text: str, target_chars: int = 800, overlap_chars: int = 150) -> List[str]:
    text = text.strip()
    if not text:
        return []
    segments = [seg.strip() for seg in re.split(r"(?<=[.!?;])\s+|\n+", text) if seg.strip()]
    chunks: List[str] = []
    acc = ""
    for seg in segments:
        if not acc:
            acc = seg
            continue
        if len(acc) + 1 + len(seg) <= target_chars:
            acc = acc + " " + seg
        else:
            chunks.append(acc.strip())
            if overlap_chars > 0:
                acc = (acc[-overlap_chars:] + " " + seg).strip()
            else:
                acc = seg
    if acc:
        chunks.append(acc.strip())
    deduped: List[str] = []
    seen = set()
    for chunk in chunks:
        key = chunk[:160]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


def detect_zones(text: str) -> List[str]:
    lowered = text.lower()
    zones = []
    for name, keywords in ZONE_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            zones.append(name)
    return zones


def build_chunks(sections: Sequence[Section]) -> List[Chunk]:
    chunks: List[Chunk] = []
    for section in sections:
        body = section.to_text()
        for idx, chunk_text in enumerate(paragraph_chunks(body), start=1):
            chunk_id = f"{section.section_id.replace('.', '_')}_{idx:02d}"
            zones = detect_zones(chunk_text)
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    section_id=section.section_id,
                    heading=section.heading,
                    text=chunk_text,
                    page_start=section.start_page,
                    page_end=section.end_page,
                    zones=zones,
                )
            )
    return chunks


def embed_chunks(chunks: Sequence[Chunk], model_name: str) -> np.ndarray:
    model = SentenceTransformer(model_name)
    texts = [chunk.text for chunk in chunks]
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=True, normalize_embeddings=True)
    return embeddings.astype(np.float32)


def write_outputs(chunks: Sequence[Chunk], embeddings: np.ndarray, out_dir: Path, pdf_name: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = out_dir / "chunks.jsonl"
    meta_path = out_dir / "metadata.json"
    id_map_path = out_dir / "chunk_ids.json"
    embeddings_path = out_dir / "embeddings.npy"
    index_path = out_dir / "index.faiss"

    with chunks_path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            payload = {
                "chunk_id": chunk.chunk_id,
                "section_id": chunk.section_id,
                "heading": chunk.heading,
                "text": chunk.text,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "zones": chunk.zones,
                "source": {"pdf": pdf_name},
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    chunk_ids = [chunk.chunk_id for chunk in chunks]
    id_map_path.write_text(json.dumps(chunk_ids, ensure_ascii=False, indent=2), encoding="utf-8")

    np.save(embeddings_path, embeddings)

    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    faiss.write_index(index, str(index_path))

    summary = {
        "pdf": pdf_name,
        "chunk_count": len(chunks),
        "embedding_dim": dim,
        "chunks_path": chunks_path.as_posix(),
        "index_path": index_path.as_posix(),
    }
    meta_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Sidewalk Area RAG assets.")
    parser.add_argument("--pdf-path", type=Path, required=True, help="Path to Sidewalk Area PDF.")
    parser.add_argument("--out-dir", type=Path, default=Path("knowledge/sidewalk_area"), help="Output directory.")
    parser.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pages = extract_pages(args.pdf_path)
    sections = build_sections(pages)
    chunks = build_chunks(sections)
    if not chunks:
        raise RuntimeError("No chunks created; verify PDF parsing.")
    embeddings = embed_chunks(chunks, args.model)
    write_outputs(chunks, embeddings, args.out_dir, pdf_name=args.pdf_path.name)
    print(f"Built {len(chunks)} chunks -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
