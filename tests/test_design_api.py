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

from roadgen3d.services.design_types import DesignDraft, DesignDraftBundle, DesignIntent
from ui.api.main import create_app


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
            ),
            warnings=(),
        )

    def generate_scene(self, draft, **kwargs):
        return {
            "compose_config": draft.compose_config_patch,
            "summary": {"instance_count": 5},
            "scene_layout_path": "/tmp/layout.json",
            "viewer_url": "http://127.0.0.1:4173/?layout=demo",
        }

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

    rebuild_response = client.post("/api/knowledge/rebuild", json={})
    assert rebuild_response.status_code == 200
    assert rebuild_response.json()["chunk_count"] == 42
