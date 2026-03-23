from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.design_types import DesignDraft, SceneGenerationResult  # noqa: E402
from roadgen3d.services.scene_jobs import SceneJobService  # noqa: E402


def _draft() -> DesignDraft:
    return DesignDraft(
        normalized_scene_query="safe complete street",
        compose_config_patch={"sidewalk_width_m": 4.0},
        citations_by_field={},
        design_summary="summary",
    )


def test_scene_job_service_runs_sync_generation():
    service = SceneJobService(
        generator=lambda draft, **kwargs: SceneGenerationResult(
            compose_config=draft.compose_config_patch,
            summary={"instance_count": 7},
            scene_layout_path="/tmp/layout.json",
            scene_glb_path="/tmp/scene.glb",
            scene_ply_path="/tmp/scene.ply",
            viewer_url="http://127.0.0.1:4173/?layout=demo",
        )
    )

    result = service.run_job_sync(draft=_draft())

    assert result.summary["instance_count"] == 7
    recent = service.list_recent_scenes(limit=1)
    assert recent[0].job_id
    assert recent[0].viewer_url.startswith("http://127.0.0.1:4173/")


def test_scene_job_service_records_failure():
    service = SceneJobService(generator=lambda draft, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    created = service.submit_job(draft=_draft())
    status = service.wait_for_job(created.job_id, timeout_s=2.0)

    assert status is not None
    assert status.status == "failed"
    assert "boom" in status.error
