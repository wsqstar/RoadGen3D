from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
EVAL_ENGINE_EXT = SRC / "roadgen3d" / "eval_engine_ext"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(EVAL_ENGINE_EXT) not in sys.path:
    sys.path.insert(0, str(EVAL_ENGINE_EXT))

from roadgen3d.eval_engine_ext.road_metrics.core import engine as eval_engine_module
from roadgen3d.eval_engine_ext.road_metrics.core.engine import EvalEngine
from roadgen3d.eval_engine_ext.road_metrics.core.config import EvalConfig
from roadgen3d.eval_engine_ext.road_metrics.evaluators import beauty_eval, safety_eval
from roadgen3d.llm.design_workflow import DesignAssistantService
from web.api.main import create_app
from road_metrics.core import engine as standalone_eval_engine_module


def _minimal_layout_payload() -> dict:
    return {
        "summary": {
            "length_m": 80.0,
            "road_width_m": 12.0,
            "sidewalk_width_m": 3.0,
            "left_clear_path_width_m": 2.2,
            "right_clear_path_width_m": 2.2,
            "left_furnishing_width_m": 0.8,
            "right_furnishing_width_m": 0.8,
            "entrance_count": 2,
            "mean_entrance_openness": 0.8,
            "mean_noise_shielding": 0.2,
            "land_use_summary": {"retail": 2},
            "spatial_context": {"bus_stop_points_xz": [], "poi_points_by_type_xz": {}},
            "composition_report": {
                "presentation_score": 0.7,
                "style_coherence": 0.6,
                "visual_clutter": 0.2,
                "spacing_rhythm": 0.5,
                "focal_readability": 0.6,
            },
        },
        "config": {
            "lane_count": 2,
            "density": 1.0,
            "vehicle_demand_level": 0.5,
            "ped_demand_level": 0.6,
            "bike_demand_level": 0.1,
            "transit_demand_level": 0.2,
        },
        "placements": [{"category": "bollard"}],
    }


def test_eval_engine_falls_back_to_structural_scores_when_llm_unavailable(monkeypatch):
    def _unavailable(*args, **kwargs):
        return {
            "available": False,
            "source": "unavailable",
            "cached": False,
            "reasoning": "N/A",
            "error": "missing credentials",
        }

    monkeypatch.setattr(eval_engine_module, "evaluate_safety", _unavailable)
    monkeypatch.setattr(eval_engine_module, "evaluate_beauty", _unavailable)

    engine = EvalEngine(EvalConfig(enable_llm_eval=True))
    result = engine.evaluate(_minimal_layout_payload())

    assert result.safety.llm_scores is None
    assert result.beauty.llm_scores is None
    assert result.safety.llm_status["available"] is False
    assert result.beauty.llm_status["available"] is False
    assert result.safety.llm_status["error"] == "missing credentials"
    assert result.beauty.llm_status["error"] == "missing credentials"
    assert result.safety.final_score == result.safety.structural_score
    assert result.beauty.final_score == result.beauty.structural_score


def test_design_assistant_returns_na_for_missing_llm_subscores(tmp_path: Path, monkeypatch):
    payload = _minimal_layout_payload()
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps(payload), encoding="utf-8")

    def _unavailable(*args, **kwargs):
        return {
            "available": False,
            "source": "unavailable",
            "cached": False,
            "reasoning": "N/A",
            "error": "missing credentials",
        }

    monkeypatch.setattr(eval_engine_module, "evaluate_safety", _unavailable)
    monkeypatch.setattr(eval_engine_module, "evaluate_beauty", _unavailable)

    service = DesignAssistantService()
    result = service.evaluate_scene_unified(layout_path=str(layout_path))

    assert result["llm_status"]["safety"]["available"] is False
    assert result["llm_status"]["beauty"]["available"] is False
    assert result["llm_status"]["safety"]["visual_input"] == "missing"
    assert result["llm_status"]["beauty"]["visual_input"] == "missing"
    assert result["safety"] is None
    assert result["beauty"] is None
    assert result["overall"] == result["structured_composite_score"]
    assert result["structured_composite"]["included_visual_llm"] is False
    assert result["visual_llm_assessment"]["status"] == "n/a"
    assert result["indicators"]["safety_lighting"] is None
    assert result["indicators"]["beauty_coherence"] is None
    assert result["evaluation_profile"] == "local_segment_v1"
    assert result["indicator_meta"]["walkability"]["TRANSIT_PROX"]["low_discrimination"] is True
    assert result["indicator_meta"]["walkability"]["TREE_SHADE"]["source"] == (
        "solar_canopy_projection_union_over_local_sidewalk_grid"
    )
    assert (
        result["indicator_meta"]["walkability"]["TREE_SHADE"]["evidence"]["method"]
        == "solar_canopy_projection_v1"
    )
    assert result["indicator_meta"]["walkability"]["FURNITURE_OCCUPATION_RATIO"]["included_in_walkability_index"] is False
    assert isinstance(result["indicators"]["transit_proximity_score"], float)
    assert "vehicle_throughput_compliance" not in result["indicators"]
    assert result["indicator_meta"]["safety"]["missing_visual_policy"] == "n/a"
    assert result["indicator_meta"]["beauty"]["structural_fallback_in_overall"] is False
    assert "rule_satisfaction" not in result["indicators"]
    assert "experimental_evidence" not in result
    assert result["child_friendly"]["score"] is None
    assert result["child_friendly"]["status"] == "missing_child_view"


