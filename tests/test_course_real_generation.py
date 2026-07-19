from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.design_types import DesignDraft, SceneContext
from roadgen3d.services.scene_jobs import SceneJobService


COURSE_BASELINE_PATCH = {
    "building_representation": "transparent_massing",
    "surrounding_building_mode": "footprint_based",
    "auto_land_use_mode": "off",
    "infill_policy": "off",
    "building_height_mode": "class_only",
    "street_furniture_profile": "none",
    "amenity_coverage_mode": "off",
    "curated_street_assets_profile": "disabled",
    "seed": 42,
}


def _minimal_heightless_annotation() -> dict[str, object]:
    return {
        "version": "roadgen3d_reference_annotation_v2",
        "plan_id": "course_minimal_heightless",
        "image_path": "",
        "image_width_px": 800,
        "image_height_px": 600,
        "pixels_per_meter": 10.0,
        "centerlines": [
            {
                "id": "road-1",
                "label": "Road 1",
                "road_width_m": 8.0,
                "carriageway_width_m": 8.0,
                "forward_drive_lane_count": 1,
                "reverse_drive_lane_count": 1,
                "bike_lane_count": 0,
                "bus_lane_count": 0,
                "parking_lane_count": 0,
                "cross_section_mode": "coarse",
                "cross_section_strips": [],
                "points": [{"x": 100, "y": 300}, {"x": 700, "y": 300}],
            }
        ],
        "junctions": [],
        "roundabouts": [],
        "control_points": [],
        "regions": [
            {
                "id": "building-no-height",
                "label": "Building without OSM height",
                "region_role": "building_region",
                "points": [
                    {"x": 200, "y": 140},
                    {"x": 340, "y": 140},
                    {"x": 340, "y": 240},
                    {"x": 200, "y": 240},
                ],
                "derived": False,
                "kind": "residential",
                "source_region_id": "building-no-height",
                "material": {},
            }
        ],
        "building_regions": [],
        "functional_zones": [],
        "surface_annotations": [],
        "station_strip_patches": [],
        "junction_compositions": [],
    }


def _run_course_baseline(service: SceneJobService, out_dir: Path):
    created = service.submit_job(
        draft=DesignDraft(
            normalized_scene_query="course baseline",
            compose_config_patch=COURSE_BASELINE_PATCH,
            citations_by_field={},
            design_summary="",
        ),
        generation_options={
            "out_dir": str(out_dir),
            "artifacts_dir": str(out_dir),
            "skip_llm": True,
            "preset_id": "skip_llm",
            "random_seed": 42,
            "build_production_artifacts": False,
            "render_presentation_artifacts": False,
            "capture_3d_views": False,
        },
        scene_context=SceneContext(
            layout_mode="reference_annotation",
            reference_annotation=_minimal_heightless_annotation(),
        ),
    )
    status = service.wait_for_job(created.job_id, timeout_s=30.0)
    assert status is not None
    assert status.status == "succeeded", status.error
    assert status.result is not None
    return status.result


def test_real_course_baseline_generates_road_and_transparent_massing_reproducibly(tmp_path: Path):
    service = SceneJobService()

    first = _run_course_baseline(service, tmp_path / "run-1")
    second = _run_course_baseline(service, tmp_path / "run-2")

    fingerprints: list[str] = []
    for result in (first, second):
        layout_path = Path(result.scene_layout_path)
        glb_path = Path(result.scene_glb_path)
        assert layout_path.is_file()
        assert glb_path.is_file()
        assert glb_path.stat().st_size > 0

        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        assert layout["summary"]["visual_surface_role_count"]["carriageway"] > 0
        assert layout["building_placements"]
        assert all(item["category"] == "building" for item in layout["placements"])
        assert all(
            item["selection_source"] == "course_transparent_massing"
            for item in layout["building_placements"]
        )
        fingerprints.append(hashlib.sha256(glb_path.read_bytes()).hexdigest())

    assert fingerprints[0] == fingerprints[1]
