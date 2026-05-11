from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.design_runtime import build_compose_config_from_draft, generate_scene_from_draft
from roadgen3d.presets import COURSE_DELIVERY_CONFIG_PATCH, SCENE_PRESETS
from roadgen3d.services.design_types import DesignDraft, RagEvidence, SceneContext, sanitize_compose_config_patch
from roadgen3d.services.scene_context_service import ResolvedSceneContext
import roadgen3d.services.design_runtime as runtime


def test_normalize_scene_generation_options_includes_design_variant_fields(tmp_path: Path):
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text("{}", encoding="utf-8")

    options = runtime.normalize_scene_generation_options(
        {
            "manifest_path": str(layout_path),
            "artifacts_dir": str(tmp_path),
            "out_dir": str(tmp_path),
            "design_variant_id": "variant-abc",
            "design_variant_name": "Variant Alpha",
            "random_seed": "42",
            "preset_id": "custom",
        }
    )

    assert options.design_variant_id == "variant-abc"
    assert options.design_variant_name == "Variant Alpha"
    assert options.random_seed == 42


def test_generate_scene_from_draft_includes_design_variant_metadata_in_summary(tmp_path: Path, monkeypatch):
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 5}}), encoding="utf-8")

    monkeypatch.setattr(
        runtime,
        "compose_street_scene",
        lambda **kwargs: SimpleNamespace(
            instance_count=5,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        ),
    )
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="safe complete street",
        compose_config_patch={"road_width_m": 6.5, "sidewalk_width_m": 4.0},
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(
        draft,
        generation_options={
            "out_dir": str(tmp_path),
            "preset_id": "skip_llm",
            "design_variant_id": "variant-007",
            "design_variant_name": "Urban Baseline",
            "random_seed": 77,
        },
    )

    assert result.summary["design_variant_id"] == "variant-007"
    assert result.summary["design_variant_name"] == "Urban Baseline"
    assert result.summary["random_seed"] == 77

def test_build_compose_config_from_draft_applies_defaults():
    draft = DesignDraft(
        normalized_scene_query="safe complete street",
        compose_config_patch={"sidewalk_width_m": 4.5, "design_rule_profile": "pedestrian_priority_v1"},
        citations_by_field={},
        design_summary="summary",
    )

    config = build_compose_config_from_draft(draft)

    assert config.query == "safe complete street"
    assert config.sidewalk_width_m == 4.5
    assert config.design_rule_profile == "pedestrian_priority_v1"
    assert config.style_preset == "civic_clean_v1"
    assert config.beauty_mode == "presentation_v1"
    assert config.layout_solver == "hybrid_milp_v1"
    assert config.program_generator == "heuristic_v1"
    assert config.allow_solver_fallback is True
    assert config.asset_scale_mode == "canonical_v1"
    assert config.asset_curation_mode == "scene_ready_first"
    assert config.curated_street_assets_profile == "fixed_hq_v1"
    assert config.scene_texture_mode == "topdown_tiles_v1"
    assert config.topdown_render_mode == "design_tiles_v1"
    assert config.lane_count == 2


def test_sanitize_compose_config_patch_accepts_course_delivery_fields():
    patch = sanitize_compose_config_patch(
        {
            "layout_solver": "hybrid_milp_v1",
            "program_generator": "heuristic_v1",
            "allow_solver_fallback": "true",
            "asset_scale_mode": "canonical_v1",
            "asset_curation_mode": "scene_ready_first",
            "curated_street_assets_profile": "fixed_hq_v1",
            "scene_texture_mode": "topdown_tiles_v1",
            "topdown_render_mode": "design_tiles_v1",
            "render_preset": "axonometric_board_v1",
            "beauty_mode": "presentation_v1",
            "unsupported": "drop-me",
        }
    )

    for key, value in COURSE_DELIVERY_CONFIG_PATCH.items():
        assert patch[key] == value
    assert "unsupported" not in patch


def test_sanitize_compose_config_patch_drops_unregistered_profiles():
    patch = sanitize_compose_config_patch(
        {
            "design_rule_profile": "transit_oriented_development_v1",
            "style_preset": "modern_urban_v1",
            "street_furniture_profile": "transit_priority",
        }
    )

    assert patch["street_furniture_profile"] == "transit_priority"
    assert "design_rule_profile" not in patch
    assert "style_preset" not in patch


def test_sanitize_compose_config_patch_accepts_osm_multiblock_fields():
    patch = sanitize_compose_config_patch(
        {
            "osm_semantic_mode": "landuse_rules_v1",
            "osm_multiblock_max_roads": "8",
            "osm_multiblock_max_extent_m": "275.5",
            "osm_context_fit_mode": "auto_design",
        }
    )

    assert patch["osm_semantic_mode"] == "landuse_rules_v1"
    assert patch["osm_multiblock_max_roads"] == 8
    assert patch["osm_multiblock_max_extent_m"] == 275.5
    assert patch["osm_context_fit_mode"] == "auto_design"


def test_shared_presets_include_course_delivery_defaults():
    for preset in SCENE_PRESETS:
        config_patch = preset["configPatch"]
        for key, value in COURSE_DELIVERY_CONFIG_PATCH.items():
            assert config_patch[key] == value


