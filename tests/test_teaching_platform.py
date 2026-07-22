from __future__ import annotations

import io
import json
import sys
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

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
from roadgen3d.teaching.service import Conflict, TeachingPlatformService
from web.api.main import create_app


class FakeDesignService:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.generation_calls: list[dict] = []
        self.scene_jobs: dict[str, object] = {}
        self._generation_lock = threading.Lock()
        self._generation_counter = 0
        self._active_generations = 0
        self.max_concurrent_generations = 0

    def get_scene_job(self, job_id: str):
        return self.scene_jobs.get(job_id)

    def generate_scene(self, draft, *, scene_context: dict, generation_options: dict, **kwargs):
        with self._generation_lock:
            self._generation_counter += 1
            call_index = self._generation_counter
            self._active_generations += 1
            self.max_concurrent_generations = max(self.max_concurrent_generations, self._active_generations)
        # Make overlap observable for the parallel Scenario C contract while
        # keeping this fixture fast.
        time.sleep(0.02)
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback({"stage": "context_resolving", "progress": 15, "message": "Fixture annotation resolved."})
            progress_callback({"stage": "layout_generation", "progress": 44, "message": "Fixture layout generated."})
            progress_callback({"stage": "mesh_generation", "progress": 76, "message": "Fixture massing generated.", "detail": {"building_count": 1}})
            progress_callback({"stage": "glb_export", "progress": 88, "message": "Fixture GLB exported."})
        call_dir = self.output_dir / f"generated-{call_index}"
        call_dir.mkdir(parents=True, exist_ok=True)
        layout_path = call_dir / "scene_layout.json"
        glb_path = call_dir / "scene.glb"
        step_glb_path = call_dir / "road-base.glb"
        step_glb_path.write_bytes(b"fixture-road-base-glb")
        layout_path.write_text(json.dumps({
            "version": "roadgen3d.scene_layout.v1",
            "placements": [{"instance_id": f"tree-{call_index}", "category": "tree"}],
            "summary": {"walkability": 70 + call_index},
            "compose_config": dict(draft.compose_config_patch),
            "production_steps": [{"step_id": "road_base", "title": "Road Base", "glb_path": str(step_glb_path)}],
        }), encoding="utf-8")
        glb_path.write_bytes(b"fixture-glb")
        if generation_options.get("capture_3d_views", True) and generation_options.get("retain_glb_policy") != "always":
            glb_path.unlink()
        with self._generation_lock:
            self.generation_calls.append({
                "draft": draft.to_dict(),
                "scene_context": scene_context,
                "generation_options": generation_options,
                "patch_overrides": dict(kwargs.get("patch_overrides") or {}),
            })
            self._active_generations -= 1
        return {"scene_layout_path": str(layout_path), "scene_glb_path": str(glb_path)}

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
    for name in ("ROADGEN_LLM_API_KEY", "GRAPHRAG_API_KEY", "key", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "")
    database = TeachingDatabase(f"sqlite:///{tmp_path / 'teaching.db'}")
    service = TeachingPlatformService(database, LocalArtifactStore(tmp_path / "objects"))
    app = create_app(design_service=FakeDesignService(tmp_path / "generated"), teaching_service=service)
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
    reviewed = normalize_teaching_geojson(first["geojson"], source_id="crs-review")
    geojson = first["geojson"]
    assert geojson["roadgen3d"]["crs"] == "EPSG:4326"
    assert geojson["roadgen3d"]["source_crs"] == "EPSG:3857"
    # Each road is split at the shared crossing so editable centerlines end
    # at the explicit junction instead of merely passing through it.
    assert first["role_counts"] == {"centerline": 4, "road_intersection": 1}
    intersection = next(item for item in geojson["features"] if item["properties"]["role"] == "road_intersection")
    assert intersection["geometry"]["coordinates"] == pytest.approx([0.0, 0.0])
    assert [item["id"] for item in geojson["features"]] == [item["id"] for item in second["geojson"]["features"]]
    assert reviewed["role_counts"] == {"centerline": 4, "road_intersection": 1}
    assert first["quality_report"]["conversion_ok"] is True


def test_osm_crossing_roads_become_editable_segments_with_source_provenance():
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "osm-road-101",
                "properties": {
                    "highway": "residential",
                    "osm_way_id": 101,
                    "osm_node_ids": [1, 2],
                    "tags": {"lanes": "4", "cycleway:left": "lane"},
                },
                "geometry": {"type": "LineString", "coordinates": [[113.0, 23.0], [113.002, 23.0]]},
            },
            {
                "type": "Feature",
                "id": "osm-road-202",
                "properties": {
                    "highway": "residential",
                    "osm_way_id": 202,
                    "osm_node_ids": [3, 4],
                    "tags": {"lanes": "3", "oneway": "yes"},
                },
                "geometry": {"type": "LineString", "coordinates": [[113.001, 22.999], [113.001, 23.001]]},
            },
        ],
    }

    normalized = normalize_teaching_geojson(payload, source_id="osm-overlay-test")
    annotation = normalized["annotation"]
    assert len(annotation["centerlines"]) == 4
    junction = annotation["junctions"][0]
    assert junction["source_mode"] == "explicit"
    assert len(junction["connected_centerline_ids"]) == 4
    assert all(
        item["source_refs"]["kind"] == "osm_road"
        and item["source_refs"]["edit_state"] == "base"
        and item["source_refs"]["osm_way_ids"]
        for item in annotation["centerlines"]
    )
    for segment in annotation["centerlines"]:
        assert junction["id"] in {segment.get("start_junction_id"), segment.get("end_junction_id")}
    profiles = {
        item["source_refs"]["osm_way_ids"][0]: (
            item["forward_drive_lane_count"],
            item["reverse_drive_lane_count"],
            item["bike_lane_count"],
        )
        for item in annotation["centerlines"]
    }
    assert profiles["101"] == (2, 2, 1)
    assert profiles["202"] == (3, 0, 0)


