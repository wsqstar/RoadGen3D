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

from roadgen3d.graph_templates import load_graph_template_annotation_payload  # noqa: E402
from roadgen3d.reference_annotation import (  # noqa: E402
    build_reference_annotation_compose_config,
    build_segment_graph_from_annotation,
    parse_reference_annotation,
)
from roadgen3d.reference_annotation_scene_bridge import build_reference_annotation_scene_bridge  # noqa: E402
from roadgen3d.services.design_types import (  # noqa: E402
    SceneGenerationResult,
    SceneJobCreateResponse,
    SceneJobStatusResponse,
)
from roadgen3d.services.scenario_designs import ScenarioDesignService  # noqa: E402
from roadgen3d.template_patch import apply_template_patch  # noqa: E402
from web.api.main import create_app  # noqa: E402


class _FakeScenarioJobService:
    default_pdf_path = Path("/tmp/guide.pdf")
    default_artifact_dir = Path("/tmp/knowledge")

    def __init__(self) -> None:
        self.created: list[dict] = []

    def create_scene_job(self, draft, **kwargs):
        job_id = f"scenario-job-{len(self.created) + 1:02d}"
        self.created.append({"job_id": job_id, "draft": draft, **kwargs})
        return SceneJobCreateResponse(
            job_id=job_id,
            status="queued",
            created_at="2026-05-06T00:00:00+00:00",
        )

    def get_scene_job(self, job_id: str):
        match = next((item for item in self.created if item["job_id"] == job_id), None)
        if match is None:
            return None
        out_dir = Path(str(match["generation_options"]["out_dir"]))
        scenario_id = out_dir.parent.name
        sample_id = out_dir.name
        layout_path = out_dir / "graph_template" / "hkust_gz_gate" / "20260506T000000Z" / "scene_layout.json"
        glb_path = layout_path.parent / "scene.glb"
        if not layout_path.exists():
            layout_path.parent.mkdir(parents=True, exist_ok=True)
            layout_path.write_text(
                json.dumps(_fake_layout_payload(match["draft"], scenario_id), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return SceneJobStatusResponse(
            job_id=job_id,
            status="succeeded",
            created_at="2026-05-06T00:00:00+00:00",
            started_at="2026-05-06T00:00:01+00:00",
            finished_at="2026-05-06T00:00:02+00:00",
            stage="succeeded",
            progress=100,
            result=SceneGenerationResult(
                compose_config={"seed": match["generation_options"]["random_seed"]},
                summary={"scenario_id": scenario_id, "sample_id": sample_id},
                scene_layout_path=str(layout_path),
                scene_glb_path=str(glb_path),
                viewer_url=f"http://127.0.0.1:4188/?layout={layout_path}",
            ),
        )

    def list_scene_jobs(self, *, limit=20):
        return [self.get_scene_job(item["job_id"]) for item in self.created[:limit]]

    def list_recent_scenes(self, *, limit=20):
        return []


def _fake_layout_payload(draft: DesignDraft, scenario_id: str) -> dict:
    config = dict(draft.compose_config_patch or {})
    config.setdefault("length_m", 80.0)
    config.setdefault("road_width_m", 12.0)
    config.setdefault("sidewalk_width_m", 3.2)
    config.setdefault("lane_count", 4)
    config.setdefault("density", 0.9)
    surfaces = []
    zones = []
    visual_roles = {
        "sidewalk": 8,
        "crossing": 4,
        "tree_pit": 8,
    }
    template_patch = dict(draft.template_patch or {})
    for operation in template_patch.get("operations", []):
        if operation.get("op") == "upsert_surface_annotation" and isinstance(operation.get("surface"), dict):
            surface = dict(operation["surface"])
            surfaces.append(surface)
            role = str(surface.get("surface_role") or surface.get("kind") or "surface")
            visual_roles[role] = visual_roles.get(role, 0) + 1
        if operation.get("op") == "upsert_functional_zone" and isinstance(operation.get("zone"), dict):
            zones.append(dict(operation["zone"]))
        updates = operation.get("updates") if isinstance(operation.get("updates"), dict) else {}
        if str(updates.get("kind") or "").strip() == "grass_belt":
            visual_roles["median_green"] = visual_roles.get("median_green", 0) + 1
            visual_roles["planting_soil"] = visual_roles.get("planting_soil", 0) + 1
    return {
        "config": config,
        "summary": {
            "length_m": config["length_m"],
            "road_width_m": config["road_width_m"],
            "sidewalk_width_m": config["sidewalk_width_m"],
            "left_clear_path_width_m": max(float(config["sidewalk_width_m"]) - 0.4, 1.2),
            "right_clear_path_width_m": max(float(config["sidewalk_width_m"]) - 0.4, 1.2),
            "left_furnishing_width_m": 0.8,
            "right_furnishing_width_m": 0.8,
            "entrance_count": 6,
            "mean_entrance_openness": 1.0,
            "mean_noise_shielding": 0.2,
            "dropped_slot_rate": 0.0,
            "visual_surface_role_count": visual_roles,
            "composition_report": {
                "presentation_score": 0.78,
                "style_coherence": 0.86,
                "visual_clutter": 0.12,
                "spacing_rhythm": 0.72,
                "focal_readability": 0.82,
            },
            "spatial_context": {
                "bus_stop_points_xz": [[0, 4]],
                "poi_points_by_type_xz": {
                    "school": [[-10, 8]],
                    "retail": [[12, -8]],
                    "park": [[20, 10]],
                },
            },
            "land_use_summary": {
                "school": 1,
                "commercial": 1,
                "park": 1,
            },
        },
        "placements": [
            {"category": "tree", "x": -20, "z": 4},
            {"category": "tree", "x": 0, "z": -4},
            {"category": "lamp", "x": 10, "z": 4},
            {"category": "lamp", "x": 30, "z": -4},
            {"category": "bench", "x": -30, "z": 5},
            {"category": "bollard", "x": 20, "z": 2},
        ],
        "surface_annotations": surfaces,
        "functional_zones": zones,
        "scenario_design": {
            "scenario_id": scenario_id,
            "design_surface_ids": [surface.get("id", "") for surface in surfaces],
            "functional_zone_ids": [zone.get("id", "") for zone in zones],
        },
    }


def test_scenario_design_service_loads_catalog_and_builds_valid_template_patch(tmp_path: Path):
    service = ScenarioDesignService(
        design_service=_FakeScenarioJobService(),
        run_root=tmp_path / "runs",
    )

    payload = service.list_scenarios()
    assert len(payload["items"]) == 7
    assert sum(1 for item in payload["items"] if item["enabled"]) == 7
    disabled_ids = {item["scenario_id"] for item in payload["items"] if not item["enabled"]}
    assert disabled_ids == set()
    assert payload["items"][0]["preview_layout_exists"] is True
    scenario_05_summary = next(
        item for item in payload["items"] if item["scenario_id"] == "scenario_05_furniture_enriched_activity_street"
    )
    scenario_07_summary = next(
        item for item in payload["items"] if item["scenario_id"] == "scenario_07_asymmetric_shared_street_pocket_park"
    )
    assert scenario_05_summary["reference_annotation_exists"] is True
    assert scenario_05_summary["reference_centerline_count"] == 10
    assert scenario_05_summary["reference_functional_zone_count"] == 6
    assert scenario_05_summary["reference_building_region_count"] == 5
    assert scenario_07_summary["reference_annotation_exists"] is True
    assert scenario_07_summary["reference_centerline_count"] == 12
    assert scenario_07_summary["reference_functional_zone_count"] == 3
    assert scenario_07_summary["reference_building_region_count"] == 6

    scenario = service._load_catalog()["scenarios"][1]
    patch = service.scenario_to_template_patch(scenario, validate=True)
    operation_kinds = [item["op"] for item in patch["operations"]]

    assert "resize_strip" in operation_kinds
    assert "upsert_region" in operation_kinds
    assert "upsert_functional_zone" not in operation_kinds
    assert "upsert_surface_annotation" not in operation_kinds
    application = apply_template_patch(load_graph_template_annotation_payload("hkust_gz_gate"), patch)
    assert application.summary["region_count"] == 3
    assert application.summary["functional_zone_count"] == 0
    assert application.summary["surface_annotation_count"] == 0
    assert application.summary["station_strip_patch_count"] == 0


def test_imported_scenario_reference_annotations_parse_and_build_graph(tmp_path: Path):
    service = ScenarioDesignService(
        design_service=_FakeScenarioJobService(),
        run_root=tmp_path / "runs",
    )

    for scenario_id, expected_centerlines, expected_zones, expected_building_regions in (
        ("scenario_05_furniture_enriched_activity_street", 10, 6, 5),
        ("scenario_07_asymmetric_shared_street_pocket_park", 12, 3, 6),
    ):
        inputs = service.generation_inputs_for_scenario(scenario_id)
        assert inputs["template_patch"] is None
        annotation_path = Path(inputs["reference_annotation_path"])
        annotation = parse_reference_annotation(json.loads(annotation_path.read_text(encoding="utf-8")))
        graph = build_segment_graph_from_annotation(
            annotation,
            config=build_reference_annotation_compose_config({"segment_length_m": 12.0}),
        )
        assert annotation.plan_id == scenario_id
        assert annotation.image_path == "/api/graph-templates/hkust_gz_gate/image"
        assert len(annotation.centerlines) == expected_centerlines
        assert len(annotation.functional_zones) == expected_zones
        assert len(annotation.building_regions) == expected_building_regions
        assert graph.nodes
        assert graph.edges


def test_scenario_design_service_builds_reference_annotation_for_each_enabled_scenario(tmp_path: Path):
    service = ScenarioDesignService(
        design_service=_FakeScenarioJobService(),
        run_root=tmp_path / "runs",
    )

    catalog = service.list_scenarios()
    for item in catalog["items"]:
        payload = service.reference_annotation_for_scenario(item["scenario_id"])
        annotation = payload["annotation"]

        assert payload["graph_template_id"] == "hkust_gz_gate"
        assert payload["preview_layout_path"].endswith("scene_layout.json")
        if item["scenario_id"] in {
            "scenario_05_furniture_enriched_activity_street",
            "scenario_07_asymmetric_shared_street_pocket_park",
        }:
            assert annotation["plan_id"] == item["scenario_id"]
        else:
            assert annotation["plan_id"] == "hkust_gz_gate"
        assert annotation["centerlines"]
        assert len(annotation["functional_zones"]) == item["functional_zone_count"]
        assert len(annotation["surface_annotations"]) == item["surface_annotation_count"]
        assert len(annotation.get("station_strip_patches", [])) == item.get("station_strip_patch_count", 0)
        assert all(
            region["source_region_id"].startswith("derived_building_region_")
            for region in annotation["regions"]
            if region.get("region_role") == "building_region"
        )


def test_scenario_design_surface_and_strip_semantics_reach_scene_bridge(tmp_path: Path):
    service = ScenarioDesignService(
        design_service=_FakeScenarioJobService(),
        run_root=tmp_path / "runs",
    )
    catalog = service._load_catalog()["scenarios"]
    scenario_02 = next(item for item in catalog if item["scenario_id"] == "scenario_02_four_lane_multimodal_safety_island")
    scenario_06 = next(item for item in catalog if item["scenario_id"] == "scenario_06_green_median_complete_street")

    application_02 = apply_template_patch(
        load_graph_template_annotation_payload("hkust_gz_gate"),
        service.scenario_to_template_patch(scenario_02, validate=True),
    )
    bridge = build_reference_annotation_scene_bridge(
        application_02.annotation,
        compose_config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
    )
    assert bridge.placement_context.surface_annotations == []
    assert application_02.annotation.get("station_strip_patches", []) == []
    for patched_centerline in application_02.annotation["centerlines"]:
        patched_center_03 = next(strip for strip in patched_centerline["cross_section_strips"] if strip["strip_id"] == "center_03")
        assert patched_center_03["kind"] == "grass_belt"
        assert patched_center_03["width_m"] == 1.24
    graph = build_segment_graph_from_annotation(
        application_02.annotation,
        config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
    )
    assert graph.nodes
    assert all(
        any(
            strip.strip_id == "center_03" and strip.kind == "grass_belt" and strip.width_m == pytest.approx(1.24)
            for strip in node.cross_section_strips
        )
        for node in graph.nodes
        if node.highway_type == "annotated_centerline"
    )

    application_06 = apply_template_patch(
        load_graph_template_annotation_payload("hkust_gz_gate"),
        service.scenario_to_template_patch(scenario_06, validate=True),
    )
    centerline_04 = next(item for item in application_06.annotation["centerlines"] if item["id"] == "centerline_04")
    center_03 = next(strip for strip in centerline_04["cross_section_strips"] if strip["strip_id"] == "center_03")
    assert center_03["kind"] == "grass_belt"
    assert center_03["width_m"] == 3.2


def test_scenario_design_run_creates_scene_jobs_and_report(tmp_path: Path):
    fake = _FakeScenarioJobService()
    service = ScenarioDesignService(design_service=fake, run_root=tmp_path / "runs")

    run = service.submit_run(
        scenario_ids=["scenario_01_basic_complete_street"],
        samples_per_scenario=3,
        base_seed=20260506,
    )
    refreshed = service.get_run(run["run_id"])
    report = service.get_report(run["run_id"])

    assert len(fake.created) == 3
    assert refreshed is not None
    assert refreshed["status"] == "succeeded"
    assert refreshed["completed_jobs"] == 3
    assert refreshed["items"][0]["scene_layout_path"].endswith("scene_layout.json")
    assert "scenario_evaluation" in refreshed["items"][0]
    assert refreshed["evaluation_summary"]["evaluated_items"] == 3
    assert Path(refreshed["manifest_path"]).exists()
    assert report is not None
    assert "Scenario Generation Report" in report["content"]
    assert "Scenario Evaluation Summary" in report["content"]


def test_scenario_design_api_creates_default_enabled_job_run(tmp_path: Path):
    fake = _FakeScenarioJobService()
    app = create_app(design_service=fake)
    app.state.scenario_design_service = ScenarioDesignService(
        design_service=fake,
        run_root=tmp_path / "runs",
    )
    client = TestClient(app)

    list_response = client.get("/api/scenario-designs")
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert len(list_payload["items"]) == 7
    assert sum(1 for item in list_payload["items"] if item["enabled"]) == 7

    annotation_response = client.get(
        "/api/scenario-designs/scenario_02_four_lane_multimodal_safety_island/reference-annotation"
    )
    assert annotation_response.status_code == 200
    annotation_payload = annotation_response.json()
    assert annotation_payload["scenario_id"] == "scenario_02_four_lane_multimodal_safety_island"
    assert annotation_payload["annotation"]["plan_id"] == "hkust_gz_gate"
    assert len(annotation_payload["annotation"]["functional_zones"]) == 0
    assert len(annotation_payload["annotation"]["surface_annotations"]) == 0
    assert len(annotation_payload["annotation"].get("station_strip_patches", [])) == 0

    scenario_05_annotation_response = client.get(
        "/api/scenario-designs/scenario_05_furniture_enriched_activity_street/reference-annotation"
    )
    assert scenario_05_annotation_response.status_code == 200
    scenario_05_payload = scenario_05_annotation_response.json()
    assert scenario_05_payload["annotation"]["plan_id"] == "scenario_05_furniture_enriched_activity_street"
    assert len(scenario_05_payload["annotation"]["functional_zones"]) == 6
    assert len(scenario_05_payload["annotation"]["building_regions"]) == 5

    scenario_07_annotation_response = client.get(
        "/api/scenario-designs/scenario_07_asymmetric_shared_street_pocket_park/reference-annotation"
    )
    assert scenario_07_annotation_response.status_code == 200
    scenario_07_payload = scenario_07_annotation_response.json()
    assert scenario_07_payload["annotation"]["plan_id"] == "scenario_07_asymmetric_shared_street_pocket_park"
    assert len(scenario_07_payload["annotation"]["functional_zones"]) == 3
    assert len(scenario_07_payload["annotation"]["building_regions"]) == 6

    missing_response = client.get("/api/scenario-designs/not-a-real-scenario/reference-annotation")
    assert missing_response.status_code == 404

    reference_run_response = client.post(
        "/api/scenario-designs/runs",
        json={"scenario_ids": ["scenario_07_asymmetric_shared_street_pocket_park"]},
    )
    assert reference_run_response.status_code == 200
    assert fake.created[-1]["scene_context"]["layout_mode"] == "reference_annotation"
    assert fake.created[-1]["scene_context"]["reference_annotation_path"].endswith(
        "scenario_07_asymmetric_shared_street_pocket_park.json"
    )
    assert fake.created[-1]["draft"].template_patch is None
    created_before_default_run = len(fake.created)

    create_response = client.post("/api/scenario-designs/runs", json={})
    assert create_response.status_code == 200
    run_payload = create_response.json()
    assert run_payload["total_jobs"] == 21
    assert len(fake.created) == created_before_default_run + 21
    first_default_job = fake.created[created_before_default_run]
    assert first_default_job["scene_context"]["graph_template_id"] == "hkust_gz_gate"
    assert first_default_job["draft"].template_patch["operations"]
    assert any(
        item["scene_context"]["layout_mode"] == "reference_annotation"
        for item in fake.created[created_before_default_run:]
    )

    status_response = client.get(f"/api/scenario-designs/runs/{run_payload['run_id']}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "succeeded"
    assert status_response.json()["completed_jobs"] == 21
    assert status_response.json()["evaluation_summary"]["evaluated_items"] == 21

    report_response = client.get(f"/api/scenario-designs/runs/{run_payload['run_id']}/report")
    assert report_response.status_code == 200
    assert report_response.json()["report_path"].endswith("SCENARIO_GENERATION_REPORT.md")
    assert "Scenario Evaluation Summary" in report_response.json()["content"]
    assert "方案 6" in report_response.json()["content"]
    assert "方案 7" in report_response.json()["content"]
