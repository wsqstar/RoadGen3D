from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from roadgen3d.capture_3d import plan_capture_targets
from roadgen3d.services.feature_quality_lab import (
    FeatureExperimentError,
    build_feature_experiment,
    build_feature_review_messages,
    feature_tri_views_from_layout,
    normalize_feature_review,
    write_feature_contact_sheet,
)
from roadgen3d.services.feature_quality_runs import FeatureQualityRunService


def test_feature_experiment_isolates_ramp_from_bus_stop() -> None:
    experiment = build_feature_experiment(
        experiment_id="ramp-v1",
        target_id="curb_ramp",
        brief="Independent ramp",
        fixed_patch={"length_m": 20.0, "bus_stop_enabled": False},
        variants=[{
            "variant_id": "right-mid",
            "patch": {
                "curb_ramp_enabled": True,
                "curb_ramp_side": "right",
                "curb_ramp_position_ratio": 0.5,
            },
        }],
    )
    assert experiment["controls"]["capture_profile"] == "feature_tri_view"
    assert experiment["controls"]["fixed_patch"]["bus_stop_enabled"] is False
    assert set(experiment["variants"][0]["patch"]) == {
        "curb_ramp_enabled",
        "curb_ramp_side",
        "curb_ramp_position_ratio",
    }


def test_feature_experiment_rejects_cross_feature_change() -> None:
    with pytest.raises(FeatureExperimentError, match="outside curb_ramp"):
        build_feature_experiment(
            experiment_id="bad",
            target_id="curb_ramp",
            brief="bad isolation",
            fixed_patch={},
            variants=[{"variant_id": "bad", "patch": {"bus_stop_enabled": True}}],
        )


def test_feature_tri_view_profile_is_fixed_and_orthographic() -> None:
    plan = plan_capture_targets(
        {"config": {"length_m": 20.0, "road_width_m": 7.0}, "placements": []},
        profile="feature_tri_view",
    )
    assert [item["target_id"] for item in plan["targets"]] == [
        "feature_top",
        "feature_longitudinal",
        "feature_cross_section",
    ]
    assert all(item["projection"] == "orthographic" for item in plan["targets"])


def test_review_patch_is_bounded_to_feature_fields() -> None:
    review = normalize_feature_review(
        {
            "scores_0_100": {"geometry_fidelity": 120, "visual_quality": 72},
            "proposed_patch": {
                "curb_ramp_side": "left",
                "bus_stop_enabled": True,
            },
        },
        target_id="curb_ramp",
    )
    assert review["scores_0_100"]["geometry_fidelity"] == 100.0
    assert review["proposed_patch"] == {"curb_ramp_side": "left"}
    assert review["rejected_patch_fields"] == ["bus_stop_enabled"]


def test_layout_views_review_messages_and_contact_sheet(tmp_path: Path) -> None:
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    views = []
    for view_id in ("feature_top", "feature_longitudinal", "feature_cross_section"):
        path = tmp_path / f"{view_id}.png"
        path.write_bytes(image)
        views.append({"view_id": view_id, "label": view_id, "path": str(path)})
    layout = tmp_path / "scene_layout.json"
    layout.write_text(json.dumps({"summary": {"render_views_3d": views}}), encoding="utf-8")
    encoded = feature_tri_views_from_layout(layout)
    experiment = build_feature_experiment(
        experiment_id="ramp-v1",
        target_id="curb_ramp",
        brief="Independent ramp",
        fixed_patch={},
        variants=[{"variant_id": "baseline", "patch": {"curb_ramp_enabled": True}}],
    )
    messages = build_feature_review_messages(
        experiment=experiment,
        variant=experiment["variants"][0],
        rendered_views=encoded,
    )
    assert len(messages[1]["content"]) == 7
    report = write_feature_contact_sheet(
        {"experiment_id": "ramp-v1", "variants": [{**experiment["variants"][0], "views": views}]},
        output_path=tmp_path / "report.html",
    )
    assert Path(report).exists()
    assert "feature_cross_section" in Path(report).read_text(encoding="utf-8")


def test_feature_run_generates_reviews_and_accepts_variant(tmp_path: Path) -> None:
    service = FeatureQualityRunService(
        design_service=_FakeFeatureDesignService(),
        output_root=tmp_path / "runs",
    )
    created = service.submit_run(
        target_id="curb_ramp",
        brief="Independent ramp",
        variant_count=3,
        base_patch={"length_m": 20.0, "bus_stop_enabled": False, "seed": 9},
        visual_review=True,
    )
    deadline = time.monotonic() + 5
    result = created
    while result["status"] not in {"succeeded", "failed"} and time.monotonic() < deadline:
        time.sleep(0.02)
        result = service.get_run(created["run_id"]) or result
    assert result["status"] == "succeeded"
    assert len(result["variants"]) == 3
    assert all(len(item["views"]) == 3 for item in result["variants"])
    assert all(item["score"] == 80.0 for item in result["variants"])
    accepted = service.accept_variant(created["run_id"], result["variants"][1]["variant_id"])
    assert accepted["patch"]["bus_stop_enabled"] is False
    assert accepted["patch"]["curb_ramp_enabled"] is True
    assert service.artifact_path(
        created["run_id"],
        result["variants"][0]["variant_id"],
        "feature_top",
    ) is not None


class _FakeVisionClient:
    def chat_json(self, _messages, **_kwargs):
        return {
            "scores_0_100": {
                "text_alignment": 80,
                "geometry_fidelity": 80,
                "placement_validity": 80,
                "material_coherence": 80,
                "visual_quality": 80,
            },
            "defects": [],
            "passed_checks": ["fixture"],
            "failed_checks": [],
            "proposed_patch": {},
            "reasoning": "fixture",
            "confidence": "high",
        }


class _FakeFeatureDesignService:
    def __init__(self) -> None:
        self.client = _FakeVisionClient()

    def _get_llm_client(self):
        return self.client

    def generate_scene(self, draft, *, generation_options, **_kwargs):
        out_dir = Path(generation_options["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        views = []
        for view_id in ("feature_top", "feature_longitudinal", "feature_cross_section"):
            path = out_dir / f"{view_id}.png"
            path.write_bytes(image)
            views.append({"view_id": view_id, "label": view_id, "path": str(path)})
        layout = out_dir / "scene_layout.json"
        layout.write_text(
            json.dumps({"config": draft.compose_config_patch, "summary": {"render_views_3d": views}}),
            encoding="utf-8",
        )
        scene = out_dir / "scene.glb"
        scene.write_bytes(b"glTF")
        return {"scene_layout_path": str(layout), "scene_glb_path": str(scene)}