def test_personal_workspace_invites_isolate_projects_and_admin_metadata(client: TestClient):
    """Professional users share deployment infrastructure, never project content."""

    boot = client.post("/api/v1/auth/bootstrap", json={
        "email": "admin@example.edu",
        "password": "admin-pass-123",
        "display_name": "Administrator",
    })
    assert boot.status_code == 201, boot.text
    admin_token = client.post("/api/v1/auth/login", json={
        "email": "admin@example.edu", "password": "admin-pass-123",
    }).json()["access_token"]

    def invite() -> str:
        response = client.post(
            "/api/v1/admin/registration-invites",
            headers=_auth(admin_token),
            json={"max_uses": 1, "expires_in_hours": 24, "note": "tenant isolation test"},
        )
        assert response.status_code == 201, response.text
        return response.json()["invite_code"]

    first = client.post("/api/v1/auth/register-personal", json={
        "email": "first@example.edu", "password": "personal-pass-123", "display_name": "First", "invite_code": invite(),
    })
    second = client.post("/api/v1/auth/register-personal", json={
        "email": "second@example.edu", "password": "personal-pass-123", "display_name": "Second", "invite_code": invite(),
    })
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    first_token, second_token = first.json()["access_token"], second.json()["access_token"]

    public_guest = client.post("/api/v1/auth/guest")
    assert public_guest.status_code == 201, public_guest.text
    public_guest_key = public_guest.json()["recovery_key"]
    assert public_guest_key.startswith("RG3D-GUEST-")

    first_workspace = client.get("/api/v1/workspace", headers=_auth(first_token))
    assert first_workspace.status_code == 200, first_workspace.text
    assert first_workspace.json()["workspace"]["scope"] == "personal"
    assert client.get("/api/v1/courses", headers=_auth(first_token)).json()["items"] == []

    first_project = client.post("/api/v1/workspace/projects", headers=_auth(first_token), json={
        "name": "First private street", "city": "广州", "design_goal": "walkable street",
    })
    second_project = client.post("/api/v1/workspace/projects", headers=_auth(second_token), json={
        "name": "Second private street", "city": "深圳", "design_goal": "safe street",
    })
    assert first_project.status_code == 201, first_project.text
    assert second_project.status_code == 201, second_project.text
    assert client.get(f"/api/v1/projects/{second_project.json()['id']}", headers=_auth(first_token)).status_code == 403
    assert client.get(f"/api/v1/projects/{first_project.json()['id']}", headers=_auth(second_token)).status_code == 403
    # An administrator receives operational metadata only, not a membership bypass.
    assert client.get(f"/api/v1/projects/{first_project.json()['id']}", headers=_auth(admin_token)).status_code == 403

    overview = client.get("/api/v1/admin/overview", headers=_auth(admin_token))
    users = client.get("/api/v1/admin/users", headers=_auth(admin_token))
    assert overview.status_code == 200 and overview.json()["users"]["total"] == 4
    assert users.status_code == 200
    first_summary = next(item for item in users.json()["items"] if item["email"] == "first@example.edu")
    assert first_summary["project_count"] == 1
    assert "layout" not in json.dumps(first_summary)
    guest_summary = next(item for item in users.json()["items"] if item["id"] == public_guest.json()["user"]["id"])
    assert guest_summary["guest_recovery_key"] == public_guest_key
    guest_detail = client.get(f"/api/v1/admin/users/{guest_summary['id']}", headers=_auth(admin_token))
    assert guest_detail.status_code == 200
    assert guest_detail.json()["guest_recovery_key"] == public_guest_key
    assert client.get("/api/v1/auth/guest-recovery-key", headers=_auth(first_token)).status_code == 403
    assert client.get("/api/v1/admin/overview", headers=_auth(first_token)).status_code == 403

    # Suspending removes active sessions immediately; a later administrator login can reactivate.
    suspended = client.post(f"/api/v1/admin/users/{first.json()['user']['id']}/status", headers=_auth(admin_token), json={"is_active": False})
    assert suspended.status_code == 200, suspended.text
    assert client.get("/api/v1/workspace", headers=_auth(first_token)).status_code == 403
    assert client.post(f"/api/v1/admin/users/{first.json()['user']['id']}/status", headers=_auth(admin_token), json={"is_active": True}).status_code == 200