def test_default_structured_evaluation_never_calls_visual_llm(tmp_path: Path, monkeypatch):
    payload = _minimal_layout_payload()
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps(payload), encoding="utf-8")
    calls = {"count": 0}

    def _unexpected(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("structured evaluation must not call visual LLM")

    monkeypatch.setattr(eval_engine_module, "evaluate_safety", _unexpected)
    monkeypatch.setattr(eval_engine_module, "evaluate_beauty", _unexpected)

    result = DesignAssistantService().evaluate_scene_unified(layout_path=str(layout_path))

    assert calls["count"] == 0
    assert result["evaluation_mode"] == "structured"
    assert result["structured_composite_score"] == result["overall"]


def test_visual_llm_scores_are_independent_from_structured_composite(tmp_path: Path, monkeypatch):
    payload = _minimal_layout_payload()
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps(payload), encoding="utf-8")
    rendered_views = [{
        "view_id": "pedestrian_forward",
        "image_data_url": "data:image/png;base64,ZmFrZQ==",
    }]

    def _visual(score: float):
        return {
            "available": True,
            "source": "llm",
            "cached": False,
            "visual_input": "provided",
            "lighting": score,
            "visibility": score,
            "protection": score,
            "activation": score,
            "coherence": score,
            "human_scale": score,
            "material_contrast": score,
            "visual_interest": score,
            "overall": score,
            "reasoning": "fixed visual rubric",
        }

    monkeypatch.setattr(eval_engine_module, "evaluate_safety", lambda *args, **kwargs: _visual(0.2))
    monkeypatch.setattr(eval_engine_module, "evaluate_beauty", lambda *args, **kwargs: _visual(0.2))
    monkeypatch.setattr(standalone_eval_engine_module, "evaluate_safety", lambda *args, **kwargs: _visual(0.2))
    monkeypatch.setattr(standalone_eval_engine_module, "evaluate_beauty", lambda *args, **kwargs: _visual(0.2))
    low = DesignAssistantService().evaluate_scene_unified(
        layout_path=str(layout_path), rendered_views=rendered_views, evaluation_mode="full"
    )
    monkeypatch.setattr(eval_engine_module, "evaluate_safety", lambda *args, **kwargs: _visual(0.9))
    monkeypatch.setattr(eval_engine_module, "evaluate_beauty", lambda *args, **kwargs: _visual(0.9))
    monkeypatch.setattr(standalone_eval_engine_module, "evaluate_safety", lambda *args, **kwargs: _visual(0.9))
    monkeypatch.setattr(standalone_eval_engine_module, "evaluate_beauty", lambda *args, **kwargs: _visual(0.9))
    high = DesignAssistantService().evaluate_scene_unified(
        layout_path=str(layout_path), rendered_views=rendered_views, evaluation_mode="full"
    )

    assert low["structured_composite_score"] == high["structured_composite_score"]
    assert low["visual_llm_assessment"]["safety"]["score"] == 20
    assert high["visual_llm_assessment"]["safety"]["score"] == 90
    assert high["visual_llm_assessment"]["included_in_structured_composite"] is False


