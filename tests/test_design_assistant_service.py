from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.knowledge.pdf_rag import KnowledgeChunk, KnowledgeSearchHit
from roadgen3d.services.design_assistant import DesignAssistantService


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    def chat_json(self, _messages, *, temperature=0.2):
        self.calls += 1
        if self.calls == 1:
            return {
                "user_goals": ["walkable complete street"],
                "style_preferences": ["all-age friendly"],
                "safety_priorities": ["pedestrian safety"],
                "follow_up_questions": [],
                "rag_queries": ["步道宽度 complete streets", "公交站点设置"],
            }
        if self.calls == 2:
            return {
                "english_queries": ["sidewalk width complete streets", "bus stop placement"],
            }
        return {
            "normalized_scene_query": "walkable all-age complete street with safe sidewalks",
            "compose_config_patch": {
                "design_rule_profile": "pedestrian_priority_v1",
                "sidewalk_width_m": 4.2,
                "lane_count": 2,
            },
            "citations_by_field": {
                "sidewalk_width_m": ["complete_streets_0001"],
                "design_rule_profile": ["complete_streets_0002"],
            },
            "design_summary": "Use a pedestrian-priority complete street with generous sidewalks.",
            "risk_notes": ["Transit demand remains moderate and should be checked in context."],
        }


class _FakeRetriever:
    def search(self, query: str, topk: int = 5):
        chunk_id = "complete_streets_0001" if "sidewalk" in query else "complete_streets_0002"
        section = "Sidewalk Width Guidance" if "sidewalk" in query else "Transit Stop Placement"
        return [
            KnowledgeSearchHit(
                chunk=KnowledgeChunk(
                    chunk_id=chunk_id,
                    doc_id="complete_streets",
                    page_start=12,
                    page_end=12,
                    section_title=section,
                    text=f"Evidence for {query}.",
                    source_path="/tmp/guide.pdf",
                ),
                score=0.91 if "sidewalk" in query else 0.87,
            )
        ]


def test_design_assistant_service_builds_draft_bundle():
    service = DesignAssistantService(
        llm_client=_FakeLLM(),
        knowledge_retriever=_FakeRetriever(),
    )

    bundle = service.draft_design(
        messages=[{"role": "user", "content": "我想做步行安全、全龄友好的街道。"}],
        user_input="我想做步行安全、全龄友好的街道。",
        current_patch={"target_street_type": "mixed_use"},
        topk=4,
    )

    assert bundle.intent.safety_priorities == ("pedestrian safety",)
    assert len(bundle.evidence) == 2
    assert bundle.draft.compose_config_patch["target_street_type"] == "mixed_use"
    assert bundle.draft.compose_config_patch["sidewalk_width_m"] == 4.2
    assert bundle.draft.citations_by_field["sidewalk_width_m"] == ("complete_streets_0001",)
    assert "pedestrian-priority" in bundle.draft.design_summary
    assert service.llm_client.calls == 3