def test_guest_public_workspace_is_publicly_readable_and_owner_writable(client: TestClient):
    first = client.post("/api/v1/auth/guest")
    second = client.post("/api/v1/auth/guest")
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["user"]["system_role"] == "guest"
    assert first.json()["workspace"]["scope"] == "public"
    first_key = first.json()["recovery_key"]
    second_key = second.json()["recovery_key"]
    assert first_key.startswith("RG3D-GUEST-") and len(first_key) == 43
    assert second_key.startswith("RG3D-GUEST-") and second_key != first_key
    assert "recovery_key" not in first.json()["user"]
    first_token = first.json()["access_token"]
    second_token = second.json()["access_token"]
    own_key = client.get("/api/v1/auth/guest-recovery-key", headers=_auth(first_token))
    assert own_key.status_code == 200 and own_key.json()["recovery_key"] == first_key
    assert client.get("/api/v1/me", headers=_auth(first_token)).json().get("guest_recovery_key") is None

    project_response = client.post("/api/v1/workspace/projects", headers=_auth(first_token), json={
        "name": "Guest public street",
        "city": "广州",
        "design_goal": "walkable public street",
    })
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()

    invalid_recovery = client.post("/api/v1/auth/guest/recover", json={"recovery_key": "RG3D-GUEST-NOT-VALID"})
    assert invalid_recovery.status_code == 403
    recovered = client.post("/api/v1/auth/guest/recover", json={"recovery_key": first_key})
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["user"]["id"] == first.json()["user"]["id"]
    assert recovered.json()["recovery_key"] == first_key
    # The recovery route refreshes the long-lived HttpOnly cookie, so the
    # restored browser can continue without an Authorization header.
    cookie_workspace = client.get("/api/v1/workspace")
    assert cookie_workspace.status_code == 200
    assert [item["id"] for item in cookie_workspace.json()["projects"]] == [project["id"]]

    imported_response = client.post(
        f"/api/v1/projects/{project['id']}/sources/geojson",
        headers=_auth(first_token),
        json={"geojson": {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "id": "guest-road-1",
                "properties": {"highway": "residential"},
                "geometry": {"type": "LineString", "coordinates": [[113.541, 22.791], [113.548, 22.798]]},
            }],
        }},
    )
    assert imported_response.status_code == 201, imported_response.text
    imported = imported_response.json()

    adopt_dir = client.app.state.design_service.output_dir / "professional-adopt"
    adopt_dir.mkdir(parents=True, exist_ok=True)
    adopt_glb = adopt_dir / "scene.glb"
    adopt_step = adopt_dir / "road-base.glb"
    adopt_layout = adopt_dir / "scene_layout.json"
    adopt_glb.write_bytes(b"fixture-glb")
    adopt_step.write_bytes(b"fixture-road-base-glb")
    adopt_layout.write_text(json.dumps({
        "version": "roadgen3d.scene_layout.v1",
        "placements": [{"instance_id": "guest-tree", "category": "tree"}],
        "summary": {"walkability": 74},
        "production_steps": [{"step_id": "road_base", "title": "Road Base", "glb_path": str(adopt_step)}],
    }), encoding="utf-8")
    client.app.state.design_service.scene_jobs["fixture-professional-job"] = SimpleNamespace(
        status="succeeded",
        result=SimpleNamespace(to_dict=lambda: {
            "scene_layout_path": str(adopt_layout),
            "scene_glb_path": str(adopt_glb),
            "summary": {"generator": "professional"},
        }),
    )
    adopted = client.post(
        f"/api/v1/projects/{project['id']}/adopt-scene-job",
        headers=_auth(first_token),
        json={"job_id": "fixture-professional-job", "source_id": imported["id"]},
    )
    assert adopted.status_code == 201, adopted.text
    revision = adopted.json()
    assert revision["provenance"]["professional_job_id"] == "fixture-professional-job"
    assert revision["auto_evaluation"]["status"] == "succeeded"

    public_list = client.get("/api/v1/public/projects")
    assert public_list.status_code == 200, public_list.text
    summary = next(item for item in public_list.json()["items"] if item["id"] == project["id"])
    assert summary["name"] == "Guest public street"
    assert summary["latest_revision"]["id"] == revision["id"]
    assert summary["latest_evaluation"]["status"] == "succeeded"
    assert "email" not in summary

    assert client.get(f"/api/v1/projects/{project['id']}", headers=_auth(second_token)).status_code == 200
    assert client.patch(
        f"/api/v1/projects/{project['id']}/workflow",
        headers=_auth(second_token),
        json={"workflow_step": "evaluation"},
    ).status_code == 403
    assert client.patch(
        f"/api/v1/projects/{project['id']}/workflow",
        headers=_auth(first_token),
        json={"workflow_step": "evaluation"},
    ).status_code == 200

    manifest_response = client.get(
        f"/api/v1/public/projects/{project['id']}/revisions/{revision['id']}/viewer-manifest",
    )
    assert manifest_response.status_code == 200, manifest_response.text
    artifact_id = manifest_response.json()["final_scene"]["artifact_id"]
    public_artifact = client.get(f"/api/v1/public/artifacts/{artifact_id}")
    assert public_artifact.status_code == 200
    assert public_artifact.content == b"fixture-glb"

    configuration_export = client.get("/api/v1/workspace/exports/configuration", headers=_auth(first_token))
    assert configuration_export.status_code == 200, configuration_export.text
    assert configuration_export.headers["content-type"].startswith("application/zip")
    assert "roadgen3d-user-config-2d-history-" in configuration_export.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(configuration_export.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["export_scope"] == "configuration_2d_history"
        assert manifest["includes_3d_results"] is False
        assert [item["id"] for item in manifest["projects"]] == [project["id"]]
        assert manifest["sources"][0]["project_id"] == project["id"]
        assert manifest["revisions"][0]["id"] == revision["id"]
        assert "history/revisions.json" in names
        assert any("/2d-and-inputs/" in name for name in names)
        assert not any("/3d/" in name or name.endswith(".glb") for name in names)
        serialized = json.dumps(manifest).lower()
        assert "password_hash" not in serialized
        assert "token_hash" not in serialized
        assert "recovery_key" not in serialized
        assert second.json()["user"]["id"] not in serialized

    full_export = client.get("/api/v1/workspace/exports/full", headers=_auth(first_token))
    assert full_export.status_code == 200, full_export.text
    assert "roadgen3d-user-full-" in full_export.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(full_export.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["export_scope"] == "full"
        assert manifest["includes_3d_results"] is True
        assert any("/3d/" in name and name.endswith(".glb") for name in names)
        assert any(item["kind"] == "scene_layout" for item in manifest["artifacts"])

    restored = client.post(
        "/api/v1/workspace/imports/configuration",
        headers=_auth(second_token),
        files={"file": ("roadgen3d-backup.zip", configuration_export.content, "application/zip")},
    )
    assert restored.status_code == 201, restored.text
    assert restored.json() == {
        "schema_version": "roadgen3d.user_data_import.v1",
        "project_count": 1,
        "artifact_count": 3,
        "source_count": 1,
        "revision_count": 1,
        "ignored_3d": True,
    }
    restored_workspace = client.get("/api/v1/workspace", headers=_auth(second_token)).json()
    assert len(restored_workspace["projects"]) == 1
    restored_project = restored_workspace["projects"][0]
    assert restored_project["id"] != project["id"]
    assert restored_project["name"] == project["name"]
    restored_sources = client.get(f"/api/v1/projects/{restored_project['id']}/sources", headers=_auth(second_token))
    restored_revisions = client.get(f"/api/v1/projects/{restored_project['id']}/revisions", headers=_auth(second_token))
    assert restored_sources.status_code == 200 and len(restored_sources.json()["items"]) == 1
    assert restored_revisions.status_code == 200 and len(restored_revisions.json()["items"]) == 1
    assert restored_revisions.json()["items"][0]["layout_artifact_id"] is None
    assert restored_revisions.json()["items"][0]["glb_artifact_id"] is None

    invalid_import = client.post(
        "/api/v1/workspace/imports/configuration",
        headers=_auth(second_token),
        files={"file": ("invalid.zip", b"not-a-zip", "application/zip")},
    )
    assert invalid_import.status_code == 422
    assert "valid ZIP" in invalid_import.json()["detail"]["message"]

    assert client.get("/api/v1/workspace/exports/unknown", headers=_auth(first_token)).status_code == 422
    assert client.post(f"/api/v1/projects/{project['id']}/exports", headers=_auth(second_token)).status_code == 403
    exported = client.post(f"/api/v1/projects/{project['id']}/exports", headers=_auth(first_token))
    assert exported.status_code == 202, exported.text
    assert exported.json()["status"] == "succeeded"
    refreshed = client.get(f"/api/v1/public/projects/{project['id']}").json()
    assert refreshed["latest_bundle"]["download_url"].startswith("/api/v1/public/artifacts/")
    assert client.get(refreshed["latest_bundle"]["download_url"]).status_code == 200
    assert client.get("/api/v1/admin/overview", headers=_auth(first_token)).status_code == 403


def test_guest_lazily_imports_local_layout_before_first_3d_edit(client: TestClient, monkeypatch, tmp_path: Path):
    artifact_root = tmp_path / "importable-artifacts"
    scene_dir = artifact_root / "starter_scenes" / "guangzhou-v6"
    scene_dir.mkdir(parents=True)
    glb_path = scene_dir / "scene.glb"
    glb_path.write_bytes(b"imported-scene-glb")
    layout_path = scene_dir / "scene_layout.json"
    layout_path.write_text(json.dumps({
        "version": "roadgen3d.scene_layout.v1",
        "placements": [{
            "instance_id": "tree-1",
            "asset_id": "tree-original",
            "category": "tree",
            "position_xyz": [1.0, 0.0, 2.0],
            "bbox_xz": [0.5, 1.5, 1.5, 2.5],
        }],
        "outputs": {"scene_glb": str(glb_path)},
    }), encoding="utf-8")
    monkeypatch.setenv("ROADGEN_IMPORTABLE_SCENE_ROOT", str(artifact_root))

    owner = client.post("/api/v1/auth/guest").json()
    visitor = client.post("/api/v1/auth/guest").json()
    project = client.post("/api/v1/workspace/projects", headers=_auth(owner["access_token"]), json={
        "name": "Lazy imported public scene",
        "city": "广州",
    }).json()
    source = client.post(
        f"/api/v1/projects/{project['id']}/sources/geojson",
        headers=_auth(owner["access_token"]),
        json={"geojson": {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"highway": "residential"},
                "geometry": {"type": "LineString", "coordinates": [[113.54, 22.79], [113.55, 22.80]]},
            }],
        }},
    ).json()

    imported = client.post(
        f"/api/v1/projects/{project['id']}/revisions/import-layout",
        headers=_auth(owner["access_token"]),
        json={"layout_path": str(layout_path), "label": "Imported before replace", "source_id": source["id"]},
    )
    assert imported.status_code == 201, imported.text
    revision = imported.json()
    assert revision["branch_kind"] == "baseline"
    assert revision["source_id"] == source["id"]
    assert revision["provenance"]["import_method"] == "professional_local_artifact"
    assert revision["provenance"]["source_linked"] is True
    assert "layout_path" not in json.dumps(revision["provenance"])

    manifest = client.get(
        f"/api/v1/public/projects/{project['id']}/revisions/{revision['id']}/viewer-manifest",
    )
    assert manifest.status_code == 200, manifest.text
    artifact_id = manifest.json()["final_scene"]["artifact_id"]
    assert client.get(f"/api/v1/public/artifacts/{artifact_id}").content == b"imported-scene-glb"

    forbidden = client.post(
        f"/api/v1/projects/{project['id']}/revisions/import-layout",
        headers=_auth(visitor["access_token"]),
        json={"layout_path": str(layout_path)},
    )
    assert forbidden.status_code == 403

    outside = tmp_path / "outside" / "scene_layout.json"
    outside.parent.mkdir()
    outside.write_text("{}", encoding="utf-8")
    rejected = client.post(
        f"/api/v1/projects/{project['id']}/revisions/import-layout",
        headers=_auth(owner["access_token"]),
        json={"layout_path": str(outside)},
    )
    assert rejected.status_code == 403