def test_design_assistant_auto_selects_network_profile_for_graph_layout(tmp_path: Path):
    payload = _minimal_layout_payload()
    payload["summary"]["road_segment_graph_summary"] = {
        "segment_count": 8,
        "road_count": 3,
        "avg_segment_length_m": 25.0,
    }
    layout_path = tmp_path / "network_scene_layout.json"
    layout_path.write_text(json.dumps(payload), encoding="utf-8")

    result = DesignAssistantService().evaluate_scene_unified(layout_path=str(layout_path))

    assert result["evaluation_profile"] == "network_v1"
    assert result["indicator_meta"]["walkability"]["TRANSIT_PROX"]["applicability"] == "network_proxy"
    assert result["indicator_meta"]["walkability"]["TREE_SHADE"]["source"] == (
        "summed_solar_projected_canopy_area_over_network_sidewalk_area_proxy"
    )


def test_design_assistant_rejects_cached_layout_only_scores(tmp_path: Path, monkeypatch):
    payload = _minimal_layout_payload()
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps(payload), encoding="utf-8")

    def _cached_without_visual_input(kind: str):
        base = {
            "available": True,
            "source": "cache",
            "cached": True,
            "reasoning": "old layout-only cache",
        }
        if kind == "safety":
            base.update({
                "lighting": 0.4,
                "visibility": 0.4,
                "protection": 0.4,
                "activation": 0.4,
                "overall": 0.4,
            })
        else:
            base.update({
                "coherence": 0.4,
                "human_scale": 0.4,
                "material_contrast": 0.4,
                "visual_interest": 0.4,
                "overall": 0.4,
            })
        return base

    monkeypatch.setattr(eval_engine_module, "evaluate_safety", lambda *args, **kwargs: _cached_without_visual_input("safety"))
    monkeypatch.setattr(eval_engine_module, "evaluate_beauty", lambda *args, **kwargs: _cached_without_visual_input("beauty"))
    monkeypatch.setattr(standalone_eval_engine_module, "evaluate_safety", lambda *args, **kwargs: _cached_without_visual_input("safety"))
    monkeypatch.setattr(standalone_eval_engine_module, "evaluate_beauty", lambda *args, **kwargs: _cached_without_visual_input("beauty"))

    service = DesignAssistantService()
    result = service.evaluate_scene_unified(layout_path=str(layout_path), evaluation_mode="full")

    assert result["llm_status"]["safety"]["source"] == "cache"
    assert result["llm_status"]["safety"]["visual_input"] == "missing"
    assert result["llm_status"]["beauty"]["source"] == "cache"
    assert result["llm_status"]["beauty"]["visual_input"] == "missing"
    assert result["safety"] is None
    assert result["beauty"] is None
    assert result["overall"] == result["structured_composite_score"]
    assert result["visual_llm_assessment"]["status"] == "n/a"


def test_eval_engine_passes_rendered_views_to_visual_evaluators(monkeypatch):
    rendered_views = [
        {
            "view_id": "pedestrian_forward",
            "label": "Pedestrian forward view",
            "image_data_url": "data:image/png;base64,ZmFrZQ==",
        }
    ]
    calls = {"safety": None, "beauty": None}

    def _safety_available(*args, **kwargs):
        calls["safety"] = kwargs
        return {
            "available": True,
            "source": "llm",
            "cached": False,
            "visual_input": "provided",
            "lighting": 0.8,
            "visibility": 0.7,
            "protection": 0.6,
            "activation": 0.75,
            "overall": 0.7,
            "reasoning": "visual safety evidence",
            "model": {"provider": "openai", "capability": "vision", "model": "vision-a"},
        }

    def _beauty_available(*args, **kwargs):
        calls["beauty"] = kwargs
        return {
            "available": True,
            "source": "llm",
            "cached": False,
            "visual_input": "provided",
            "coherence": 0.8,
            "human_scale": 0.75,
            "material_contrast": 0.65,
            "visual_interest": 0.7,
            "overall": 0.72,
            "reasoning": "visual beauty evidence",
            "model": {"provider": "openai", "capability": "vision", "model": "vision-a"},
        }

    monkeypatch.setattr(eval_engine_module, "evaluate_safety", _safety_available)
    monkeypatch.setattr(eval_engine_module, "evaluate_beauty", _beauty_available)

    engine = EvalEngine(EvalConfig(enable_llm_eval=True))
    result = engine.evaluate(_minimal_layout_payload(), rendered_views=rendered_views)

    assert calls["safety"]["rendered_views"] == rendered_views
    assert calls["beauty"]["rendered_views"] == rendered_views
    assert result.safety.llm_status["visual_input"] == "provided"
    assert result.beauty.llm_status["visual_input"] == "provided"
    assert result.safety.llm_status["model"]["model"] == "vision-a"
    assert result.beauty.llm_status["model"]["capability"] == "vision"
    assert result.safety.llm_scores is not None
    assert result.beauty.llm_scores is not None


