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
from roadgen3d.services.design_types import ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS
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
        if self.calls == 3:
            return {
                "normalized_scene_query": "walkable all-age complete street with safe sidewalks",
                "compose_config_patch": {
                    "design_rule_profile": "pedestrian_priority_v1",
                    "sidewalk_width_m": 4.2,
                },
                "citations_by_field": {
                    "sidewalk_width_m": ["complete_streets_0001"],
                    "design_rule_profile": ["complete_streets_0002"],
                },
                "design_summary": "Use a pedestrian-priority complete street with generous sidewalks.",
                "risk_notes": ["Transit demand remains moderate and should be checked in context."],
            }
        if self.calls == 4:
            return {
                "field_queries": {
                    "road_width_m": ["travel lane width complete streets"],
                    "lane_count": ["lane allocation complete streets"],
                    "transit_demand_level": ["bus stop placement"],
                }
            }
        return {
            "normalized_scene_query": "walkable all-age complete street with safe sidewalks and moderate transit access",
            "compose_config_patch": {
                "design_rule_profile": "pedestrian_priority_v1",
                "target_street_type": "complete_streets",
                "objective_profile": "balanced",
                "city_context": "mixed_use urban corridor",
                "length_m": 90.0,
                "road_width_m": 7.2,
                "sidewalk_width_m": 4.2,
                "lane_count": 2,
                "density": 1.1,
                "ped_demand_level": "high",
                "bike_demand_level": "medium",
                "transit_demand_level": "medium",
                "vehicle_demand_level": "low",
            },
            "citations_by_field": {
                "sidewalk_width_m": ["complete_streets_0001"],
                "design_rule_profile": ["complete_streets_0002"],
                "road_width_m": ["complete_streets_0003"],
                "lane_count": ["complete_streets_0003"],
                "transit_demand_level": ["complete_streets_0002"],
            },
            "design_summary": "Use a pedestrian-priority complete street with generous sidewalks and modest carriageway width.",
            "risk_notes": ["Vehicle access stays low to protect pedestrian safety."],
        }


class _FakeRetriever:
    def search(self, query: str, topk: int = 5):
        if "sidewalk" in query:
            chunk_id = "complete_streets_0001"
            section = "Sidewalk Width Guidance"
            score = 0.91
        elif "lane" in query or "road" in query or "travel" in query:
            chunk_id = "complete_streets_0003"
            section = "Lane Allocation Guidance"
            score = 0.89
        else:
            chunk_id = "complete_streets_0002"
            section = "Transit Stop Placement"
            score = 0.87
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
                score=score,
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

    assert bundle.stage == "draft_ready"
    assert bundle.intent.safety_priorities == ("pedestrian safety",)
    assert len(bundle.evidence) == 3
    assert all(field in bundle.draft.compose_config_patch for field in ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS)
    assert bundle.draft.compose_config_patch["target_street_type"] == "mixed_use"
    assert bundle.draft.compose_config_patch["sidewalk_width_m"] == 4.2
    assert bundle.draft.citations_by_field["sidewalk_width_m"] == ("complete_streets_0001",)
    assert bundle.draft.citations_by_field["road_width_m"] == ("complete_streets_0003",)
    assert bundle.draft.parameter_sources_by_field["sidewalk_width_m"] == "rag"
    assert bundle.draft.parameter_sources_by_field["city_context"] == "llm_inferred"
    assert "pedestrian-priority" in bundle.draft.design_summary
    assert service.llm_client.calls == 5


class _ClarificationFirstLLM:
    def __init__(self):
        self.calls = 0

    def chat_json(self, _messages, *, temperature=0.2):
        self.calls += 1
        return {
            "user_goals": ["walkable complete street"],
            "style_preferences": ["all-age friendly"],
            "safety_priorities": ["pedestrian safety"],
            "follow_up_questions": [
                "Which city or neighborhood context should this street fit into?",
                "Should the street prioritize transit access or keep it secondary?",
            ],
            "rag_queries": ["complete streets pedestrian safety"],
        }


class _FailIfRetrieverRuns:
    def search(self, query: str, topk: int = 5):
        raise AssertionError(f"retriever should not run during clarification stage: {query} / {topk}")


def test_design_assistant_service_returns_clarification_stage_before_rag():
    service = DesignAssistantService(
        llm_client=_ClarificationFirstLLM(),
        knowledge_retriever=_FailIfRetrieverRuns(),
    )

    bundle = service.draft_design(
        messages=[{"role": "user", "content": "我想做步行安全、全龄友好的街道。"}],
        user_input="我想做步行安全、全龄友好的街道。",
        current_patch={},
        topk=4,
    )

    assert bundle.stage == "clarification_required"
    assert bundle.draft is None
    assert bundle.evidence == ()
    assert len(bundle.intent.follow_up_questions) == 2
    assert service.llm_client.calls == 1