def test_preset_rag_config_loads_from_repo_assets():
    config = runtime._load_preset_rag_config("pedestrian_friendly")

    assert config["knowledge_source"] == "graph_rag"
    assert "pedestrian priority street design safety" in config["rag_queries"]


def test_build_compose_config_from_draft_applies_explicit_beauty_fields():
    draft = DesignDraft(
        normalized_scene_query="lush neighborhood street",
        compose_config_patch={
            "sidewalk_width_m": 4.0,
            "style_preset": "lush_walkable_v1",
            "beauty_mode": "presentation_v1",
        },
        citations_by_field={},
        design_summary="summary",
    )

    config = build_compose_config_from_draft(draft)

    assert config.style_preset == "lush_walkable_v1"
    assert config.beauty_mode == "presentation_v1"


def test_generate_scene_from_draft_wraps_existing_scene_pipeline(tmp_path: Path, monkeypatch):
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 8, "dropped_slots": 1}}), encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        runtime,
        "compose_street_scene",
        lambda **kwargs: (
            captured.update(
                {
                    "model_name": kwargs.get("model_name"),
                    "model_dir": kwargs.get("model_dir"),
                    "local_files_only": kwargs.get("local_files_only"),
                }
            )
            or SimpleNamespace(
                instance_count=8,
                dropped_slots=1,
                outputs={
                    "scene_layout": str(layout_path),
                    "scene_glb": str(tmp_path / "scene.glb"),
                    "scene_ply": str(tmp_path / "scene.ply"),
                },
            )
        ),
    )
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="safe complete street",
        compose_config_patch={"road_width_m": 6.5, "sidewalk_width_m": 4.0},
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(draft)

    assert result.viewer_url.startswith("http://127.0.0.1:4173/")
    assert result.summary["instance_count"] == 8
    assert result.compose_config["road_width_m"] == 6.5
    assert captured["model_name"] == "openai/clip-vit-base-patch32"
    assert captured["model_dir"] == runtime.DEFAULT_CLIP_MODEL_DIR
    assert captured["local_files_only"] is True


def test_generate_scene_from_draft_passes_progress_callback_to_compose(tmp_path: Path, monkeypatch):
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 4}}), encoding="utf-8")
    received_events: list[dict[str, object]] = []
    captured: dict[str, object] = {}

    def _fake_compose(**kwargs):
        captured["progress_callback"] = kwargs.get("progress_callback")
        kwargs["progress_callback"]({
            "stage": "asset_composition",
            "progress": 66,
            "message": "Placing street assets.",
        })
        return SimpleNamespace(
            instance_count=4,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        )

    monkeypatch.setattr(runtime, "compose_street_scene", _fake_compose)
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="safe complete street",
        compose_config_patch={"road_width_m": 6.5, "sidewalk_width_m": 4.0},
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(draft, progress_callback=received_events.append)

    assert result.summary["instance_count"] == 4
    assert callable(captured["progress_callback"])
    assert any(event["stage"] == "asset_composition" and event["progress"] == 66 for event in received_events)