def test_shared_workflow_source_annotation_review_and_project_viewer_manifest(client: TestClient):
    _teacher_token, student_token, course = _bootstrap_course_and_student(client)
    project = client.post("/api/v1/projects", headers=_auth(student_token), json={
        "course_id": course["id"],
        "name": "Shared workbench contract",
        "city": "广州",
        "aoi_bbox": [113.541, 22.791, 113.548, 22.798],
    }).json()
    imported = client.post(f"/api/v1/projects/{project['id']}/sources/geojson", headers=_auth(student_token), json={
        "geojson": {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "id": "osm-road-1",
                "properties": {"highway": "residential"},
                "geometry": {"type": "LineString", "coordinates": [[113.541, 22.791], [113.548, 22.798]]},
            }],
        },
    }).json()

    workflow_response = client.get(
        f"/api/v1/projects/{project['id']}/sources/{imported['id']}/workflow-source",
        headers=_auth(student_token),
    )
    assert workflow_response.status_code == 200, workflow_response.text
    workflow_source = workflow_response.json()
    assert workflow_source["annotation"]["centerlines"][0]["id"] == "osm-road-1"
    assert workflow_source["annotation"]["image_width_px"] != 1024
    assert workflow_source["annotation"]["pixels_per_meter"] == pytest.approx(2.0)

    reviewed_response = client.post(
        f"/api/v1/projects/{project['id']}/sources/{imported['id']}/review",
        headers=_auth(student_token),
        json={
            "annotation": workflow_source["annotation"],
            "actions": [{"op": "approve_reference_annotation", "feature_id": "osm-road-1"}],
            "notes": "Approved in shared editor",
        },
    )
    assert reviewed_response.status_code == 201, reviewed_response.text
    reviewed = reviewed_response.json()
    assert reviewed["quality_report"]["review_annotation_preserved"] is True

    generated = client.post(f"/api/v1/projects/{project['id']}/generate", headers=_auth(student_token), json={
        "source_id": reviewed["id"],
        "generation_mode": "baseline",
    })
    assert generated.status_code == 202, generated.text
    revision = generated.json()["result"]["revision"]
    manifest_response = client.get(
        f"/api/v1/projects/{project['id']}/revisions/{revision['id']}/viewer-manifest",
        headers=_auth(student_token),
    )
    assert manifest_response.status_code == 200, manifest_response.text
    manifest = manifest_response.json()
    assert manifest["final_scene"]["artifact_id"] == revision["glb_artifact_id"]
    assert manifest["final_scene"]["glb_url"] == ""
    assert manifest["layout_path"] == f"project-revision:{revision['id']}"
    assert manifest["production_steps"][0]["step_id"] == "road_base"
    assert manifest["production_steps"][0]["artifact_id"]
    assert manifest["production_steps"][0]["glb_url"] == ""
    assert "/Users/" not in json.dumps(manifest)
    assert manifest["layout_revision"]["revision"] == revision["revision_number"]


