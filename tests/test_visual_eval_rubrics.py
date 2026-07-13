from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.eval_engine_ext.road_metrics.evaluators import beauty_eval, safety_eval

_VIEW = {
    "view_id": "pedestrian_forward",
    "label": "Pedestrian forward",
    "kind": "street",
    "projection": "perspective",
    "horizontal_fov_deg": 60.0,
    "vertical_fov_deg": 40.0,
    "content_origin": "viewer_webgl_capture",
    "image_data_url": "data:image/png;base64,ZmFrZQ==",
}

_EVALUATORS = (
    pytest.param(
        safety_eval,
        safety_eval.evaluate_safety,
        safety_eval._build_safety_eval_messages,
        ("lighting", "visibility", "protection", "activation", "overall"),
        "safety",
        id="safety",
    ),
    pytest.param(
        beauty_eval,
        beauty_eval.evaluate_beauty,
        beauty_eval._build_beauty_eval_messages,
        ("coherence", "human_scale", "material_contrast", "visual_interest", "overall"),
        "beauty",
        id="beauty",
    ),
)


class _FakeVisionClient:
    def __init__(self, payload: object):
        self.payload = payload
        self.calls = 0

    def chat_json(self, _messages, *, temperature: float, capability: str):
        self.calls += 1
        assert temperature == 0.0
        assert capability == "vision"
        return self.payload


def _valid_live_payload(score_keys: tuple[str, ...]) -> dict[str, Any]:
    scores = {key: float((index % 5) + 1) for index, key in enumerate(score_keys)}
    return {
        **scores,
        "reasoning": "Visible features are cited without treating context as proof.",
        "evidence": {
            key: [{"view_id": _VIEW["view_id"], "observation": f"Visible evidence for {key}."}]
            for key in score_keys
        },
        "limitations": ["One synthetic perspective view provides limited coverage."],
        "confidence": 0.7,
    }


def _valid_cached_payload(score_keys: tuple[str, ...]) -> dict[str, Any]:
    return {
        **{key: 0.6 for key in score_keys},
        "reasoning": "Cached visible-evidence assessment.",
        "evidence": {
            key: [{"view_id": _VIEW["view_id"], "observation": f"Visible evidence for {key}."}]
            for key in score_keys
        },
        "limitations": ["Single-view coverage."],
        "confidence": 0.6,
    }


@pytest.mark.parametrize("_module,_evaluate,build_messages,score_keys,_name", _EVALUATORS)
def test_visual_eval_prompts_define_strict_visible_evidence_rubrics(
    _module,
    _evaluate,
    build_messages,
    score_keys: tuple[str, ...],
    _name: str,
):
    messages = build_messages({"context_metric": 0.9}, [_VIEW])
    system_prompt = messages[0]["content"]
    user_payload = json.loads(messages[1]["content"][0]["text"])

    assert "ONLY from directly visible image evidence" in system_prompt
    assert "synthetic renders" in system_prompt
    assert "Structured metrics are context only" in system_prompt
    assert "NEVER visual proof" in system_prompt
    assert "view_id" in system_prompt
    assert '"evidence"' in system_prompt
    assert '"limitations"' in system_prompt
    assert '"confidence"' in system_prompt
    assert "Confidence is one top-level number from 0 to 1" in system_prompt
    assert "projection" in system_prompt
    assert "field of view" in system_prompt
    assert "content_origin" in system_prompt
    for forbidden_inference in ("nighttime", "operational", "social", "material", "regulatory"):
        assert forbidden_inference in system_prompt

    rubric_starts = [system_prompt.index(f"{key} (") for key in score_keys]
    for index, (key, start) in enumerate(zip(score_keys, rubric_starts)):
        end = rubric_starts[index + 1] if index + 1 < len(rubric_starts) else len(system_prompt)
        dimension_rubric = system_prompt[start:end]
        for anchor in range(1, 6):
            assert f"{anchor} =" in dimension_rubric, f"missing anchor {anchor} for {key}"

    assert user_payload["structured_features_context_only"] == {"context_metric": 0.9}
    assert user_payload["rendered_views"][0]["view_id"] == _VIEW["view_id"]
    assert user_payload["rendered_views"][0]["projection"] == "perspective"
    assert "context" in user_payload["instruction"]
    assert "synthetic-render/content-origin" in user_payload["instruction"]
    assert set(json.loads(system_prompt[system_prompt.index('{"') : system_prompt.index("}\n") + 1])["evidence"]) == set(score_keys)
    if _name == "safety":
        assert "Daylight cannot establish nighttime illumination or safety" in system_prompt
        assert "visible active-frontage and passive-surveillance cues" in system_prompt
        assert "never infer actual activity, occupancy, or crime" in system_prompt
    else:
        assert "Synthetic texture/render choices may create or suppress apparent material contrast" in system_prompt
        assert "subject to synthetic texture limitations" in system_prompt