def test_generate_scene_from_draft_custom_preset_uses_llm_graph_context(tmp_path: Path, monkeypatch):
    from roadgen3d.llm import design_workflow

    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 3}}), encoding="utf-8")
    received_events: list[dict[str, object]] = []
    captured: dict[str, object] = {}

    bridge = SimpleNamespace(
        summary_metadata={
            "layout_mode": "graph_template",
            "graph_template_id": "demo_template",
            "road_count": 7,
            "junction_count": 2,
            "total_length_m": 123.5,
        },
        road_segment_graph=SimpleNamespace(),
        projected_features=SimpleNamespace(),
        placement_context=SimpleNamespace(),
    )
    monkeypatch.setattr(runtime, "build_graph_template_scene_bridge", lambda *args, **kwargs: bridge)

    class _FakeLlmClient:
        def chat_json(self, messages):
            user_content = messages[-1]["content"][0]["text"]
            payload = json.loads(user_content)
            captured["llm_payload"] = payload
            captured["graph_summary"] = payload["graph_summary"]
            return {
                "compose_config_patch": {
                    "road_width_m": 9.0,
                    "sidewalk_width_m": 4.2,
                    "density": 0.72,
                    "design_rule_profile": "pedestrian_priority_v1",
                },
                "design_summary": "LLM derived walkable street.",
            }

    class _FakeAssistant:
        def _retrieve_evidence(self, *, queries, topk, knowledge_source):
            captured["rag_queries"] = tuple(queries)
            captured["knowledge_source"] = knowledge_source
            return [
                RagEvidence(
                    chunk_id="guide-001",
                    doc_id="complete-streets",
                    section_title="Pedestrian Clear Path",
                    page_start=12,
                    page_end=13,
                    text="Keep pedestrian clear paths wide and continuous near transit stops.",
                    source_path="guides/complete-streets.pdf",
                    score=0.91,
                    knowledge_source=knowledge_source,
                    parameter_hints={"sidewalk_width_m": "Widen clear path near transit."},
                )
            ]

        def _retrieve_scenario_parameter_evidence(self, *, queries, topk, parameter_names=None):
            captured["structured_rag_queries"] = tuple(queries)
            return [
                RagEvidence(
                    chunk_id=(
                        "scenario_parameters::matrix::street_type_walkable_commercial_corridor::"
                        "sidewalk_width_m"
                    ),
                    doc_id="scenario_parameter_triples",
                    section_title="Walkable Commercial Corridor / sidewalk_width_m",
                    page_start=0,
                    page_end=0,
                    text=(
                        '{"scenario_id":"street_type.walkable_commercial_corridor",'
                        '"parameter_name":"sidewalk_width_m","normalized_value":3.658,"unit":"m"}'
                    ),
                    source_path="knowledge/scenario_parameter_triples.jsonl",
                    score=0.97,
                    knowledge_source="scenario_parameters",
                    parameter_hints={"sidewalk_width_m": "Structured sidewalk width triple."},
                )
            ]

        def _get_llm_client(self):
            return _FakeLlmClient()

    monkeypatch.setattr(design_workflow, "DesignAssistantService", _FakeAssistant)
    monkeypatch.setattr(
        runtime,
        "compose_street_scene",
        lambda **kwargs: SimpleNamespace(
            instance_count=3,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        ),
    )
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="make it very walkable",
        compose_config_patch={},
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(
        draft,
        scene_context={"layout_mode": "graph_template", "graph_template_id": "demo_template"},
        generation_options={"preset_id": "custom"},
        progress_callback=received_events.append,
    )

    assert captured["graph_summary"]["road_count"] == 7
    assert captured["rag_queries"]
    assert captured["knowledge_source"] == "graph_rag"
    user_payload = captured["llm_payload"]
    assert user_payload["graph_summary"]["road_count"] == 7
    assert user_payload["knowledge_source"] == "graph_rag"
    assert user_payload["rag_queries"]
    assert user_payload["rag_evidence"][0]["chunk_id"] == "guide-001"
    assert user_payload["rag_evidence"][0]["parameter_hints"]["sidewalk_width_m"]
    assert any(item["knowledge_source"] == "scenario_parameters" for item in user_payload["rag_evidence"])
    assert result.compose_config["road_width_m"] == 9.0
    assert result.compose_config["sidewalk_width_m"] == 4.2
    llm_event = next(event for event in received_events if event["stage"] == "context_resolving" and event["progress"] == 18)
    detail = llm_event["detail"]
    assert detail["graph_summary"]["road_count"] == 7
    assert detail["evidence_count"] == 2
    assert detail["rag_evidence"][0]["chunk_id"] == "guide-001"
    assert any(item["knowledge_source"] == "scenario_parameters" for item in detail["rag_evidence"])
    assert detail["parameter_sources_by_field"]["query"] == "prompt_input"
    assert detail["parameter_sources_by_field"]["road_width_m"] == "llm_derived"
    assert detail["parameter_sources_by_field"]["target_street_type"] == "default_after_llm"
    assert "target_street_type" in detail["defaulted_fields"]
    assert "target_street_type" not in detail["llm_raw_fields"]


def test_generate_scene_from_draft_uses_preset_rag_queries(tmp_path: Path, monkeypatch):
    from roadgen3d.llm import design_workflow

    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 2}}), encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        runtime,
        "build_graph_template_scene_bridge",
        lambda *args, **kwargs: SimpleNamespace(
            summary_metadata={"layout_mode": "graph_template", "road_count": 2},
            road_segment_graph=SimpleNamespace(),
            projected_features=SimpleNamespace(),
            placement_context=SimpleNamespace(),
        ),
    )

    class _FakeLlmClient:
        def chat_json(self, messages):
            payload = json.loads(messages[-1]["content"][0]["text"])
            captured["llm_payload"] = payload
            return {
                "compose_config_patch": {"road_width_m": 8.0, "sidewalk_width_m": 3.2},
                "design_summary": "Preset RAG adjusted street.",
            }

    class _FakeAssistant:
        def _retrieve_evidence(self, *, queries, topk, knowledge_source):
            captured["rag_queries"] = tuple(queries)
            captured["knowledge_source"] = knowledge_source
            return [
                RagEvidence(
                    chunk_id="preset-guide-001",
                    doc_id="complete-streets",
                    section_title="Pedestrian Priority",
                    page_start=1,
                    page_end=1,
                    text="Prioritize pedestrian safety on walkable streets.",
                    source_path="guides/complete-streets.pdf",
                    score=0.9,
                    knowledge_source=knowledge_source,
                )
            ]

        def _retrieve_scenario_parameter_evidence(self, *, queries, topk, parameter_names=None):
            captured["structured_rag_queries"] = tuple(queries)
            return [
                RagEvidence(
                    chunk_id="scenario_parameters::preset::pedestrian_friendly::sidewalk_width_m",
                    doc_id="scenario_parameter_triples",
                    section_title="Pedestrian Friendly / sidewalk_width_m",
                    page_start=0,
                    page_end=0,
                    text=(
                        '{"scenario_id":"preset.pedestrian_friendly",'
                        '"parameter_name":"sidewalk_width_m","normalized_value":3.2,"unit":"m"}'
                    ),
                    source_path="knowledge/scenario_parameter_triples.jsonl",
                    score=0.95,
                    knowledge_source="scenario_parameters",
                )
            ]

        def _get_llm_client(self):
            return _FakeLlmClient()

    monkeypatch.setattr(design_workflow, "DesignAssistantService", _FakeAssistant)
    monkeypatch.setattr(
        runtime,
        "compose_street_scene",
        lambda **kwargs: SimpleNamespace(
            instance_count=2,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        ),
    )
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="make it safer",
        compose_config_patch={},
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(
        draft,
        scene_context={"layout_mode": "graph_template", "graph_template_id": "demo_template"},
        generation_options={"preset_id": "pedestrian_friendly"},
    )

    assert captured["knowledge_source"] == "graph_rag"
    assert captured["rag_queries"][0] == "make it safer"
    assert "pedestrian priority street design safety" in captured["rag_queries"]
    assert captured["structured_rag_queries"] == captured["rag_queries"]
    user_payload = captured["llm_payload"]
    assert user_payload["knowledge_source"] == "graph_rag"
    assert "pedestrian priority street design safety" in user_payload["rag_queries"]
    assert any(item["knowledge_source"] == "scenario_parameters" for item in user_payload["rag_evidence"])
    assert result.compose_config["sidewalk_width_m"] == 3.2


