from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.knowledge.pdf_rag import (
    ExtractedPage,
    PdfKnowledgeBaseBuilder,
    PdfKnowledgeBaseRetriever,
)


class _FakeEmbedder:
    def encode_texts(self, texts):
        rows = []
        for text in texts:
            lowered = str(text).lower()
            rows.append(
                [
                    1.0 if "sidewalk" in lowered else 0.0,
                    1.0 if "lane" in lowered else 0.0,
                    1.0 if "bus" in lowered else 0.0,
                    float(len(lowered) % 7) / 7.0,
                ]
            )
        return np.asarray(rows, dtype=np.float32)


def test_pdf_knowledge_builder_writes_expected_artifacts(tmp_path: Path):
    pdf_path = tmp_path / "guide.pdf"
    pdf_path.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "knowledge"

    builder = PdfKnowledgeBaseBuilder(embedder=_FakeEmbedder(), target_chars=120, overlap_chars=20)
    builder.extract_pages = lambda _path: [
        ExtractedPage(
            page_number=1,
            text="1.1 Sidewalk Widths\n\nSidewalk clear paths should feel generous.\n\nBus stops need curb space.",
        ),
        ExtractedPage(
            page_number=2,
            text="2.1 Lane Allocation\n\nKeep lane counts compact when pedestrian safety matters.",
        ),
    ]

    artifacts = builder.build(pdf_path, out_dir)

    assert (out_dir / "chunks.jsonl").exists()
    assert (out_dir / "metadata.json").exists()
    assert (out_dir / "index.faiss").exists()
    assert (out_dir / "embeddings.npy").exists()
    assert artifacts.chunk_count >= 2

    rows = [
        json.loads(line)
        for line in (out_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[0]["doc_id"] == "guide"
    assert rows[0]["section_title"].startswith("1.1 Sidewalk Widths")
    assert rows[0]["page_start"] == 1
    assert "source_path" in rows[0]


def test_pdf_knowledge_retriever_returns_ranked_hits(tmp_path: Path):
    pdf_path = tmp_path / "guide.pdf"
    pdf_path.write_text("placeholder", encoding="utf-8")
    out_dir = tmp_path / "knowledge"

    builder = PdfKnowledgeBaseBuilder(embedder=_FakeEmbedder())
    builder.extract_pages = lambda _path: [
        ExtractedPage(page_number=1, text="1.1 Sidewalk Design\n\nSidewalk width should protect pedestrians."),
        ExtractedPage(page_number=2, text="2.1 Transit Stops\n\nBus stop placement should support curb access."),
    ]
    builder.build(pdf_path, out_dir)

    retriever = PdfKnowledgeBaseRetriever(artifact_dir=out_dir, embedder=_FakeEmbedder())
    results = retriever.search("pedestrian sidewalk safety", topk=2)

    assert len(results) == 2
    assert results[0].chunk.section_title.startswith("1.1 Sidewalk Design")
    assert results[0].score >= results[1].score
