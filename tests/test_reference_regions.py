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

from roadgen3d.reference_annotation import parse_reference_annotation  # noqa: E402
from roadgen3d.reference_annotation_scene_bridge import build_reference_annotation_scene_bridge  # noqa: E402
from roadgen3d.reference_regions import derive_regions_from_annotation  # noqa: E402
from web.api.main import create_app  # noqa: E402


class _NoopDesignService:
    default_pdf_path = Path("/tmp/guide.pdf")
    default_artifact_dir = Path("/tmp/knowledge")


def _region_first_payload() -> dict:
    return {
        "version": "roadgen3d_reference_annotation_v2",
        "plan_id": "region_first_test",
        "image_path": "test.png",
        "image_width_px": 300,
        "image_height_px": 200,
        "pixels_per_meter": 5.0,
        "centerlines": [
            {
                "id": "main_axis",
                "label": "Main Axis",
                "points": [
                    {"x": 20.0, "y": 100.0},
                    {"x": 280.0, "y": 100.0},
                ],
                "road_width_m": 10.0,
                "forward_drive_lane_count": 1,
                "reverse_drive_lane_count": 1,
            }
        ],
        "junctions": [],
        "roundabouts": [],
        "control_points": [],
        "regions": [
            {
                "id": "scene_region_main",
                "label": "Scene Region",
                "region_role": "scene_region",
                "points": [
                    {"x": 20.0, "y": 40.0},
                    {"x": 280.0, "y": 40.0},
                    {"x": 280.0, "y": 160.0},
                    {"x": 20.0, "y": 160.0},
                ],
            }
        ],
        "building_regions": [],
        "functional_zones": [],
        "surface_annotations": [],
    }


def test_reference_annotation_parses_regions() -> None:
    annotation = parse_reference_annotation(_region_first_payload())

    assert len(annotation.regions) == 1
    assert annotation.regions[0].feature_id == "scene_region_main"
    assert annotation.regions[0].region_role == "scene_region"
    assert annotation.to_dict()["regions"][0]["points"][0] == {"x": 20.0, "y": 40.0}


def test_derive_regions_splits_scene_region_by_road() -> None:
    pytest.importorskip("shapely")

    result = derive_regions_from_annotation(_region_first_payload())

    assert result["summary"]["scene_region_count"] == 1
    assert result["summary"]["derived_region_count"] == 2
    assert len(result["derived_regions"]) == 2
    assert len(result["building_regions"]) == 2
    assert {item["side"] for item in result["derived_regions"]} == {"left", "right"}
    assert all(item["area_m2"] > 100.0 for item in result["derived_regions"])


def test_scene_bridge_uses_region_aoi_and_derived_building_regions() -> None:
    pytest.importorskip("shapely")

    bridge = build_reference_annotation_scene_bridge(_region_first_payload())

    assert bridge.projected_features.bbox_m == pytest.approx((-26.0, -12.0, 26.0, 12.0))
    assert len(bridge.placement_context.regions) == 1
    assert len(bridge.placement_context.derived_regions) == 2
    assert len(bridge.placement_context.building_regions) == 2
    assert bridge.summary_metadata["derived_building_region_count"] == 2


def test_derive_regions_api_returns_derived_regions() -> None:
    pytest.importorskip("shapely")

    client = TestClient(create_app(design_service=_NoopDesignService()))
    response = client.post(
        "/api/reference-annotations/derive-regions",
        json={"annotation": _region_first_payload()},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["derived_region_count"] == 2
    assert len(payload["building_regions"]) == 2
