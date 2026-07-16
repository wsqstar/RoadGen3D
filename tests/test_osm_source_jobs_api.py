from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from web.api.main import create_app
from roadgen3d.services.osm_source_jobs import OsmSourceJobService


class _OsmJobs:
    def __init__(self) -> None:
        self.request = None
        self.selection = None

    def create_job(self, request):
        self.request = dict(request)
        return {"id": "job-1", "kind": "osm_acquisition", "status": "queued", "stage": "queued", "progress": 0, "progress_mode": "determinate", "message": "queued", "detail": {}, "operations": [], "result": {}, "error": ""}

    def get_job(self, job_id):
        if job_id != "job-1":
            return None
        return {"id": job_id, "kind": "osm_acquisition", "status": "running", "stage": "overpass_fetch", "progress": 10, "progress_mode": "indeterminate", "message": "fetching", "detail": {"attempt": 1}, "operations": [], "result": {}, "error": ""}

    def cancel_job(self, job_id):
        return {"id": job_id, "status": "cancelled"} if job_id == "job-1" else None

    def retry_job(self, job_id):
        return {"id": "job-2", "status": "queued"} if job_id == "job-1" else None

    def select(self, preview_id, **selection):
        if preview_id != "preview-1":
            raise KeyError(preview_id)
        self.selection = selection
        return {"source": {"source_id": selection["source_id"]}, "osm_study": {"selection": selection}}

    def shutdown(self):
        return None


def test_osm_job_routes_create_poll_and_select():
    app = create_app()
    fake = _OsmJobs()
    app.state.osm_source_job_service.shutdown()
    app.state.osm_source_job_service = fake
    client = TestClient(app)

    created = client.post("/api/scene-sources/osm/jobs", json={
        "source_id": "fixture",
        "aoi_bbox": [113.26, 23.12, 113.27, 23.13],
    })
    assert created.status_code == 202
    assert created.json()["id"] == "job-1"
    assert fake.request["aoi_bbox"] == [113.26, 23.12, 113.27, 23.13]

    polled = client.get("/api/scene-sources/osm/jobs/job-1")
    assert polled.status_code == 200
    assert polled.json()["progress_mode"] == "indeterminate"

    selected = client.post("/api/scene-sources/osm/previews/preview-1/selection", json={
        "seed_logical_road_id": "logical-road-1",
        "hop_count": 2,
        "context_buffer_m": 100,
        "source_id": "selected-source",
    })
    assert selected.status_code == 200
    assert fake.selection["hop_count"] == 2
    assert fake.selection["context_buffer_m"] == 100


def test_osm_job_routes_reject_invalid_selection_and_unknown_job():
    app = create_app()
    fake = _OsmJobs()
    app.state.osm_source_job_service.shutdown()
    app.state.osm_source_job_service = fake
    client = TestClient(app)
    assert client.get("/api/scene-sources/osm/jobs/missing").status_code == 404
    invalid = client.post("/api/scene-sources/osm/previews/preview-1/selection", json={
        "seed_logical_road_id": "logical-road-1",
        "hop_count": 3,
        "context_buffer_m": 100,
    })
    assert invalid.status_code == 422


def test_local_osm_job_progress_is_monotonic_and_cancel_is_terminal(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()

    def _blocked_preview(**kwargs):
        callback = kwargs["progress_callback"]
        for index in range(60):
            callback({
                "stage": "overpass_fetch",
                "progress": (index * 7) % 80,
                "message": f"operation {index}",
                "detail": {"progress_mode": "indeterminate", "attempt": 1},
            })
        started.set()
        assert release.wait(timeout=2)
        callback({
            "stage": "preview_ready",
            "progress": 99,
            "message": "late success callback",
            "detail": {"progress_mode": "determinate"},
        })
        raise RuntimeError("late worker completion")

    monkeypatch.setattr(
        "roadgen3d.services.osm_source_jobs.build_osm_road_preview",
        _blocked_preview,
    )
    service = OsmSourceJobService(cache_dir=tmp_path, max_workers=1)
    try:
        created = service.create_job({"aoi_bbox": [113.26, 23.12, 113.27, 23.13]})
        assert started.wait(timeout=2)

        running = service.get_job(created["id"])
        assert running is not None
        progresses = [operation["progress"] for operation in running["operations"]]
        assert progresses == sorted(progresses)
        assert len(progresses) == 50
        assert running["progress_mode"] == "indeterminate"

        cancelled = service.cancel_job(created["id"])
        assert cancelled is not None
        assert cancelled["status"] == "cancelled"
        release.set()
        time.sleep(0.05)

        terminal = service.get_job(created["id"])
        assert terminal is not None
        assert terminal["status"] == "cancelled"
        assert terminal["stage"] == "cancelled"
    finally:
        release.set()
        service.shutdown()