def test_visual_evaluator_normalizers_preserve_view_context():
    rendered_views = [
        {
            "view_id": "bench_eye_1",
            "label": "Bench eye view",
            "kind": "bench_eye",
            "camera": [4.0, 1.35, 2.0],
            "target": [4.0, 0.8, 0.0],
            "priority": 75,
            "projection": "perspective",
            "horizontal_fov_deg": 63.0,
            "vertical_fov_deg": 41.0,
            "content_origin": "viewer_webgl_capture",
            "image_data_url": "data:image/png;base64,ZmFrZQ==",
        }
    ]

    safety_views = safety_eval._normalize_rendered_views(rendered_views, None)
    beauty_views = beauty_eval._normalize_rendered_views(rendered_views, None)

    assert safety_views[0]["kind"] == "bench_eye"
    assert beauty_views[0]["camera"] == [4.0, 1.35, 2.0]
    assert safety_views[0]["projection"] == "perspective"
    assert beauty_views[0]["horizontal_fov_deg"] == 63.0
    safety_messages = safety_eval._build_safety_eval_messages({}, safety_views)
    beauty_messages = beauty_eval._build_beauty_eval_messages({}, beauty_views)
    assert "viewer_webgl_capture" in safety_messages[1]["content"][0]["text"]
    assert "bench_eye" in safety_messages[1]["content"][0]["text"]
    assert "bench_eye" in beauty_messages[1]["content"][0]["text"]


def test_visual_evaluator_cache_keys_include_model_and_camera_context():
    rendered_views = [{
        "view_id": "street_1",
        "label": "Street view",
        "kind": "street",
        "camera": [0.0, 1.6, 8.0],
        "target": [0.0, 1.2, 0.0],
        "projection": "perspective",
        "horizontal_fov_deg": 60.0,
        "vertical_fov_deg": 40.0,
        "content_origin": "viewer_webgl_capture",
        "image_data_url": "data:image/png;base64,ZmFrZQ==",
    }]
    normalized = safety_eval._normalize_rendered_views(rendered_views, None)
    model_a = {"provider": "openai", "capability": "vision", "model": "model-a"}
    model_b = {"provider": "openai", "capability": "vision", "model": "model-b"}

    safety_a = safety_eval._cache_key({}, normalized, model_a)
    safety_b = safety_eval._cache_key({}, normalized, model_b)
    beauty_a = beauty_eval._cache_key({}, normalized, model_a)
    beauty_b = beauty_eval._cache_key({}, normalized, model_b)

    assert safety_a != safety_b
    assert beauty_a != beauty_b


def test_visual_cache_hit_does_not_require_live_credentials(monkeypatch):
    identity = {
        "provider": "openai",
        "protocol": "openai_chat_completions",
        "capability": "vision",
        "model": "vision-a",
        "endpoint_fingerprint": "sha256:test",
    }
    monkeypatch.setattr(
        safety_eval.LLMSettings,
        "public_identity_from_env",
        classmethod(lambda cls, capability="text": identity),
    )
    monkeypatch.setattr(safety_eval, "_load_cached", lambda _key: {
        "lighting": 0.8,
        "visibility": 0.7,
        "protection": 0.6,
        "activation": 0.7,
        "overall": 0.7,
        "reasoning": "cached visual evidence",
        "evidence": {
            key: [{"view_id": "street_1", "observation": f"Visible evidence for {key}."}]
            for key in ("lighting", "visibility", "protection", "activation", "overall")
        },
        "limitations": ["Single synthetic view."],
        "confidence": 0.6,
    })

    class FailIfClientConstructed:
        def __init__(self):
            raise AssertionError("cache hit must not require a live client")

    monkeypatch.setattr(safety_eval, "LLMClient", FailIfClientConstructed)
    result = safety_eval.evaluate_safety(
        features={},
        rendered_views=[{
            "view_id": "street_1",
            "label": "Street view",
            "image_data_url": "data:image/png;base64,ZmFrZQ==",
        }],
    )

    assert result["source"] == "cache"
    assert result["model"] == identity