def test_generate_scene_from_draft_custom_preset_keeps_explicit_patch_over_llm(tmp_path: Path, monkeypatch):
    from roadgen3d.llm import design_workflow

    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 5}}), encoding="utf-8")
    received_events: list[dict[str, object]] = []
    captured: dict[str, object] = {}

    bridge = SimpleNamespace(
        summary_metadata={
            "layout_mode": "graph_template",
            "graph_template_id": "demo_template",
            "road_count": 4,
            "junction_count": 1,
            "total_length_m": 88.0,
        },
        road_segment_graph=SimpleNamespace(),
        projected_features=SimpleNamespace(),
        placement_context=SimpleNamespace(),
    )
    monkeypatch.setattr(runtime, "build_graph_template_scene_bridge", lambda *args, **kwargs: bridge)

    class _FakeLlmClient:
        def chat_json(self, messages):
            user_content = messages[-1]["content"][0]["text"]
            payload = json.loads(user_content)
            captured["current_patch"] = payload["current_patch"]
            return {
                "compose_config_patch": {
                    "road_width_m": 15.0,
                    "sidewalk_width_m": 6.0,
                    "density": 0.72,
                    "design_rule_profile": "pedestrian_priority_v1",
                },
                "design_summary": "LLM tried to widen the road.",
            }

    class _FakeAssistant:
        def _get_llm_client(self):
            return _FakeLlmClient()

    monkeypatch.setattr(design_workflow, "DesignAssistantService", _FakeAssistant)
    monkeypatch.setattr(
        runtime,
        "compose_street_scene",
        lambda **kwargs: SimpleNamespace(
            instance_count=5,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        ),
    )
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="keep my explicit widths",
        compose_config_patch={"road_width_m": 6.5, "sidewalk_width_m": 4.0},
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(
        draft,
        scene_context={"layout_mode": "graph_template", "graph_template_id": "demo_template"},
        generation_options={"preset_id": "custom"},
        progress_callback=received_events.append,
    )

    assert captured["current_patch"]["road_width_m"] == 6.5
    assert result.compose_config["road_width_m"] == 6.5
    assert result.compose_config["sidewalk_width_m"] == 4.0
    assert result.compose_config["density"] == 0.72
    llm_event = next(event for event in received_events if event["stage"] == "context_resolving" and event["progress"] == 18)
    detail = llm_event["detail"]
    assert detail["parameter_sources_by_field"]["road_width_m"] == "explicit_input"
    assert detail["parameter_sources_by_field"]["sidewalk_width_m"] == "explicit_input"
    assert detail["parameter_sources_by_field"]["density"] == "llm_derived"
    assert detail["parameter_sources_by_field"]["target_street_type"] == "default_after_llm"
    assert "road_width_m" in detail["overridden_llm_fields"]
    assert "sidewalk_width_m" in detail["overridden_llm_fields"]


