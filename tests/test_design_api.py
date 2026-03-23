from __future__ import annotations

import sys
from pathlib import Path

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
    SceneGenerationResult,
    SceneJobCreateResponse,
    SceneJobStatusResponse,
    SceneRecord,
)
from web.api.main import create_app  # noqa: E402


class _FakeService:
    default_pdf_path = Path("/tmp/guide.pdf")
    default_artifact_dir = Path("/tmp/knowledge")

    def draft_design(self, **kwargs):
        return DesignDraftBundle(
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

    def generate_scene(self, draft, **kwargs):
        return {
            "compose_config": draft.compose_config_patch,
            "summary": {"instance_count": 5, "clearance_m": float("inf")},
            "scene_layout_path": "/tmp/layout.json",
            "scene_glb_path": "/tmp/scene.glb",
            "scene_ply_path": "/tmp/scene.ply",
            "viewer_url": "http://127.0.0.1:4173/?layout=demo",
        }

    def create_scene_job(self, draft, **kwargs):
        return SceneJobCreateResponse(job_id="job-demo", status="queued", created_at="2026-03-23T00:00:00+00:00")

    def list_scene_jobs(self, *, limit=20):
        return [
            SceneJobStatusResponse(
                job_id="job-demo",
                status="succeeded",
                created_at="2026-03-23T00:00:00+00:00",
                started_at="2026-03-23T00:00:01+00:00",
                finished_at="2026-03-23T00:00:02+00:00",
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


def test_design_api_endpoints_return_expected_shapes():
    client = TestClient(create_app(design_service=_FakeService()))

    draft_response = client.post(
        "/api/design/draft",
        json={
            "messages": [{"role": "user", "content": "请做一条全龄友好的街道。"}],
            "user_input": "请做一条全龄友好的街道。",
            "current_patch": {},
        },
    )
    assert draft_response.status_code == 200
    assert draft_response.json()["draft"]["compose_config_patch"]["sidewalk_width_m"] == 4.0
    assert draft_response.json()["draft"]["parameter_sources_by_field"]["sidewalk_width_m"] == "rag"

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
            "patch_overrides": {},
            "generation_options": {},
        },
    )
    assert generate_response.status_code == 200
    assert generate_response.json()["viewer_url"].startswith("http://127.0.0.1:4173/")
    assert "Infinity" not in generate_response.text
    assert generate_response.json()["summary"]["clearance_m"] is None

    job_create_response = client.post(
        "/api/scene/jobs",
        json={
            "draft": {
                "normalized_scene_query": "walkable street",
                "compose_config_patch": {"sidewalk_width_m": 4.0},
                "citations_by_field": {},
                "design_summary": "summary",
                "risk_notes": [],
            }
        },
    )
    assert job_create_response.status_code == 200
    assert job_create_response.json()["status"] == "queued"

    job_list_response = client.get("/api/scene/jobs")
    assert job_list_response.status_code == 200
    assert job_list_response.json()["items"][0]["status"] == "succeeded"

    job_status_response = client.get("/api/scene/jobs/job-demo")
    assert job_status_response.status_code == 200
    assert job_status_response.json()["result"]["scene_layout_path"] == "/tmp/layout.json"
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