@pytest.mark.parametrize("module,evaluate,_build,score_keys,name", _EVALUATORS)
@pytest.mark.parametrize(
    "defect,expected_detail",
    (
        ("missing", "missing required score key 'overall'."),
        ("non_numeric", "required score key 'overall' must be a finite number from 1 to 5."),
        ("below_range", "required score key 'overall' must be between 1 and 5; got 0."),
        ("fractional", "required score key 'overall' must be an integer from 1 to 5; got 2.5."),
        ("above_range", "required score key 'overall' must be between 1 and 5; got 6."),
    ),
)
def test_live_visual_payload_validation_rejects_invalid_required_scores(
    monkeypatch,
    module,
    evaluate,
    _build,
    score_keys: tuple[str, ...],
    name: str,
    defect: str,
    expected_detail: str,
):
    payload = _valid_live_payload(score_keys)
    if defect == "missing":
        del payload["overall"]
    elif defect == "non_numeric":
        payload["overall"] = "5"
    elif defect == "below_range":
        payload["overall"] = 0
    elif defect == "above_range":
        payload["overall"] = 6
    else:
        payload["overall"] = 2.5

    client = _FakeVisionClient(payload)
    monkeypatch.setattr(module, "_load_cached", lambda _key: None)
    monkeypatch.setattr(module, "_save_cached", lambda _key, _result: None)

    result = evaluate(features={}, rendered_views=[_VIEW], llm_client=client)

    assert client.calls == 1
    assert result["available"] is False
    assert result["source"] == "unavailable"
    assert result["cached"] is False
    assert result["reasoning"] == "N/A"
    assert result["error"] == f"Invalid live {name} evaluation payload: {expected_detail}"


@pytest.mark.parametrize("module,evaluate,_build,score_keys,name", _EVALUATORS)
@pytest.mark.parametrize(
    "defect,expected_detail",
    (
        ("missing_evidence", "evidence must be an object keyed by every score dimension."),
        ("unknown_view", "evidence for 'overall' cites an unknown view_id."),
        ("empty_limitations", "limitations must be a non-empty array of strings."),
        ("invalid_confidence", "confidence must be a finite number from 0 to 1."),
    ),
)
def test_live_visual_payload_validation_rejects_untraceable_metadata(
    monkeypatch,
    module,
    evaluate,
    _build,
    score_keys: tuple[str, ...],
    name: str,
    defect: str,
    expected_detail: str,
):
    payload = _valid_live_payload(score_keys)
    if defect == "missing_evidence":
        del payload["evidence"]
    elif defect == "unknown_view":
        payload["evidence"]["overall"][0]["view_id"] = "invented_view"
    elif defect == "empty_limitations":
        payload["limitations"] = []
    else:
        payload["confidence"] = 1.5

    client = _FakeVisionClient(payload)
    monkeypatch.setattr(module, "_load_cached", lambda _key: None)
    monkeypatch.setattr(module, "_save_cached", lambda _key, _result: None)

    result = evaluate(features={}, rendered_views=[_VIEW], llm_client=client)

    assert client.calls == 1
    assert result["available"] is False
    assert result["source"] == "unavailable"
    assert result["error"] == f"Invalid live {name} evaluation payload: {expected_detail}"


@pytest.mark.parametrize("module,evaluate,_build,score_keys,name", _EVALUATORS)
@pytest.mark.parametrize(
    "defect,expected_detail",
    (
        ("missing", "missing required score key 'overall'."),
        ("out_of_range", "required score key 'overall' must be between 0 and 1; got 1.1."),
    ),
)
def test_cached_visual_payload_validation_rejects_invalid_required_scores(
    monkeypatch,
    module,
    evaluate,
    _build,
    score_keys: tuple[str, ...],
    name: str,
    defect: str,
    expected_detail: str,
):
    payload = _valid_cached_payload(score_keys)
    if defect == "missing":
        del payload["overall"]
    else:
        payload["overall"] = 1.1

    client = _FakeVisionClient(_valid_live_payload(score_keys))
    monkeypatch.setattr(module, "_load_cached", lambda _key: payload)

    result = evaluate(features={}, rendered_views=[_VIEW], llm_client=client)

    assert client.calls == 0
    assert result["available"] is False
    assert result["source"] == "unavailable"
    assert result["cached"] is True
    assert result["reasoning"] == "N/A"
    assert result["error"] == f"Invalid cached {name} evaluation payload: {expected_detail}"


@pytest.mark.parametrize("module,evaluate,_build,score_keys,_name", _EVALUATORS)
def test_live_visual_payload_validation_accepts_scores_and_retains_metadata(
    monkeypatch,
    module,
    evaluate,
    _build,
    score_keys: tuple[str, ...],
    _name: str,
):
    payload = _valid_live_payload(score_keys)
    saved: dict[str, Mapping[str, Any]] = {}
    client = _FakeVisionClient(payload)
    monkeypatch.setattr(module, "_load_cached", lambda _key: None)
    monkeypatch.setattr(module, "_save_cached", lambda key, result: saved.update({key: result}))

    result = evaluate(features={}, rendered_views=[_VIEW], llm_client=client)

    assert result["available"] is True
    assert result["source"] == "llm"
    assert result["cached"] is False
    for key in score_keys:
        assert result[key] == payload[key] / 5.0
    assert result["evidence"] == payload["evidence"]
    assert result["limitations"] == payload["limitations"]
    assert result["confidence"] == 0.7
    assert next(iter(saved.values()))["evidence"] == payload["evidence"]


@pytest.mark.parametrize("module,evaluate,_build,score_keys,_name", _EVALUATORS)
def test_cached_visual_payload_validation_accepts_normalized_scores_and_metadata(
    monkeypatch,
    module,
    evaluate,
    _build,
    score_keys: tuple[str, ...],
    _name: str,
):
    payload = _valid_cached_payload(score_keys)
    client = _FakeVisionClient(_valid_live_payload(score_keys))
    monkeypatch.setattr(module, "_load_cached", lambda _key: dict(payload))

    result = evaluate(features={}, rendered_views=[_VIEW], llm_client=client)

    assert client.calls == 0
    assert result["available"] is True
    assert result["source"] == "cache"
    assert result["cached"] is True
    for key in score_keys:
        assert result[key] == payload[key]
    assert result["evidence"] == payload["evidence"]
    assert result["limitations"] == payload["limitations"]
    assert result["confidence"] == 0.6
