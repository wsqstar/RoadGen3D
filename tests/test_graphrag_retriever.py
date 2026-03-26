from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.knowledge.graphrag import GraphRagKnowledgeRetriever  # noqa: E402


def test_graphrag_retriever_searches_merged_txt_corpus(tmp_path: Path):
    txt_dir = tmp_path / "graphrag_txt" / "Complete-Streets-Design-Handbook-2024"
    txt_dir.mkdir(parents=True)
    (txt_dir / "011_TREATMENT_4.3.1.txt").write_text(
        "\n".join(
            [
                "书名: Complete-Streets-Design-Handbook-2024",
                "章节: TREATMENT_4.3.1",
                "",
                "Minimum sidewalk width is 12 feet on most street types.",
                "Transit stops and commercial activity should prioritize wider sidewalks.",
            ]
        ),
        encoding="utf-8",
    )
    (txt_dir / "012_TREATMENT_4.3.2.txt").write_text(
        "\n".join(
            [
                "书名: Complete-Streets-Design-Handbook-2024",
                "章节: TREATMENT_4.3.2",
                "",
                "Two people walking side-by-side require five feet of clear sidewalk space.",
            ]
        ),
        encoding="utf-8",
    )

    retriever = GraphRagKnowledgeRetriever(project_dir=tmp_path)
    sync_info = retriever.sync_input_corpus()
    status = retriever.describe()
    hits = retriever.search("minimum sidewalk width near transit", topk=2)

    assert sync_info["source_file_count"] == 2
    assert sync_info["copied_count"] == 2
    assert (tmp_path / "graphrag_quickstart" / "input" / "011_TREATMENT_4.3.1.txt").exists()
    assert (tmp_path / "graphrag_quickstart" / "cache" / "roadgen3d_input_manifest.json").exists()
    assert status.available is True
    assert status.item_count == 2
    assert status.runtime_mode == "static_fallback"
    assert status.synced_input_count == 2
    assert len(hits) == 2
    assert hits[0].chunk.chunk_id.startswith("graphrag_txt::")
    assert hits[0].chunk.page_start == 11
    assert hits[0].chunk.section_title == "TREATMENT_4.3.1"
    assert "sidewalk width" in hits[0].chunk.text.lower()