def test_generate_scene_from_draft_style_blend_preserves_base_and_promotes_target(tmp_path: Path, monkeypatch):
    from roadgen3d.llm import design_workflow

    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 4}}), encoding="utf-8")
    received_events: list[dict[str, object]] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        runtime,
        "build_graph_template_scene_bridge",
        lambda *args, **kwargs: SimpleNamespace(
            summary_metadata={
                "layout_mode": "graph_template",
                "graph_template_id": "demo_template",
                "road_count": 2,
                "junction_count": 1,
                "total_length_m": 80.0,
            },
            road_segment_graph=SimpleNamespace(),
            projected_features=SimpleNamespace(),
            placement_context=SimpleNamespace(),
        ),
    )

    class _FakeLlmClient:
        def chat_json(self, messages):
            payload = json.loads(messages[-1]["content"][0]["text"])
            captured["current_patch"] = payload["current_patch"]
            return {
                "compose_config_patch": {
                    "street_furniture_profile": "transit_priority",
                    "design_rule_profile": "transit_oriented_development_v1",
                    "style_preset": "modern_urban_v1",
                    "density": 0.95,
                    "transit_demand_level": "high",
                    "vehicle_demand_level": "high",
                },
                "design_summary": "LLM suggested transit emphasis, including invalid profile names.",
            }

    class _FakeAssistant:
        def _get_llm_client(self):
            return _FakeLlmClient()

    monkeypatch.setattr(design_workflow, "DesignAssistantService", _FakeAssistant)
    monkeypatch.setattr(
        runtime,
        "compose_street_scene",
        lambda **kwargs: SimpleNamespace(
            instance_count=4,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        ),
    )
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="把当前步行友好街道融合公交优先风格，增加公交设施优先级。",
        compose_config_patch={
            "street_furniture_profile": "pedestrian_friendly",
            "street_furniture_profile_source": "manual",
            "street_furniture_profile_confidence": 1.0,
            "design_rule_profile": "pedestrian_priority_v1",
            "objective_profile": "balanced",
            "style_preset": "analytical_diorama_v1",
            "density": 0.5,
            "ped_demand_level": "high",
            "bike_demand_level": "medium",
            "transit_demand_level": "medium",
            "vehicle_demand_level": "low",
            "minimum_category_presence": ("lamp", "bench", "trash", "bollard"),
        },
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(
        draft,
        scene_context={"layout_mode": "graph_template", "graph_template_id": "demo_template"},
        generation_options={"preset_id": "pedestrian_friendly"},
        progress_callback=received_events.append,
    )

    assert captured["current_patch"]["street_furniture_profile"] == "pedestrian_friendly"
    assert result.compose_config["street_furniture_profile"] == "pedestrian_friendly"
    assert result.compose_config["design_rule_profile"] == "pedestrian_priority_v1"
    assert result.compose_config["style_preset"] == "analytical_diorama_v1"
    assert result.compose_config["density"] == 0.7
    assert result.compose_config["ped_demand_level"] == "high"
    assert result.compose_config["transit_demand_level"] == "high"
    assert result.compose_config["vehicle_demand_level"] == "medium"
    assert result.compose_config["max_bus_stops_per_scene"] == 2
    assert result.compose_config["allow_demo_bus_stop_when_osm_absent"] is True
    assert "bus_stop" in result.compose_config["minimum_category_presence"]
    assert "bollard" in result.compose_config["minimum_category_presence"]
    llm_event = next(event for event in received_events if event["stage"] == "context_resolving" and event["progress"] == 18)
    detail = llm_event["detail"]
    assert detail["style_blend_mode"] == "blend"
    assert detail["style_blend_base_profile"] == "pedestrian_friendly"
    assert detail["style_blend_target_profile"] == "transit_priority"
    assert detail["parameter_sources_by_field"]["street_furniture_profile"] == "explicit_input"
    assert detail["parameter_sources_by_field"]["design_rule_profile"] == "explicit_input"
    assert detail["parameter_sources_by_field"]["style_preset"] == "explicit_input"
    assert detail["parameter_sources_by_field"]["density"] == "style_blend_target"
    assert "bus_stop" in detail["style_blend_patch"]["minimum_category_presence"]
    assert "street_furniture_profile" in detail["style_blend_preserved_explicit_fields"]
    assert "density" in detail["style_blend_overridden_explicit_fields"]
    assert "street_furniture_profile" in detail["overridden_llm_fields"]
    assert "style_preset" not in detail["llm_raw_fields"]
    assert "design_rule_profile" not in detail["llm_raw_fields"]


def test_generate_scene_from_draft_style_transfer_target_overrides_old_explicit_preset(tmp_path: Path, monkeypatch):
    from roadgen3d.llm import design_workflow

    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 4}}), encoding="utf-8")
    received_events: list[dict[str, object]] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        runtime,
        "build_graph_template_scene_bridge",
        lambda *args, **kwargs: SimpleNamespace(
            summary_metadata={
                "layout_mode": "graph_template",
                "graph_template_id": "demo_template",
                "road_count": 2,
                "junction_count": 1,
                "total_length_m": 80.0,
            },
            road_segment_graph=SimpleNamespace(),
            projected_features=SimpleNamespace(),
            placement_context=SimpleNamespace(),
        ),
    )

    class _FakeLlmClient:
        def chat_json(self, messages):
            payload = json.loads(messages[-1]["content"][0]["text"])
            captured["current_patch"] = payload["current_patch"]
            return {
                "compose_config_patch": {
                    "street_furniture_profile": "transit_priority",
                    "design_rule_profile": "transit_oriented_development_v1",
                    "style_preset": "modern_urban_v1",
                    "density": 0.75,
                    "transit_demand_level": "high",
                    "vehicle_demand_level": "medium",
                },
                "design_summary": "LLM requested a transit priority conversion.",
            }

    class _FakeAssistant:
        def _get_llm_client(self):
            return _FakeLlmClient()

    monkeypatch.setattr(design_workflow, "DesignAssistantService", _FakeAssistant)
    monkeypatch.setattr(
        runtime,
        "compose_street_scene",
        lambda **kwargs: SimpleNamespace(
            instance_count=4,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        ),
    )
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="把当前步行友好街道转为公交优先风格，增加公交设施优先级。",
        compose_config_patch={
            "street_furniture_profile": "pedestrian_friendly",
            "street_furniture_profile_source": "manual",
            "street_furniture_profile_confidence": 1.0,
            "design_rule_profile": "pedestrian_priority_v1",
            "objective_profile": "balanced",
            "style_preset": "analytical_diorama_v1",
            "density": 0.5,
            "ped_demand_level": "high",
            "bike_demand_level": "medium",
            "transit_demand_level": "medium",
            "vehicle_demand_level": "low",
        },
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(
        draft,
        scene_context={"layout_mode": "graph_template", "graph_template_id": "demo_template"},
        generation_options={"preset_id": "pedestrian_friendly"},
        progress_callback=received_events.append,
    )

    assert captured["current_patch"]["street_furniture_profile"] == "pedestrian_friendly"
    assert result.compose_config["street_furniture_profile"] == "transit_priority"
    assert result.compose_config["design_rule_profile"] == "transit_priority_v1"
    assert result.compose_config["style_preset"] == "transit_modern_v1"
    assert result.compose_config["density"] == 0.85
    assert result.compose_config["transit_demand_level"] == "high"
    assert result.compose_config["vehicle_demand_level"] == "high"
    llm_event = next(event for event in received_events if event["stage"] == "context_resolving" and event["progress"] == 18)
    detail = llm_event["detail"]
    assert detail["style_transfer_target_profile"] == "transit_priority"
    assert detail["parameter_sources_by_field"]["style_preset"] == "style_transfer_target"
    assert detail["parameter_sources_by_field"]["design_rule_profile"] == "style_transfer_target"
    assert "style_preset" in detail["style_transfer_overridden_explicit_fields"]
    assert "design_rule_profile" in detail["style_transfer_overridden_explicit_fields"]