def test_public_osm_scene_source_uses_shared_full_normalizer(client: TestClient, monkeypatch):
    from roadgen3d.services import osm_scene_source as osm_scene_source_service

    monkeypatch.setattr(osm_scene_source_service, "fetch_osm_data", lambda *_args, **_kwargs: {"elements": [{"id": 1}]})
    monkeypatch.setattr(osm_scene_source_service, "raw_osm_to_geojson", lambda _raw: {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "osm-road-1",
                "properties": {"tags": {"highway": "residential"}, "highway": "residential"},
                "geometry": {"type": "LineString", "coordinates": [[113.541, 22.791], [113.548, 22.798]]},
            },
            {
                "type": "Feature",
                "id": "osm-building-2",
                "properties": {"tags": {"building": "university"}},
                "geometry": {"type": "Polygon", "coordinates": [[[113.542, 22.792], [113.543, 22.792], [113.543, 22.793], [113.542, 22.792]]]},
            },
            {
                "type": "Feature",
                "id": "osm-tree-3",
                "properties": {"tags": {"natural": "tree"}},
                "geometry": {"type": "Point", "coordinates": [113.544, 22.794]},
            },
        ],
    })
    response = client.post("/api/scene-sources/osm", json={
        "source_id": "guangzhou-shared-osm",
        "aoi_bbox": [113.541, 22.791, 113.548, 22.798],
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"]["producer"] == "osm"
    assert payload["annotation"]["centerlines"][0]["id"] == "osm-road-1"
    assert payload["annotation"]["control_points"][0]["kind"] == "tree_candidate"
    assert payload["aligned_buildings"][0]["editable"] is False
    assert payload["osm"]["attribution"] == "© OpenStreetMap contributors"

    _teacher_token, student_token, course = _bootstrap_course_and_student(client)
    project_response = client.post("/api/v1/projects", headers=_auth(student_token), json={
        "course_id": course["id"],
        "name": "Shared OSM parity",
        "city": "广州",
        "design_goal": "walkable street",
        "aoi_bbox": [113.541, 22.791, 113.548, 22.798],
    })
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    import_response = client.post(
        f"/api/v1/projects/{project['id']}/sources/osm",
        headers=_auth(student_token),
        json={},
    )
    assert import_response.status_code == 202, import_response.text
    job = import_response.json()
    for _ in range(50):
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.02)
        job = client.get(f"/api/v1/jobs/{job['id']}", headers=_auth(student_token)).json()
    assert job["status"] == "succeeded", job
    sources = client.get(f"/api/v1/projects/{project['id']}/sources", headers=_auth(student_token)).json()["items"]
    assert len(sources) == 1
    workflow_source = client.get(
        f"/api/v1/projects/{project['id']}/sources/{sources[0]['id']}/workflow-source",
        headers=_auth(student_token),
    )
    assert workflow_source.status_code == 200, workflow_source.text
    course_payload = workflow_source.json()
    assert [item["id"] for item in course_payload["annotation"]["centerlines"]] == [item["id"] for item in payload["annotation"]["centerlines"]]
    assert [item["id"] for item in course_payload["annotation"]["control_points"]] == [item["id"] for item in payload["annotation"]["control_points"]]
    assert course_payload["source_alignment"] == payload["source_alignment"]


def test_course_osm_preview_selects_road_without_refetch(client: TestClient, monkeypatch):
    from roadgen3d.services.osm_road_study import preview_bundle_from_raw

    raw = {
        "elements": [
            {"type": "node", "id": 1, "lon": 113.5410, "lat": 22.7940},
            {"type": "node", "id": 2, "lon": 113.5440, "lat": 22.7940},
            {"type": "node", "id": 3, "lon": 113.5470, "lat": 22.7940},
            {"type": "node", "id": 10, "lon": 113.5430, "lat": 22.7942},
            {"type": "node", "id": 11, "lon": 113.5435, "lat": 22.7942},
            {"type": "node", "id": 12, "lon": 113.5435, "lat": 22.7947},
            {"type": "node", "id": 13, "lon": 113.5430, "lat": 22.7947},
            {"type": "way", "id": 101, "nodes": [1, 2], "tags": {"highway": "residential", "name": "Course Road"}},
            {"type": "way", "id": 102, "nodes": [2, 3], "tags": {"highway": "residential", "name": "Course Road"}},
            {"type": "way", "id": 201, "nodes": [10, 11, 12, 13, 10], "tags": {"building": "yes"}},
        ]
    }
    calls = 0

    def _preview(**kwargs):
        nonlocal calls
        calls += 1
        callback = kwargs.get("progress_callback")
        if callback:
            callback({"stage": "parse_features", "progress": 64, "message": "fixture parsed"})
            callback({"stage": "prepare_selection", "progress": 94, "message": "fixture ready"})
        return preview_bundle_from_raw(
            raw_osm=raw,
            aoi_bbox=kwargs["aoi_bbox"],
            source_id=kwargs["source_id"],
            preview_id=kwargs["preview_id"],
        )

    monkeypatch.setattr("roadgen3d.teaching.service.build_osm_road_preview", _preview)
    _teacher_token, student_token, course = _bootstrap_course_and_student(client)
    project = client.post("/api/v1/projects", headers=_auth(student_token), json={
        "course_id": course["id"],
        "name": "Road study course flow",
        "city": "广州",
        "aoi_bbox": [113.5400, 22.7920, 113.5480, 22.7980],
    }).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/osm-previews",
        headers=_auth(student_token),
        json={},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    for _ in range(50):
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.02)
        job = client.get(f"/api/v1/jobs/{job['id']}", headers=_auth(student_token)).json()
    assert job["status"] == "succeeded", job
    preview = job["result"]
    seed_id = preview["logical_roads"]["features"][0]["properties"]["logical_road_id"]
    selection = {
        "raw_artifact_id": preview["raw_artifact_id"],
        "preview_id": preview["preview_id"],
        "seed_logical_road_id": seed_id,
        "hop_count": 1,
        "context_buffer_m": 100,
    }
    preview_response = client.post(
        f"/api/v1/projects/{project['id']}/osm-previews/{preview['preview_id']}/selection-preview",
        headers=_auth(student_token),
        json=selection,
    )
    assert preview_response.status_code == 200, preview_response.text
    assert len(preview_response.json()["annotation"]["centerlines"]) == 2
    selected = client.post(
        f"/api/v1/projects/{project['id']}/osm-previews/{preview['preview_id']}/selection",
        headers=_auth(student_token),
        json=selection,
    )
    assert selected.status_code == 201, selected.text
    assert selected.json()["kind"] == "osm_road_study"
    assert selected.json()["osm_study"]["selection"]["context_buffer_m"] == 100
    assert calls == 1


def test_shared_osm_scene_source_validates_bbox_and_forwards_cache_policy(tmp_path: Path, monkeypatch):
    from roadgen3d.services import osm_scene_source as osm_scene_source_service

    calls: list[dict] = []

    def fake_fetch(bbox, cache_dir, *, force_refetch=False):
        calls.append({"bbox": bbox, "cache_dir": Path(cache_dir), "force_refetch": force_refetch})
        return {"elements": []}

    monkeypatch.setattr(osm_scene_source_service, "fetch_osm_data", fake_fetch)
    monkeypatch.setattr(osm_scene_source_service, "raw_osm_to_geojson", lambda _raw: {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "id": "osm-road-force-refresh",
            "properties": {"highway": "residential"},
            "geometry": {"type": "LineString", "coordinates": [[113.542, 22.792], [113.547, 22.797]]},
        }],
    })
    bundle = osm_scene_source_service.fetch_normalized_osm_scene_source(
        aoi_bbox=[113.541, 22.791, 113.548, 22.798],
        source_id="shared-force-refresh",
        cache_dir=tmp_path / "osm-cache",
        force_refetch=True,
    )
    assert calls == [{
        "bbox": (113.541, 22.791, 113.548, 22.798),
        "cache_dir": tmp_path / "osm-cache",
        "force_refetch": True,
    }]
    assert bundle["normalized"]["annotation"]["plan_id"] == "shared-force-refresh"
    with pytest.raises(ValueError, match="reversed"):
        osm_scene_source_service.fetch_normalized_osm_scene_source(
            aoi_bbox=[113.548, 22.798, 113.541, 22.791],
            source_id="invalid-bbox",
            cache_dir=tmp_path / "osm-cache",
        )


