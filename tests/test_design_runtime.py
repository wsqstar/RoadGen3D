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
from roadgen3d.services.design_types import DesignDraft, SceneContext
from roadgen3d.services.scene_context_service import ResolvedSceneContext
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
    assert config.style_preset == "civic_clean_v1"
    assert config.beauty_mode == "presentation_v1"
    assert config.lane_count == 2


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
        generation_options={"out_dir": str(tmp_path)},
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
        generation_options={"out_dir": str(tmp_path)},
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
