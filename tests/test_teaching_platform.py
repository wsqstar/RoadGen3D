from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from roadgen3d.teaching.artifacts import LocalArtifactStore
from roadgen3d.teaching.database import TeachingDatabase
from roadgen3d.teaching.geojson_pipeline import normalize_teaching_geojson
from roadgen3d.teaching.service import TeachingPlatformService
from web.api.main import create_app


class FakeDesignService:
    def evaluate_scene_unified(self, *, layout_path: str, evaluation_profile: str, evaluation_config: dict, **_kwargs):
        layout = json.loads(open(layout_path, encoding="utf-8").read())
        weights = evaluation_config["aggregation"]["dimension_weights"]
        walkability = float((layout.get("summary") or {}).get("walkability", 70.0))
        safety = 80.0
        beauty = 60.0
        overall = sum({"walkability": walkability, "safety": safety, "beauty": beauty}[key] * value for key, value in weights.items())
        return {
            "walkability": walkability,
            "safety": safety,
            "beauty": beauty,
            "overall": overall,
            "score_weights": weights,
            "llm_status": {"status": "fixture"},
        }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ROADGEN_ALLOW_DEV_BOOTSTRAP", "1")
    database = TeachingDatabase(f"sqlite:///{tmp_path / 'teaching.db'}")
    service = TeachingPlatformService(database, LocalArtifactStore(tmp_path / "objects"))
    app = create_app(design_service=FakeDesignService(), teaching_service=service)
    with TestClient(app) as test_client:
        yield test_client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bootstrap_course_and_student(client: TestClient):
    response = client.post("/api/v1/auth/bootstrap", json={
        "email": "teacher@example.edu",
        "password": "teacher-pass-123",
        "display_name": "Teacher",
    })
    assert response.status_code == 201, response.text
    login = client.post("/api/v1/auth/login", json={"email": "teacher@example.edu", "password": "teacher-pass-123"})
    teacher_token = login.json()["access_token"]
    course = client.post("/api/v1/courses", headers=_auth(teacher_token), json={"name": "Urban Design 101", "code": "UD101"})
    assert course.status_code == 201, course.text
    course_payload = course.json()
    register = client.post("/api/v1/auth/register", json={
        "email": "student@example.edu",
        "password": "student-pass-123",
        "display_name": "Student",
        "course_code": "UD101",
        "invite_code": course_payload["invite_code"],
    })
    assert register.status_code == 201, register.text
    student_login = client.post("/api/v1/auth/login", json={"email": "student@example.edu", "password": "student-pass-123"})
    return teacher_token, student_login.json()["access_token"], course_payload


def test_geojson_crs_transform_stable_ids_and_intersection_annotation():
    payload = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": [
            {"type": "Feature", "properties": {"highway": "residential"}, "geometry": {"type": "LineString", "coordinates": [[0, -100], [0, 100]]}},
            {"type": "Feature", "properties": {"highway": "footway"}, "geometry": {"type": "LineString", "coordinates": [[-100, 0], [100, 0]]}},
        ],
    }
    first = normalize_teaching_geojson(payload, source_id="crs-test")
    second = normalize_teaching_geojson(payload, source_id="crs-test-2")
    geojson = first["geojson"]
    assert geojson["roadgen3d"]["crs"] == "EPSG:4326"
    assert geojson["roadgen3d"]["source_crs"] == "EPSG:3857"
    assert first["role_counts"] == {"centerline": 2, "road_intersection": 1}
    assert geojson["features"][2]["geometry"]["coordinates"] == pytest.approx([0.0, 0.0])
    assert [item["id"] for item in geojson["features"]] == [item["id"] for item in second["geojson"]["features"]]
    assert first["quality_report"]["conversion_ok"] is True


