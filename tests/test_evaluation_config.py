from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
EVAL_ENGINE_EXT = SRC / "roadgen3d" / "eval_engine_ext"
for path in (ROOT, SRC, EVAL_ENGINE_EXT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from road_metrics.core import engine as eval_engine_module
from road_metrics.core.config import AggregationConfig, EvalConfig
from road_metrics.core.engine import EvalEngine
from roadgen3d.llm.design_workflow import DesignAssistantService
from web.api.main import create_app


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
            "spatial_context": {
                "bus_stop_points_xz": [],
                "poi_points_by_type_xz": {},
            },
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


def _unavailable_visual_score(*_args, **_kwargs) -> dict:
    return {
        "available": False,
        "source": "unavailable",
        "cached": False,
        "reasoning": "N/A",
        "error": "disabled in focused test",
    }


def test_default_and_manual_dimension_weights_are_normalized() -> None:
    default = EvalConfig.default().aggregation.normalized_dimension_weights(
        ("walkability", "safety", "beauty")
    )
    assert default == pytest.approx({
        "walkability": 1.0 / 3.0,
        "safety": 1.0 / 3.0,
        "beauty": 1.0 / 3.0,
    })

    configured = EvalConfig.from_dict({
        "aggregation": {
            "dimension_weights": {"walkability": 2, "safety": 1, "beauty": 1}
        }
    })
    assert configured.aggregation.normalized_dimension_weights(
        ("walkability", "safety", "beauty")
    ) == pytest.approx({
        "walkability": 0.5,
        "safety": 0.25,
        "beauty": 0.25,
    })
    assert EvalEngine(configured)._aggregate_scores(0.9, 0.6, 0.3) == pytest.approx(0.675)


def test_unconfigured_future_component_falls_back_to_raw_weight_one() -> None:
    aggregation = AggregationConfig(dimension_weights={"walkability": 2.0})
    resolved = aggregation.normalized_dimension_weights(
        ("walkability", "safety", "beauty", "climate")
    )
    assert resolved == pytest.approx({
        "walkability": 0.4,
        "safety": 0.2,
        "beauty": 0.2,
        "climate": 0.2,
    })


def test_evaluation_config_round_trip_preserves_raw_overrides_and_defaults() -> None:
    configured = EvalConfig.from_dict({
        "evaluation_profile": "network_v1",
        "aggregation": {
            "dimension_weights": {"walkability": 2.0, "safety": 1.0, "beauty": 1.0}
        },
        "walkability": {
            "clear_width_min": 2.0,
            "clear_width_ideal": 3.8,
            "amenity_density_ideal": 0.2,
            "amenity_count_density_ideal": 0.12,
            "lamp_spacing_m": 22.0,
            "transit_stop_spacing_m": 360.0,
            "crossing_spacing_m": 70.0,
            "entrance_density_ideal": 0.05,
            "tree_shade_grid_resolution_m": 0.4,
            "tree_sun_azimuth_deg": 160.0,
            "tree_sun_elevation_deg": 38.0,
            "tree_canopy_center_height_ratio": 0.68,
            "tree_canopy_vertical_ratio": 0.22,
        },
    })

    restored = EvalConfig.from_dict(configured.to_dict())

    assert restored.to_dict() == configured.to_dict()
    assert restored.walkability.network_mode is True
    assert restored.aggregation.dimension_weights == {
        "walkability": 2.0,
        "safety": 1.0,
        "beauty": 1.0,
    }


@pytest.mark.parametrize(
    "override, message",
    [
        (
            {"aggregation": {"dimension_weights": {"walkability": -1.0}}},
            "must be non-negative",
        ),
        (
            {"aggregation": {"dimension_weights": {"walkability": math.inf}}},
            "must be finite",
        ),
        (
            {
                "aggregation": {
                    "dimension_weights": {
                        "walkability": 0.0,
                        "safety": 0.0,
                        "beauty": 0.0,
                    }
                }
            },
            "positive weight",
        ),
        (
            {"walkability": {"clear_width_min": 3.0, "clear_width_ideal": 2.0}},
            "greater than clear_width_min",
        ),
        (
            {"walkability": {"tree_shade_grid_resolution_m": 0.0}},
            "must be greater than zero",
        ),
        (
            {"walkability": {"tree_sun_elevation_deg": 0.5}},
            "must be in [1, 90]",
        ),
        (
            {"walkability": {"tree_canopy_vertical_ratio": 0.6}},
            "must be in (0, 0.5]",
        ),
        (
            {
                "walkability": {
                    "tree_canopy_center_height_ratio": 0.9,
                    "tree_canopy_vertical_ratio": 0.2,
                }
            },
            "canopy radius within the tree height",
        ),
    ],
)
def test_malformed_evaluation_config_is_rejected(override: dict, message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        EvalConfig.from_dict(override)


def test_unified_service_merges_profile_overrides_and_preserves_visual_na(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eval_engine_module, "evaluate_safety", _unavailable_visual_score)
    monkeypatch.setattr(eval_engine_module, "evaluate_beauty", _unavailable_visual_score)
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps(_minimal_layout_payload()), encoding="utf-8")

    result = DesignAssistantService().evaluate_scene_unified(
        layout_path=str(layout_path),
        evaluation_profile="network_v1",
        evaluation_config={
            "aggregation": {
                "dimension_weights": {"walkability": 2, "safety": 1, "beauty": 1}
            },
            "walkability": {
                "clear_width_min": 2.1,
                "clear_width_ideal": 3.6,
                "tree_sun_azimuth_deg": 150.0,
            },
        },
    )

    assert result["score_weights"] == pytest.approx({
        "walkability": 0.5,
        "safety": 0.25,
        "beauty": 0.25,
    })
    effective_config = result["effective_evaluation_config"]
    assert effective_config["aggregation"]["dimension_weights"] == pytest.approx({
        "walkability": 0.5,
        "safety": 0.25,
        "beauty": 0.25,
    })
    assert effective_config["walkability"]["clear_width_min"] == 2.1
    assert effective_config["walkability"]["tree_sun_azimuth_deg"] == 150.0
    assert effective_config["walkability"]["lamp_spacing_m"] == 25.0
    tree_shade_meta = result["indicator_meta"]["walkability"]["TREE_SHADE"]
    assert tree_shade_meta["source"] == (
        "summed_solar_projected_canopy_area_over_network_sidewalk_area_proxy"
    )
    assert tree_shade_meta["evidence"]["method"] == "solar_canopy_projection_v1"
    assert tree_shade_meta["evidence"]["sun_azimuth_deg"] == 150.0
    assert result["safety"] is None
    assert result["beauty"] is None
    assert result["overall"] is None


def test_unified_api_forwards_evaluation_config_and_returns_service_payload() -> None:
    class _Service:
        default_pdf_path = Path("/tmp/guide.pdf")
        default_artifact_dir = Path("/tmp/knowledge")

        def __init__(self) -> None:
            self.kwargs = None

        def evaluate_scene_unified(self, **kwargs):
            self.kwargs = kwargs
            return {
                "score_weights": {
                    "walkability": 0.5,
                    "safety": 0.25,
                    "beauty": 0.25,
                },
                "effective_evaluation_config": {
                    **kwargs["evaluation_config"],
                    "aggregation": {
                        "dimension_weights": {
                            "walkability": 0.5,
                            "safety": 0.25,
                            "beauty": 0.25,
                        }
                    },
                },
            }

    service = _Service()
    client = TestClient(create_app(design_service=service))
    evaluation_config = {
        "aggregation": {
            "dimension_weights": {"walkability": 2, "safety": 1, "beauty": 1}
        },
        "walkability": {
            "clear_width_min": 2.0,
            "clear_width_ideal": 3.5,
            "tree_sun_elevation_deg": 35.0,
        },
    }

    response = client.post(
        "/api/design/evaluate/unified",
        json={
            "layout_path": "/tmp/scene_layout.json",
            "evaluation_config": evaluation_config,
        },
    )

    assert response.status_code == 200
    assert service.kwargs["evaluation_config"] == evaluation_config
    assert response.json()["effective_evaluation_config"]["aggregation"] == {
        "dimension_weights": {
            "walkability": 0.5,
            "safety": 0.25,
            "beauty": 0.25,
        }
    }
    assert response.json()["score_weights"] == {
        "walkability": 0.5,
        "safety": 0.25,
        "beauty": 0.25,
    }


def test_unified_api_returns_400_for_all_zero_weights(tmp_path: Path) -> None:
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps(_minimal_layout_payload()), encoding="utf-8")
    client = TestClient(create_app(design_service=DesignAssistantService()))

    response = client.post(
        "/api/design/evaluate/unified",
        json={
            "layout_path": str(layout_path),
            "evaluation_config": {
                "aggregation": {
                    "dimension_weights": {
                        "walkability": 0,
                        "safety": 0,
                        "beauty": 0,
                    }
                }
            },
        },
    )

    assert response.status_code == 400
    assert "positive weight" in response.json()["detail"]