def test_geojson_building_footprint_persists_exact_region_and_osm_height():
    from roadgen3d.reference_annotation_scene_bridge import build_reference_annotation_scene_bridge

    normalized = normalize_teaching_geojson({
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "road-1",
                "properties": {"highway": "residential"},
                "geometry": {"type": "LineString", "coordinates": [[113.541, 22.791], [113.548, 22.798]]},
            },
            {
                "type": "Feature",
                "id": "building-42",
                "properties": {"building": "university", "building:levels": "6"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [113.5420, 22.7920],
                        [113.5424, 22.7920],
                        [113.5424, 22.7923],
                        [113.5420, 22.7923],
                        [113.5420, 22.7920],
                    ]],
                },
            },
        ],
    }, source_id="building-persistence")

    building_region = next(
        region for region in normalized["annotation"]["regions"]
        if region["region_role"] == "building_region"
    )
    assert building_region["id"] == "building-42"
    assert len(building_region["points"]) == 4
    assert building_region["material"] == {
        "target_height_m": 18.0,
        "height_source": "osm.building_levels",
        "building_levels": 6.0,
    }
    bridge = build_reference_annotation_scene_bridge(
        normalized["annotation"],
        compose_config={"auto_land_use_mode": "off", "surrounding_building_mode": "footprint_based"},
    )
    assert bridge.placement_context.building_regions[0]["region_id"] == "building-42"
    assert bridge.placement_context.building_regions[0]["target_height_m"] == pytest.approx(18.0)


def test_course_project_geojson_revision_evaluation_compare_and_export(client: TestClient, monkeypatch):
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

    reviewed_geojson = json.loads(json.dumps(normalized))
    reviewed_geojson["features"][0]["properties"].update({
        "annotation_status": "human_modified",
        "annotation_source": "manual.course_review",
        "annotation_confidence": 1.0,
    })
    reviewed_geojson["features"].append({
        "type": "Feature",
        "id": "manual-tree-1",
        "properties": {
            "role": "tree_candidate",
            "annotation_status": "human_added",
            "annotation_source": "manual.course_review",
            "annotation_confidence": 1.0,
        },
        "geometry": {"type": "Point", "coordinates": [113.545, 22.795]},
    })
    reviewed_response = client.post(
        f"/api/v1/projects/{project['id']}/sources/{source['id']}/review",
        headers=_auth(student_token),
        json={"geojson": reviewed_geojson, "actions": [{"op": "add_feature", "feature_id": "manual-tree-1"}], "notes": "Checked against OSM basemap"},
    )
    assert reviewed_response.status_code == 201, reviewed_response.text
    reviewed = reviewed_response.json()
    assert reviewed["kind"] == "reviewed_annotation"
    assert reviewed["provenance"]["parent_source_id"] == source["id"]
    assert reviewed["quality_report"]["review_delta"]["feature_count_after"] == 4
    assert reviewed["quality_report"]["conversion_ok"] is True
    assert reviewed["quality_report"]["review_delta"]["conversion_ok"] is False
    listed_sources = client.get(f"/api/v1/projects/{project['id']}/sources", headers=_auth(student_token)).json()["items"]
    assert listed_sources[0]["role_counts"] == {"centerline": 1, "building_footprint": 1, "tree_candidate": 2}

    baseline_job = client.post(f"/api/v1/projects/{project['id']}/generate", headers=_auth(student_token), json={
        "source_id": reviewed["id"],
        "generation_mode": "baseline",
        "prompt": "walkable campus edge",
    })
    assert baseline_job.status_code == 202, baseline_job.text
    assert baseline_job.json()["status"] == "succeeded"
    assert baseline_job.json()["stage"] == "succeeded"
    assert baseline_job.json()["progress"] == 100
    assert any(item["stage"] == "mesh_generation" for item in baseline_job.json()["operations"])
    baseline = baseline_job.json()["result"]["revision"]
    assert baseline["branch_kind"] == "baseline"
    assert baseline["provenance"]["generation_method"] == "parametric"
    assert baseline["provenance"]["building_representation"] == "transparent_massing"
    assert baseline["provenance"]["massing_material"]["opacity"] == pytest.approx(0.88)
    assert client.app.state.design_service.generation_calls[0]["patch_overrides"] == {
        "building_representation": "transparent_massing",
        "surrounding_building_mode": "footprint_based",
        "auto_land_use_mode": "off",
        "infill_policy": "off",
        "building_height_mode": "class_only",
    }
    assert baseline_job.json()["result"]["evaluation"]["status"] == "succeeded"
    human = client.post(f"/api/v1/projects/{project['id']}/revisions", headers=_auth(student_token), json={
        "source_id": source["id"],
        "parent_id": baseline["id"],
        "branch_kind": "human_edit",
        "label": "Student tree edit",
        "commands": [{"command_id": "add-tree-1", "op": "add_instance", "category": "tree"}],
        "evaluation_weights": {"walkability": 45, "safety": 35, "beauty": 20},
        "layout": {"version": "roadgen3d.scene_layout.v1", "placements": [{"instance_id": "tree-1", "category": "tree"}], "summary": {"walkability": 82}},
    })
    assert human.status_code == 201, human.text
    assert human.json()["auto_evaluation"]["weights"] == {"walkability": 0.45, "safety": 0.35, "beauty": 0.2}

    compared = client.post(f"/api/v1/projects/{project['id']}/comparisons", headers=_auth(student_token), json={"revision_ids": [baseline["id"], human.json()["id"]]})
    assert compared.status_code == 200
    assert compared.json()["items"][1]["score_delta"]["walkability"] == pytest.approx(11.0)
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

    capabilities = client.get("/api/v1/capabilities", headers=_auth(student_token))
    assert capabilities.status_code == 200
    assert capabilities.json()["llm"]["configured"] is False
    assert capabilities.json()["design_generation"]["redesign_default"] == "parametric"

    redesign_job = client.post(f"/api/v1/projects/{project['id']}/generate", headers=_auth(student_token), json={
        "source_id": reviewed["id"],
        "parent_revision_id": baseline["id"],
        "generation_mode": "auto",
        "prompt": "walkable campus edge",
        "goal_weights": {"walkability": 70, "safety": 20, "beauty": 10},
        "candidate_count": 3,
        "minimum_scores": {"walkability": 74},
    })
    assert redesign_job.status_code == 202, redesign_job.text
    redesign = redesign_job.json()["result"]["revision"]
    assert redesign["branch_kind"] == "ai_edit"
    assert redesign["parent_id"] == baseline["id"]
    assert redesign["provenance"]["requested_generation_mode"] == "auto"
    assert redesign["provenance"]["resolved_generation_mode"] == "parametric"
    assert redesign["provenance"]["goal_weights"] == {"walkability": 0.7, "safety": 0.2, "beauty": 0.1}
    redesign_result = redesign_job.json()["result"]
    assert len(redesign_result["candidates"]) == 3
    assert redesign_result["solver_trace"]["candidate_count"] == 3
    assert redesign_result["solver_trace"]["selected_revision_id"] == redesign["id"]
    assert redesign_result["solver_trace"]["selection_status"] == "evaluated_improving_local_best"
    assert redesign_result["solver_trace"]["parallel_limit"] == 2
    assert redesign_result["solver_trace"]["claim_scope"] == "best feasible local candidate that improves the parent under the requested weights; not a global optimum"
    assert [item["feasible"] for item in redesign_result["solver_trace"]["candidates"]] == [False, False, True]
    selected_trace = next(item for item in redesign_result["solver_trace"]["candidates"] if item["selected"])
    assert selected_trace["scores"]["walkability"] == 74.0
    assert selected_trace["improves_parent"] is True
    assert selected_trace["score_improvement"] > 0.0
    assert client.app.state.design_service.max_concurrent_generations == 2
    assert redesign["provenance"]["solver_selected"] is True
    assert redesign["provenance"]["generation_method"] == "parametric_search"
    redesign_revisions = client.get(f"/api/v1/projects/{project['id']}/revisions", headers=_auth(student_token)).json()["items"]
    c_revisions = [item for item in redesign_revisions if item["branch_kind"] == "ai_edit"]
    assert len(c_revisions) == 3
    assert sum(bool(item["provenance"].get("solver_selected")) for item in c_revisions) == 1
    assert {item["provenance"]["search_profile"] for item in c_revisions} == {
        "weighted_anchor", "pedestrian_capacity", "amenity_greening",
    }

    monkeypatch.setenv("ROADGEN_LLM_API_KEY", "fixture-key")
    llm_job = client.post(f"/api/v1/projects/{project['id']}/generate", headers=_auth(student_token), json={
        "source_id": reviewed["id"],
        "parent_revision_id": redesign["id"],
        "generation_mode": "auto",
        "prompt": "walkable campus edge",
        "goal_weights": {"walkability": 20, "safety": 30, "beauty": 50},
    })
    assert llm_job.status_code == 202, llm_job.text
    llm_revision = llm_job.json()["result"]["revision"]
    assert llm_revision["parent_id"] == redesign["id"]
    assert llm_revision["provenance"]["resolved_generation_mode"] == "parametric"
    assert llm_revision["provenance"]["generation_method"] == "parametric"


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