def test_course_project_geojson_revision_evaluation_compare_and_export(client: TestClient):
    teacher_token, student_token, course = _bootstrap_course_and_student(client)
    project_response = client.post("/api/v1/projects", headers=_auth(student_token), json={
        "course_id": course["id"],
        "name": "Guangzhou Street Studio",
        "city": "广州",
        "design_goal": "walkable campus edge",
        "aoi_bbox": [113.54, 22.79, 113.55, 22.80],
    })
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()

    # Teachers can see course work; students remain owner-scoped.
    teacher_projects = client.get("/api/v1/projects", headers=_auth(teacher_token)).json()["items"]
    assert [item["id"] for item in teacher_projects] == [project["id"]]

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"highway": "residential", "name": "教学路"}, "geometry": {"type": "LineString", "coordinates": [[113.541, 22.791], [113.548, 22.798]]}},
            {"type": "Feature", "properties": {"building": "university"}, "geometry": {"type": "Polygon", "coordinates": [[[113.542, 22.792], [113.543, 22.792], [113.543, 22.793], [113.542, 22.792]]]}},
            {"type": "Feature", "properties": {"natural": "tree"}, "geometry": {"type": "Point", "coordinates": [113.544, 22.794]}},
        ],
    }
    imported = client.post(f"/api/v1/projects/{project['id']}/sources/geojson", headers=_auth(student_token), json={"geojson": geojson})
    assert imported.status_code == 201, imported.text
    source = imported.json()
    assert source["quality_report"] == {
        "conversion_ok": True,
        "geo_delta": 0.0,
        "geo_delta_unit": "m",
        "topology_ok": True,
        "lost_feature_ids": [],
        "feature_count_before": 3,
        "feature_count_after": 3,
    }
    assert source["role_counts"] == {"centerline": 1, "building_footprint": 1, "tree_candidate": 1}

    normalized_download = client.get(f"/api/v1/artifacts/{source['normalized_artifact_id']}", headers=_auth(student_token))
    assert normalized_download.status_code == 200
    normalized = normalized_download.json()
    assert normalized["roadgen3d"]["crs"] == "EPSG:4326"
    assert normalized["features"][2]["properties"]["annotation_confidence"] == pytest.approx(0.98)

    baseline = client.post(f"/api/v1/projects/{project['id']}/revisions", headers=_auth(student_token), json={
        "source_id": source["id"],
        "branch_kind": "baseline",
        "label": "Initial generated scene",
        "layout": {"version": "roadgen3d.scene_layout.v1", "placements": [], "summary": {"walkability": 70}},
    })
    assert baseline.status_code == 201, baseline.text
    assert baseline.json()["auto_evaluation"]["status"] == "succeeded"
    human = client.post(f"/api/v1/projects/{project['id']}/revisions", headers=_auth(student_token), json={
        "source_id": source["id"],
        "parent_id": baseline.json()["id"],
        "branch_kind": "human_edit",
        "label": "Student tree edit",
        "commands": [{"command_id": "add-tree-1", "op": "add_instance", "category": "tree"}],
        "evaluation_weights": {"walkability": 45, "safety": 35, "beauty": 20},
        "layout": {"version": "roadgen3d.scene_layout.v1", "placements": [{"instance_id": "tree-1", "category": "tree"}], "summary": {"walkability": 82}},
    })
    assert human.status_code == 201, human.text
    assert human.json()["auto_evaluation"]["weights"] == {"walkability": 0.45, "safety": 0.35, "beauty": 0.2}

    compared = client.post(f"/api/v1/projects/{project['id']}/comparisons", headers=_auth(student_token), json={"revision_ids": [baseline.json()["id"], human.json()["id"]]})
    assert compared.status_code == 200
    assert compared.json()["items"][1]["score_delta"]["walkability"] == pytest.approx(12.0)
    assert "not causal" in compared.json()["claim_scope"]

    exported = client.post(f"/api/v1/projects/{project['id']}/exports", headers=_auth(student_token))
    assert exported.status_code == 202, exported.text
    job = exported.json()
    assert job["status"] == "succeeded"
    bundle_id = job["result"]["id"]
    bundle = client.get(f"/api/v1/artifacts/{bundle_id}", headers=_auth(student_token))
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == "roadgen3d.project_bundle.v1"
        assert len(manifest["revisions"]) == 2
        assert len(manifest["evaluations"]) == 2


def test_student_cannot_read_another_students_project(client: TestClient):
    _teacher_token, first_token, course = _bootstrap_course_and_student(client)
    second_register = client.post("/api/v1/auth/register", json={
        "email": "student2@example.edu",
        "password": "student2-pass-123",
        "display_name": "Student Two",
        "course_code": course["code"],
        "invite_code": course["invite_code"],
    })
    assert second_register.status_code == 201
    second_token = client.post("/api/v1/auth/login", json={"email": "student2@example.edu", "password": "student2-pass-123"}).json()["access_token"]
    project = client.post("/api/v1/projects", headers=_auth(first_token), json={"course_id": course["id"], "name": "Private student work"}).json()
    forbidden = client.get(f"/api/v1/projects/{project['id']}", headers=_auth(second_token))
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "forbidden"


def test_durable_job_recovery_and_cancellation_are_terminal(client: TestClient):
    response = client.post("/api/v1/auth/bootstrap", json={
        "email": "jobs@example.edu",
        "password": "teacher-pass-123",
        "display_name": "Jobs Teacher",
    })
    assert response.status_code == 201
    token = client.post("/api/v1/auth/login", json={"email": "jobs@example.edu", "password": "teacher-pass-123"}).json()["access_token"]
    actor = client.get("/api/v1/me", headers=_auth(token)).json()
    service = client.app.state.teaching_service

    running = service.create_job(actor["id"], None, kind="project_export", payload={})
    service.update_job(running["id"], status="running", progress=40)
    assert running["id"] in service.recover_incomplete_jobs()
    assert service.get_job(actor["id"], running["id"])["status"] == "queued"

    cancelled = service.cancel_job(actor["id"], running["id"])
    assert cancelled["status"] == "cancelled"
    after_late_completion = service.update_job(running["id"], status="succeeded", progress=100, result={"unexpected": True})
    assert after_late_completion["status"] == "cancelled"
    assert after_late_completion["result"] == {}