def test_generate_scene_from_draft_custom_preset_fails_when_llm_fails(monkeypatch):
    from roadgen3d.llm import design_workflow

    received_events: list[dict[str, object]] = []

    class _FailingAssistant:
        def _get_llm_client(self):
            class _Client:
                def chat_json(self, _messages):
                    raise RuntimeError("llm offline")

            return _Client()

    monkeypatch.setattr(design_workflow, "DesignAssistantService", _FailingAssistant)
    monkeypatch.setattr(
        runtime,
        "compose_street_scene",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("compose should not run after LLM failure")),
    )
    draft = DesignDraft(
        normalized_scene_query="make it very walkable",
        compose_config_patch={},
        citations_by_field={},
        design_summary="summary",
    )

    try:
        generate_scene_from_draft(
            draft,
            scene_context={"layout_mode": "graph_template", "graph_template_id": "hkust_gz_gate"},
            generation_options={"preset_id": "custom"},
            progress_callback=received_events.append,
        )
    except RuntimeError as exc:
        assert "LLM parameter derivation failed" in str(exc)
    else:
        raise AssertionError("custom LLM generation should fail when LLM derivation fails")
    failure_event = next(event for event in received_events if event["stage"] == "context_resolving" and event["progress"] == 15)
    assert failure_event["detail"]["llm_derivation_status"] == "failed"
    assert failure_event["detail"]["llm_error_type"] == "RuntimeError"


def test_generate_scene_from_draft_uses_sanitized_cached_layout_summary(tmp_path: Path, monkeypatch):
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text('{"summary":{"instance_count": 8, "clearance_m": Infinity}}', encoding="utf-8")
    cached_layout = tmp_path / "cached_scene_layout.json"
    cached_layout.write_text(
        json.dumps({"summary": {"instance_count": 8, "clearance_m": None}}, ensure_ascii=True),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        runtime,
        "compose_street_scene",
        lambda **kwargs: SimpleNamespace(
            instance_count=8,
            dropped_slots=1,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        ),
    )
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda _layout: cached_layout)
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="safe complete street",
        compose_config_patch={"road_width_m": 6.5, "sidewalk_width_m": 4.0},
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(draft)

    assert result.summary["instance_count"] == 8
    assert result.summary["clearance_m"] is None