def test_project_asset_palette_is_tenant_scoped_and_fingerprint_pinned(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from roadgen3d.services import asset_manifest_registry as registry

    manifest = tmp_path / "palette.jsonl"
    mesh = tmp_path / "tree.glb"
    mesh.write_bytes(b"glTF fixture")
    manifest.write_text(json.dumps({
        "asset_id": "tree-ready",
        "category": "tree",
        "text_desc": "Teaching tree",
        "mesh_path": mesh.name,
        "latent_path": "tree.pt",
        "scene_eligible": True,
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(registry, "_registered_manifests", lambda: {"palette.jsonl": manifest})
    summary = registry.summarize_manifest("palette.jsonl")

    _teacher_token, student_token, course = _bootstrap_course_and_student(client)
    project = client.post("/api/v1/projects", headers=_auth(student_token), json={
        "course_id": course["id"], "name": "Palette owner",
    }).json()
    palette = {
        "schemaVersion": "roadgen3d.asset-palette.v1",
        "assets": [{
            "manifestName": "palette.jsonl",
            "assetId": "tree-ready",
            "fingerprint": summary["fingerprint"],
            "category": "tree",
            "label": "My tree",
        }],
    }
    saved = client.put(
        f"/api/v1/projects/{project['id']}/asset-palette",
        headers=_auth(student_token),
        json=palette,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["assets"][0]["assetId"] == "tree-ready"
    assert client.get(
        f"/api/v1/projects/{project['id']}/asset-palette",
        headers=_auth(student_token),
    ).json() == saved.json()

    second_register = client.post("/api/v1/auth/register", json={
        "email": "palette2@example.edu",
        "password": "palette2-pass-123",
        "display_name": "Palette Two",
        "course_code": course["code"],
        "invite_code": course["invite_code"],
    })
    assert second_register.status_code == 201
    second_token = client.post("/api/v1/auth/login", json={
        "email": "palette2@example.edu", "password": "palette2-pass-123",
    }).json()["access_token"]
    forbidden = client.get(
        f"/api/v1/projects/{project['id']}/asset-palette",
        headers=_auth(second_token),
    )
    assert forbidden.status_code == 403


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
    for index in range(55):
        service.update_job_progress(running["id"], {
            "stage": "layout_generation",
            "progress": index,
            "message": f"step {index}",
        })
    progressed = service.get_job(actor["id"], running["id"])
    assert progressed["progress"] == 54
    assert len(progressed["operations"]) == 50
    service.update_job_progress(running["id"], {"stage": "context_resolving", "progress": 12, "message": "late lower progress"})
    assert service.get_job(actor["id"], running["id"])["progress"] == 54
    assert running["id"] in service.recover_incomplete_jobs()
    assert service.get_job(actor["id"], running["id"])["status"] == "queued"

    cancelled = service.cancel_job(actor["id"], running["id"])
    assert cancelled["status"] == "cancelled"
    after_late_completion = service.update_job(running["id"], status="succeeded", progress=100, result={"unexpected": True})
    assert after_late_completion["status"] == "cancelled"
    assert after_late_completion["result"] == {}


def test_each_user_can_hold_at_most_five_active_jobs(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROADGEN_MAX_ACTIVE_JOBS_PER_USER", "5")
    response = client.post("/api/v1/auth/bootstrap", json={
        "email": "quota@example.edu",
        "password": "teacher-pass-123",
        "display_name": "Quota Teacher",
    })
    assert response.status_code == 201
    token = client.post(
        "/api/v1/auth/login",
        json={"email": "quota@example.edu", "password": "teacher-pass-123"},
    ).json()["access_token"]
    actor = client.get("/api/v1/me", headers=_auth(token)).json()
    service = client.app.state.teaching_service

    jobs = [
        service.create_job(actor["id"], None, kind="project_export", payload={"index": index})
        for index in range(5)
    ]
    with pytest.raises(Conflict, match=r"Active job quota reached \(5/5\)"):
        service.create_job(actor["id"], None, kind="project_export", payload={"index": 5})

    service.cancel_job(actor["id"], jobs[0]["id"])
    replacement = service.create_job(actor["id"], None, kind="project_export", payload={"index": 5})
    assert replacement["status"] == "queued"


def test_scene_generation_failure_is_public_retryable_once_and_preserves_progress(client: TestClient, monkeypatch):
    _teacher_token, student_token, course = _bootstrap_course_and_student(client)
    project = client.post("/api/v1/projects", headers=_auth(student_token), json={
        "course_id": course["id"],
        "name": "Failure contract studio",
        "aoi_bbox": [113.54, 22.79, 113.55, 22.80],
    }).json()
    imported = client.post(f"/api/v1/projects/{project['id']}/sources/geojson", headers=_auth(student_token), json={
        "geojson": {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"highway": "residential"},
                "geometry": {"type": "LineString", "coordinates": [[113.541, 22.791], [113.548, 22.798]]},
            }],
        },
    }).json()
    normalized = client.get(
        f"/api/v1/artifacts/{imported['normalized_artifact_id']}",
        headers=_auth(student_token),
    ).json()
    reviewed = client.post(
        f"/api/v1/projects/{project['id']}/sources/{imported['id']}/review",
        headers=_auth(student_token),
        json={"geojson": normalized, "actions": [], "notes": "approved"},
    ).json()

    def fail_after_layout(*_args, **kwargs):
        progress_callback = kwargs.get("progress_callback")
        assert progress_callback is not None
        progress_callback({"stage": "layout_generation", "progress": 44, "message": "Layout ready."})
        raise NameError("name '_private_internal_symbol' is not defined")

    monkeypatch.setattr(client.app.state.design_service, "generate_scene", fail_after_layout)
    first = client.post(
        f"/api/v1/projects/{project['id']}/generate",
        headers=_auth(student_token),
        json={"source_id": reviewed["id"], "generation_mode": "baseline"},
    )
    assert first.status_code == 202
    failed = first.json()
    assert failed["status"] == "failed"
    assert failed["stage"] == "failed"
    assert failed["progress"] == 41
    assert failed["message"] == "3D场景生成失败。你的地图和2D标注已经保存，可以重试或返回检查标注。"
    assert failed["error"] == failed["message"]
    assert "_private_internal_symbol" not in json.dumps(failed, ensure_ascii=False)
    assert failed["detail"]["last_successful_stage"] == "layout_generation"
    assert failed["detail"]["failure"]["code"] == "scene_generation_failed"
    assert failed["detail"]["failure"]["retryable"] is True
    assert failed["detail"]["failure"]["debug_reference"].startswith("RG3D-")

    retried = client.post(f"/api/v1/jobs/{failed['id']}/retry", headers=_auth(student_token))
    assert retried.status_code == 202
    retried_failure = retried.json()
    assert retried_failure["status"] == "failed"
    assert retried_failure["progress"] == 41
    assert retried_failure["detail"]["failure"]["retryable"] is False
    blocked = client.post(f"/api/v1/jobs/{retried_failure['id']}/retry", headers=_auth(student_token))
    assert blocked.status_code == 409


def test_local_generation_returns_immediately_and_project_job_list_recovers_status(client: TestClient, monkeypatch):
    monkeypatch.setenv("ROADGEN_JOB_MODE", "local")
    _teacher_token, student_token, course = _bootstrap_course_and_student(client)
    project = client.post("/api/v1/projects", headers=_auth(student_token), json={
        "course_id": course["id"],
        "name": "Local progress studio",
        "aoi_bbox": [113.54, 22.79, 113.55, 22.80],
    }).json()
    imported = client.post(f"/api/v1/projects/{project['id']}/sources/geojson", headers=_auth(student_token), json={
        "geojson": {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"highway": "residential"},
                "geometry": {"type": "LineString", "coordinates": [[113.541, 22.791], [113.548, 22.798]]},
            }],
        },
    }).json()
    normalized = client.get(f"/api/v1/artifacts/{imported['normalized_artifact_id']}", headers=_auth(student_token)).json()
    reviewed = client.post(
        f"/api/v1/projects/{project['id']}/sources/{imported['id']}/review",
        headers=_auth(student_token),
        json={"geojson": normalized, "actions": [], "notes": "approved"},
    ).json()
    response = client.post(f"/api/v1/projects/{project['id']}/generate", headers=_auth(student_token), json={
        "source_id": reviewed["id"],
        "generation_mode": "baseline",
    })
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    job_id = response.json()["id"]
    job = response.json()
    for _ in range(100):
        job = client.get(f"/api/v1/jobs/{job_id}", headers=_auth(student_token)).json()
        if job["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    assert job["status"] == "succeeded", job
    listed = client.get(
        f"/api/v1/projects/{project['id']}/jobs?kind=scene_generate&limit=1",
        headers=_auth(student_token),
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == job_id
    assert listed.json()["items"][0]["operations"]


def test_transparent_massing_material_contract():
    from roadgen3d.street_layout import _placeholder_building_entry

    entry = _placeholder_building_entry(
        asset_id="massing-fixture",
        frontage_width_m=10.0,
        depth_m=8.0,
        height_class="midrise",
        theme_name="context_white_massing",
        target_height_m=15.0,
    )
    material = entry.mesh.visual.material
    assert material.name == "roadgen3d_transparent_massing"
    assert list(material.baseColorFactor) == [244, 247, 248, 224]
    assert material.alphaMode == "BLEND"
    assert material.roughnessFactor == pytest.approx(1.0)
    assert material.metallicFactor == pytest.approx(0.0)

    footprint_entry = _placeholder_building_entry(
        asset_id="massing-footprint-fixture",
        frontage_width_m=20.0,
        depth_m=20.0,
        height_class="midrise",
        theme_name="context_white_massing",
        target_height_m=18.0,
        polygon_xz=((10.0, 20.0), (16.0, 20.0), (15.0, 24.0), (10.0, 20.0)),
        center_xz=(13.0, 22.0),
    )
    assert footprint_entry.native_height_y == pytest.approx(18.0)
    assert footprint_entry.raw_size_m["width_m"] == pytest.approx(6.0)
    assert footprint_entry.raw_size_m["depth_m"] == pytest.approx(4.0)
