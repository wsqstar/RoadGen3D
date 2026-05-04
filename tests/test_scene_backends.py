from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.scene_backends import (  # noqa: E402
    DEFAULT_GROUND_MATERIAL_MANIFEST_PATH,
    ManifestGroundMaterialBackend,
    ManifestObjectAssetBackend,
    ManifestSkyBackend,
)


def test_object_backend_merges_v2_overlay_with_legacy_manifest(tmp_path: Path):
    legacy = tmp_path / "legacy.jsonl"
    legacy.write_text(
        json.dumps(
            {
                "asset_id": "bench_legacy",
                "category": "bench",
                "text_desc": "legacy bench",
                "mesh_path": "bench.glb",
                "latent_path": "bench.pt",
                "license": "cc-by-4.0",
                "source": "legacy",
                "split": "train",
            }
        )
        + "\n"
        + json.dumps(
            {
                "asset_id": "lamp_legacy",
                "category": "lamp",
                "text_desc": "legacy lamp",
                "mesh_path": "lamp.glb",
                "latent_path": "lamp.pt",
                "license": "cc-by-4.0",
                "source": "legacy",
                "split": "train",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    overlay = tmp_path / "overlay.jsonl"
    overlay.write_text(
        json.dumps(
            {
                "asset_id": "bench_legacy",
                "source_dataset": "overlay",
                "category": "bench",
                "text_desc": "overlay bench",
                "mesh_path": "bench.glb",
                "latent_path": "bench.pt",
                "license": "cc-by-4.0",
                "split": "train",
                "affordance_tags": ["sit"],
            }
        ),
        encoding="utf-8",
    )

    backend = ManifestObjectAssetBackend(manifest_path=legacy, manifest_v2_path=overlay)
    backend_name, rows = backend.load_rows()

    assert backend_name == "manifest_v2_overlay"
    assert len(rows) == 2
    bench = next(row for row in rows if row["asset_id"] == "bench_legacy")
    lamp = next(row for row in rows if row["asset_id"] == "lamp_legacy")
    assert bench["source_dataset"] == "overlay"
    assert bench["affordance_tags"] == ["sit"]
    assert lamp["category"] == "lamp"


def test_material_and_sky_backends_select_matching_records(tmp_path: Path):
    materials = tmp_path / "ground_material_manifest.jsonl"
    materials.write_text(
        json.dumps(
            {
                "material_id": "safe_sidewalk",
                "surface_type": "sidewalk",
                "source_dataset": "demo_materials",
                "license": "internal",
                "albedo_path": "sidewalk.png",
                "style_tags": ["walkable", "all_age"],
            }
        )
        + "\n"
        + json.dumps(
            {
                "material_id": "default_road",
                "surface_type": "carriageway",
                "source_dataset": "demo_materials",
                "license": "internal",
                "albedo_path": "road.png",
                "style_tags": ["urban"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sky = tmp_path / "sky_manifest.jsonl"
    sky.write_text(
        json.dumps(
            {
                "sky_id": "warm_evening",
                "source_dataset": "demo_sky",
                "license": "internal",
                "time_of_day": "evening",
                "illumination_tags": ["warm", "golden"],
            }
        )
        + "\n"
        + json.dumps(
            {
                "sky_id": "clear_day",
                "source_dataset": "demo_sky",
                "license": "internal",
                "time_of_day": "day",
                "illumination_tags": ["neutral"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    config = SimpleNamespace(
        query="all age walkable golden complete street",
        objective_profile="balanced",
        design_rule_profile="pedestrian_priority_v1",
        city_context="generic_city",
        style_preset="civic_clean_v1",
    )

    ground_selection = ManifestGroundMaterialBackend(manifest_path=materials).select_for_config(config)
    sky_selection = ManifestSkyBackend(manifest_path=sky).select_for_config(config)

    assert ground_selection.material_ids_by_role["sidewalk"] == "safe_sidewalk"
    assert ground_selection.texture_overrides["sidewalk"].endswith("sidewalk.png")
    assert sky_selection is not None
    assert sky_selection.sky_id == "warm_evening"


def test_default_course_material_manifest_covers_viewer_surface_roles():
    config = SimpleNamespace(
        query="course demo complete street with lanes and sidewalks",
        objective_profile="balanced",
        design_rule_profile="balanced_complete_street_v1",
        city_context="generic_city",
        style_preset="civic_clean_v1",
    )

    selection = ManifestGroundMaterialBackend(
        manifest_path=DEFAULT_GROUND_MATERIAL_MANIFEST_PATH,
    ).select_for_config(config)

    for role in (
        "carriageway",
        "sidewalk",
        "curb",
        "crossing",
        "lane_mark",
        "lane_edge_mark",
        "grass",
        "bus_lane",
        "parking_lane",
        "shared_street_surface",
    ):
        assert selection.material_ids_by_role[role]
        assert selection.texture_overrides[role]
