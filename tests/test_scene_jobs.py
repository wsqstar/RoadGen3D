from __future__ import annotations

import sys
import json
from pathlib import Path
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.design_types import DesignDraft, SceneContext, SceneGenerationResult  # noqa: E402
from roadgen3d.services.scene_jobs import SceneJobService  # noqa: E402


def _draft() -> DesignDraft:
    return DesignDraft(
        normalized_scene_query="safe complete street",
        compose_config_patch={"sidewalk_width_m": 4.0},
        citations_by_field={},
        design_summary="summary",
    )


def test_scene_job_service_runs_sync_generation():
    captured = {}

    def _generator(draft, **kwargs):
        captured["scene_context"] = kwargs.get("scene_context")
        return SceneGenerationResult(
            compose_config=draft.compose_config_patch,
            summary={"instance_count": 7},
            scene_layout_path="/tmp/layout.json",
            scene_glb_path="/tmp/scene.glb",
            scene_ply_path="/tmp/scene.ply",
            viewer_url="http://127.0.0.1:4173/?layout=demo",
        )

    service = SceneJobService(
        generator=_generator
    )

    result = service.run_job_sync(
        draft=_draft(),
        scene_context=SceneContext(
            layout_mode="osm",
            aoi_bbox=(113.2660, 23.1280, 113.2710, 23.1325),
            city_name_en="guangzhou",
        ),
    )

    assert result.summary["instance_count"] == 7
    assert captured["scene_context"].layout_mode == "osm"
    recent = service.list_recent_scenes(limit=1)
    assert recent[0].job_id
    assert recent[0].viewer_url.startswith("http://127.0.0.1:4173/")


def test_scene_job_service_records_failure():
    def _generator(draft, **kwargs):
        kwargs["progress_callback"]({
            "stage": "asset_loading",
            "progress": 33,
            "message": "Assets loaded before failure.",
        })
        raise RuntimeError("boom")

    service = SceneJobService(generator=_generator)

    created = service.submit_job(draft=_draft())
    status = service.wait_for_job(created.job_id, timeout_s=2.0)

    assert status is not None
    assert status.status == "failed"
    assert status.stage == "failed"
    assert status.progress == 33
    assert status.operations[-1]["stage"] == "failed"
    assert "boom" in status.error


def test_scene_job_service_exposes_running_progress():
    progress_recorded = Event()
    release_generator = Event()

    def _generator(draft, **kwargs):
        kwargs["progress_callback"]({
            "stage": "asset_composition",
            "progress": 64,
            "message": "Placing street assets.",
            "detail": {"placed_slots": 7, "total_slots": 11},
        })
        progress_recorded.set()
        release_generator.wait(timeout=2.0)
        return SceneGenerationResult(
            compose_config=draft.compose_config_patch,
            summary={"instance_count": 7},
            scene_layout_path="/tmp/layout.json",
            scene_glb_path="/tmp/scene.glb",
            scene_ply_path="/tmp/scene.ply",
            viewer_url="http://127.0.0.1:4173/?layout=demo",
        )

    service = SceneJobService(generator=_generator)
    created = service.submit_job(draft=_draft())

    assert progress_recorded.wait(timeout=2.0)
    running = service.get_job(created.job_id)
    assert running is not None
    assert running.status == "running"
    assert running.stage == "asset_composition"
    assert running.progress == 64
    assert running.operations[-1]["message"] == "Placing street assets."
    assert running.operations[-1]["detail"]["placed_slots"] == 7

    release_generator.set()
    completed = service.wait_for_job(created.job_id, timeout_s=2.0)
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.stage == "succeeded"
    assert completed.progress == 100


def test_scene_job_service_preserves_graph_template_scene_context():
    captured = {}

    def _generator(draft, **kwargs):
        captured["scene_context"] = kwargs.get("scene_context")
        return SceneGenerationResult(
            compose_config=draft.compose_config_patch,
            summary={"instance_count": 3, "layout_mode": "graph_template"},
            scene_layout_path="/tmp/layout.json",
            scene_glb_path="/tmp/scene.glb",
            scene_ply_path="/tmp/scene.ply",
            viewer_url="http://127.0.0.1:4173/?layout=demo",
        )

    service = SceneJobService(generator=_generator)

    result = service.run_job_sync(
        draft=_draft(),
        scene_context=SceneContext(
            layout_mode="graph_template",
            graph_template_id="hkust_gz_gate",
        ),
    )

    assert result.summary["layout_mode"] == "graph_template"
    assert captured["scene_context"].layout_mode == "graph_template"
    assert captured["scene_context"].graph_template_id == "hkust_gz_gate"