def test_design_assistant_auto_selects_representative_captured_views(tmp_path: Path):
    payload = _minimal_layout_payload()
    capture_dir = tmp_path / "view_captures"
    capture_dir.mkdir()
    capture_views = []
    for index, kind in enumerate((
        "street",
        "junction_pedestrian",
        "junction_pedestrian",
        "bench_eye",
        "window_view",
        "rooftop",
        "overview",
        "junction",
        "building",
    ), start=1):
        path = capture_dir / f"{index:02d}_{kind}.png"
        path.write_bytes(b"png")
        capture_views.append({
            "view_id": f"{kind}_{index}",
            "label": f"{kind} view",
            "kind": kind,
            "priority": 100 - index,
            "path": str(path),
        })
    payload["summary"]["render_views_3d"] = capture_views
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps(payload), encoding="utf-8")

    class _FakeEngine:
        def __init__(self):
            self.rendered_views = None
            self.config = SimpleNamespace(
                aggregation=SimpleNamespace(
                    walkability_weight=0.45,
                    safety_weight=0.35,
                    beauty_weight=0.20,
                )
            )

        def evaluate(self, payload, *, rendered_views=None, image_path=None):
            self.rendered_views = list(rendered_views or [])
            return SimpleNamespace(
                walkability=SimpleNamespace(
                    walkability_index=0.8,
                    protection=0.7,
                    comfort=0.8,
                    delight=0.75,
                    sid_clr=0.8,
                    furn_d=0.6,
                    tree_shade=0.7,
                    transit_prox=0.5,
                ),
                safety=SimpleNamespace(
                    final_score=0.72,
                    llm_scores={"lighting": 0.7, "visibility": 0.8, "protection": 0.6, "activation": 0.7},
                    llm_status={"available": True, "visual_input": "provided"},
                    diagnosis={},
                ),
                beauty=SimpleNamespace(
                    final_score=0.74,
                    llm_scores={"coherence": 0.7, "human_scale": 0.8, "material_contrast": 0.7, "visual_interest": 0.75},
                    llm_status={"available": True, "visual_input": "provided"},
                    diagnosis={},
                ),
                evaluation_score=0.76,
            )

    service = DesignAssistantService()
    fake_engine = _FakeEngine()
    service.eval_engine = fake_engine

    result = service.evaluate_scene_unified(layout_path=str(layout_path), evaluation_mode="full")

    assert result["llm_status"]["safety"]["visual_input"] == "provided"
    assert len(fake_engine.rendered_views) == 8
    assert {"bench_eye", "junction_pedestrian", "window_view", "rooftop"}.issubset({
        view.get("kind")
        for view in fake_engine.rendered_views
    })


def test_unified_api_passes_rendered_views_to_service():
    class _Service:
        default_pdf_path = Path("/tmp/guide.pdf")
        default_artifact_dir = Path("/tmp/knowledge")

        def __init__(self):
            self.kwargs = None

        def evaluate_scene_unified(self, **kwargs):
            self.kwargs = kwargs
            return {
                "walkability": 80,
                "safety": 70,
                "beauty": 72,
                "overall": 74,
                "evaluation": "ok",
                "suggestions": [],
                "indicators": {},
                "config_patch": {},
                "llm_status": {
                    "safety": {"source": "llm", "visual_input": "provided"},
                    "beauty": {"source": "llm", "visual_input": "provided"},
                },
            }

    service = _Service()
    client = TestClient(create_app(design_service=service))
    rendered_views = [
        {
            "view_id": "pedestrian_forward",
            "label": "Pedestrian forward view",
            "image_data_url": "data:image/png;base64,ZmFrZQ==",
        }
    ]

    response = client.post(
        "/api/design/evaluate/unified",
        json={
            "layout_path": "/tmp/scene_layout.json",
            "rendered_views": rendered_views,
            "evaluation_profile": "network_v1",
        },
    )

    assert response.status_code == 200
    assert service.kwargs["layout_path"] == "/tmp/scene_layout.json"
    assert service.kwargs["rendered_views"] == rendered_views
    assert service.kwargs["evaluation_profile"] == "network_v1"


