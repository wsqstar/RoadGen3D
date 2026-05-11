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
from roadgen3d.services import scene_backends as scene_backends_module  # noqa: E402


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

    assert backend_name == "manifest_multi_merged"
    assert len(rows) == 2
    bench = next(row for row in rows if row["asset_id"] == "bench_legacy")
    lamp = next(row for row in rows if row["asset_id"] == "lamp_legacy")
    assert bench["source_dataset"] == "overlay"
    assert bench["affordance_tags"] == ["sit"]
    assert lamp["category"] == "lamp"


def test_object_backend_manifest_disable_is_global_deny_vote(tmp_path: Path):
    disabled_library = tmp_path / "disabled.jsonl"
    disabled_library.write_text(
        json.dumps(
            {
                "asset_id": "lamp_shared",
                "category": "lamp",
                "text_desc": "disabled lamp",
                "mesh_path": "lamp.glb",
                "latent_path": "lamp.pt",
                "scene_eligible": False,
                "scene_exclusion_reason": "bad_scale",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    enabled_library = tmp_path / "enabled.jsonl"
    enabled_library.write_text(
        json.dumps(
            {
                "asset_id": "lamp_shared",
                "category": "lamp",
                "text_desc": "enabled lamp overlay",
                "mesh_path": "lamp.glb",
                "latent_path": "lamp.pt",
                "scene_eligible": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    backend = ManifestObjectAssetBackend(manifest_paths=(disabled_library, enabled_library))
    _, rows = backend.load_rows()

    row = rows[0]
    assert row["asset_id"] == "lamp_shared"
    assert row["text_desc"] == "enabled lamp overlay"
    assert row["scene_eligible"] is False
    assert row["scene_exclusion_reason"] == "disabled_by_manifest_source"
    assert str(disabled_library.resolve()) in row["scene_disabled_manifest_sources"]
    assert row["scene_disabled_manifest_reasons"] == ["bad_scale"]


def test_object_backend_default_disable_index_blocks_enabled_overlay(tmp_path: Path, monkeypatch):
    disabled_library = tmp_path / "default_disabled.jsonl"
    disabled_library.write_text(
        json.dumps(
            {
                "asset_id": "lamp_global_disabled",
                "category": "lamp",
                "text_desc": "globally disabled lamp",
                "mesh_path": "lamp.glb",
                "latent_path": "lamp.pt",
                "scene_eligible": False,
                "scene_exclusion_reason": "manual_review_failed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    enabled_library = tmp_path / "enabled.jsonl"
    enabled_library.write_text(
        json.dumps(
            {
                "asset_id": "lamp_global_disabled",
                "category": "lamp",
                "text_desc": "enabled duplicate lamp",
                "mesh_path": "lamp.glb",
                "latent_path": "lamp.pt",
                "scene_eligible": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(scene_backends_module, "_GLOBAL_DISABLE_MANIFEST_PATHS", (disabled_library.resolve(),))
    scene_backends_module._global_scene_disabled_asset_records.cache_clear()

    try:
        backend = ManifestObjectAssetBackend(manifest_path=enabled_library)
        _, rows = backend.load_rows()
    finally:
        scene_backends_module._global_scene_disabled_asset_records.cache_clear()

    row = rows[0]
    assert row["text_desc"] == "enabled duplicate lamp"
    assert row["scene_eligible"] is False
    assert row["scene_disabled_manifest_reasons"] == ["manual_review_failed"]


def test_object_backend_normalizes_traffic_sign_orientation_metadata(tmp_path: Path):
    legacy = tmp_path / "legacy_signs.jsonl"
    legacy.write_text(
        json.dumps(
            {
                "asset_id": "sign_split",
                "category": "traffic_sign",
                "text_desc": "split traffic sign",
                "mesh_path": "sign.glb",
                "latent_path": "sign.pt",
                "canonical_front": "negative_z",
                "yaw_deg": 15,
            }
        )
        + "\n"
        + json.dumps(
            {
                "asset_id": "sign_without_front",
                "category": "traffic_sign",
                "text_desc": "traffic sign default front",
                "mesh_path": "sign_default.glb",
                "latent_path": "sign_default.pt",
                "yaw_deg": "bad-yaw",
            }
        )
        + "\n"
        + json.dumps(
            {
                "asset_id": "lamp_plain",
                "category": "lamp",
                "text_desc": "lamp for baseline",
                "mesh_path": "lamp.glb",
                "latent_path": "lamp.pt",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    backend = ManifestObjectAssetBackend(manifest_path=legacy)
    _, rows = backend.load_rows()

    explicit = next(row for row in rows if row["asset_id"] == "sign_split")
    defaulted = next(row for row in rows if row["asset_id"] == "sign_without_front")
    lamp = next(row for row in rows if row["asset_id"] == "lamp_plain")

    assert explicit["canonical_front"] == "-Z"
    assert explicit["yaw_deg"] == 15.0
    assert defaulted["canonical_front"] == "-Z"
    assert defaulted["yaw_deg"] == 0.0
    assert lamp["canonical_front"] == "+Z"



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
                "weather_tags": ["clear", "warm"],
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
    assert sky_selection.weather_tags == ("clear", "warm")
    assert sky_selection.illumination_tags == ("warm", "golden")
    assert sky_selection.to_dict()["weather_tags"] == ["clear", "warm"]


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
        "planting_soil",
        "bus_lane",
        "parking_lane",
        "safety_island",
        "shared_street_surface",
        "garden",
        "parking",
        "plaza",
    ):
        assert selection.material_ids_by_role[role]
        assert selection.texture_overrides[role]
