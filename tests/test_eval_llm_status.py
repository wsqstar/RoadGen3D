from __future__ import annotations

import json
import sys
from pathlib import Path

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
    assert result["overall"] is None
    assert result["indicators"]["safety_lighting"] is None
    assert result["indicators"]["beauty_coherence"] is None


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
    result = service.evaluate_scene_unified(layout_path=str(layout_path))

    assert result["llm_status"]["safety"]["source"] == "cache"
    assert result["llm_status"]["safety"]["visual_input"] == "missing"
    assert result["llm_status"]["beauty"]["source"] == "cache"
    assert result["llm_status"]["beauty"]["visual_input"] == "missing"
    assert result["safety"] is None
    assert result["beauty"] is None
    assert result["overall"] is None


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
        }

    monkeypatch.setattr(eval_engine_module, "evaluate_safety", _safety_available)
    monkeypatch.setattr(eval_engine_module, "evaluate_beauty", _beauty_available)

    engine = EvalEngine(EvalConfig(enable_llm_eval=True))
    result = engine.evaluate(_minimal_layout_payload(), rendered_views=rendered_views)

    assert calls["safety"]["rendered_views"] == rendered_views
    assert calls["beauty"]["rendered_views"] == rendered_views
    assert result.safety.llm_status["visual_input"] == "provided"
    assert result.beauty.llm_status["visual_input"] == "provided"
    assert result.safety.llm_scores is not None
    assert result.beauty.llm_scores is not None


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
        json={"layout_path": "/tmp/scene_layout.json", "rendered_views": rendered_views},
    )

    assert response.status_code == 200
    assert service.kwargs["layout_path"] == "/tmp/scene_layout.json"
    assert service.kwargs["rendered_views"] == rendered_views
