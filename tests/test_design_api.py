from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.design_types import (  # noqa: E402
    DesignDraft,
    DesignDraftBundle,
    DesignIntent,
    RagEvidence,
    SceneGenerationResult,
    SceneJobCreateResponse,
    SceneJobStatusResponse,
    SceneRecord,
)
from roadgen3d.capture_3d import Capture3DResult  # noqa: E402
from roadgen3d.template_patch import TEMPLATE_PATCH_SCHEMA_VERSION  # noqa: E402
from roadgen3d.services.branch_benchmarks import BranchBenchmarkBatchService, BranchBenchmarkStore  # noqa: E402
from web.api.main import create_app  # noqa: E402
import web.api.main as api_main  # noqa: E402


def test_api_root_returns_viewer_and_health_hints():
    client = TestClient(create_app(design_service=_FakeService()))

    response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["health_url"] == "/api/health"
    assert payload["viewer_url"].startswith("http://127.0.0.1:4173")


def test_health_exposes_only_active_model_capabilities():
    client = TestClient(create_app(design_service=_FakeService()))

    response = client.get("/api/health")

    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert set(capabilities) == {"llm"}


def test_osm_semantic_preview_endpoint_returns_preview(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_preview(**kwargs):
        captured.update(kwargs)
        return {
            "semantic_mode": "landuse_rules_v1",
            "summary": {"semantic_block_count": 1},
            "osm_semantic_blocks": [{"block_id": "school", "semantic_profile_id": "child_friendly_school"}],
            "segment_semantic_profiles": [],
        }

    monkeypatch.setattr(api_main, "build_osm_semantic_preview", _fake_preview)
    client = TestClient(create_app(design_service=_FakeService()))

    response = client.post(
        "/api/osm/semantic-preview",
        json={
            "aoi_bbox": [116.39, 39.90, 116.395, 39.905],
            "compose_config": {"osm_multiblock_max_roads": "6", "osm_context_fit_mode": "auto_design", "unsupported": "drop"},
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"]["semantic_block_count"] == 1
    assert captured["aoi_bbox"] == (116.39, 39.9, 116.395, 39.905)
    assert captured["compose_config_patch"] == {"osm_multiblock_max_roads": 6, "osm_context_fit_mode": "auto_design"}


class _FakeService:
    default_pdf_path = Path("/tmp/guide.pdf")
    default_artifact_dir = Path("/tmp/knowledge")

    def __init__(self):
        self.last_scene_context = None
        self.last_knowledge_source = None

    def draft_design(self, **kwargs):
        self.last_knowledge_source = kwargs.get("knowledge_source")
        return DesignDraftBundle(
            stage="draft_ready",
            intent=DesignIntent(
                user_goals=("walkable street",),
                style_preferences=("all-age friendly",),
                safety_priorities=("pedestrian safety",),
                follow_up_questions=(),
                rag_queries=("sidewalk width",),
            ),
            evidence=(),
            draft=DesignDraft(
                normalized_scene_query="walkable street",
                compose_config_patch={"sidewalk_width_m": 4.0},
                citations_by_field={},
                design_summary="summary",
                parameter_sources_by_field={"sidewalk_width_m": "rag", "road_width_m": "llm_inferred"},
            ),
            warnings=(),
        )

    def list_knowledge_sources(self):
        return [
            {
                "key": "hybrid",
                "label": "Hybrid",
                "available": True,
                "description": "Combined PDF + GraphRAG search.",
                "artifact_count": 2,
                "item_count": 10,
            },
            {
                "key": "pdf_rag",
                "label": "PDF RAG",
                "available": True,
                "description": "PDF chunk retrieval.",
                "artifact_count": 1,
                "item_count": 8,
            },
            {
                "key": "graph_rag",
                "label": "GraphRAG",
                "available": True,
                "description": "Merged txt and graph community reports.",
                "artifact_count": 1,
                "item_count": 2,
            },
            {
                "key": "scenario_parameters",
                "label": "Scenario Parameters",
                "available": True,
                "description": "Structured scenario-parameter-value triples.",
                "artifact_count": 1,
                "item_count": 146,
                "artifact_path": "/tmp/scenario_parameter_triples.jsonl",
                "fingerprint": "demo-fingerprint",
            },
        ]

    def search_knowledge(self, *, query: str, topk: int = 6, knowledge_source: str = "hybrid"):
        self.last_knowledge_source = knowledge_source
        return [
            RagEvidence(
                chunk_id="graph_001",
                doc_id="graphrag_community_report",
                section_title=f"match for {query}",
                page_start=0,
                page_end=0,
                text="Sidewalks should stay generous near transit stops.",
                source_path="/tmp/graphrag/community_reports.parquet",
                score=0.88,
                relevance_reason="Matched RAG query: sidewalk width",
                knowledge_source=knowledge_source,
            )
        ][:topk]

    def generate_scene(self, draft, **kwargs):
        scene_context = kwargs.get("scene_context")
        self.last_scene_context = scene_context
        if getattr(scene_context, "layout_mode", "") == "osm" and getattr(scene_context, "aoi_bbox", None) is None:
            raise RuntimeError("OSM scene context requires an AOI bbox.")
        return {
            "compose_config": draft.compose_config_patch,
            "summary": {
                "instance_count": 5,
                "clearance_m": float("inf"),
                "layout_mode": getattr(scene_context, "layout_mode", "template"),
                "requested_aoi_bbox": list(getattr(scene_context, "aoi_bbox", []) or []),
            },
            "scene_layout_path": "/tmp/layout.json",
            "scene_glb_path": "/tmp/scene.glb",
            "scene_ply_path": "/tmp/scene.ply",
            "viewer_url": "http://127.0.0.1:4173/?layout=demo",
        }

    def create_scene_job(self, draft, **kwargs):
        scene_context = kwargs.get("scene_context")
        self.last_scene_context = scene_context
        if getattr(scene_context, "layout_mode", "") == "osm" and getattr(scene_context, "aoi_bbox", None) is None:
            raise RuntimeError("OSM scene context requires an AOI bbox.")
        return SceneJobCreateResponse(job_id="job-demo", status="queued", created_at="2026-03-23T00:00:00+00:00")

    def list_scene_jobs(self, *, limit=20):
        return [
            SceneJobStatusResponse(
                job_id="job-demo",
                status="succeeded",
                created_at="2026-03-23T00:00:00+00:00",
                started_at="2026-03-23T00:00:01+00:00",
                finished_at="2026-03-23T00:00:02+00:00",
                stage="succeeded",
                progress=100,
                operations=(
                    {
                        "timestamp": "2026-03-23T00:00:02+00:00",
                        "stage": "succeeded",
                        "progress": 100,
                        "message": "Scene generation completed.",
                    },
                ),
                result=SceneGenerationResult(
                    compose_config={"sidewalk_width_m": 4.0},
                    summary={"instance_count": 5, "clearance_m": float("inf")},
                    scene_layout_path="/tmp/layout.json",
                    scene_glb_path="/tmp/scene.glb",
                    scene_ply_path="/tmp/scene.ply",
                    viewer_url="http://127.0.0.1:4173/?layout=demo",
                ),
                trace={
                    "schema_version": "generation_trace_v1",
                    "provenance": {
                        "rag_evidence": [
                            {
                                "chunk_id": "scenario_parameters::demo",
                                "knowledge_source": "scenario_parameters",
                            }
                        ],
                        "citations_by_field": {"sidewalk_width_m": ["scenario_parameters::demo"]},
                    },
                    "llm_recommendation": {
                        "config_patch": {"sidewalk_width_m": 4.0},
                    },
                    "process": {"stage_tree": []},
                    "result": {"scene_layout_path": "/tmp/layout.json"},
                    "evaluation": {"status": "succeeded", "walkability": 80},
                },
            )
        ][:limit]

    def get_scene_job(self, job_id: str):
        if job_id != "job-demo":
            return None
        return self.list_scene_jobs(limit=1)[0]

    def cancel_scene_job(self, job_id: str):
        if job_id != "job-demo":
            return None
        return SceneJobStatusResponse(
            job_id="job-demo",
            status="cancelled",
            created_at="2026-03-23T00:00:00+00:00",
            started_at="2026-03-23T00:00:01+00:00",
            finished_at="2026-03-23T00:00:01+00:00",
            stage="cancelled",
            progress=55,
            operations=(),
        )

    def list_recent_scenes(self, *, limit=20):
        return [
            SceneRecord(
                job_id="job-demo",
                status="succeeded",
                created_at="2026-03-23T00:00:00+00:00",
                finished_at="2026-03-23T00:00:02+00:00",
                scene_layout_path="/tmp/layout.json",
                scene_glb_path="/tmp/scene.glb",
                scene_ply_path="/tmp/scene.ply",
                viewer_url="http://127.0.0.1:4173/?layout=demo",
                summary={"instance_count": 5, "clearance_m": float("inf")},
            )
        ][:limit]

    def evaluate_scene_unified(self, **kwargs):
        return {
            "walkability": 77,
            "safety": 74,
            "beauty": 81,
            "overall": 77.3,
            "evaluation": "synthetic visual evaluation",
            "suggestions": [],
            "config_patch": {},
        }

    def rebuild_knowledge(self, **kwargs):
        return {"output_dir": "/tmp/knowledge", "chunk_count": 42}

    def list_china_cities(self):
        return [
            {
                "name_zh": "广州",
                "name_en": "guangzhou",
                "province": "广东省",
                "bbox": [113.2660, 23.1280, 113.2710, 23.1325],
            }
        ]


class _FakeBranchRunService:
    def __init__(self):
        self.created = None

    def submit_run(self, **kwargs):
        self.created = kwargs
        return {"run_id": "branch-demo", "status": "queued", "created_at": "2026-03-23T00:00:00+00:00"}

    def get_run(self, run_id: str):
        if run_id != "branch-demo":
            return None
        return {
            "run_id": run_id,
            "status": "succeeded",
            "stage": "succeeded",
            "progress": 100,
            "target_samples": 100,
            "search_mode": "pareto",
            "preset_id": "pedestrian_friendly",
            "preset_name": "Pedestrian Friendly",
            "preset_color": "#4CAF50",
            "benchmark_id": "batch-demo",
            "batch_id": "batch-demo",
            "persist_to_benchmark": True,
            "early_stop_patience": 20,
            "early_stop_triggered": False,
            "early_stop_reason": "",
            "retain_topk_artifacts": 10,
            "score_with_rendered_views": True,
            "retained_artifact_nodes": ["node-a"],
            "retained_artifact_count": 1,
            "completed_samples": 1,
            "attempted_samples": 1,
            "best_node_id": "node-a",
            "frontier": ["node-a"],
            "pareto_front": ["node-a"],
            "pareto_front_size": 1,
            "nodes": [
                {
                    "node_id": "node-a",
                    "parent_id": None,
                    "depth": 0,
                    "rank": 1,
                    "status": "succeeded",
                    "score": 80,
                    "scene_layout_path": "/tmp/layout.json",
                    "scene_glb_path": "/tmp/scene.glb",
                    "artifacts_retained": True,
                    "artifact_rank": 1,
                    "artifact_paths": ["/tmp/scene.glb"],
                    "trace": {
                        "schema_version": "generation_trace_v1",
                        "node_id": "node-a",
                        "provenance": {"rag_evidence": []},
                        "llm_recommendation": {"config_patch": {"sidewalk_width_m": 4.0}},
                        "process": {"growth_tree_node": {"node_id": "node-a"}},
                        "result": {"scene_layout_path": "/tmp/layout.json"},
                        "evaluation": {"status": "succeeded", "walkability": 70},
                    },
                    "optimization_directives": [],
                    "rejected_edits": [],
                    "influence_rows": [
                        {
                            "id": "llm_patch:sidewalk_width_m",
                            "group": "llm_constraints",
                            "source_type": "llm_patch",
                            "label": "sidewalk_width_m",
                            "active": True,
                        }
                    ],
                }
            ],
            "scatter_points": [
                {
                    "node_id": "node-a",
                    "x": 70,
                    "y": 75,
                    "z": 72,
                    "walkability": 70,
                    "safety": 75,
                    "beauty": 72,
                    "overall": 80,
                    "delta_walkability": None,
                    "delta_safety": None,
                    "delta_beauty": None,
                    "delta_overall": None,
                    "depth": 0,
                    "rank": 1,
                    "status": "succeeded",
                    "is_pareto_front": True,
                    "pareto_rank": 0,
                    "dominated_by_count": 0,
                }
            ],
        }

    def list_runs(self, *, limit=20):
        return [self.get_run("branch-demo")][:limit]


def test_design_api_endpoints_return_expected_shapes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROADGEN_RAG_MODE", "experimental")
    service = _FakeService()
    client = TestClient(create_app(design_service=service))

    draft_response = client.post(
        "/api/design/draft",
        json={
            "messages": [{"role": "user", "content": "请做一条全龄友好的街道。"}],
            "user_input": "请做一条全龄友好的街道。",
            "current_patch": {},
            "knowledge_source": "graph_rag",
        },
    )
    assert draft_response.status_code == 200
    assert draft_response.json()["stage"] == "draft_ready"
    assert draft_response.json()["draft"]["compose_config_patch"]["sidewalk_width_m"] == 4.0
    assert draft_response.json()["draft"]["parameter_sources_by_field"]["sidewalk_width_m"] == "rag"
    assert service.last_knowledge_source == "graph_rag"

    generate_response = client.post(
        "/api/design/generate",
        json={
            "draft": {
                "normalized_scene_query": "walkable street",
                "compose_config_patch": {"sidewalk_width_m": 4.0},
                "citations_by_field": {},
                "design_summary": "summary",
                "risk_notes": [],
            },
            "scene_context": {
                "layout_mode": "osm",
                "aoi_bbox": [113.2660, 23.1280, 113.2710, 23.1325],
                "city_name_en": "guangzhou",
            },
            "patch_overrides": {},
            "generation_options": {},
        },
    )
    assert generate_response.status_code == 200
    assert generate_response.json()["viewer_url"].startswith("http://127.0.0.1:4173/")
    assert "Infinity" not in generate_response.text
    assert generate_response.json()["summary"]["clearance_m"] is None
    assert service.last_scene_context is not None
    assert service.last_scene_context.layout_mode == "osm"

    job_create_response = client.post(
        "/api/scene/jobs",
        json={
            "draft": {
                "normalized_scene_query": "walkable street",
                "compose_config_patch": {"sidewalk_width_m": 4.0},
                "citations_by_field": {},
                "design_summary": "summary",
                "risk_notes": [],
            },
            "scene_context": {
                "layout_mode": "osm",
                "aoi_bbox": [113.2660, 23.1280, 113.2710, 23.1325],
                "city_name_en": "guangzhou",
            },
        },
    )
    assert job_create_response.status_code == 200
    assert job_create_response.json()["status"] == "queued"

    job_list_response = client.get("/api/scene/jobs")
    assert job_list_response.status_code == 200
    assert job_list_response.json()["items"][0]["status"] == "succeeded"
    assert job_list_response.json()["items"][0]["stage"] == "succeeded"
    assert job_list_response.json()["items"][0]["progress"] == 100

    job_status_response = client.get("/api/scene/jobs/job-demo")
    assert job_status_response.status_code == 200
    assert job_status_response.json()["result"]["scene_layout_path"] == "/tmp/layout.json"
    assert job_status_response.json()["stage"] == "succeeded"
    assert job_status_response.json()["progress"] == 100
    assert job_status_response.json()["operations"][-1]["message"] == "Scene generation completed."
    assert "Infinity" not in job_status_response.text
    assert job_status_response.json()["result"]["summary"]["clearance_m"] is None
    assert job_status_response.json()["trace"]["schema_version"] == "generation_trace_v1"
    assert job_status_response.json()["trace"]["evaluation"]["status"] == "succeeded"
    assert job_status_response.json()["trace"]["provenance"]["rag_evidence"][0]["knowledge_source"] == "scenario_parameters"

    job_cancel_response = client.post("/api/scene/jobs/job-demo/cancel")
    assert job_cancel_response.status_code == 200
    assert job_cancel_response.json()["status"] == "cancelled"
    assert job_cancel_response.json()["stage"] == "cancelled"

    recent_response = client.get("/api/scenes/recent")
    assert recent_response.status_code == 200
    assert recent_response.json()["items"][0]["viewer_url"].startswith("http://127.0.0.1:4173/")
    assert "Infinity" not in recent_response.text
    assert recent_response.json()["items"][0]["summary"]["clearance_m"] is None

    rebuild_response = client.post("/api/knowledge/rebuild", json={})
    assert rebuild_response.status_code == 200
    assert rebuild_response.json()["chunk_count"] == 42

    source_response = client.get("/api/knowledge/sources")
    assert source_response.status_code == 200
    # The fake service returns its legacy experimental inventory; the router
    # still reports that these sources are not product features.
    assert source_response.json()["items"][0]["key"] == "hybrid"
    assert source_response.json()["product_available"] is False
    assert source_response.json()["experimental_api_available"] is True
    assert any(item["key"] == "scenario_parameters" for item in source_response.json()["items"])

    search_response = client.post(
        "/api/knowledge/search",
        json={"query": "sidewalk width near transit", "knowledge_source": "graph_rag", "topk": 3},
    )
    assert search_response.status_code == 200
    assert search_response.json()["items"][0]["knowledge_source"] == "graph_rag"
    assert service.last_knowledge_source == "graph_rag"

    default_search_response = client.post(
        "/api/knowledge/search",
        json={"query": "default knowledge source", "topk": 1},
    )
    assert default_search_response.status_code == 200
    assert default_search_response.json()["knowledge_source"] == "none"
    assert service.last_knowledge_source == "none"

    scenario_search_response = client.post(
        "/api/knowledge/search",
        json={"query": "walkable commercial sidewalk width", "knowledge_source": "scenario_parameters", "topk": 1},
    )
    assert scenario_search_response.status_code == 200
    assert scenario_search_response.json()["knowledge_source"] == "scenario_parameters"
    assert scenario_search_response.json()["items"][0]["knowledge_source"] == "scenario_parameters"
    assert service.last_knowledge_source == "scenario_parameters"

    geo_response = client.get("/api/geo/china-cities")
    assert geo_response.status_code == 200
    assert geo_response.json()["items"][0]["name_en"] == "guangzhou"

    reference_plan_response = client.get("/api/reference-plans")
    assert reference_plan_response.status_code == 200
    assert any(item["plan_id"] == "hkust_gz_gate" for item in reference_plan_response.json()["items"])

    reference_plan_image_response = client.get("/api/reference-plans/hkust_gz_gate/image")
    assert reference_plan_image_response.status_code == 200
    assert reference_plan_image_response.headers["content-type"].startswith("image/")

    graph_template_response = client.get("/api/graph-templates")
    assert graph_template_response.status_code == 200
    assert any(item["template_id"] == "hkust_gz_gate" for item in graph_template_response.json()["items"])

    graph_template_image_response = client.get("/api/graph-templates/hkust_gz_gate/image")
    assert graph_template_image_response.status_code == 200
    assert graph_template_image_response.headers["content-type"].startswith("image/")

    template_patch_response = client.post(
        "/api/graph-templates/hkust_gz_gate/template-patch/preview",
        json={
            "include_graph_payload": False,
            "patch": {
                "schema_version": TEMPLATE_PATCH_SCHEMA_VERSION,
                "variant_id": "api_patch_demo",
                "operations": [
                    {"op": "remove_strip", "centerline_id": "centerline_04", "strip_id": "center_02"},
                    {"op": "remove_strip", "centerline_id": "centerline_04", "strip_id": "center_05"},
                ],
            },
        },
    )
    assert template_patch_response.status_code == 200
    assert template_patch_response.json()["summary"]["variant_id"] == "api_patch_demo"
    patched_centerline = template_patch_response.json()["annotation"]["centerlines"][0]
    assert patched_centerline["lane_count"] == 2

    annotation_convert_response = client.post(
        "/api/reference-annotations/convert",
        json={
            "annotation": {
                "plan_id": "hkust_gz_gate",
                "image_width_px": 1200,
                "image_height_px": 800,
                "pixels_per_meter": 10.0,
                "centerlines": [
                    {
                        "id": "main_axis",
                        "road_width_m": 25.2,
                        "reference_width_px": 218.0,
                        "forward_drive_lane_count": 1,
                        "reverse_drive_lane_count": 1,
                        "bus_lane_count": 1,
                        "parking_lane_count": 1,
                        "cross_section_mode": "detailed",
                        "cross_section_strips": [
                            {"strip_id": "left_furnishing", "zone": "left", "kind": "nearroad_furnishing", "width_m": 1.5, "direction": "none", "order_index": 0},
                            {"strip_id": "left_sidewalk", "zone": "left", "kind": "clear_sidewalk", "width_m": 2.5, "direction": "none", "order_index": 1},
                            {"strip_id": "left_frontage", "zone": "left", "kind": "frontage_reserve", "width_m": 2.0, "direction": "none", "order_index": 2},
                            {"strip_id": "rev_park", "zone": "center", "kind": "parking_lane", "width_m": 2.2, "direction": "reverse", "order_index": 0},
                            {"strip_id": "rev_drive", "zone": "center", "kind": "drive_lane", "width_m": 3.2, "direction": "reverse", "order_index": 1},
                            {"strip_id": "median_01", "zone": "center", "kind": "median", "width_m": 0.3, "direction": "none", "order_index": 2},
                            {"strip_id": "fwd_drive", "zone": "center", "kind": "drive_lane", "width_m": 3.2, "direction": "forward", "order_index": 3},
                            {"strip_id": "fwd_bus", "zone": "center", "kind": "bus_lane", "width_m": 3.4, "direction": "forward", "order_index": 4},
                            {"strip_id": "right_furnishing", "zone": "right", "kind": "nearroad_furnishing", "width_m": 1.5, "direction": "none", "order_index": 0},
                            {"strip_id": "right_sidewalk", "zone": "right", "kind": "clear_sidewalk", "width_m": 2.5, "direction": "none", "order_index": 1},
                            {"strip_id": "right_frontage", "zone": "right", "kind": "frontage_reserve", "width_m": 2.0, "direction": "none", "order_index": 2},
                        ],
                        "street_furniture_instances": [
                            {"instance_id": "bench_01", "centerline_id": "main_axis", "strip_id": "left_furnishing", "kind": "bench", "station_m": 7.5, "lateral_offset_m": -8.1},
                            {"instance_id": "lamp_01", "centerline_id": "main_axis", "strip_id": "right_frontage", "kind": "lamp", "station_m": 22.0, "lateral_offset_m": 10.1, "yaw_deg": 90.0},
                        ],
                        "points": [
                            {"x": 120, "y": 400},
                            {"x": 520, "y": 400},
                            {"x": 980, "y": 360},
                        ],
                    },
                    {
                        "id": "north_branch",
                        "road_width_m": 9.0,
                        "reference_width_px": 78.0,
                        "forward_drive_lane_count": 1,
                        "reverse_drive_lane_count": 1,
                        "bus_lane_count": 1,
                        "points": [
                            {"x": 520, "y": 400},
                            {"x": 520, "y": 160},
                        ],
                    },
                ],
                "junctions": [{"x": 520, "y": 400, "kind": "intersection"}],
                "roundabouts": [{"x": 980, "y": 360, "radius_px": 48}],
                "control_points": [{"x": 120, "y": 400, "kind": "gateway"}],
                "building_regions": [
                    {
                        "id": "building_region_01",
                        "label": "North Court",
                        "center_px": {"x": 320, "y": 260},
                        "width_px": 180,
                        "height_px": 120,
                        "yaw_deg": 25,
                    }
                ],
            },
            "compose_config": {"segment_length_m": 9.0},
        },
    )
    assert annotation_convert_response.status_code == 200
    assert annotation_convert_response.json()["graph"]["mode"] == "annotation"
    assert annotation_convert_response.json()["summary"]["centerline_count"] == 2
    assert annotation_convert_response.json()["summary"]["segment_count"] > 0
    assert annotation_convert_response.json()["summary"]["junction_count"] == 1
    assert annotation_convert_response.json()["summary"]["derived_junction_count"] == 1
    assert annotation_convert_response.json()["summary"]["topology_junction_count"] == 1
    assert annotation_convert_response.json()["summary"]["t_junction_count"] == 1
    assert annotation_convert_response.json()["summary"]["cross_junction_count"] == 0
    assert annotation_convert_response.json()["summary"]["building_region_count"] == 1
    assert len(annotation_convert_response.json()["road_profiles"]) == 2
    assert len(annotation_convert_response.json()["cross_section_profiles"]) == 2
    assert len(annotation_convert_response.json()["street_furniture_instances"]) == 2
    assert len(annotation_convert_response.json()["derived_junctions"]) == 1
    assert len(annotation_convert_response.json()["metaurban_asset_hints"]) >= 2
    assert len(annotation_convert_response.json()["annotation"]["building_regions"]) == 1
    assert annotation_convert_response.json()["annotation"]["building_regions"][0]["id"] == "building_region_01"
    assert annotation_convert_response.json()["annotation"]["building_regions"][0]["yaw_deg"] == pytest.approx(25.0)
    assert annotation_convert_response.json()["metaurban_asset_guide"]["download_command"].endswith("pull_asset.py --update")
    assert annotation_convert_response.json()["road_profiles"][0]["reference_width_px"] == 218.0
    assert annotation_convert_response.json()["road_profiles"][0]["carriageway_width_m"] == pytest.approx(12.3)
    assert annotation_convert_response.json()["cross_section_profiles"][0]["strip_count"] == 11
    assert any(
        item["annotation_id"] == "main_axis" and item["strip_id"] == "left_furnishing" and "Lamp_post" in item["suggested_assets"]
        for item in annotation_convert_response.json()["metaurban_asset_hints"]
    )
    assert annotation_convert_response.json()["summary"]["metaurban_asset_hint_count"] == len(
        annotation_convert_response.json()["metaurban_asset_hints"]
    )
    assert annotation_convert_response.json()["graph"]["nodes"][0]["road_width_m"] > 0
    assert "lane_profile" in annotation_convert_response.json()["graph"]["nodes"][0]
    assert "cross_section_strips" in annotation_convert_response.json()["graph"]["nodes"][0]
    assert "street_furniture_instances" in annotation_convert_response.json()["graph"]["nodes"][0]
    assert "metaurban_asset_hints" in annotation_convert_response.json()["graph"]["nodes"][0]
    assert annotation_convert_response.json()["derived_junctions"][0]["kind"] == "t_junction"

    metaurban_generate_response = client.post(
        "/api/design/generate",
        json={
            "draft": {
                "normalized_scene_query": "campus gateway boulevard",
                "compose_config_patch": {"sidewalk_width_m": 4.0},
                "citations_by_field": {},
                "design_summary": "summary",
                "risk_notes": [],
            },
            "scene_context": {
                "layout_mode": "metaurban",
                "reference_plan_id": "hkust_gz_gate",
            },
        },
    )
    assert metaurban_generate_response.status_code == 200
    assert metaurban_generate_response.json()["summary"]["layout_mode"] == "metaurban"
    assert service.last_scene_context is not None
    assert service.last_scene_context.reference_plan_id == "hkust_gz_gate"

    graph_template_generate_response = client.post(
        "/api/design/generate",
        json={
            "draft": {
                "normalized_scene_query": "campus gateway boulevard",
                "compose_config_patch": {"sidewalk_width_m": 4.0},
                "citations_by_field": {},
                "design_summary": "summary",
                "risk_notes": [],
            },
            "scene_context": {
                "layout_mode": "graph_template",
                "graph_template_id": "hkust_gz_gate",
            },
        },
    )
    assert graph_template_generate_response.status_code == 200
    assert graph_template_generate_response.json()["summary"]["layout_mode"] == "graph_template"
    assert service.last_scene_context is not None
    assert service.last_scene_context.graph_template_id == "hkust_gz_gate"

    invalid_osm_response = client.post(
        "/api/design/generate",
        json={
            "draft": {
                "normalized_scene_query": "walkable street",
                "compose_config_patch": {"sidewalk_width_m": 4.0},
                "citations_by_field": {},
                "design_summary": "summary",
                "risk_notes": [],
            },
            "scene_context": {"layout_mode": "osm"},
        },
    )
    assert invalid_osm_response.status_code == 400


def test_scene_job_scenario_does_not_replace_inline_reference_annotation():
    service = _FakeService()
    client = TestClient(create_app(design_service=service))
    user_annotation = {
        "schema_version": "roadgen3d_reference_annotation_v2",
        "plan_id": "user_osm_study_area",
        "image_width_px": 1200,
        "image_height_px": 800,
        "pixels_per_meter": 2.0,
        "centerlines": [
            {
                "id": "osm-road-user-main",
                "road_width_m": 18.0,
                "points": [{"x": 100, "y": 400}, {"x": 1100, "y": 400}],
            }
        ],
        "junctions": [],
        "control_points": [],
        "building_regions": [
            {
                "id": "osm-building-user-1",
                "center_px": {"x": 300, "y": 250},
                "width_px": 80,
                "height_px": 60,
            }
        ],
    }

    response = client.post(
        "/api/scene/jobs",
        json={
            "draft": {
                "normalized_scene_query": "generate from the approved OSM annotation",
                "compose_config_patch": {},
                "citations_by_field": {},
                "design_summary": "user source",
                "risk_notes": [],
            },
            "scene_context": {
                "layout_mode": "reference_annotation",
                "reference_annotation": user_annotation,
                "source_context": {
                    "source": {"source_id": "user-osm-source", "kind": "osm"},
                    "aligned_buildings": [],
                    "source_alignment": {"status": "aligned"},
                },
                "scenario_id": "scenario_01_basic_complete_street",
                "scenario_title": "Basic Complete Street",
            },
            "generation_options": {
                "scenario_compose_patch_applied": False,
            },
        },
    )

    assert response.status_code == 200
    assert service.last_scene_context is not None
    assert service.last_scene_context.layout_mode == "reference_annotation"
    assert service.last_scene_context.reference_annotation["plan_id"] == "user_osm_study_area"
    assert service.last_scene_context.reference_annotation["centerlines"][0]["id"] == "osm-road-user-main"
    assert service.last_scene_context.template_patch is None


def test_branch_run_api_endpoints_return_expected_shapes():
    app = create_app(design_service=_FakeService())
    branch_service = _FakeBranchRunService()
    app.state.branch_run_service = branch_service
    client = TestClient(app)

    create_response = client.post(
        "/api/design/branch-runs",
        json={
            "prompt": "Generate three walkable alternatives",
            "topk": 3,
            "rounds": 2,
            "target_samples": 100,
            "search_mode": "pareto",
            "early_stop_patience": 20,
            "retain_topk_artifacts": 10,
            "score_with_rendered_views": True,
            "graph_template_id": "hkust_gz_gate",
            "knowledge_source": "graph_rag",
            "preset_id": "pedestrian_friendly",
            "preset_config_patch": {"density": 0.5},
            "benchmark_id": "batch-demo",
            "batch_id": "batch-demo",
            "persist_to_benchmark": True,
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["run_id"] == "branch-demo"
    assert branch_service.created["topk"] == 3
    assert branch_service.created["target_samples"] == 100
    assert branch_service.created["search_mode"] == "pareto"
    assert branch_service.created["early_stop_patience"] == 20
    assert branch_service.created["retain_topk_artifacts"] == 10
    assert branch_service.created["score_with_rendered_views"] is True
    assert branch_service.created["preset_id"] == "pedestrian_friendly"
    assert branch_service.created["preset_config_patch"] == {"density": 0.5}
    assert branch_service.created["benchmark_id"] == "batch-demo"
    assert branch_service.created["persist_to_benchmark"] is True

    status_response = client.get("/api/design/branch-runs/branch-demo")
    assert status_response.status_code == 200
    assert status_response.json()["nodes"][0]["optimization_directives"] == []
    assert status_response.json()["nodes"][0]["trace"]["process"]["growth_tree_node"]["node_id"] == "node-a"
    assert status_response.json()["scatter_points"][0]["x"] == 70
    assert status_response.json()["scatter_points"][0]["z"] == 72
    assert status_response.json()["pareto_front_size"] == 1
    assert status_response.json()["retained_artifact_count"] == 1
    assert status_response.json()["preset_id"] == "pedestrian_friendly"
    assert status_response.json()["scatter_points"][0]["is_pareto_front"] is True
    assert status_response.json()["nodes"][0]["influence_rows"][0]["source_type"] == "llm_patch"

    list_response = client.get("/api/design/branch-runs")
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["run_id"] == "branch-demo"


def test_benchmark_api_persists_branch_and_manual_evaluation_samples(tmp_path: Path):
    store = BranchBenchmarkStore(tmp_path / "bench")
    app = create_app(design_service=_FakeService(), benchmark_store=store)
    branch_service = _FakeBranchRunService()
    app.state.branch_run_service = branch_service
    app.state.benchmark_batch_service = BranchBenchmarkBatchService(
        branch_run_service=branch_service,
        benchmark_store=store,
    )
    client = TestClient(app)

    store.upsert_branch_run(branch_service.get_run("branch-demo"), default_preset_id="pedestrian_friendly")
    samples_response = client.get("/api/design/benchmark-samples?refresh=false")
    assert samples_response.status_code == 200
    payload = samples_response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["preset_id"] == "pedestrian_friendly"
    assert payload["items"][0]["walkability"] == 70
    assert payload["items"][0]["is_pareto_front"] is True
    assert payload["items"][0]["generation_method"] == "pure_llm"

    method_response = client.get("/api/design/benchmark-samples?generation_method=pure_llm&refresh=false")
    assert method_response.status_code == 200
    assert method_response.json()["total"] == 1
    assert method_response.json()["items"][0]["node_id"] == "node-a"

    empty_method_response = client.get("/api/design/benchmark-samples?generation_method=parametric&refresh=false")
    assert empty_method_response.status_code == 200
    assert empty_method_response.json()["total"] == 0

    eval_response = client.post(
        "/api/design/evaluate/unified",
        json={
            "layout_path": str(tmp_path / "manual_layout.json"),
            "preset_id": "balanced_complete",
            "persist_to_benchmark": True,
        },
    )
    assert eval_response.status_code == 200
    filtered_response = client.get("/api/design/benchmark-samples?preset_id=balanced_complete&refresh=false")
    assert filtered_response.status_code == 200
    assert filtered_response.json()["total"] == 1
    assert filtered_response.json()["items"][0]["source"] == "manual_evaluation"

    batch_response = client.post(
        "/api/design/benchmark-batches",
        json={"preset_ids": ["pedestrian_friendly"], "target_samples": 1},
    )
    assert batch_response.status_code == 200
    assert batch_response.json()["children"][0]["preset_id"] == "pedestrian_friendly"


def test_benchmark_read_endpoints_do_not_refresh_by_default(tmp_path: Path):
    class _SpyBenchmarkStore(BranchBenchmarkStore):
        def __init__(self, root: Path):
            super().__init__(root)
            self.import_calls = 0

        def import_branch_manifests(self, branch_root=None):  # type: ignore[override]
            self.import_calls += 1
            return {"imported_samples": 0, "branch_root": str(branch_root or "")}

    store = _SpyBenchmarkStore(tmp_path / "bench")
    store.upsert_samples([{
        "sample_id": "sample-a",
        "preset_id": "balanced_complete",
        "preset_name": "Balanced Complete",
        "preset_color": "#607D8B",
        "label": "sample-a",
        "created_at": "2026-05-01T00:00:00+00:00",
        "status": "succeeded",
        "walkability": 72,
        "safety": 70,
        "beauty": 68,
        "overall": 70,
    }])
    client = TestClient(create_app(design_service=_FakeService(), benchmark_store=store))

    samples_response = client.get("/api/design/benchmark-samples")
    analysis_response = client.get("/api/design/benchmark-analysis")

    assert samples_response.status_code == 200
    assert analysis_response.status_code == 200
    assert store.import_calls == 0

    refresh_response = client.get("/api/design/benchmark-samples?refresh=true")

    assert refresh_response.status_code == 200
    assert store.import_calls == 1


def test_benchmark_analysis_endpoint_extracts_features_and_statistics(tmp_path: Path):
    store = BranchBenchmarkStore(tmp_path / "bench")
    samples = []
    for pair in range(4):
        preset_id = "pedestrian_friendly" if pair < 2 else "balanced_complete"
        for child_index in range(2):
            node_id = f"n{pair}_{child_index}"
            layout_path = tmp_path / f"{node_id}" / "scene_layout.json"
            layout_path.parent.mkdir(parents=True, exist_ok=True)
            road_width = 8.0 + pair * 2.0 + child_index * (pair + 1)
            tree_count = 2 + pair + child_index
            layout_path.write_text(
                json.dumps({
                    "config": {
                        "road_width_m": road_width,
                        "sidewalk_width_m": 3.0 + child_index,
                        "density": 0.5 + pair * 0.1,
                        "target_street_type": "walkable",
                    },
                    "summary": {
                        "road_width_m": road_width,
                        "sidewalk_width_m": 3.0 + child_index,
                        "left_clear_path_width_m": 2.0 + child_index,
                        "right_clear_path_width_m": 2.0 + child_index,
                        "length_m": 80.0,
                        "rule_satisfaction_rate": 0.5 + pair * 0.1,
                        "compliance_rate_total": 0.6 + pair * 0.08,
                    },
                    "placements": [
                        {"category": "tree", "asset_id": f"tree-{idx}"}
                        for idx in range(tree_count)
                    ] + [
                        {"category": "bench", "asset_id": "bench-a"},
                    ],
                    "building_placements": [{"instance_id": f"b{idx}"} for idx in range(pair + 1)],
                    "building_footprints": [{"id": f"fp{idx}"} for idx in range(pair + 1)],
                }),
                encoding="utf-8",
            )
            walkability = 40 + pair * 8 + child_index * (pair + 2)
            samples.append({
                "sample_id": f"run-demo:{node_id}",
                "source": "branch_run",
                "run_id": "run-demo",
                "node_id": node_id,
                "parent_id": f"n{pair}_0" if child_index else "",
                "preset_id": preset_id,
                "preset_name": preset_id,
                "preset_color": "#4CAF50" if preset_id == "pedestrian_friendly" else "#607D8B",
                "label": node_id,
                "created_at": f"2026-05-04T00:00:0{pair}{child_index}+00:00",
                "status": "succeeded",
                "scene_layout_path": str(layout_path),
                "config_patch": {
                    "road_width_m": road_width,
                    "density": 0.5 + pair * 0.1,
                    "target_street_type": "walkable",
                },
                "influence_rows": [
                    {
                        "source_type": "search_patch",
                        "field": "road_width_m",
                        "value": road_width,
                        "active": True,
                    }
                ],
                "walkability": walkability,
                "safety": 45 + pair * 5 + child_index,
                "beauty": 50 + pair * 4 + child_index,
                "overall": walkability,
                "delta_walkability": pair + 2 if child_index else None,
                "delta_safety": 1 if child_index else None,
                "delta_beauty": 1 if child_index else None,
                "delta_overall": pair + 2 if child_index else None,
            })
    store.upsert_samples(samples)
    client = TestClient(create_app(design_service=_FakeService(), benchmark_store=store))

    response = client.get("/api/design/benchmark-analysis?refresh=false&limit=20")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 8
    first_sample = payload["samples"][0]
    assert "input_features" in first_sample
    assert "scene_features" in first_sample
    assert any(sample["scene_features"]["tree_count"] >= 2 for sample in payload["samples"])
    assert any(
        row["mode"] == "pooled"
        and row["feature"] == "scene.road_width_m"
        and row["outcome"] == "walkability"
        for row in payload["correlations"]
    )
    assert any(row["mode"] == "delta" for row in payload["correlations"])
    assert any(row["feature"] == "meta.preset_id" for row in payload["categorical_effects"])
    assert any("Feature importance skipped" in warning for warning in payload["warnings"])


def test_design_api_supports_clarification_stage():
    class _ClarificationService(_FakeService):
        def draft_design(self, **kwargs):
            return DesignDraftBundle(
                stage="clarification_required",
                intent=DesignIntent(
                    user_goals=("walkable street",),
                    style_preferences=("all-age friendly",),
                    safety_priorities=("pedestrian safety",),
                    follow_up_questions=("Which city should this street fit into?",),
                    rag_queries=("complete streets pedestrian safety",),
                ),
                evidence=(),
                draft=None,
                warnings=("Additional clarification is required before drafting a street design.",),
            )

    client = TestClient(create_app(design_service=_ClarificationService()))
    response = client.post(
        "/api/design/draft",
        json={
            "messages": [{"role": "user", "content": "请做一条全龄友好的街道。"}],
            "user_input": "请做一条全龄友好的街道。",
            "current_patch": {},
            "knowledge_source": "hybrid",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stage"] == "clarification_required"
    assert payload["draft"] is None
    assert payload["intent"]["follow_up_questions"] == ["Which city should this street fit into?"]


def test_design_api_defaults_draft_requests_to_zero_retrieval():
    service = _FakeService()
    client = TestClient(create_app(design_service=service))

    response = client.post(
        "/api/design/draft",
        json={
            "messages": [{"role": "user", "content": "请做一条全龄友好的街道。"}],
            "user_input": "请做一条全龄友好的街道。",
            "current_patch": {},
        },
    )

    assert response.status_code == 200
    assert service.last_knowledge_source == "none"


def test_knowledge_api_is_disabled_by_default():
    service = _FakeService()
    client = TestClient(create_app(design_service=service))

    sources = client.get("/api/knowledge/sources")
    assert sources.status_code == 200
    assert sources.json() == {
        "mode": "disabled",
        "product_available": False,
        "experimental_api_available": False,
        "items": service.list_knowledge_sources(),
    }
    search = client.post(
        "/api/knowledge/search",
        json={"query": "sidewalk width", "knowledge_source": "graph_rag"},
    )
    assert search.status_code == 503


def test_rebuild_layout_glb_reexports_and_updates_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest_path = tmp_path / "real_assets_manifest.jsonl"
    manifest_path.write_text("", encoding="utf-8")
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(
        json.dumps({
            "outputs": {"scene_glb": ""},
            "summary": {"instance_count": 3},
            "config": {},
            "street_program": {"bands": []},
            "placements": [],
        }),
        encoding="utf-8",
    )

    def fake_rebuild_glb_from_layout(*, layout_path: Path, manifest_path: Path, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        glb_path = out_dir / "scene.glb"
        glb_path.write_bytes(b"glb")
        return {"scene_glb": str(glb_path)}

    monkeypatch.setattr("web.api.main.rebuild_glb_from_layout", fake_rebuild_glb_from_layout)

    client = TestClient(create_app(design_service=_FakeService()))
    response = client.post(
        "/api/design/rebuild-layout-glb",
        json={"layout_path": str(layout_path), "manifest_path": str(manifest_path)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rebuilt"] is True
    assert Path(payload["scene_glb_path"]).exists()
    updated = json.loads(layout_path.read_text(encoding="utf-8"))
    assert updated["outputs"]["scene_glb"] == payload["scene_glb_path"]
    assert updated["summary"]["scene_glb_rebuilt_from_layout"] is True


def test_capture_views_endpoint_invokes_backend_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest_path = tmp_path / "real_assets_manifest.jsonl"
    manifest_path.write_text("", encoding="utf-8")
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(json.dumps({"outputs": {}, "summary": {}}), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_capture_views_for_layout(**kwargs):
        captured.update(kwargs)
        return Capture3DResult(
            status="succeeded",
            layout_path=str(layout_path),
            capture_manifest_path=str(tmp_path / "view_captures" / "capture_manifest.json"),
            scene_glb_path="",
            view_count=1,
            views=[{"view_id": "street_1", "path": str(tmp_path / "street.png")}],
        )

    monkeypatch.setattr("web.api.main.capture_views_for_layout", fake_capture_views_for_layout)

    client = TestClient(create_app(design_service=_FakeService()))
    response = client.post(
        "/api/design/capture-views",
        json={
            "layout_path": str(layout_path),
            "manifest_path": str(manifest_path),
            "capture_profile": "quick_12",
            "capture_resolution": [640, 360],
            "retain_glb_policy": "always",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["view_count"] == 1
    assert captured["layout_path"] == layout_path
    assert captured["manifest_path"] == manifest_path
    assert captured["options"]["capture_profile"] == "quick_12"
    assert captured["options"]["capture_resolution"] == [640, 360]


def test_scene_diff_endpoint_reports_missing_layouts():
    client = TestClient(create_app(design_service=_FakeService()))

    response = client.post(
        "/api/scenes/diff",
        json={"layout_a": "/tmp/roadgen3d-missing-a.json", "layout_b": "/tmp/roadgen3d-missing-b.json"},
    )

    assert response.status_code == 404


def test_asset_split_endpoint_rejects_unsupported_method_before_manifest_lookup():
    client = TestClient(create_app(design_service=_FakeService()))

    response = client.post(
        "/api/asset-manifest/split-selected",
        json={"manifest_name": "missing.jsonl", "asset_id": "asset-demo", "method": "unsupported"},
    )

    assert response.status_code == 400
    assert "Unsupported split method" in response.json()["detail"]