def test_generate_scene_from_draft_applies_osm_scene_context(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 4, "building_footprint_count": 12}}), encoding="utf-8")

    def _fake_compose(**kwargs):
        captured["config"] = kwargs["config"]
        return SimpleNamespace(
            instance_count=4,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        )

    monkeypatch.setattr(
        runtime,
        "compose_street_scene",
        _fake_compose,
    )
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")
    monkeypatch.setattr(
        runtime,
        "resolve_scene_context",
        lambda scene_context, *, config, artifacts_dir: ResolvedSceneContext(
            scene_context=scene_context,
            requested_aoi_bbox=(113.2660, 23.1280, 113.2710, 23.1325),
            effective_aoi_bbox=(113.2670, 23.1290, 113.2700, 23.1320),
            city_name_en="guangzhou",
            selected_road_osm_id=202,
            selected_road_discovered_poi_count=5,
            selected_road_discovered_poi_score=4.2,
            selected_road_discovered_core_poi_count=2,
            selected_road_source="cached_discovery",
            probe_metrics={"row_width_m": 13.2},
        ),
    )

    draft = DesignDraft(
        normalized_scene_query="safe complete street",
        compose_config_patch={"road_width_m": 6.5, "sidewalk_width_m": 4.0},
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(
        draft,
        scene_context=SceneContext(
            layout_mode="osm",
            aoi_bbox=(113.2660, 23.1280, 113.2710, 23.1325),
            city_name_en="guangzhou",
        ),
    )

    config = captured["config"]
    assert config.layout_mode == "osm"
    assert config.aoi_bbox == (113.2670, 23.1290, 113.2700, 23.1320)
    assert config.selected_road_osm_id == 202
    assert result.summary["requested_aoi_bbox"] == [113.266, 23.128, 113.271, 23.1325]
    assert result.summary["city_name_en"] == "guangzhou"


def test_generate_scene_from_draft_applies_osm_multiblock_context(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(
        json.dumps(
            {
                "summary": {
                    "instance_count": 4,
                    "layout_mode": "osm_multiblock",
                    "semantic_block_count": 3,
                }
            }
        ),
        encoding="utf-8",
    )

    def _fake_compose(**kwargs):
        captured["config"] = kwargs["config"]
        return SimpleNamespace(
            instance_count=4,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        )

    monkeypatch.setattr(runtime, "compose_street_scene", _fake_compose)
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")
    monkeypatch.setattr(
        runtime,
        "resolve_scene_context",
        lambda scene_context, *, config, artifacts_dir: ResolvedSceneContext(
            scene_context=scene_context,
            requested_aoi_bbox=(113.2660, 23.1280, 113.2710, 23.1325),
            effective_aoi_bbox=(113.2660, 23.1280, 113.2710, 23.1325),
            city_name_en="guangzhou",
            road_selection="all",
            selected_road_source="multiblock_aoi",
            probe_metrics={"semantic_mode": "landuse_rules_v1"},
        ),
    )

    draft = DesignDraft(
        normalized_scene_query="multi block semantic street",
        compose_config_patch={"osm_multiblock_max_roads": 8, "osm_multiblock_max_extent_m": 275.0},
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(
        draft,
        scene_context=SceneContext(
            layout_mode="osm_multiblock",
            aoi_bbox=(113.2660, 23.1280, 113.2710, 23.1325),
            city_name_en="guangzhou",
        ),
        generation_options={"capture_3d_views": False, "preset_id": "skip_llm"},
    )

    config = captured["config"]
    assert config.layout_mode == "osm_multiblock"
    assert config.aoi_bbox == (113.2660, 23.1280, 113.2710, 23.1325)
    assert config.road_selection == "all"
    assert config.selected_road_osm_id is None
    assert config.osm_multiblock_max_roads == 8
    assert result.summary["semantic_block_count"] == 3


def test_generate_scene_from_draft_requires_bbox_for_osm_scene_context():
    draft = DesignDraft(
        normalized_scene_query="safe complete street",
        compose_config_patch={"road_width_m": 6.5, "sidewalk_width_m": 4.0},
        citations_by_field={},
        design_summary="summary",
    )

    try:
        generate_scene_from_draft(
            draft,
            scene_context={"layout_mode": "osm"},
        )
    except RuntimeError as exc:
        assert "AOI bbox" in str(exc)
    else:
        raise AssertionError("Expected missing OSM bbox to raise RuntimeError")


def test_generate_scene_from_draft_supports_metaurban_reference_layout(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 6, "frontage_parcel_count": 3}}), encoding="utf-8")

    monkeypatch.setattr(
        runtime,
        "resolve_scene_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resolve_scene_context should not run for metaurban")),
    )
    monkeypatch.setattr(
        runtime,
        "build_metaurban_scene_bridge",
        lambda config, *, plan_id: SimpleNamespace(
            road_segment_graph=object(),
            projected_features=object(),
            placement_context=object(),
            summary_metadata={
                "layout_mode": "metaurban",
                "reference_plan_id": plan_id,
                "reference_plan_label": "HKUST-GZ Gate",
                "total_network_length_m": 188.0,
            },
        ),
    )

    def _fake_compose(**kwargs):
        captured["config"] = kwargs["config"]
        captured["road_segment_graph_override"] = kwargs["road_segment_graph_override"]
        captured["projected_features_override"] = kwargs["projected_features_override"]
        captured["placement_context_override"] = kwargs["placement_context_override"]
        return SimpleNamespace(
            instance_count=6,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        )

    monkeypatch.setattr(runtime, "compose_street_scene", _fake_compose)
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="campus gateway boulevard",
        compose_config_patch={
            "road_width_m": 10.5,
            "sidewalk_width_m": 3.0,
            "lane_count": 3,
            "length_m": 96.0,
        },
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(
        draft,
        generation_options={"out_dir": str(tmp_path), "preset_id": "skip_llm"},
        scene_context=SceneContext(
            layout_mode="metaurban",
            reference_plan_id="hkust_gz_gate",
        ),
    )

    layout_path = Path(result.scene_layout_path)
    payload = json.loads(layout_path.read_text(encoding="utf-8"))

    assert layout_path.exists()
    assert captured["config"].layout_mode == "metaurban"
    assert captured["road_segment_graph_override"] is not None
    assert captured["projected_features_override"] is not None
    assert captured["placement_context_override"] is not None
    assert result.viewer_url.startswith("http://127.0.0.1:4173/")
    assert result.scene_glb_path.endswith("scene.glb")
    assert result.summary["layout_mode"] == "metaurban"
    assert result.summary["reference_plan_id"] == "hkust_gz_gate"
    assert payload["summary"]["total_network_length_m"] > 0.0


