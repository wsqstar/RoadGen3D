from __future__ import annotations

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
from web.api.main import create_app  # noqa: E402


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
            )
        ][:limit]

    def get_scene_job(self, job_id: str):
        if job_id != "job-demo":
            return None
        return self.list_scene_jobs(limit=1)[0]

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
            "best_node_id": "node-a",
            "frontier": ["node-a"],
            "nodes": [
                {
                    "node_id": "node-a",
                    "parent_id": None,
                    "depth": 0,
                    "rank": 1,
                    "status": "succeeded",
                    "score": 80,
                    "scene_layout_path": "/tmp/layout.json",
                    "optimization_directives": [],
                    "rejected_edits": [],
                }
            ],
            "scatter_points": [
                {"node_id": "node-a", "x": 70, "y": 80, "overall": 80, "depth": 0, "rank": 1, "status": "succeeded"}
            ],
        }

    def list_runs(self, *, limit=20):
        return [self.get_run("branch-demo")][:limit]


def test_design_api_endpoints_return_expected_shapes():
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
    assert source_response.json()["items"][0]["key"] == "hybrid"

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
    assert default_search_response.json()["knowledge_source"] == "graph_rag"
    assert service.last_knowledge_source == "graph_rag"

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
            "graph_template_id": "hkust_gz_gate",
            "knowledge_source": "graph_rag",
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["run_id"] == "branch-demo"
    assert branch_service.created["topk"] == 3

    status_response = client.get("/api/design/branch-runs/branch-demo")
    assert status_response.status_code == 200
    assert status_response.json()["nodes"][0]["optimization_directives"] == []
    assert status_response.json()["scatter_points"][0]["x"] == 70

    list_response = client.get("/api/design/branch-runs")
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["run_id"] == "branch-demo"


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


def test_design_api_defaults_draft_requests_to_graph_rag():
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
    assert service.last_knowledge_source == "graph_rag"
