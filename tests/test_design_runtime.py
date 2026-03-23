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
from roadgen3d.services.design_types import DesignDraft
import roadgen3d.services.design_runtime as runtime


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
    assert config.lane_count == 2


def test_generate_scene_from_draft_wraps_existing_scene_pipeline(tmp_path: Path, monkeypatch):
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {"instance_count": 8, "dropped_slots": 1}}), encoding="utf-8")

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