def test_generate_scene_from_draft_supports_graph_template_layout(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 9, "junction_geometry_count": 3}}), encoding="utf-8")

    monkeypatch.setattr(
        runtime,
        "resolve_scene_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resolve_scene_context should not run for graph_template")),
    )
    monkeypatch.setattr(
        runtime,
        "build_graph_template_scene_bridge",
        lambda config, *, template_id: SimpleNamespace(
            road_segment_graph=object(),
            projected_features=object(),
            placement_context=object(),
            summary_metadata={
                "layout_mode": "graph_template",
                "graph_template_id": template_id,
                "graph_template_label": "HKUST-GZ Gate Graph",
                "graph_template_source_format": "roadgen3d_reference_annotation_v2",
            },
        ),
    )

    def _fake_compose(**kwargs):
        captured["config"] = kwargs["config"]
        captured["road_segment_graph_override"] = kwargs["road_segment_graph_override"]
        captured["projected_features_override"] = kwargs["projected_features_override"]
        captured["placement_context_override"] = kwargs["placement_context_override"]
        return SimpleNamespace(
            instance_count=9,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        )

    monkeypatch.setattr(runtime, "compose_street_scene", _fake_compose)
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="campus gateway boulevard",
        compose_config_patch={
            "road_width_m": 10.5,
            "sidewalk_width_m": 3.0,
            "lane_count": 3,
            "length_m": 96.0,
        },
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(
        draft,
        generation_options={"out_dir": str(tmp_path), "preset_id": "skip_llm"},
        scene_context=SceneContext(
            layout_mode="graph_template",
            graph_template_id="hkust_gz_gate",
        ),
    )

    payload = json.loads(Path(result.scene_layout_path).read_text(encoding="utf-8"))

    assert captured["config"].layout_mode == "graph_template"
    assert captured["road_segment_graph_override"] is not None
    assert captured["projected_features_override"] is not None
    assert captured["placement_context_override"] is not None
    assert result.summary["layout_mode"] == "graph_template"
    assert result.summary["graph_template_id"] == "hkust_gz_gate"
    assert payload["summary"]["graph_template_source_format"] == "roadgen3d_reference_annotation_v2"


def test_generate_scene_from_draft_supports_reference_annotation_layout(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}
    annotation_path = tmp_path / "reference_annotation.json"
    annotation_path.write_text(
        json.dumps({
            "version": "roadgen3d_reference_annotation_v2",
            "plan_id": "scenario_demo",
            "image_path": "/api/graph-templates/hkust_gz_gate/image",
            "image_width_px": 1024,
            "image_height_px": 170,
            "pixels_per_meter": 1.5,
            "centerlines": [],
        }),
        encoding="utf-8",
    )
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 5}}), encoding="utf-8")

    monkeypatch.setattr(
        runtime,
        "resolve_scene_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resolve_scene_context should not run for reference_annotation")),
    )

    def _fake_bridge(annotation_payload, *, compose_config):
        captured["annotation_payload"] = annotation_payload
        captured["bridge_config"] = compose_config
        return SimpleNamespace(
            road_segment_graph=object(),
            projected_features=object(),
            placement_context=object(),
            summary_metadata={
                "layout_mode": "annotation",
                "generator": "reference_annotation_bridge_v1",
                "centerline_count": 2,
            },
        )

    monkeypatch.setattr(runtime, "build_reference_annotation_scene_bridge", _fake_bridge)

    def _fake_compose(**kwargs):
        captured["config"] = kwargs["config"]
        captured["road_segment_graph_override"] = kwargs["road_segment_graph_override"]
        captured["projected_features_override"] = kwargs["projected_features_override"]
        captured["placement_context_override"] = kwargs["placement_context_override"]
        return SimpleNamespace(
            instance_count=5,
            dropped_slots=0,
            outputs={
                "scene_layout": str(layout_path),
                "scene_glb": str(tmp_path / "scene.glb"),
                "scene_ply": str(tmp_path / "scene.ply"),
            },
        )

    monkeypatch.setattr(runtime, "compose_street_scene", _fake_compose)
    monkeypatch.setattr(runtime, "cache_scene_layout_for_viewer", lambda layout: Path(layout))
    monkeypatch.setattr(runtime, "build_web_viewer_url", lambda _layout: "http://127.0.0.1:4173/?layout=demo")

    draft = DesignDraft(
        normalized_scene_query="reference annotation scene",
        compose_config_patch={"road_width_m": 12.0, "sidewalk_width_m": 3.0, "lane_count": 4},
        citations_by_field={},
        design_summary="summary",
    )
    result = generate_scene_from_draft(
        draft,
        generation_options={"out_dir": str(tmp_path), "preset_id": "skip_llm"},
        scene_context={
            "layout_mode": "reference_annotation",
            "reference_annotation_path": str(annotation_path),
            "scenario_id": "scenario_demo",
            "scenario_title": "Scenario Demo",
        },
    )

    payload = json.loads(Path(result.scene_layout_path).read_text(encoding="utf-8"))

    assert captured["annotation_payload"]["plan_id"] == "scenario_demo"
    assert captured["config"].layout_mode == "reference_annotation"
    assert captured["road_segment_graph_override"] is not None
    assert captured["projected_features_override"] is not None
    assert captured["placement_context_override"] is not None
    assert result.summary["layout_mode"] == "annotation"
    assert result.summary["scenario_id"] == "scenario_demo"
    assert result.summary["reference_annotation_path"] == str(annotation_path)
    assert payload["summary"]["generator"] == "reference_annotation_bridge_v1"