def test_unified_api_returns_child_friendly_na_without_child_view(tmp_path: Path, monkeypatch):
    payload = _minimal_layout_payload()
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps(payload), encoding="utf-8")

    def _available(*args, **kwargs):
        return {
            "available": True,
            "source": "llm",
            "cached": False,
            "visual_input": "provided",
            "lighting": 0.7,
            "visibility": 0.7,
            "protection": 0.7,
            "activation": 0.7,
            "coherence": 0.7,
            "human_scale": 0.7,
            "material_contrast": 0.7,
            "visual_interest": 0.7,
            "overall": 0.7,
            "reasoning": "visual evidence",
        }

    monkeypatch.setattr(eval_engine_module, "evaluate_safety", _available)
    monkeypatch.setattr(eval_engine_module, "evaluate_beauty", _available)
    monkeypatch.setattr(standalone_eval_engine_module, "evaluate_safety", _available)
    monkeypatch.setattr(standalone_eval_engine_module, "evaluate_beauty", _available)

    service = DesignAssistantService()
    client = TestClient(create_app(design_service=service))
    rendered_views = [
        {
            "view_id": "pedestrian_forward",
            "label": "Pedestrian forward view",
            "image_data_url": "data:image/png;base64,ZmFrZQ==",
        }
    ]

    response = client.post(
        "/api/design/evaluate/unified",
        json={"layout_path": str(layout_path), "rendered_views": rendered_views, "evaluation_mode": "full"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["evaluation_profile"] == "local_segment_v1"
    assert payload["indicator_meta"]["walkability"]["TRANSIT_PROX"]["low_discrimination"] is True
    assert payload["child_friendly"]["score"] is None
    assert payload["child_friendly"]["status"] == "missing_child_view"


def test_unified_api_scores_child_friendly_with_child_view(tmp_path: Path, monkeypatch):
    payload = _minimal_layout_payload()
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps(payload), encoding="utf-8")

    def _available(*args, **kwargs):
        return {
            "available": True,
            "source": "llm",
            "cached": False,
            "visual_input": "provided",
            "lighting": 0.7,
            "visibility": 0.7,
            "protection": 0.7,
            "activation": 0.7,
            "coherence": 0.7,
            "human_scale": 0.7,
            "material_contrast": 0.7,
            "visual_interest": 0.7,
            "overall": 0.7,
            "reasoning": "visual evidence",
        }

    monkeypatch.setattr(eval_engine_module, "evaluate_safety", _available)
    monkeypatch.setattr(eval_engine_module, "evaluate_beauty", _available)
    monkeypatch.setattr(standalone_eval_engine_module, "evaluate_safety", _available)
    monkeypatch.setattr(standalone_eval_engine_module, "evaluate_beauty", _available)

    service = DesignAssistantService()
    client = TestClient(create_app(design_service=service))
    rendered_views = [
        {
            "view_id": "pedestrian_forward",
            "label": "Pedestrian forward view",
            "image_data_url": "data:image/png;base64,ZmFrZQ==",
        },
        {
            "view_id": "child_forward",
            "label": "Child forward view",
            "image_data_url": "data:image/png;base64,ZmFrZQ==",
        },
    ]

    response = client.post(
        "/api/design/evaluate/unified",
        json={
            "layout_path": str(layout_path),
            "rendered_views": rendered_views,
            "evaluation_mode": "full",
        },
    )

    assert response.status_code == 200
    child_friendly = response.json()["child_friendly"]
    assert child_friendly["status"] == "scored_structural_v1"
    assert isinstance(child_friendly["score"], int)
    assert child_friendly["indicators"]["visual_input"] == "provided"
    assert child_friendly["indicators"]["view_id"] == "child_forward"
    assert child_friendly["indicators"]["visual_pixels_scored"] is False
    assert child_friendly["indicators"]["image_role"] == "availability_gate_only"
    assert child_friendly["limitations"]
