from __future__ import annotations

import io
import json
import sys
import time
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
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.generation_calls: list[dict] = []

    def generate_scene(self, draft, *, scene_context: dict, generation_options: dict, **kwargs):
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback({"stage": "context_resolving", "progress": 15, "message": "Fixture annotation resolved."})
            progress_callback({"stage": "layout_generation", "progress": 44, "message": "Fixture layout generated."})
            progress_callback({"stage": "mesh_generation", "progress": 76, "message": "Fixture massing generated.", "detail": {"building_count": 1}})
            progress_callback({"stage": "glb_export", "progress": 88, "message": "Fixture GLB exported."})
        call_index = len(self.generation_calls) + 1
        call_dir = self.output_dir / f"generated-{call_index}"
        call_dir.mkdir(parents=True, exist_ok=True)
        layout_path = call_dir / "scene_layout.json"
        glb_path = call_dir / "scene.glb"
        layout_path.write_text(json.dumps({
            "version": "roadgen3d.scene_layout.v1",
            "placements": [{"instance_id": f"tree-{call_index}", "category": "tree"}],
            "summary": {"walkability": 70 + call_index},
            "compose_config": dict(draft.compose_config_patch),
        }), encoding="utf-8")
        glb_path.write_bytes(b"fixture-glb")
        if generation_options.get("capture_3d_views", True) and generation_options.get("retain_glb_policy") != "always":
            glb_path.unlink()
        self.generation_calls.append({
            "draft": draft.to_dict(),
            "scene_context": scene_context,
            "generation_options": generation_options,
            "patch_overrides": dict(kwargs.get("patch_overrides") or {}),
        })
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
    assert first["role_counts"] == {"centerline": 2, "road_intersection": 1}
    assert geojson["features"][2]["geometry"]["coordinates"] == pytest.approx([0.0, 0.0])
    assert [item["id"] for item in geojson["features"]] == [item["id"] for item in second["geojson"]["features"]]
    assert reviewed["role_counts"] == {"centerline": 2, "road_intersection": 1}
    assert first["quality_report"]["conversion_ok"] is True


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
    assert baseline["provenance"]["massing_material"]["opacity"] == pytest.approx(0.42)
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
    })
    assert redesign_job.status_code == 202, redesign_job.text
    redesign = redesign_job.json()["result"]["revision"]
    assert redesign["branch_kind"] == "ai_edit"
    assert redesign["parent_id"] == baseline["id"]
    assert redesign["provenance"]["requested_generation_mode"] == "auto"
    assert redesign["provenance"]["resolved_generation_mode"] == "parametric"
    assert redesign["provenance"]["goal_weights"] == {"walkability": 0.7, "safety": 0.2, "beauty": 0.1}

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
    assert llm_revision["provenance"]["resolved_generation_mode"] == "llm"
    assert llm_revision["provenance"]["generation_method"] == "llm_assisted"


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
    assert list(material.baseColorFactor) == [244, 247, 248, 107]
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