def test_scene_job_service_records_graph_template_scene_summary_in_recent_scenes():
    captured = {}

    def _generator(draft, **kwargs):
        captured["scene_context"] = kwargs.get("scene_context")
        return SceneGenerationResult(
            compose_config=draft.compose_config_patch,
            summary={
                "layout_mode": "graph_template",
                "graph_template_id": "hkust_gz_gate",
                "building_generation_mode": "building_region_direct",
                "building_footprint_count": 2,
                "infill_footprint_count": 0,
            },
            scene_layout_path="/tmp/layout.json",
            scene_glb_path="/tmp/scene.glb",
            scene_ply_path="/tmp/scene.ply",
            viewer_url="http://127.0.0.1:4173/?layout=demo",
        )

    service = SceneJobService(generator=_generator)

    created = service.submit_job(
        draft=_draft(),
        scene_context=SceneContext(
            layout_mode="graph_template",
            graph_template_id="hkust_gz_gate",
        ),
    )
    status = service.wait_for_job(created.job_id, timeout_s=2.0)

    assert status is not None
    assert status.status == "succeeded"
    assert captured["scene_context"].layout_mode == "graph_template"
    assert captured["scene_context"].graph_template_id == "hkust_gz_gate"

    recent = service.list_recent_scenes(limit=1)
    assert len(recent) == 1
    assert recent[0].job_id == created.job_id
    assert recent[0].summary["layout_mode"] == "graph_template"
    assert recent[0].summary["graph_template_id"] == "hkust_gz_gate"
    assert recent[0].summary["building_generation_mode"] == "building_region_direct"
    assert recent[0].summary["building_footprint_count"] == 2


def test_scene_job_service_builds_generation_trace_and_writes_artifact(tmp_path: Path):
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {}, "placements": []}), encoding="utf-8")

    def _generator(draft, **kwargs):
        kwargs["progress_callback"]({
            "stage": "context_resolving",
            "progress": 18,
            "message": "LLM derived config parameters.",
            "detail": {
                "llm_derivation_status": "succeeded",
                "design_summary": "LLM selected wider sidewalks.",
                "config_patch": {"sidewalk_width_m": 4.0, "density": 0.7},
                "llm_raw_fields": ["sidewalk_width_m"],
                "defaulted_fields": ["density"],
                "parameter_sources_by_field": {"sidewalk_width_m": "llm_derived"},
                "citations_by_field": {"sidewalk_width_m": ["scenario_parameters::demo"]},
                "rag_queries": ["walkable sidewalk width"],
                "rag_evidence": [
                    {
                        "chunk_id": "scenario_parameters::demo",
                        "doc_id": "scenario_parameter_triples",
                        "section_title": "Walkable / sidewalk_width_m",
                        "page_start": 0,
                        "page_end": 0,
                        "text": "{\"scenario_label\":\"Walkable\",\"parameter_name\":\"sidewalk_width_m\"}",
                        "source_path": "knowledge/scenario_parameter_triples.jsonl",
                        "score": 0.97,
                        "knowledge_source": "scenario_parameters",
                    }
                ],
                "knowledge_source": "graph_rag",
            },
        })
        return SceneGenerationResult(
            compose_config=draft.compose_config_patch,
            summary={"instance_count": 7},
            scene_layout_path=str(layout_path),
            scene_glb_path=str(tmp_path / "scene.glb"),
            viewer_url="http://127.0.0.1:4173/?layout=demo",
        )

    def _evaluator(**kwargs):
        assert kwargs["layout_path"] == str(layout_path)
        return {"walkability": 88, "safety": None, "beauty": None, "overall": None, "evaluation": "solid"}

    service = SceneJobService(generator=_generator, evaluator=_evaluator)
    created = service.submit_job(draft=_draft())
    status = service.wait_for_job(created.job_id, timeout_s=2.0)

    assert status is not None
    assert status.status == "succeeded"
    trace = status.trace
    assert trace["schema_version"] == "generation_trace_v1"
    assert trace["provenance"]["rag_evidence"][0]["knowledge_source"] == "scenario_parameters"
    assert trace["provenance"]["citations_by_field"]["sidewalk_width_m"] == ["scenario_parameters::demo"]
    assert trace["llm_recommendation"]["config_patch"]["sidewalk_width_m"] == 4.0
    assert trace["evaluation"]["status"] == "succeeded"
    assert trace["evaluation"]["walkability"] == 88
    trace_path = tmp_path / "generation_trace.json"
    assert trace_path.exists()
    saved = json.loads(trace_path.read_text(encoding="utf-8"))
    assert saved["job_id"] == created.job_id
    assert saved["result"]["generation_trace_path"] == str(trace_path)


def test_scene_job_service_evaluation_failure_does_not_fail_generation(tmp_path: Path):
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"summary": {}, "placements": []}), encoding="utf-8")

    def _generator(draft, **_kwargs):
        return SceneGenerationResult(
            compose_config=draft.compose_config_patch,
            summary={"instance_count": 1},
            scene_layout_path=str(layout_path),
        )

    def _evaluator(**_kwargs):
        raise RuntimeError("evaluation unavailable")

    service = SceneJobService(generator=_generator, evaluator=_evaluator)
    created = service.submit_job(draft=_draft())
    status = service.wait_for_job(created.job_id, timeout_s=2.0)

    assert status is not None
    assert status.status == "succeeded"
    assert status.trace["evaluation"]["status"] == "failed"
    assert "evaluation unavailable" in status.trace["evaluation"]["error"]
