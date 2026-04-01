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
from roadgen3d.llm.design_workflow import DesignAssistantService


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
    def __init__(self):
        self.calls = 0

    def search(self, query: str, topk: int = 5):
        self.calls += 1
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


class _FakeGraphRetriever:
    def __init__(self):
        self.calls = 0

    def describe(self):
        return type(
            "_GraphStatus",
            (),
            {
                "to_dict": lambda self: {
                    "key": "graph_rag",
                    "label": "GraphRAG",
                    "available": True,
                    "description": "Merged txt corpus.",
                    "artifact_count": 1,
                    "item_count": 2,
                }
            },
        )()

    def search(self, query: str, topk: int = 5):
        self.calls += 1
        return [
            KnowledgeSearchHit(
                chunk=KnowledgeChunk(
                    chunk_id="graph_0001",
                    doc_id="graphrag_community_report",
                    page_start=0,
                    page_end=0,
                    section_title="GraphRAG sidewalk guidance",
                    text=f"Graph evidence for {query}.",
                    source_path="/tmp/graphrag/community_reports.parquet",
                ),
                score=0.84,
            )
        ][:topk]


def test_design_assistant_service_builds_draft_bundle(tmp_path: Path):
    service = DesignAssistantService(
        llm_client=_FakeLLM(),
        knowledge_retriever=_FakeRetriever(),
        draft_cache_dir=tmp_path,
    )

    bundle = service.draft_design(
        messages=[{"role": "user", "content": "我想做步行安全、全龄友好的街道。"}],
        user_input="我想做步行安全、全龄友好的街道。",
        current_patch={"target_street_type": "mixed_use"},
        topk=4,
        knowledge_source="pdf_rag",
    )

    assert bundle.stage == "draft_ready"
    assert bundle.intent.safety_priorities == ("pedestrian safety",)
    assert len(bundle.evidence) == 3
    assert all(field in bundle.draft.compose_config_patch for field in ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS)
    assert bundle.draft.compose_config_patch["target_street_type"] == "mixed_use"
    assert bundle.draft.compose_config_patch["sidewalk_width_m"] == 4.2
    assert bundle.draft.compose_config_patch["style_preset"] == "civic_clean_v1"
    assert bundle.draft.compose_config_patch["beauty_mode"] == "presentation_v1"
    assert bundle.draft.citations_by_field["sidewalk_width_m"] == ("complete_streets_0001",)
    assert bundle.draft.citations_by_field["road_width_m"] == ("complete_streets_0003",)
    assert bundle.draft.parameter_sources_by_field["sidewalk_width_m"] == "rag"
    assert bundle.draft.parameter_sources_by_field["city_context"] == "llm_inferred"
    assert bundle.draft.parameter_sources_by_field["style_preset"] == "system_default"
    assert "pedestrian-priority" in bundle.draft.design_summary
    assert service.llm_client.calls == 5


def test_design_assistant_service_supports_graph_and_hybrid_knowledge_search(tmp_path: Path):
    service = DesignAssistantService(
        llm_client=_FakeLLM(),
        knowledge_retriever=_FakeRetriever(),
        graph_knowledge_retriever=_FakeGraphRetriever(),
        draft_cache_dir=tmp_path,
    )

    graph_results = service.search_knowledge(
        query="sidewalk width near transit",
        topk=3,
        knowledge_source="graph_rag",
    )
    hybrid_results = service.search_knowledge(
        query="sidewalk width near transit",
        topk=4,
        knowledge_source="hybrid",
    )

    assert len(graph_results) == 1
    assert graph_results[0].knowledge_source == "graph_rag"
    assert graph_results[0].chunk_id == "graph_0001"
    assert len(hybrid_results) >= 2
    assert {item.knowledge_source for item in hybrid_results} == {"pdf_rag", "graph_rag"}


def test_design_assistant_service_defaults_to_graph_rag(tmp_path: Path):
    service = DesignAssistantService(
        llm_client=_FakeLLM(),
        knowledge_retriever=_FakeRetriever(),
        graph_knowledge_retriever=_FakeGraphRetriever(),
        draft_cache_dir=tmp_path,
    )

    results = service.search_knowledge(
        query="sidewalk width near transit",
        topk=2,
    )

    assert len(results) == 1
    assert results[0].knowledge_source == "graph_rag"
    assert results[0].chunk_id == "graph_0001"


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


class _FailIfLLMRuns:
    def chat_json(self, _messages, *, temperature=0.2):
        raise AssertionError("llm should not run on cache hit")


def test_design_assistant_service_returns_clarification_stage_before_rag(tmp_path: Path):
    service = DesignAssistantService(
        llm_client=_ClarificationFirstLLM(),
        knowledge_retriever=_FailIfRetrieverRuns(),
        draft_cache_dir=tmp_path,
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


def test_design_assistant_service_reuses_cached_bundle_for_identical_prompt(tmp_path: Path):
    retriever = _FakeRetriever()
    service = DesignAssistantService(
        llm_client=_FakeLLM(),
        knowledge_retriever=retriever,
        draft_cache_dir=tmp_path,
    )

    first = service.draft_design(
        messages=[{"role": "user", "content": "我想做步行安全、全龄友好的街道。"}],
        user_input="我想做步行安全、全龄友好的街道。",
        current_patch={},
        topk=4,
        knowledge_source="pdf_rag",
    )
    llm_call_count = service.llm_client.calls
    retriever_call_count = retriever.calls

    second = service.draft_design(
        messages=[{"role": "user", "content": "我想做步行安全、全龄友好的街道。"}],
        user_input="我想做步行安全、全龄友好的街道。",
        current_patch={},
        topk=4,
        knowledge_source="pdf_rag",
    )

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.draft is not None
    assert second.draft.compose_config_patch["sidewalk_width_m"] == 4.2
    assert service.llm_client.calls == llm_call_count
    assert retriever.calls == retriever_call_count


def test_design_assistant_service_loads_cached_bundle_from_disk(tmp_path: Path):
    cache_dir = tmp_path / "draft_cache"
    prompt = "步行安全，全龄友好"
    producer = DesignAssistantService(
        llm_client=_FakeLLM(),
        knowledge_retriever=_FakeRetriever(),
        draft_cache_dir=cache_dir,
    )
    produced = producer.draft_design(
        messages=[{"role": "user", "content": prompt}],
        user_input=prompt,
        current_patch={},
        topk=4,
        knowledge_source="pdf_rag",
    )

    consumer = DesignAssistantService(
        llm_client=_FailIfLLMRuns(),
        knowledge_retriever=_FailIfRetrieverRuns(),
        draft_cache_dir=cache_dir,
    )
    cached = consumer.draft_design(
        messages=[{"role": "user", "content": prompt}],
        user_input=prompt,
        current_patch={},
        topk=4,
        knowledge_source="pdf_rag",
    )

    assert produced.cache_hit is False
    assert cached.cache_hit is True
    assert cached.draft is not None
    assert produced.draft is not None
    assert cached.draft.normalized_scene_query == produced.draft.normalized_scene_query
    assert cached.evidence[0].chunk_id == produced.evidence[0].chunk_id
