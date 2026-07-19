from __future__ import annotations

import inspect
import importlib.util
import json
import random
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Sequence

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.types import (
    BuildingPlacementPlan,
    DEFAULT_BUILDING_FRONT_SETBACK_MAX_M,
    DEFAULT_BUILDING_FRONT_SETBACK_MIN_M,
    GeneratedLot,
    LayoutSlotPlan,
    RetrievalHit,
    StreetComposeConfig,
    StreetComposeResult,
    StreetPlacement,
)
from roadgen3d.asset_scale import compute_asset_scale
from roadgen3d.reference_annotation import ANNOTATION_SCHEMA_VERSION, build_reference_annotation_compose_config
from roadgen3d.reference_annotation_scene_bridge import build_reference_annotation_scene_bridge
from roadgen3d.building_placement import (
    building_forbidden_geometry,
    building_footprint_points,
    resolve_building_pose,
)
from roadgen3d.street_layout import compose_street_scene
import roadgen3d.street_layout as street_layout
from roadgen3d.poi_rules import PoiContext
from roadgen3d.scene_textures import apply_default_scene_texture, create_scene_texture_tracker
from roadgen3d.scene_layout_payload import SCENE_LAYOUT_SCHEMA_VERSION


def _load_legacy_gradio_app():
    spec = importlib.util.find_spec("app")
    if spec is None or not spec.origin:
        return None
    origin = Path(spec.origin).resolve()
    try:
        origin.relative_to(ROOT)
    except ValueError:
        return None
    import app as legacy_app  # type: ignore[import-not-found]

    return legacy_app


class _MissingLegacyGradioApp:
    __test__ = False

    def __getattr__(self, name: str):
        if name == "__test__" or name.startswith("__") or name.startswith("_pytest"):
            raise AttributeError(name)
        pytest.skip("Legacy Gradio app module is not present; active UI uses web/api and web/viewer.")


app = _load_legacy_gradio_app() or _MissingLegacyGradioApp()


def test_load_legacy_gradio_app_ignores_missing_spec(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert _load_legacy_gradio_app() is None


def test_load_legacy_gradio_app_ignores_out_of_repo_spec(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(origin="/tmp/out-of-repo/app.py"),
    )
    assert _load_legacy_gradio_app() is None


def _has_embedded_texture(scene_or_mesh) -> bool:
    geometry = getattr(scene_or_mesh, "geometry", None)
    if isinstance(geometry, dict):
        meshes = geometry.values()
    else:
        meshes = [scene_or_mesh]
    for mesh in meshes:
        visual = getattr(mesh, "visual", None)
        material = getattr(visual, "material", None)
        if getattr(visual, "uv", None) is None:
            continue
        if getattr(material, "baseColorTexture", None) is not None:
            return True
    return False


def _make_mesh(path: Path, kind: str = "box") -> None:
    trimesh = pytest.importorskip("trimesh")
    if kind == "cylinder":
        mesh = trimesh.creation.cylinder(radius=0.1, height=1.5, sections=16)
    else:
        mesh = trimesh.creation.box(extents=(0.8, 0.5, 0.5))
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)


def _write_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _build_real_rows(base_dir: Path, *, include_buildings: bool = False) -> list[dict[str, object]]:
    categories = [
        ("bench_01", "bench"),
        ("lamp_modern_production", "lamp"),
        ("objaverse_trash_f16b7d84113d4cba869412ee95769910", "trash"),
        ("tree_01", "tree"),
        ("bus_stop_01", "bus_stop"),
        ("mailbox_01", "mailbox"),
        ("hydrant_01", "hydrant"),
        ("bollard_01", "bollard"),
    ]
    rows: list[dict[str, object]] = []
    for idx, (asset_id, category) in enumerate(categories):
        mesh_path = base_dir / "meshes" / f"{asset_id}.glb"
        _make_mesh(mesh_path, kind="cylinder" if category in {"lamp", "tree"} else "box")
        rows.append(
            {
                "asset_id": asset_id,
                "category": category,
                "text_desc": f"a roadside {category}",
                "mesh_path": str(mesh_path),
                "latent_path": str(base_dir / "latents" / f"{asset_id}.pt"),
                "license": "cc-by",
                "source": "test",
                "split": "train",
            }
        )
        if category in {"lamp", "trash", "bollard", "tree"}:
            rows[-1]["quality_tier"] = 3
            rows[-1]["scene_eligible"] = True
        if category == "tree":
            rows[-1]["source"] = "external_import"
            rows[-1]["quality_notes"] = ["tree_upright_validated", "scene_ready"]
    if include_buildings:
        for idx, asset_id in enumerate(("building_01", "building_02"), start=len(rows)):
            mesh_path = base_dir / "meshes" / f"{asset_id}.glb"
            _make_mesh(mesh_path, kind="box")
            rows.append(
                {
                    "asset_id": asset_id,
                    "category": "building",
                    "asset_role": "building",
                    "theme_tags": ["commercial", "transit", "medium"],
                    "frontage_width_m": 14.0 + idx,
                    "depth_m": 10.0,
                    "height_class": "midrise" if asset_id.endswith("01") else "highrise",
                    "text_desc": "a contemporary street-side building",
                    "mesh_path": str(mesh_path),
                    "latent_path": str(base_dir / "latents" / f"{asset_id}.pt"),
                    "license": "cc-by",
                    "source": "test",
                    "split": "train",
                }
            )
    return rows


def _setup_fake_retrieval(monkeypatch, asset_ids: list[str]) -> None:
    import roadgen3d.street_layout as street_layout

    class FakeEmbedder:
        def __init__(self, *args, **kwargs):
            pass

        def encode_texts(self, texts):
            return np.ones((len(texts), 8), dtype=np.float32)

    class FakeIndexStore:
        @classmethod
        def load(cls, *args, **kwargs):
            return cls()

        def add(self, embeddings, ids):
            return None

        def search(self, query_embeddings, topk=1):
            ranked = [
                RetrievalHit(asset_id=asset_id, score=float(1.0 - i * 0.01))
                for i, asset_id in enumerate(asset_ids[:topk])
            ]
            return [list(ranked) for _ in range(query_embeddings.shape[0])]

    monkeypatch.setattr(street_layout, "ClipTextEmbedder", FakeEmbedder)
    monkeypatch.setattr(street_layout, "FaissIndexStore", FakeIndexStore)


def _build_config(seed: int = 42) -> StreetComposeConfig:
    return StreetComposeConfig(
        query="modern clean urban street",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=seed,
        topk_per_category=20,
        max_trials_per_slot=30,
    )


def _build_osm_response(
    *,
    include_building: bool = True,
    include_bus_stop: bool = False,
    include_fire_hydrant: bool = False,
    overlap_anchor_points: bool = False,
) -> dict[str, object]:
    elements: list[dict[str, object]] = [
        {"type": "node", "id": 1, "lon": 116.3900, "lat": 39.9000},
        {"type": "node", "id": 2, "lon": 116.3904, "lat": 39.9000},
        {"type": "node", "id": 3, "lon": 116.3908, "lat": 39.9000},
        {"type": "node", "id": 4, "lon": 116.3912, "lat": 39.9000},
        {
            "type": "way",
            "id": 100,
            "nodes": [1, 2, 3, 4],
            "tags": {"highway": "tertiary"},
        },
        {
            "type": "node",
            "id": 20,
            "lon": 116.39015,
            "lat": 39.90002,
            "tags": {"entrance": "yes"},
        },
        {
            "type": "node",
            "id": 21,
            "lon": 116.39082,
            "lat": 39.90002,
            "tags": {"railway": "subway_entrance"},
        },
    ]
    if include_bus_stop:
        elements.append(
            {
                "type": "node",
                "id": 22,
                "lon": 116.39055,
                "lat": 39.90002,
                "tags": {"highway": "bus_stop"},
            }
        )
    if include_fire_hydrant:
        fire_lon = 116.39055 if overlap_anchor_points else 116.39058
        fire_lat = 39.90002 if overlap_anchor_points else 39.900025
        elements.append(
            {
                "type": "node",
                "id": 23,
                "lon": fire_lon,
                "lat": fire_lat,
                "tags": {"emergency": "fire_hydrant"},
            }
        )
    if include_building:
        elements.extend(
            [
                {"type": "node", "id": 30, "lon": 116.39045, "lat": 39.90015},
                {"type": "node", "id": 31, "lon": 116.39065, "lat": 39.90015},
                {"type": "node", "id": 32, "lon": 116.39065, "lat": 39.90030},
                {"type": "node", "id": 33, "lon": 116.39045, "lat": 39.90030},
                {
                    "type": "way",
                    "id": 200,
                    "nodes": [30, 31, 32, 33],
                    "tags": {"building": "yes", "name": "Test Block"},
                },
            ]
        )
    return {"elements": elements}


def _build_osm_config(
    tmp_path: Path,
    *,
    seed: int = 42,
    surrounding_building_mode: str = "grid_growth",
    land_use_asymmetry_strength: float = 0.0,
    left_right_bias: float = 0.0,
    building_front_setback_min_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MIN_M,
    building_front_setback_max_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MAX_M,
    zoning_granularity: str = "fine",
    streetwall_continuity: float = 0.95,
    infill_policy: str = "aggressive",
) -> StreetComposeConfig:
    return StreetComposeConfig(
        query="urban street",
        length_m=80.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=seed,
        topk_per_category=20,
        max_trials_per_slot=20,
        layout_mode="osm",
        constraint_mode="off",
        aoi_bbox=(116.3898, 39.8998, 116.3914, 39.9005),
        osm_cache_dir=str(tmp_path / "osm_cache"),
        selected_road_osm_id=100,
        road_selection="primary_road",
        segment_length_m=18.0,
        enable_surrounding_buildings=True,
        surrounding_building_mode=surrounding_building_mode,
        building_search_topk=3,
        land_use_asymmetry_strength=land_use_asymmetry_strength,
        left_right_bias=left_right_bias,
        building_front_setback_min_m=building_front_setback_min_m,
        building_front_setback_max_m=building_front_setback_max_m,
        zoning_granularity=zoning_granularity,
        streetwall_continuity=streetwall_continuity,
        infill_policy=infill_policy,
    )


def _build_graph_template_bridge_with_building_regions():
    annotation_payload = {
        "version": ANNOTATION_SCHEMA_VERSION,
        "plan_id": "region_only_graph_template",
        "image_path": "/tmp/region_only_graph_template.png",
        "image_width_px": 1200,
        "image_height_px": 800,
        "pixels_per_meter": 10.0,
        "centerlines": [
            {
                "id": "main_axis",
                "label": "Main Axis",
                "road_width_m": 18.0,
                "reference_width_px": 180.0,
                "forward_drive_lane_count": 1,
                "reverse_drive_lane_count": 1,
                "cross_section_mode": "detailed",
                "cross_section_strips": [
                    {"strip_id": "left_furnishing", "zone": "left", "kind": "nearroad_furnishing", "width_m": 1.2, "direction": "none", "order_index": 0},
                    {"strip_id": "left_sidewalk", "zone": "left", "kind": "clear_sidewalk", "width_m": 2.4, "direction": "none", "order_index": 1},
                    {"strip_id": "left_frontage", "zone": "left", "kind": "frontage_reserve", "width_m": 2.0, "direction": "none", "order_index": 2},
                    {"strip_id": "rev_drive", "zone": "center", "kind": "drive_lane", "width_m": 3.3, "direction": "reverse", "order_index": 0},
                    {"strip_id": "median_01", "zone": "center", "kind": "median", "width_m": 0.3, "direction": "none", "order_index": 1},
                    {"strip_id": "fwd_drive", "zone": "center", "kind": "drive_lane", "width_m": 3.3, "direction": "forward", "order_index": 2},
                    {"strip_id": "right_furnishing", "zone": "right", "kind": "nearroad_furnishing", "width_m": 1.2, "direction": "none", "order_index": 0},
                    {"strip_id": "right_sidewalk", "zone": "right", "kind": "clear_sidewalk", "width_m": 2.4, "direction": "none", "order_index": 1},
                    {"strip_id": "right_frontage", "zone": "right", "kind": "frontage_reserve", "width_m": 2.0, "direction": "none", "order_index": 2},
                ],
                "points": [
                    {"x": 160, "y": 400},
                    {"x": 1040, "y": 400},
                ],
            }
        ],
        "junctions": [],
        "roundabouts": [],
        "control_points": [],
        "building_regions": [
            {
                "id": "building_region_01",
                "label": "North Court",
                "center_px": {"x": 360, "y": 255},
                "width_px": 180,
                "height_px": 120,
                "yaw_deg": 18.0,
            },
            {
                "id": "building_region_02",
                "label": "South Court",
                "center_px": {"x": 820, "y": 555},
                "width_px": 200,
                "height_px": 140,
                "yaw_deg": -22.0,
            },
        ],
    }
    return build_reference_annotation_scene_bridge(
        annotation_payload,
        compose_config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 18.0}),
    )


def test_apply_default_scene_texture_assigns_uv_and_basecolor_texture():
    trimesh = pytest.importorskip("trimesh")
    mesh = trimesh.creation.box(extents=(2.0, 0.08, 1.0))
    mesh.apply_translation([0.0, 0.04, 0.0])
    tracker = create_scene_texture_tracker("topdown_tiles_v1")

    textured = apply_default_scene_texture(
        mesh,
        surface_role="carriageway",
        tint_rgba=[65, 68, 72, 255],
        roughness=0.95,
        texture_mode="topdown_tiles_v1",
        tracker=tracker,
    )

    assert getattr(textured.visual, "uv", None) is not None
    assert getattr(textured.visual.material, "baseColorTexture", None) is not None
    assert tracker.textured_geometry_count == 1
    assert tracker.fallback_used is False


def test_apply_default_scene_texture_falls_back_when_asset_missing(monkeypatch: pytest.MonkeyPatch):
    trimesh = pytest.importorskip("trimesh")
    import roadgen3d.scene_textures as scene_textures

    mesh = trimesh.creation.box(extents=(2.0, 0.08, 1.0))
    tracker = create_scene_texture_tracker("topdown_tiles_v1")
    monkeypatch.setitem(scene_textures._TEXTURE_PATHS, "carriageway", Path("/tmp/does_not_exist_texture.png"))
    scene_textures._load_texture_rgba.cache_clear()

    textured = apply_default_scene_texture(
        mesh,
        surface_role="carriageway",
        tint_rgba=[65, 68, 72, 255],
        roughness=0.95,
        texture_mode="topdown_tiles_v1",
        tracker=tracker,
    )

    assert getattr(textured.visual.material, "baseColorTexture", None) is None
    assert tracker.fallback_used is True
    assert tracker.missing_assets


def test_apply_default_scene_texture_uses_world_space_uv_continuity():
    trimesh = pytest.importorskip("trimesh")

    def _uv_samples(mesh):
        samples: dict[tuple[float, float, float], set[tuple[float, float]]] = {}
        vertices = np.asarray(mesh.vertices, dtype=float)
        uv = np.asarray(mesh.visual.uv, dtype=float)
        top_y = float(vertices[:, 1].max())
        for vertex, uv_pair in zip(vertices, uv):
            if abs(float(vertex[1]) - top_y) > 1e-6:
                continue
            key = (round(float(vertex[0]), 3), round(float(vertex[1]), 3), round(float(vertex[2]), 3))
            samples.setdefault(key, set()).add((round(float(uv_pair[0]), 4), round(float(uv_pair[1]), 4)))
        return samples

    left = trimesh.creation.box(extents=(2.0, 0.08, 1.0))
    left.apply_translation([-1.0, 0.04, 0.0])
    right = trimesh.creation.box(extents=(2.0, 0.08, 1.0))
    right.apply_translation([1.0, 0.04, 0.0])

    left = apply_default_scene_texture(
        left,
        surface_role="sidewalk",
        tint_rgba=[165, 168, 172, 255],
        roughness=0.70,
        texture_mode="topdown_tiles_v1",
        tracker=create_scene_texture_tracker("topdown_tiles_v1"),
    )
    right = apply_default_scene_texture(
        right,
        surface_role="sidewalk",
        tint_rgba=[165, 168, 172, 255],
        roughness=0.70,
        texture_mode="topdown_tiles_v1",
        tracker=create_scene_texture_tracker("topdown_tiles_v1"),
    )

    left_samples = _uv_samples(left)
    right_samples = _uv_samples(right)
    assert left_samples[(0.0, 0.08, -0.5)] & right_samples[(0.0, 0.08, -0.5)]
    assert left_samples[(0.0, 0.08, 0.5)] & right_samples[(0.0, 0.08, 0.5)]


def test_compute_asset_scale_canonical_tree_uses_height_target():
    scale_info = compute_asset_scale(
        category="tree",
        width_m=1.0,
        depth_m=1.0,
        height_m=1.5,
        mode="canonical_v1",
    )

    assert scale_info["applied_scale"] == pytest.approx(4.5, rel=1e-3)
    assert scale_info["canonical_target"]["height_m"] == pytest.approx(7.0)
    assert scale_info["scale_fallback_used"] is False


def test_compute_asset_scale_canonical_can_correct_extreme_asset_sizes():
    huge_tree = compute_asset_scale(
        category="tree",
        width_m=8.0,
        depth_m=8.0,
        height_m=100.0,
        mode="canonical_v1",
    )
    tiny_lamp = compute_asset_scale(
        category="lamp",
        width_m=0.05,
        depth_m=0.05,
        height_m=0.5,
        mode="canonical_v1",
    )

    assert huge_tree["applied_scale"] == pytest.approx(0.07, rel=1e-3)
    assert tiny_lamp["applied_scale"] == pytest.approx(12.0, rel=1e-3)
    assert huge_tree["native_size_m"]["height_m"] == pytest.approx(100.0)
    assert tiny_lamp["canonical_target"]["height_m"] == pytest.approx(6.0)
    assert tiny_lamp["scale_gate_failed"] is False


def test_compute_asset_scale_flags_assets_that_cannot_meet_category_bounds():
    scale_info = compute_asset_scale(
        category="lamp",
        width_m=20.0,
        depth_m=20.0,
        height_m=6.0,
        mode="canonical_v1",
    )

    assert scale_info["applied_scale"] == pytest.approx(1.0)
    assert scale_info["scale_gate_failed"] is True
    assert "width_m_outside" in scale_info["scale_gate_reason"]


def test_compute_asset_scale_native_raw_keeps_identity():
    scale_info = compute_asset_scale(
        category="bench",
        width_m=0.8,
        depth_m=0.5,
        height_m=0.5,
        mode="native_raw",
    )

    assert scale_info["applied_scale"] == pytest.approx(1.0)
    assert scale_info["asset_scale_mode"] == "native_raw"


def test_building_density_selector_keeps_balanced_spacing():
    lots = []
    for idx in range(20):
        side = "left" if idx < 10 else "right"
        local_idx = idx if side == "left" else idx - 10
        lots.append(
            GeneratedLot(
                lot_id=f"lot_{idx:03d}",
                polygon_xz=((float(local_idx), 0.0), (float(local_idx + 1), 0.0), (float(local_idx + 1), 4.0), (float(local_idx), 4.0)),
                center_xz=(float(local_idx) + 0.5, 2.0 if side == "left" else -2.0),
                side=side,
                land_use_type="commercial",
                theme_id="theme_000",
                frontage_width_m=8.0,
                depth_m=8.0,
                street_edge_xz=(float(local_idx) + 0.5, 0.0),
                placement_xz=(float(local_idx) + 0.5, 2.0 if side == "left" else -2.0),
            )
        )

    selected, summary = street_layout._select_building_lots_for_density(
        lots,
        density=0.5,
        max_per_100m=8.0,
        buildable_frontage_by_side={"left": 80.0, "right": 80.0},
    )

    assert len(selected) == 10
    assert summary["removed_lot_count"] == 10
    assert summary["selected_by_side"] == {"left": 5, "right": 5}
    assert {lot.side for lot in selected} == {"left", "right"}


def test_mesh_metadata_and_loaded_mesh_apply_manifest_source_scale(tmp_path: Path):
    trimesh = pytest.importorskip("trimesh")
    mesh_path = tmp_path / "huge_tree.glb"
    mesh_path.parent.mkdir(parents=True, exist_ok=True)
    trimesh.creation.box(extents=(100.0, 50.0, 20.0)).export(mesh_path)

    row = {
        "asset_id": "huge_tree",
        "category": "tree",
        "text_desc": "oversized external tree",
        "mesh_path": str(mesh_path),
        "latent_path": str(tmp_path / "latent.pt"),
        "scale": 0.01,
    }
    metadata = street_layout._load_mesh_metadata([row])["huge_tree"]
    entry = street_layout._load_single_mesh(metadata)

    assert metadata.source_scale == pytest.approx(0.01)
    assert metadata.source_scale_source == "manifest_scale"
    assert metadata.source_scale_confidence == "explicit"
    assert metadata.half_x * 2.0 == pytest.approx(1.0)
    assert metadata.native_height_y == pytest.approx(0.5)
    assert entry.half_x * 2.0 == pytest.approx(1.0)
    assert entry.native_height_y == pytest.approx(0.5)


def test_source_scale_infers_consistent_metric_dimensions_and_rejects_conflicts():
    scale, source, confidence, rejected, metric_size = street_layout._source_scale_for_row(
        {"dimensions_m": {"width": 20.0, "depth": 4.0, "height": 10.0}},
        np.asarray([10.0, 5.0, 2.0], dtype=np.float64),
    )

    assert scale == pytest.approx(2.0)
    assert source == "metric_dimensions_m"
    assert confidence == "metric_high"
    assert rejected == ""
    assert metric_size["width_m"] == pytest.approx(20.0)

    rejected_scale, rejected_source, rejected_confidence, rejected_reason, _ = street_layout._source_scale_for_row(
        {"dimensions_m": {"width": 20.0, "depth": 999.0, "height": 10.0}},
        np.asarray([10.0, 5.0, 2.0], dtype=np.float64),
    )

    assert rejected_scale == pytest.approx(1.0)
    assert rejected_source == "native_bbox"
    assert rejected_confidence == "rejected"
    assert rejected_reason.startswith("metric_ratio_conflict")


def test_source_scale_accepts_swapped_horizontal_metric_axes():
    scale, source, confidence, rejected, _ = street_layout._source_scale_for_row(
        {"dimensions_m": {"width": 20.0, "depth": 4.0, "height": 10.0}},
        np.asarray([4.0, 10.0, 20.0], dtype=np.float64),
    )

    assert scale == pytest.approx(1.0)
    assert source == "metric_dimensions_m"
    assert confidence == "metric_high_swapped_axes"
    assert rejected == ""


def test_real_building_scale_rejects_assets_that_need_extreme_fit_scale():
    entry = street_layout._MeshMetadata(
        asset_id="oversized_building",
        half_x=50.0,
        half_z=50.0,
        min_y=0.0,
        native_height_y=20.0,
    )

    decision = street_layout._resolve_real_building_scale(
        entry=entry,
        frontage_width_m=10.0,
        depth_m=10.0,
        target_height_m=12.0,
    )

    assert decision["accepted"] is False
    assert decision["reason"] == "building_asset_rejected_size_mismatch"
    assert decision["scale"] == pytest.approx(1.0)
    assert decision["required_scale_to_fit"] < 0.75


def test_real_building_scale_preserves_reasonable_native_size():
    entry = street_layout._MeshMetadata(
        asset_id="reasonable_building",
        half_x=5.0,
        half_z=4.0,
        min_y=0.0,
        native_height_y=12.0,
    )

    decision = street_layout._resolve_real_building_scale(
        entry=entry,
        frontage_width_m=12.0,
        depth_m=10.0,
        target_height_m=12.0,
    )

    assert decision["accepted"] is True
    assert decision["scale"] == pytest.approx(1.0)
    assert decision["final_size_m"]["height_m"] == pytest.approx(12.0)


class _UnitFakeEmbedder:
    def encode_texts(self, texts):
        return np.ones((len(texts), 8), dtype=np.float32)


class _UnitFakeIndexStore:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query_embeddings, topk=1):
        return [self._hits[:topk]]


def _assert_no_overlap(bboxes: list[list[float]]) -> None:
    for i, a in enumerate(bboxes):
        for j, b in enumerate(bboxes):
            if j <= i:
                continue
            intersects = not (a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2])
            assert not intersects, f"overlap found between {i} and {j}: {a} vs {b}"


def _bbox_overlap_area_values(a: Sequence[float], b: Sequence[float]) -> float:
    overlap_x = min(float(a[1]), float(b[1])) - max(float(a[0]), float(b[0]))
    overlap_z = min(float(a[3]), float(b[3])) - max(float(a[2]), float(b[2]))
    if overlap_x <= 0.0 or overlap_z <= 0.0:
        return 0.0
    return float(overlap_x * overlap_z)


def _asset_row(
    asset_id: str,
    category: str,
    *,
    generator_type: str = "",
    source: str = "procedural_generated",
    quality_tier: int | None = None,
    scene_eligible: bool | None = None,
    runtime_profile: str = "",
    mesh_face_count: int | None = None,
    quality_notes: list[str] | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "asset_id": asset_id,
        "category": category,
        "text_desc": f"{asset_id} desc",
        "mesh_path": "",
        "latent_path": "",
        "generator_type": generator_type,
        "source": source,
    }
    if quality_tier is not None:
        row["quality_tier"] = quality_tier
    if scene_eligible is not None:
        row["scene_eligible"] = scene_eligible
    if runtime_profile:
        row["runtime_profile"] = runtime_profile
    if mesh_face_count is not None:
        row["mesh_face_count"] = mesh_face_count
    if quality_notes is not None:
        row["quality_notes"] = quality_notes
    return row


def test_street_compose_outputs_created(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    result = compose_street_scene(
        config=_build_config(seed=42),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
        export_format="both",
        out_dir=tmp_path / "artifacts",
    )
    assert isinstance(result, StreetComposeResult)
    assert result.instance_count > 0
    assert Path(result.outputs["scene_glb"]).exists()
    assert Path(result.outputs["scene_glb"]).stat().st_size > 0
    assert Path(result.outputs["scene_ply"]).exists()
    assert Path(result.outputs["scene_ply"]).stat().st_size > 0
    assert Path(result.outputs["scene_layout"]).exists()
    layout_payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = layout_payload.get("summary", {})
    assert "unique_asset_count" in summary
    assert "diversity_ratio" in summary
    assert "per_category_unique" in summary
    assert "selection_source_counts" in summary
    assert "scene_graph" in layout_payload
    assert summary["scene_graph_node_count"] == len(layout_payload["scene_graph"]["nodes"])
    assert summary["scene_graph_edge_count"] == len(layout_payload["scene_graph"]["edges"])
    assert summary["scene_graph_available_categories"]
    assert all("slot_id" in placement for placement in layout_payload.get("placements", []))


def test_street_compose_records_asset_scale_summary_and_scaled_instances(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    config = StreetComposeConfig(
        query="tree lined walkable street",
        length_m=60.0,
        road_width_m=7.0,
        sidewalk_width_m=2.4,
        lane_count=2,
        density=1.0,
        seed=7,
        topk_per_category=20,
        max_trials_per_slot=30,
        asset_scale_mode="canonical_v1",
    )
    result = compose_street_scene(
        config=config,
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )
    layout_payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = layout_payload.get("summary", {})
    scaled_placements = [
        placement
        for placement in layout_payload.get("placements", [])
        if str(placement.get("category", "")) in {"bench", "lamp", "tree"}
    ]

    assert summary["asset_scale_mode"] == "canonical_v1"
    assert summary["asset_scale_summary"]
    assert scaled_placements
    assert any(float(placement.get("scale", 1.0) or 1.0) > 1.0 for placement in scaled_placements)
    assert any((placement.get("canonical_target", {}) or {}) for placement in scaled_placements)


def test_street_compose_native_raw_preserves_identity_asset_scale(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    config = StreetComposeConfig(
        query="street furniture",
        length_m=60.0,
        road_width_m=7.0,
        sidewalk_width_m=2.4,
        lane_count=2,
        density=1.0,
        seed=9,
        topk_per_category=20,
        max_trials_per_slot=30,
        asset_scale_mode="native_raw",
    )
    result = compose_street_scene(
        config=config,
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )
    layout_payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    placements = [
        placement
        for placement in layout_payload.get("placements", [])
        if str(placement.get("category", "")) in {"bench", "lamp", "tree"}
    ]

    assert placements
    assert all(float(placement.get("scale", 1.0) or 1.0) == pytest.approx(1.0) for placement in placements)


def test_load_mesh_cache_preserves_multi_geometry_scene_assets(tmp_path: Path):
    trimesh = pytest.importorskip("trimesh")
    scene_asset = trimesh.Scene()
    scene_asset.add_geometry(trimesh.creation.box(extents=(1.0, 1.0, 1.0)), node_name="part_a")
    part_b = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    part_b.apply_translation([2.0, 0.0, 0.0])
    scene_asset.add_geometry(part_b, node_name="part_b")
    mesh_path = tmp_path / "multi_part.glb"
    scene_asset.export(mesh_path)

    cache = street_layout._load_mesh_cache(
        [
            {
                "asset_id": "scene_asset",
                "mesh_path": str(mesh_path),
                "category": "bench",
                "text_desc": "multi-part asset",
                "latent_path": str(tmp_path / "latent.pt"),
            }
        ]
    )
    entry = cache["scene_asset"]

    assert entry.is_scene is True
    assert len(entry.mesh.geometry) == 2

    parent_scene = trimesh.Scene()
    street_layout._add_instance_meshes(
        scene=parent_scene,
        placements=[
            StreetPlacement(
                instance_id="inst_0001",
                asset_id="scene_asset",
                category="bench",
                score=1.0,
                position_xyz=[0.0, 0.5, 0.0],
                yaw_deg=0.0,
                scale=1.0,
                bbox_xz=[-1.0, 1.0, -1.0, 1.0],
                selection_source="test",
            )
        ],
        mesh_cache=cache,
    )
    assert len(parent_scene.geometry) == 2


def test_load_real_manifest_preserves_scene_ready_fields(tmp_path: Path):
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    mesh_path = tmp_path / "meshes" / "lamp_scene_ready.glb"
    _make_mesh(mesh_path)
    _write_manifest(
        manifest,
        [
            {
                "asset_id": "lamp_scene_ready",
                "category": "lamp",
                "text_desc": "scene ready lamp",
                "mesh_path": str(mesh_path),
                "latent_path": str(tmp_path / "latents" / "lamp_scene_ready.pt"),
                "scene_eligible": True,
                "mesh_face_count": 1234,
                "quality_tier": 3,
                "quality_notes": ["scene_ready", "high_face_count"],
            }
        ],
    )

    rows = street_layout._load_real_manifest(manifest)

    assert rows[0]["scene_eligible"] is True
    assert rows[0]["mesh_face_count"] == 1234
    assert rows[0]["quality_notes"] == ["scene_ready", "high_face_count"]


def test_load_real_manifest_skips_unrepairable_missing_mesh(tmp_path: Path):
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    valid_mesh = tmp_path / "meshes" / "valid.glb"
    _make_mesh(valid_mesh)
    _write_manifest(
        manifest,
        [
            {
                "asset_id": "missing_sign",
                "category": "traffic_sign",
                "text_desc": "missing traffic sign",
                "mesh_path": str(tmp_path / "missing.glb"),
                "latent_path": str(tmp_path / "missing.pt"),
            },
            {
                "asset_id": "valid_sign",
                "category": "traffic_sign",
                "text_desc": "valid traffic sign",
                "mesh_path": str(valid_mesh),
                "latent_path": str(tmp_path / "valid.pt"),
            },
        ],
    )

    rows = street_layout._load_real_manifest(manifest)

    assert [row["asset_id"] for row in rows] == ["valid_sign"]


def test_load_real_manifest_repairs_split_component_mesh_path(tmp_path: Path):
    manifest = tmp_path / "data" / "street_furniture_manifest.jsonl"
    split_dir = tmp_path / "data" / "street_furniture" / "assets_split" / "parent" / "projection"
    repaired_mesh = split_dir / "sign_052.glb"
    _make_mesh(repaired_mesh)
    missing_mesh = tmp_path / "data" / "real" / "normalized_meshes" / "parent-split-052.glb"
    _write_manifest(
        manifest,
        [
            {
                "asset_id": "parent-split-052",
                "category": "traffic_sign",
                "text_desc": "traffic sign split component",
                "mesh_path": str(missing_mesh),
                "latent_path": str(split_dir / "parent-split-052.pt"),
                "asset_composition_type": "split_component",
                "split_index": 52,
                "split_output_dir": str(split_dir),
            }
        ],
    )

    rows = street_layout._load_real_manifest(manifest)
    metadata = street_layout._load_mesh_metadata(rows)

    assert rows[0]["mesh_path"] == str(repaired_mesh.resolve())
    assert "parent-split-052" in metadata


def test_curated_virtual_assets_skip_disabled_tree_when_tree_pool_unusable():
    cache = street_layout._LazyMeshCache({})
    injected = street_layout._inject_curated_virtual_assets(
        [
            {
                "asset_id": "legacy_tree",
                "category": "tree",
                "text_desc": "legacy procedural tree",
                "source": "procedural_generated",
                "scene_eligible": True,
                "quality_tier": 3,
            }
        ],
        cache,
        profile="fixed_hq_v1",
    )

    tree_rows = [
        row
        for row in injected
        if str(row.get("category", "")) == "tree" and street_layout._is_external_tree_asset(row)
    ]
    assert [row["asset_id"] for row in tree_rows] == []
    assert "curated_tree_module_v1" not in cache


def test_add_instance_meshes_adds_doors_only_for_procedural_buildings(tmp_path: Path):
    trimesh = pytest.importorskip("trimesh")
    mesh_path = tmp_path / "building_asset.glb"
    building_mesh = trimesh.creation.box(extents=(10.0, 12.0, 8.0))
    mesh_path.parent.mkdir(parents=True, exist_ok=True)
    building_mesh.export(mesh_path)

    cache = street_layout._load_mesh_cache(
        [
            {
                "asset_id": "building_asset",
                "mesh_path": str(mesh_path),
                "category": "building",
                "text_desc": "street-side building asset",
                "latent_path": str(tmp_path / "latent.pt"),
            }
        ]
    )
    entry = cache["building_asset"]
    placement_y = -float(entry.min_y)
    placement = StreetPlacement(
        instance_id="inst_bldg_0001",
        asset_id="building_asset",
        category="building",
        score=1.0,
        position_xyz=[0.0, placement_y, 0.0],
        yaw_deg=0.0,
        scale=1.0,
        bbox_xz=[-5.0, 5.0, -4.0, 4.0],
        selection_source="building_asset",
        placement_group="building",
        scale_xyz=[1.0, 1.0, 1.0],
    )
    real_building_plan = BuildingPlacementPlan(
        instance_id="inst_bldg_0001",
        footprint_id="lot_001",
        theme_id="theme_000",
        asset_id="building_asset",
        selection_source="building_asset",
        position_xyz=[0.0, placement_y, 0.0],
        yaw_deg=0.0,
        scale=1.0,
        scale_xyz=[1.0, 1.0, 1.0],
        bbox_xz=[-5.0, 5.0, -4.0, 4.0],
        frontage_width_m=10.0,
        depth_m=8.0,
        side="left",
        land_use_type="commercial",
        street_edge_xz=(0.0, -4.0),
        placement_xz=(0.0, 0.0),
        anchor_geom_id="lot_001",
        door_added=False,
        door_missing_reason="real_building_asset_has_native_door",
    )

    real_scene = trimesh.Scene()
    street_layout._add_instance_meshes(
        scene=real_scene,
        placements=[placement],
        mesh_cache=cache,
        building_plans_by_instance={real_building_plan.instance_id: real_building_plan},
    )

    node_names = set(real_scene.graph.nodes_geometry)
    assert any(node_name.startswith("inst_bldg_0001") for node_name in node_names)
    assert not any(node_name.startswith("inst_bldg_0001_door_") for node_name in node_names)

    fallback_plan = BuildingPlacementPlan(
        instance_id="inst_bldg_0001",
        footprint_id="lot_001",
        theme_id="theme_000",
        asset_id="building_asset",
        selection_source="procedural_fallback",
        position_xyz=[0.0, placement_y, 0.0],
        yaw_deg=0.0,
        scale=1.0,
        scale_xyz=[1.0, 1.0, 1.0],
        bbox_xz=[-5.0, 5.0, -4.0, 4.0],
        frontage_width_m=10.0,
        depth_m=8.0,
        side="left",
        land_use_type="commercial",
        street_edge_xz=(0.0, -4.0),
        placement_xz=(0.0, 0.0),
        anchor_geom_id="lot_001",
        door_added=True,
        door_facing="front",
        door_center_local_x=0.0,
        door_width_m=1.2,
        door_height_m=2.4,
        door_dims_m={"width_m": 1.2, "height_m": 2.4, "thickness_m": 0.08},
        door_center_world_xyz=[0.0, 1.2, -4.055],
    )
    fallback_scene = trimesh.Scene()
    street_layout._add_instance_meshes(
        scene=fallback_scene,
        placements=[placement],
        mesh_cache=cache,
        building_plans_by_instance={fallback_plan.instance_id: fallback_plan},
    )

    fallback_node_names = set(fallback_scene.graph.nodes_geometry)
    assert any(node_name.startswith("inst_bldg_0001_door_") for node_name in fallback_node_names)
    assert len(fallback_scene.geometry) >= 4


def test_building_door_local_pose_selects_facade_nearest_road():
    entry = street_layout._MeshCacheEntry(
        mesh=object(),
        half_x=4.0,
        half_z=3.0,
        min_y=0.0,
        native_height_y=8.0,
        center_x=1.25,
        center_z=-0.5,
    )

    right_pose = street_layout._building_door_local_pose(
        street_edge_xz=(0.0, 8.0),
        placement_xz=(0.0, 0.0),
        yaw_deg=0.0,
        side="right",
        entry=entry,
        scale_xyz=[1.0, 1.0, 1.0],
        facade_offset_m=0.055,
    )
    assert right_pose is not None
    (right_local_x, right_local_z), right_facing = right_pose
    assert right_facing == "back"
    assert right_local_x == pytest.approx(1.25)
    assert right_local_z == pytest.approx(-0.5 + 3.0 + 0.055)

    left_pose = street_layout._building_door_local_pose(
        street_edge_xz=(0.0, -8.0),
        placement_xz=(0.0, 0.0),
        yaw_deg=0.0,
        side="left",
        entry=entry,
        scale_xyz=[1.0, 1.0, 1.0],
        facade_offset_m=0.055,
    )
    assert left_pose is not None
    (left_local_x, left_local_z), left_facing = left_pose
    assert left_facing == "front"
    assert left_local_x == pytest.approx(1.25)
    assert left_local_z == pytest.approx(-0.5 - 3.0 - 0.055)


def test_building_door_rotation_helpers_match_yaw_transform_convention():
    local_x, local_z = 0.0, 2.0
    world_x, world_z = street_layout._rotate_local_xz_to_world(local_x, local_z, 90.0)
    assert world_x == pytest.approx(2.0)
    assert world_z == pytest.approx(0.0, abs=1e-6)

    roundtrip_x, roundtrip_z = street_layout._rotate_world_xz_to_local(world_x, world_z, 90.0)
    assert roundtrip_x == pytest.approx(local_x, abs=1e-6)
    assert roundtrip_z == pytest.approx(local_z, abs=1e-6)


def test_building_forbidden_geometry_includes_sidewalks_and_junction_surfaces():
    shapely_geometry = pytest.importorskip("shapely.geometry")

    carriageway = shapely_geometry.box(-8.0, -1.0, 8.0, 1.0)
    sidewalk = shapely_geometry.box(-8.0, 1.0, 8.0, 3.0)
    perpendicular_sidewalk = shapely_geometry.box(3.0, -4.0, 5.0, 4.0)
    building_buffer_probe = shapely_geometry.box(-6.0, 4.0, -2.0, 7.0)
    placement_ctx = SimpleNamespace(
        carriageway=carriageway,
        carriageway_polygon=carriageway,
        sidewalk_zone=sidewalk,
        left_sidewalk_zone=sidewalk,
        right_sidewalk_zone=None,
        road_arm_geometries=[],
        strip_zones={
            "left_clear_sidewalk": sidewalk,
            "left_building_buffer": building_buffer_probe,
        },
        segment_strip_zones={
            "cross_street": {
                "right_clear_sidewalk": perpendicular_sidewalk,
                "right_building_buffer": shapely_geometry.box(5.5, -3.0, 8.0, 3.0),
            }
        },
        junction_geometries=[
            {
                "normalized_surface_patches": [
                    {
                        "surface_role": "crossing",
                        "geometry": shapely_geometry.box(-1.0, -3.0, 1.0, 3.0),
                    },
                    {
                        "surface_role": "sidewalk",
                        "geometry": shapely_geometry.box(5.0, 1.0, 7.0, 4.0),
                    },
                ],
                "sidewalk_corner_patches": [
                    {"geometry": shapely_geometry.box(7.0, 1.0, 8.0, 3.0)}
                ],
            }
        ],
    )

    forbidden = building_forbidden_geometry(placement_ctx)

    assert forbidden.intersection(sidewalk).area == pytest.approx(sidewalk.area)
    assert forbidden.intersection(perpendicular_sidewalk).area == pytest.approx(perpendicular_sidewalk.area)
    assert forbidden.intersection(shapely_geometry.box(-1.0, -3.0, 1.0, 3.0)).area > 0.0
    assert forbidden.intersection(building_buffer_probe).area == pytest.approx(0.0)


def test_building_forbidden_geometry_includes_annotation_surfaces_and_functional_zones():
    shapely_geometry = pytest.importorskip("shapely.geometry")

    carriageway = shapely_geometry.box(-8.0, -1.0, 8.0, 1.0)
    transit_pad = shapely_geometry.box(-6.0, 2.0, -3.0, 4.0)
    plaza_points = [(2.0, 2.0), (6.0, 2.0), (6.0, 5.0), (2.0, 5.0)]
    placement_ctx = SimpleNamespace(
        carriageway=carriageway,
        carriageway_polygon=carriageway,
        sidewalk_zone=shapely_geometry.MultiPolygon(),
        left_sidewalk_zone=None,
        right_sidewalk_zone=None,
        road_arm_geometries=[],
        strip_zones={},
        segment_strip_zones={},
        junction_geometries=[],
        surface_annotations=[
            {
                "surface_role": "transit_pad",
                "geometry": transit_pad,
            }
        ],
        functional_zones=[
            {
                "kind": "plaza",
                "points": plaza_points,
            }
        ],
    )

    forbidden = building_forbidden_geometry(placement_ctx)

    assert forbidden.intersection(transit_pad).area == pytest.approx(transit_pad.area)
    assert forbidden.intersection(shapely_geometry.Polygon(plaza_points)).area == pytest.approx(12.0)


def test_building_ground_underlay_excludes_functional_zones_and_stays_below_roads():
    shapely_geometry = pytest.importorskip("shapely.geometry")
    shapely_ops = pytest.importorskip("shapely.ops")

    carriageway = shapely_geometry.box(-1.0, -10.0, 1.0, 10.0)
    plaza = shapely_geometry.box(2.0, -2.0, 6.0, 2.0)
    placement_ctx = SimpleNamespace(
        aoi_polygon=shapely_geometry.box(-10.0, -10.0, 10.0, 10.0),
        carriageway=carriageway,
        carriageway_polygon=carriageway,
        sidewalk_zone=shapely_geometry.MultiPolygon(),
        left_sidewalk_zone=None,
        right_sidewalk_zone=None,
        road_arm_geometries=[],
        strip_zones={},
        segment_strip_zones={},
        junction_geometries=[],
        surface_annotations=[],
        functional_zones=[
            {
                "kind": "plaza",
                "points": [(2.0, -2.0), (6.0, -2.0), (6.0, 2.0), (2.0, 2.0)],
            }
        ],
    )
    plan = SimpleNamespace(
        bbox_xz=(7.0, 9.0, 5.0, 7.0),
        placement_xz=(8.0, 6.0),
        street_edge_xz=(1.0, 6.0),
        side="left",
        door_center_world_xyz=None,
        door_width_m=1.0,
        front_setback_m=1.0,
    )

    geometries = street_layout._derive_building_ground_surface_geometries(
        [plan],
        placement_ctx=placement_ctx,
        config=SimpleNamespace(land_use_buffer_m=20.0, length_m=20.0, road_width_m=2.0),
    )
    grass = shapely_ops.unary_union(geometries["grass"])

    assert grass.intersection(carriageway).area == pytest.approx(0.0)
    assert grass.intersection(plaza).area == pytest.approx(0.0)
    assert (
        street_layout.BUILDING_GRASS_UNDERLAY_Y_MIN_M
        + street_layout.BUILDING_GRASS_UNDERLAY_HEIGHT_M
    ) < 0.0


def test_building_access_path_is_flush_with_grass_underlay():
    trimesh = pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    carriageway = shapely_geometry.box(-1.0, -10.0, 1.0, 10.0)
    placement_ctx = SimpleNamespace(
        aoi_polygon=shapely_geometry.box(-10.0, -10.0, 10.0, 10.0),
        carriageway=carriageway,
        carriageway_polygon=carriageway,
        sidewalk_zone=shapely_geometry.MultiPolygon(),
        left_sidewalk_zone=None,
        right_sidewalk_zone=None,
        road_arm_geometries=[],
        strip_zones={},
        segment_strip_zones={},
        junction_geometries=[],
        surface_annotations=[],
        functional_zones=[],
    )
    plan = SimpleNamespace(
        bbox_xz=(7.0, 9.0, 5.0, 7.0),
        placement_xz=(8.0, 6.0),
        street_edge_xz=(1.0, 6.0),
        side="left",
        door_center_world_xyz=None,
        door_width_m=1.0,
        front_setback_m=1.0,
    )
    scene = trimesh.Scene()

    counts = street_layout._add_building_ground_surfaces(
        scene,
        [plan],
        placement_ctx=placement_ctx,
        config=SimpleNamespace(land_use_buffer_m=20.0, length_m=20.0, road_width_m=2.0),
        palette={},
        texture_mode="solid_color_legacy",
    )
    access_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("building_access_path_")
    ]
    grass_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("building_land_grass_")
    ]

    assert counts["access_path_count"] >= 1
    assert access_meshes
    assert grass_meshes
    grass_top = street_layout.BUILDING_GRASS_UNDERLAY_TOP_M
    assert max(float(mesh.bounds[1][1]) for mesh in grass_meshes) == pytest.approx(grass_top)
    assert max(float(mesh.bounds[1][1]) for mesh in access_meshes) == pytest.approx(grass_top)


def test_resolve_building_pose_pushes_building_out_of_sidewalk_forbidden_area():
    shapely_geometry = pytest.importorskip("shapely.geometry")

    sidewalk = shapely_geometry.box(-5.0, 1.0, 5.0, 3.0)
    placement_ctx = SimpleNamespace(
        carriageway=shapely_geometry.box(-5.0, -1.0, 5.0, 1.0),
        carriageway_polygon=shapely_geometry.box(-5.0, -1.0, 5.0, 1.0),
        sidewalk_zone=sidewalk,
        left_sidewalk_zone=sidewalk,
        right_sidewalk_zone=None,
        road_arm_geometries=[],
        strip_zones={},
        segment_strip_zones={},
        junction_geometries=[],
    )

    forbidden = building_forbidden_geometry(placement_ctx)
    pose = resolve_building_pose(
        target_center_xz=(0.0, 2.0),
        street_edge_xz=(0.0, 1.0),
        side="left",
        yaw_deg=0.0,
        half_x=1.0,
        half_z=1.0,
        center_x=0.0,
        center_z=0.0,
        scale=1.0,
        placement_ctx=placement_ctx,
        forbidden_geometry=forbidden,
        vehicle_clearance_m=0.10,
        max_push_m=6.0,
    )

    assert not pose.rejected
    assert pose.adjusted
    assert pose.visual_center_xz[1] > 4.0
    footprint = shapely_geometry.Polygon(
        building_footprint_points(
            placement_xz=pose.placement_xz,
            yaw_deg=0.0,
            half_x=1.0,
            half_z=1.0,
            center_x=0.0,
            center_z=0.0,
            scale=1.0,
        )
    )
    assert footprint.intersection(forbidden.buffer(0.10)).area == pytest.approx(0.0)


def test_tree_candidate_uses_trunk_footprint_for_carriageway_clearance():
    shapely_geometry = pytest.importorskip("shapely.geometry")

    carriageway = shapely_geometry.box(-6.0, -1.0, 6.0, 1.0)
    sidewalk = shapely_geometry.box(-6.0, 1.0, 6.0, 3.0)
    placement_ctx = SimpleNamespace(
        carriageway=carriageway,
        carriageway_polygon=carriageway,
        sidewalk_zone=sidewalk,
        left_sidewalk_zone=sidewalk,
        right_sidewalk_zone=None,
        strip_zones={"left_nearroad_furnishing": sidewalk},
        segment_strip_zones={},
    )
    slot = LayoutSlotPlan(
        slot_id="tree_slot",
        category="tree",
        band_name="left_nearroad_furnishing",
        x_center_m=0.0,
        z_center_m=2.0,
        spacing_m=6.0,
        side="left",
        priority=1.0,
        required=True,
    )
    entry = street_layout._MeshMetadata(
        asset_id="wide_canopy_tree",
        half_x=2.0,
        half_z=2.0,
        min_y=0.0,
        native_height_y=6.0,
    )
    config = replace(_build_config(), road_width_m=2.0, sidewalk_width_m=2.0)

    def evaluate_at(z: float):
        return street_layout._evaluate_slot_candidate(
            candidate={
                "tier": "unit_test",
                "point_xz": (0.0, float(z)),
                "yaw_deg": 0.0,
                "anchor_distance_m": 0.0,
            },
            slot=slot,
            category="tree",
            band_width_m=2.0,
            entry=entry,
            scale_info={"applied_scale": 1.0},
            placements=[],
            spatial_hash=street_layout.UniformSpatialHash(cell_size_m=4.0),
            existing_bboxes=[],
            placement_ctx=placement_ctx,
            theme_segment=None,
            road_segment_graph=None,
            theme_poi_points={},
            poi_ctx=None,
            rule_set=None,
            config=config,
            entrance_registry=street_layout.PlacedAssetRegistry(),
            carriageway_boundary=None,
            entrance_points_xz=[],
        )

    near_curb_candidate, near_curb_reason = evaluate_at(1.05)
    assert near_curb_candidate is None
    assert near_curb_reason == "intrudes_carriageway"

    safe_candidate, safe_reason = evaluate_at(1.95)
    assert safe_reason is None
    assert safe_candidate is not None
    assert street_layout._bbox_intrudes_carriageway(
        tuple(float(value) for value in safe_candidate["bbox"]),
        placement_ctx=placement_ctx,
        config=config,
    )


def test_place_building_targets_centers_offset_mesh_and_keeps_lanes_clear(monkeypatch):
    shapely_geometry = pytest.importorskip("shapely.geometry")

    road = shapely_geometry.box(-20.0, -2.0, 20.0, 2.0)
    placement_ctx = SimpleNamespace(
        carriageway_polygon=road,
        carriageway=road,
        strip_zones={},
        junction_geometries=[],
    )
    mesh_cache = street_layout._LazyMeshCache(
        {
            "offset_building": street_layout._MeshMetadata(
                asset_id="offset_building",
                half_x=5.0,
                half_z=4.0,
                min_y=0.0,
                center_x=0.0,
                center_z=-4.0,
                native_height_y=12.0,
            )
        }
    )
    row = {
        "asset_id": "offset_building",
        "category": "building",
        "asset_role": "building",
    }

    def fake_rank_buildings(**_kwargs):
        return [(row, 1.0)], {
            "query": "fake building",
            "hit_count": 1,
            "candidate_count": 1,
            "candidates": [{"asset_id": "offset_building", "category": "building", "score": 1.0}],
        }

    monkeypatch.setattr(street_layout, "_rank_building_candidates_for_target", fake_rank_buildings)
    config = StreetComposeConfig(
        query="offset building lane guard",
        length_m=40.0,
        road_width_m=4.0,
        sidewalk_width_m=2.0,
        lane_count=2,
        density=1.0,
        seed=7,
        topk_per_category=1,
        max_trials_per_slot=1,
        layout_mode="osm",
        constraint_mode="off",
    )
    targets = [
        {
            "target_id": "lot_001",
            "target_kind": "lot",
            "source": "road_buffer",
            "placement_xz": (0.0, 6.0),
            "center_xz": (0.0, 6.0),
            "street_edge_xz": (0.0, 2.0),
            "frontage_width_m": 10.0,
            "depth_m": 8.0,
            "yaw_deg": 0.0,
            "theme_id": "theme_000",
            "land_use_type": "commercial",
            "side": "left",
            "height_class": "midrise",
            "target_height_m": 12.0,
            "front_setback_m": 0.25,
            "placement_strategy": "frontage_setback",
        }
    ]

    placements, plans, _predictions, summary, _next_idx = street_layout._place_building_targets(
        targets=targets,
        config=config,
        theme_segments=(),
        resolved_program=SimpleNamespace(),
        placement_ctx=placement_ctx,
        embedder=object(),
        index_store=object(),
        asset_by_id={"offset_building": row},
        mesh_cache=mesh_cache,
        rng=random.Random(7),
        start_instance_index=1,
        road_type="urban",
    )

    assert len(placements) == 1
    assert len(plans) == 1
    placement = placements[0]
    assert placement.position_xyz[2] > 2.0
    assert summary["building_mesh_origin_centered_count"] == 1
    assert summary["building_lane_intrusion_adjusted_count"] <= 1
    assert summary["building_lane_guard_push_rejected_count"] == 0
    footprint = shapely_geometry.Polygon(
        building_footprint_points(
            placement_xz=(placement.position_xyz[0], placement.position_xyz[2]),
            yaw_deg=placement.yaw_deg,
            half_x=5.0,
            half_z=4.0,
            center_x=0.0,
            center_z=-4.0,
            scale=placement.scale_xyz,
        )
    )
    assert footprint.intersection(road.buffer(0.05)).area == pytest.approx(0.0)


def test_street_compose_no_overlap_aabb(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    result = compose_street_scene(
        config=_build_config(seed=7),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )
    _assert_no_overlap([placement.bbox_xz for placement in result.placements])


def test_street_placement_to_dict_serializes_required_flag():
    placement = StreetPlacement(
        instance_id="inst_required",
        asset_id="bench_01",
        category="bench",
        score=1.0,
        position_xyz=[0.0, 0.0, 0.0],
        yaw_deg=0.0,
        scale=1.0,
        bbox_xz=[-0.5, 0.5, -0.25, 0.25],
        selection_source="test",
        required=True,
    )

    payload = placement.to_dict()

    assert payload["required"] is True


def test_slot_placement_sort_prioritizes_core_infrastructure_order():
    slots = [
        LayoutSlotPlan("tree_required", "tree", "left_furnishing", 0.0, 2.0, 12.0, "left", 1.0, True),
        LayoutSlotPlan("bench_optional", "bench", "left_clear", 0.0, 3.5, 12.0, "left", 0.5, False),
        LayoutSlotPlan("mailbox_optional", "mailbox", "right_clear", 0.0, -3.5, 12.0, "right", 0.5, False),
        LayoutSlotPlan("lamp_optional", "lamp", "left_furnishing", 0.0, 2.0, 12.0, "left", 0.5, False),
    ]

    ordered = sorted(slots, key=street_layout._slot_placement_sort_key)

    assert [slot.category for slot in ordered] == ["lamp", "tree", "bench", "mailbox"]


def test_default_sky_dome_placement_uses_mesh_bounds_for_backdrop_scale():
    metadata = SimpleNamespace(
        half_x=48.071982,
        half_z=48.018283,
        native_height_y=96.137937,
    )

    placement = street_layout._default_sky_dome_placement(
        _build_config(seed=11),
        {
            "asset_id": street_layout.DEFAULT_SKY_DOME_ASSET_ID,
            "dimensions_m": {"width": 160.0, "height": 160.0, "depth": 160.0},
        },
        metadata,
    )

    assert placement is not None
    assert placement.scale > 10.0
    assert placement.bbox_xz[0] < placement.bbox_xz[1]
    assert placement.bbox_xz[2] < placement.bbox_xz[3]
    assert placement.bbox_xz[1] - placement.bbox_xz[0] >= street_layout.DEFAULT_SKY_DOME_MIN_DIAMETER_M
    assert placement.bbox_xz[3] - placement.bbox_xz[2] >= street_layout.DEFAULT_SKY_DOME_MIN_DIAMETER_M


def test_default_sky_dome_material_replaces_extracted_black_texture():
    pytest.importorskip("trimesh")
    row = street_layout._default_sky_dome_row()
    if row is None:
        pytest.skip("default sky dome fixture is not available")

    metadata = street_layout._load_mesh_metadata([row])[street_layout.DEFAULT_SKY_DOME_ASSET_ID]
    entry = street_layout._load_single_mesh(metadata)
    geometries = entry.mesh.geometry.values() if getattr(entry.mesh, "geometry", None) else [entry.mesh]
    materials = [
        getattr(getattr(geom, "visual", None), "material", None)
        for geom in geometries
    ]

    assert any(getattr(material, "name", "") == street_layout.DEFAULT_SKY_DOME_MATERIAL_NAME for material in materials)
    sky_material = next(material for material in materials if getattr(material, "baseColorTexture", None) is not None)
    texture = sky_material.baseColorTexture
    top_pixel = texture.getpixel((texture.width // 2, 8))
    assert sum(top_pixel[:3]) > 240
    assert getattr(sky_material, "emissiveTexture", None) is not None


def test_template_scene_layout_contains_simplified_production_steps(tmp_path: Path, monkeypatch):
    trimesh = pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])

    result = compose_street_scene(
        config=_build_config(seed=17),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    steps = payload["production_steps"]
    summary = payload["summary"]

    assert [step["step_id"] for step in steps] == [
        "road_base",
        "furniture_required",
        "furniture_optional",
        "scene_preview",
    ]
    assert int(summary["production_step_count"]) == 4
    assert summary["final_production_step_id"] == "scene_preview"
    assert summary["scene_texture_mode"] == "topdown_tiles_v1"
    assert summary["scene_texture_pack"] == "topdown_tiles_v1"
    assert summary["scene_texture_fallback_used"] is False
    assert summary["scene_texture_missing_assets"] == []
    assert Path(payload["outputs"]["production_steps_dir"]).exists()
    assert Path(payload["outputs"]["production_steps_manifest"]).exists()
    assert all(Path(step["glb_path"]).exists() for step in steps)
    assert all(step["scene_texture_mode"] == "topdown_tiles_v1" for step in steps)
    assert all(bool(step["textured_base_enabled"]) for step in steps)
    assert steps[0]["counts"]["street_furniture_count"] == 0
    assert steps[1]["counts"]["visible_instance_count"] <= steps[2]["counts"]["visible_instance_count"]
    assert summary["street_furniture_side_counts"]["left"] > 0
    assert summary["street_furniture_side_counts"]["right"] > 0
    assert summary["street_furniture_balance_ok"] is True
    loaded_scene = trimesh.load(Path(result.outputs["scene_glb"]), force="scene")
    assert _has_embedded_texture(loaded_scene) is True
    loaded_step = trimesh.load(Path(steps[0]["glb_path"]), force="scene")
    assert _has_embedded_texture(loaded_step) is True


def test_street_compose_seed_deterministic(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    result_a = compose_street_scene(
        config=_build_config(seed=99),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts_a",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts_a",
    )
    result_b = compose_street_scene(
        config=_build_config(seed=99),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts_b",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts_b",
    )
    sig_a = [
        (p.asset_id, round(p.position_xyz[0], 6), round(p.position_xyz[2], 6), round(p.yaw_deg, 6))
        for p in result_a.placements
    ]
    sig_b = [
        (p.asset_id, round(p.position_xyz[0], 6), round(p.position_xyz[2], 6), round(p.yaw_deg, 6))
        for p in result_b.placements
    ]
    assert sig_a == sig_b


def test_street_compose_writes_placement_log_and_locks_tree_species_per_theme(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    for row in rows:
        if str(row.get("category", "")) == "tree":
            row["source"] = "objaverse_import"
            row["quality_notes"] = ["tree_upright_validated"]
    extra_tree_mesh = tmp_path / "data" / "meshes" / "tree_02.glb"
    _make_mesh(extra_tree_mesh, kind="cylinder")
    rows.append(
        {
            "asset_id": "tree_02",
            "category": "tree",
            "text_desc": "a second roadside tree",
            "mesh_path": str(extra_tree_mesh),
            "latent_path": str(tmp_path / "data" / "latents" / "tree_02.pt"),
            "license": "cc-by",
            "source": "objaverse_import",
            "split": "train",
            "quality_notes": ["tree_upright_validated"],
        }
    )
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])

    original_solve = street_layout.LayoutSolverRuntime.solve

    def fake_solve(self, solver_input):
        result = original_solve(self, solver_input)
        left_band = next(
            band
            for band in result.resolved_program.bands
            if str(getattr(band, "side", "")) == "left" and str(getattr(band, "kind", "")) in {"furnishing", "transit_edge"}
        )
        right_band = next(
            band
            for band in result.resolved_program.bands
            if str(getattr(band, "side", "")) == "right" and str(getattr(band, "kind", "")) in {"furnishing", "transit_edge"}
        )
        boosted_slots = tuple(result.slot_plans) + (
            LayoutSlotPlan(
                slot_id="tree_boost_left",
                category="tree",
                band_name=str(left_band.name),
                x_center_m=-12.0,
                z_center_m=float(left_band.z_center_m),
                spacing_m=18.0,
                side="left",
                priority=1.0,
            ),
            LayoutSlotPlan(
                slot_id="tree_boost_right",
                category="tree",
                band_name=str(right_band.name),
                x_center_m=12.0,
                z_center_m=float(right_band.z_center_m),
                spacing_m=18.0,
                side="right",
                priority=1.0,
            ),
        )
        return replace(result, slot_plans=boosted_slots)

    monkeypatch.setattr(street_layout.LayoutSolverRuntime, "solve", fake_solve)

    result = compose_street_scene(
        config=_build_config(seed=11),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    placement_log_path = Path(summary["placement_log_path"])
    tree_placements = [
        placement
        for placement in payload["placements"]
        if placement["category"] == "tree"
    ]

    assert summary["placement_logging_mode"] == "full_with_ui_summary"
    assert placement_log_path.exists()
    assert payload["placement_decision_log"]["path"] == str(placement_log_path)
    assert summary["placement_log_summary"]["event_count"] > 0
    log_lines = [
        json.loads(line)
        for line in placement_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(line["event_type"] == "slot_generated" for line in log_lines)
    assert any(line["event_type"] == "placement_selected" for line in log_lines)
    assert summary["tree_species_policy"] == "per_theme_single_species"
    assert tree_placements
    asset_ids_by_theme: dict[str, set[str]] = {}
    for placement in tree_placements:
        asset_ids_by_theme.setdefault(str(placement.get("theme_id", "") or ""), set()).add(str(placement["asset_id"]))
    assert all(len(asset_ids) == 1 for asset_ids in asset_ids_by_theme.values())
    assert summary["tree_asset_by_theme"]


def test_street_compose_real_manifest_required(tmp_path: Path):
    pytest.importorskip("trimesh")
    mesh_path = tmp_path / "mesh.glb"
    _make_mesh(mesh_path, kind="box")
    manifest = tmp_path / "bad_manifest.jsonl"
    _write_manifest(
        manifest,
        [
            {
                "asset_id": "bench_01",
                "text_desc": "a bench",
                "mesh_path": str(mesh_path),
                "latent_path": str(tmp_path / "bench.pt"),
            }
        ],
    )
    with pytest.raises(ValueError, match="missing required fields"):
        compose_street_scene(
            config=_build_config(seed=1),
            manifest_path=manifest,
            artifacts_dir=tmp_path / "artifacts",
            local_files_only=True,
            device="cpu",
            out_dir=tmp_path / "artifacts",
        )


def test_street_compose_gradio_callback_returns_model_path(tmp_path: Path, monkeypatch):
    pytest.importorskip("gradio")
    def fake_compose(**kwargs):
        return StreetComposeResult(
            query="urban street",
            instance_count=1,
            dropped_slots=0,
            placements=[
                StreetPlacement(
                    instance_id="inst_0001",
                    asset_id="bench_01",
                    category="bench",
                    score=0.9,
                    position_xyz=[0.0, 0.0, 0.0],
                    yaw_deg=0.0,
                    scale=1.0,
                    bbox_xz=[-1.0, 1.0, -0.5, 0.5],
                    selection_source="faiss",
                )
            ],
            outputs={
                "scene_glb": str(glb_path),
                "scene_ply": str(ply_path),
                "scene_layout": str(layout_path),
            },
        )

    manifest_path = tmp_path / "real_assets_manifest.jsonl"
    manifest_path.write_text(
        json.dumps(
            {
                "asset_id": "bench_01",
                "category": "bench",
                "text_desc": "test bench",
                "latent_path": str(tmp_path / "latents" / "bench_01.pt"),
                "source": "parametric_generated",
                "generator_type": "parametric_v1",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(app, "compose_street_scene", fake_compose)
    summary, rows, layout_json, model_path, files = app.run_street_compose(
        dataset_profile="real",
        query="urban street",
        real_manifest_text=str(manifest_path),
        artifacts_dir_text=str(tmp_path),
        model_name="openai/clip-vit-base-patch32",
        model_dir_text="",
        local_files_only=True,
        device="cpu",
        street_length_m=80.0,
        street_road_width_m=8.0,
        street_sidewalk_width_m=2.5,
        street_lane_count=2,
        street_density=1.0,
        street_seed=0,
        street_topk_per_category=20,
        street_max_trials_per_slot=30,
        export_format="both",
    )
    assert "Street compose done" in summary
    assert model_path and model_path.endswith("scene.glb")
    assert rows and rows[0][0] == "inst_0001"
    assert rows[0][-1] == "parametric_v1"
    assert layout_json
    assert any(str(path).endswith("scene_layout.json") for path in files)


def test_prepare_web_viewer_outputs_adds_url_and_updates_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("gradio")
def test_prepare_web_viewer_outputs_mirrors_external_layout_into_repo_and_uses_standalone_viewer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("gradio")
def test_prepare_web_viewer_outputs_sanitizes_infinity_before_persisting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("gradio")
def test_run_street_compose_defaults_shift_to_walkable_narrow_street():
    pytest.importorskip("gradio")
def test_street_compose_gradio_callback_propagates_objective_and_demand_controls(tmp_path: Path, monkeypatch):
    pytest.importorskip("gradio")
    def fake_compose(**kwargs):
        config = kwargs["config"]
        captured_config["objective_profile"] = config.objective_profile
        captured_config["ped_demand_level"] = config.ped_demand_level
        captured_config["bike_demand_level"] = config.bike_demand_level
        captured_config["transit_demand_level"] = config.transit_demand_level
        captured_config["vehicle_demand_level"] = config.vehicle_demand_level
        captured_config["asset_scale_mode"] = config.asset_scale_mode
        captured_config["land_use_asymmetry_strength"] = config.land_use_asymmetry_strength
        captured_config["left_right_bias"] = config.left_right_bias
        captured_config["building_front_setback_min_m"] = config.building_front_setback_min_m
        captured_config["building_front_setback_max_m"] = config.building_front_setback_max_m
        captured_config["zoning_granularity"] = config.zoning_granularity
        captured_config["streetwall_continuity"] = config.streetwall_continuity
        captured_config["infill_policy"] = config.infill_policy
        captured_config["tree_species_policy"] = config.tree_species_policy
        captured_config["furniture_balance_policy"] = config.furniture_balance_policy
        captured_config["placement_logging_mode"] = config.placement_logging_mode
        return StreetComposeResult(
            query="urban street",
            instance_count=1,
            dropped_slots=0,
            placements=[],
            outputs={
                "scene_glb": str(glb_path),
                "scene_layout": str(layout_path),
                "placement_decisions": str(placement_log_path),
                "layout_solver_requested": "hybrid_milp_v1",
                "layout_solver_used": "hybrid_milp_v1",
            },
        )

    manifest_path = tmp_path / "real_assets_manifest.jsonl"
    manifest_path.write_text(
        json.dumps(
            {
                "asset_id": "bench_01",
                "category": "bench",
                "text_desc": "test bench",
                "latent_path": str(tmp_path / "latents" / "bench_01.pt"),
                "source": "parametric_generated",
                "generator_type": "parametric_v1",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(app, "compose_street_scene", fake_compose)
    summary, rows, layout_json, model_path, files = app.run_street_compose(
        dataset_profile="real",
        query="urban street",
        real_manifest_text=str(manifest_path),
        artifacts_dir_text=str(tmp_path),
        local_files_only=True,
        device="cpu",
        objective_profile="commerce",
        ped_demand_level="high",
        bike_demand_level="medium",
        transit_demand_level="low",
        vehicle_demand_level="medium",
        asset_scale_mode="native_raw",
        land_use_asymmetry_strength=0.55,
        left_right_bias=-0.25,
        building_front_setback_min_m=1.1,
        building_front_setback_max_m=1.9,
        zoning_granularity="fine",
        streetwall_continuity=0.9,
        infill_policy="balanced",
        tree_species_policy="per_theme_single_species",
        furniture_balance_policy="overall_balanced",
        placement_logging_mode="full_with_ui_summary",
    )

    assert captured_config == {
        "objective_profile": "commerce",
        "ped_demand_level": "high",
        "bike_demand_level": "medium",
        "transit_demand_level": "low",
        "vehicle_demand_level": "medium",
        "asset_scale_mode": "native_raw",
        "land_use_asymmetry_strength": 0.55,
        "left_right_bias": -0.25,
        "building_front_setback_min_m": 1.1,
        "building_front_setback_max_m": 1.9,
        "zoning_granularity": "fine",
        "streetwall_continuity": 0.9,
        "infill_policy": "balanced",
        "tree_species_policy": "per_theme_single_species",
        "furniture_balance_policy": "overall_balanced",
        "placement_logging_mode": "full_with_ui_summary",
    }
    assert "objective_profile: commerce" in summary
    assert "demand_levels: ped=high, bike=medium, transit=low, vehicle=medium" in summary
    assert "solver_backend_requested: hybrid_milp_v1" in summary
    assert "solver_backend_used: hybrid_milp_v1" in summary
    assert "asset_scale_mode: native_raw" in summary
    assert "selected_highway_type: tertiary" in summary
    assert "land_use_asymmetry_strength: 0.55" in summary
    assert "left_right_bias: -0.25" in summary
    assert "building_front_setback_range_m: 1.10-1.90" in summary
    assert "zoning_granularity: fine" in summary
    assert "streetwall_continuity: 0.90" in summary
    assert "infill_policy: balanced" in summary
    assert "tree_species_policy: per_theme_single_species" in summary
    assert 'tree_asset_by_theme: {"theme_000": "tree_02"}' in summary
    assert "furniture_balance_policy: overall_balanced" in summary
    assert "placement_logging_mode: full_with_ui_summary" in summary
    assert f"placement_log_path: {placement_log_path}" in summary
    assert "frontage_parcel_count: 12" in summary
    assert "infill_footprint_count: 3" in summary
    assert rows == []
    assert layout_json
    assert model_path == str(glb_path)
    assert str(layout_path) in files
    assert str(placement_log_path) in files


def test_extract_solver_diagnostics_aggregates_osm_band_view():
    pytest.importorskip("gradio")
def test_extract_placement_decision_summary_reads_new_fields():
    pytest.importorskip("gradio")
def test_extract_street_scale_summary_reports_scale_and_road_selection():
    pytest.importorskip("gradio")
def test_extract_cross_section_preview_builds_template_cross_section():
    pytest.importorskip("gradio")
def test_extract_cross_section_preview_builds_osm_aggregated_cross_section():
    pytest.importorskip("gradio")
def test_extract_solver_diagnostics_tolerates_legacy_layout_payload():
    pytest.importorskip("gradio")
def test_extract_cross_section_preview_tolerates_legacy_layout_payload():
    pytest.importorskip("gradio")
def test_pick_category_candidate_parametric_first_prefers_parametric_bench(monkeypatch):
    asset_by_id = {
        "bench_legacy": _asset_row("bench_legacy", "bench", source="procedural_generated"),
        "bench_param": _asset_row("bench_param", "bench", generator_type="parametric_v1", source="parametric_generated"),
    }
    hits = [
        RetrievalHit(asset_id="bench_legacy", score=0.95),
        RetrievalHit(asset_id="bench_param", score=0.85),
    ]
    monkeypatch.setattr(street_layout, "_softmax_weights", lambda scores, temperature: [1.0] + [0.0] * (len(scores) - 1))

    row, score, source = street_layout._pick_category_candidate(
        query="street",
        category="bench",
        topk=2,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=list(asset_by_id.values()),
        used_asset_ids=set(),
        rng=random.Random(0),
        config=StreetComposeConfig(
            query="street",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=0,
            topk_per_category=2,
            max_trials_per_slot=5,
            asset_curation_mode="parametric_first",
            curated_street_assets_profile="disabled",
        ),
    )

    assert row["asset_id"] == "bench_param"
    assert score > 0.85
    assert source == "faiss_softmax"


def test_pick_category_candidate_scene_ready_first_prefers_production_bench(monkeypatch):
    asset_by_id = {
        "bench_legacy": _asset_row(
            "bench_legacy",
            "bench",
            source="procedural_generated",
            quality_tier=3,
            scene_eligible=True,
            mesh_face_count=900,
        ),
        "bench_param": _asset_row(
            "bench_param",
            "bench",
            generator_type="parametric_v1",
            source="parametric_generated",
            runtime_profile="production",
            quality_tier=3,
            scene_eligible=True,
            mesh_face_count=1552,
        ),
    }
    hits = [
        RetrievalHit(asset_id="bench_legacy", score=0.95),
        RetrievalHit(asset_id="bench_param", score=0.84),
    ]
    monkeypatch.setattr(street_layout, "_softmax_weights", lambda scores, temperature: [1.0] + [0.0] * (len(scores) - 1))

    row, score, source = street_layout._pick_category_candidate(
        query="street",
        category="bench",
        topk=2,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=list(asset_by_id.values()),
        used_asset_ids=set(),
        rng=random.Random(0),
        config=StreetComposeConfig(
            query="street",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=0,
            topk_per_category=2,
            max_trials_per_slot=5,
            asset_curation_mode="scene_ready_first",
            curated_street_assets_profile="disabled",
        ),
    )

    assert row["asset_id"] == "bench_param"
    assert score > 0.84
    assert source == "faiss_softmax"


def test_pick_category_candidate_legacy_prefers_non_parametric_bench(monkeypatch):
    asset_by_id = {
        "bench_param": _asset_row("bench_param", "bench", generator_type="parametric_v1", source="parametric_generated"),
        "bench_legacy": _asset_row("bench_legacy", "bench", source="procedural_generated"),
    }
    hits = [
        RetrievalHit(asset_id="bench_param", score=0.95),
        RetrievalHit(asset_id="bench_legacy", score=0.85),
    ]
    monkeypatch.setattr(street_layout, "_softmax_weights", lambda scores, temperature: [1.0] + [0.0] * (len(scores) - 1))

    row, score, source = street_layout._pick_category_candidate(
        query="street",
        category="bench",
        topk=2,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=list(asset_by_id.values()),
        used_asset_ids=set(),
        rng=random.Random(0),
        config=StreetComposeConfig(
            query="street",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=0,
            topk_per_category=2,
            max_trials_per_slot=5,
            asset_curation_mode="legacy",
            curated_street_assets_profile="disabled",
        ),
    )

    assert row["asset_id"] == "bench_legacy"
    assert score > 0.85
    assert source == "faiss_softmax"


def test_pick_category_candidate_legacy_falls_back_when_only_parametric_exists(monkeypatch):
    asset_by_id = {
        "lamp_param": _asset_row("lamp_param", "lamp", generator_type="parametric_v1", source="parametric_generated"),
    }
    hits = [RetrievalHit(asset_id="lamp_param", score=0.91)]
    monkeypatch.setattr(street_layout, "_softmax_weights", lambda scores, temperature: [1.0])

    row, score, source = street_layout._pick_category_candidate(
        query="street",
        category="lamp",
        topk=1,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=list(asset_by_id.values()),
        used_asset_ids=set(),
        rng=random.Random(0),
        config=StreetComposeConfig(
            query="street",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=0,
            topk_per_category=1,
            max_trials_per_slot=5,
            asset_curation_mode="legacy",
            curated_street_assets_profile="disabled",
        ),
    )

    assert row["asset_id"] == "lamp_param"
    assert score > 0.91
    assert source == "faiss_softmax"


def test_pick_category_candidate_parametric_first_does_not_override_non_priority_categories(monkeypatch):
    asset_by_id = {
        "tree_legacy": _asset_row("tree_legacy", "tree", source="procedural_generated"),
        "tree_param": _asset_row("tree_param", "tree", generator_type="parametric_v1", source="parametric_generated"),
    }
    hits = [
        RetrievalHit(asset_id="tree_legacy", score=0.92),
        RetrievalHit(asset_id="tree_param", score=0.88),
    ]
    monkeypatch.setattr(street_layout, "_softmax_weights", lambda scores, temperature: [1.0] + [0.0] * (len(scores) - 1))

    row, _score, _source = street_layout._pick_category_candidate(
        query="street",
        category="tree",
        topk=2,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=list(asset_by_id.values()),
        used_asset_ids=set(),
        rng=random.Random(0),
        config=StreetComposeConfig(
            query="street",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=0,
            topk_per_category=2,
            max_trials_per_slot=5,
            asset_curation_mode="parametric_first",
            curated_street_assets_profile="disabled",
        ),
    )

    assert row["asset_id"] == "tree_legacy"


def test_pick_category_candidate_scene_ready_first_prefers_scene_ready_lamp(monkeypatch):
    asset_by_id = {
        "lamp_preview": _asset_row(
            "lamp_preview",
            "lamp",
            generator_type="parametric_v1",
            source="parametric_generated",
            runtime_profile="preview",
            quality_tier=1,
            scene_eligible=False,
            mesh_face_count=92,
        ),
        "lamp_curated": _asset_row(
            "lamp_curated",
            "lamp",
            source="procedural_generated",
            quality_tier=3,
            scene_eligible=True,
            mesh_face_count=1450,
        ),
    }
    hits = [
        RetrievalHit(asset_id="lamp_preview", score=0.97),
        RetrievalHit(asset_id="lamp_curated", score=0.86),
    ]
    monkeypatch.setattr(street_layout, "_softmax_weights", lambda scores, temperature: [1.0] + [0.0] * (len(scores) - 1))

    row, score, source = street_layout._pick_category_candidate(
        query="street",
        category="lamp",
        topk=2,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=list(asset_by_id.values()),
        used_asset_ids=set(),
        rng=random.Random(0),
        config=StreetComposeConfig(
            query="street",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=0,
            topk_per_category=2,
            max_trials_per_slot=5,
            asset_curation_mode="scene_ready_first",
            curated_street_assets_profile="disabled",
        ),
    )

    assert row["asset_id"] == "lamp_curated"
    assert score > 0.86
    assert source == "faiss_softmax"


def test_pick_category_candidate_scene_ready_first_falls_back_when_only_ineligible_assets_exist(monkeypatch):
    asset_by_id = {
        "tree_lowpoly": _asset_row(
            "tree_lowpoly",
            "tree",
            source="procedural_generated",
            quality_tier=0,
            scene_eligible=False,
            mesh_face_count=40,
        ),
    }
    hits = [RetrievalHit(asset_id="tree_lowpoly", score=0.91)]
    monkeypatch.setattr(street_layout, "_softmax_weights", lambda scores, temperature: [1.0])

    row, score, source = street_layout._pick_category_candidate(
        query="street",
        category="tree",
        topk=1,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=list(asset_by_id.values()),
        used_asset_ids=set(),
        rng=random.Random(0),
        config=StreetComposeConfig(
            query="street",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=0,
            topk_per_category=1,
            max_trials_per_slot=5,
            asset_curation_mode="scene_ready_first",
            curated_street_assets_profile="disabled",
        ),
    )

    assert row["asset_id"] == "tree_lowpoly"


def test_pick_category_candidate_fixed_hq_uses_seeded_allowlist(monkeypatch):
    asset_by_id = {
        "lamp_modern_production": _asset_row(
            "lamp_modern_production",
            "lamp",
            source="parametric_generated",
            quality_tier=3,
            scene_eligible=True,
        ),
        "lamp_allowlist_alt": _asset_row(
            "lamp_allowlist_alt",
            "lamp",
            source="urbanverse",
            quality_tier=3,
            scene_eligible=True,
        ),
        "objaverse_trash_f16b7d84113d4cba869412ee95769910": _asset_row(
            "objaverse_trash_f16b7d84113d4cba869412ee95769910",
            "trash",
            source="objaverse_import",
            quality_tier=3,
            scene_eligible=True,
        ),
        "curated_railing_module_v1": _asset_row(
            "curated_railing_module_v1",
            "bollard",
            source="curated_virtual",
            quality_tier=3,
            scene_eligible=False,
        ),
        "e62f62684b614e38998b890a974e1820": _asset_row(
            "e62f62684b614e38998b890a974e1820",
            "bollard",
            source="urbanverse",
            quality_tier=3,
            scene_eligible=True,
        ),
        "lamp_other": _asset_row("lamp_other", "lamp"),
        "trash_other": _asset_row("trash_other", "trash"),
        "bollard_other": _asset_row("bollard_other", "bollard", scene_eligible=False),
    }
    hits = [
        RetrievalHit(asset_id="lamp_other", score=0.99),
        RetrievalHit(asset_id="trash_other", score=0.98),
        RetrievalHit(asset_id="bollard_other", score=0.97),
    ]
    monkeypatch.setattr(street_layout, "_softmax_weights", lambda scores, temperature: [1.0] + [0.0] * (len(scores) - 1))
    config = StreetComposeConfig(
        query="street",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=0,
        topk_per_category=3,
        max_trials_per_slot=5,
        curated_street_assets_profile="fixed_hq_v1",
    )

    lamp_row, lamp_score, lamp_source = street_layout._pick_category_candidate(
        query="street",
        category="lamp",
        topk=3,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=[row for row in asset_by_id.values() if row["category"] == "lamp"],
        used_asset_ids=set(),
        rng=random.Random(0),
        config=config,
        stable_selection_key="seed=0:lamp:theme_a",
    )
    lamp_row_repeat, _lamp_score_repeat, lamp_source_repeat = street_layout._pick_category_candidate(
        query="street",
        category="lamp",
        topk=3,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=[row for row in asset_by_id.values() if row["category"] == "lamp"],
        used_asset_ids=set(),
        rng=random.Random(999),
        config=config,
        stable_selection_key="seed=0:lamp:theme_a",
    )
    lamp_ids_by_theme = {
        street_layout._pick_category_candidate(
            query="street",
            category="lamp",
            topk=3,
            embedder=_UnitFakeEmbedder(),
            index_store=_UnitFakeIndexStore(hits),
            asset_by_id=asset_by_id,
            category_pool=[row for row in asset_by_id.values() if row["category"] == "lamp"],
            used_asset_ids=set(),
            rng=random.Random(0),
            config=config,
            stable_selection_key=f"seed={seed}:lamp:theme_a",
        )[0]["asset_id"]
        for seed in range(12)
    }
    trash_row, trash_score, trash_source = street_layout._pick_category_candidate(
        query="street",
        category="trash",
        topk=3,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=[row for row in asset_by_id.values() if row["category"] == "trash"],
        used_asset_ids=set(),
        rng=random.Random(0),
        config=config,
        stable_selection_key="seed=0:trash:theme_a",
    )
    bollard_row, bollard_score, bollard_source = street_layout._pick_category_candidate(
        query="street",
        category="bollard",
        topk=3,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=[row for row in asset_by_id.values() if row["category"] == "bollard"],
        used_asset_ids=set(),
        rng=random.Random(0),
        config=config,
        stable_selection_key="seed=0:bollard:theme_a",
    )

    assert lamp_row["asset_id"] in {"lamp_modern_production", "lamp_allowlist_alt"}
    assert (lamp_score, lamp_source) == (1.0, "curated_allowlist_stable")
    assert (lamp_row_repeat["asset_id"], lamp_source_repeat) == (lamp_row["asset_id"], "curated_allowlist_stable")
    assert len(lamp_ids_by_theme) >= 2
    assert (trash_row["asset_id"], trash_score, trash_source) == (
        "objaverse_trash_f16b7d84113d4cba869412ee95769910",
        1.0,
        "curated_allowlist_stable",
    )
    assert (bollard_row["asset_id"], bollard_score, bollard_source) == (
        "e62f62684b614e38998b890a974e1820",
        1.0,
        "curated_allowlist_stable",
    )


def test_validate_curated_locked_assets_reports_available_fallbacks():
    asset_by_id = {
        "lamp_modern_production": _asset_row("lamp_modern_production", "lamp", scene_eligible=True),
        "e62f62684b614e38998b890a974e1820": _asset_row(
            "e62f62684b614e38998b890a974e1820",
            "bollard",
            source="urbanverse",
            scene_eligible=True,
        ),
    }

    usable = street_layout._validate_curated_locked_assets(asset_by_id=asset_by_id, profile="fixed_hq_v1")

    assert usable["lamp"] == "lamp_modern_production"
    assert usable["bollard"] == "e62f62684b614e38998b890a974e1820"
    assert "trash" not in usable


def test_osm_bench_yaw_aligns_parallel_to_carriageway():
    shapely_geometry = pytest.importorskip("shapely.geometry")
    carriageway = shapely_geometry.box(-20.0, -4.0, 20.0, 4.0)
    placement_ctx = SimpleNamespace(carriageway=carriageway)

    candidates = street_layout._search_tier_exact_candidates(
        category="bench",
        anchor_target_xz=(0.0, -5.5),
        placement_ctx=placement_ctx,
    )

    assert len(candidates) == 1
    assert abs(float(candidates[0]["yaw_deg"])) <= 1e-6


def test_osm_mailbox_yaw_still_faces_carriageway():
    shapely_geometry = pytest.importorskip("shapely.geometry")
    carriageway = shapely_geometry.box(-20.0, -4.0, 20.0, 4.0)
    placement_ctx = SimpleNamespace(carriageway=carriageway)

    candidates = street_layout._search_tier_exact_candidates(
        category="mailbox",
        anchor_target_xz=(0.0, -5.5),
        placement_ctx=placement_ctx,
    )

    assert len(candidates) == 1
    assert abs(float(candidates[0]["yaw_deg"]) - 0.0) <= 1e-6


def test_osm_beauty_scene_proxies_skip_linear_road_overlays():
    trimesh = pytest.importorskip("trimesh")
    scene = trimesh.Scene()
    placement_ctx = SimpleNamespace(road_reference=None)
    street_program = SimpleNamespace(road_width_m=8.0, lane_count=2)
    poi_ctx = PoiContext(
        entrance_points_xz=(),
        bus_stop_points_xz=(),
        fire_points_xz=(),
        poi_points_by_type_xz={"crossing": ((0.0, 0.0),)},
    )

    street_layout._add_beauty_scene_proxies(
        scene,
        config=StreetComposeConfig(
            query="street",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=0,
            topk_per_category=1,
            max_trials_per_slot=5,
            layout_mode="osm",
        ),
        street_program=street_program,
        placement_ctx=placement_ctx,
        poi_ctx=poi_ctx,
        placements=[],
    )

    node_names = set(scene.graph.nodes_geometry)
    assert not any(name.startswith("lane_mark_") for name in node_names)
    assert not any(name.startswith("curb_") for name in node_names)
    assert not any(name.startswith("crossing_patch_") for name in node_names)


def test_tree_pit_proxy_is_compact_and_stays_outside_carriageway():
    trimesh = pytest.importorskip("trimesh")
    scene = trimesh.Scene()
    placement_ctx = SimpleNamespace(road_reference=None)
    street_program = SimpleNamespace(road_width_m=8.0, lane_count=2)
    poi_ctx = PoiContext(
        entrance_points_xz=(),
        bus_stop_points_xz=(),
        fire_points_xz=(),
        poi_points_by_type_xz={},
    )

    street_layout._add_beauty_scene_proxies(
        scene,
        config=StreetComposeConfig(
            query="street",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=0,
            topk_per_category=1,
            max_trials_per_slot=5,
            layout_mode="template",
        ),
        street_program=street_program,
        placement_ctx=placement_ctx,
        poi_ctx=poi_ctx,
        placements=[
            StreetPlacement(
                instance_id="tree_0001",
                asset_id="tree_asset",
                category="tree",
                score=1.0,
                position_xyz=[0.0, street_layout.SIDEWALK_ELEVATION_M, 4.15],
                yaw_deg=0.0,
                scale=1.0,
                bbox_xz=[-0.2, 0.2, 3.95, 4.35],
                selection_source="test",
                anchor_geom_id="left_furnishing",
            )
        ],
    )

    tree_pit_mesh = scene.geometry[scene.graph["tree_pit_0"][1]]
    vertices = np.asarray(tree_pit_mesh.vertices)
    assert float(tree_pit_mesh.extents[0]) == pytest.approx(street_layout._TREE_PIT_SIZE_M)
    assert float(tree_pit_mesh.extents[2]) == pytest.approx(street_layout._TREE_PIT_SIZE_M)
    assert float(vertices[:, 2].min()) >= 4.0 + street_layout._TREE_PIT_ROAD_CLEARANCE_M - 1e-6
    assert float(vertices[:, 2].min()) < 4.15


def test_osm_curb_zone_excludes_road_endpoint_caps():
    shapely_geometry = pytest.importorskip("shapely.geometry")

    carriageway = shapely_geometry.box(-5.0, -1.0, 5.0, 1.0)
    elevated_side_zone = shapely_geometry.box(-5.0, 1.0, 5.0, 3.0).union(
        shapely_geometry.box(-5.0, -3.0, 5.0, -1.0)
    )

    curb_zone = street_layout._build_curb_boundary_zone(carriageway, elevated_side_zone, 0.2)

    assert curb_zone.intersection(shapely_geometry.box(-4.5, 1.0, 4.5, 1.22)).area > 1.0
    assert curb_zone.intersection(shapely_geometry.box(-4.5, -1.22, 4.5, -1.0)).area > 1.0
    assert curb_zone.intersection(shapely_geometry.box(5.01, -0.9, 5.22, 0.9)).area == pytest.approx(0.0)
    assert curb_zone.intersection(shapely_geometry.box(-5.22, -0.9, -5.01, 0.9)).area == pytest.approx(0.0)


def test_osm_curb_uses_normalized_junction_vehicle_surfaces():
    trimesh = pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    road_arm = shapely_geometry.box(-5.0, -1.0, 0.0, 1.0)
    junction_carriageway = shapely_geometry.box(0.0, -1.0, 2.0, 1.0)
    sidewalk_zone = shapely_geometry.box(-5.0, 1.0, 0.0, 3.0)
    junction_sidewalk = shapely_geometry.box(0.0, 1.0, 2.0, 3.0)
    placement_ctx = SimpleNamespace(
        carriageway=road_arm,
        sidewalk_zone=sidewalk_zone,
        road_arm_geometries=[road_arm],
        junction_geometries=[
            {
                "normalized_surface_patches": [
                    {"surface_role": "carriageway", "geometry": junction_carriageway},
                    {"surface_role": "sidewalk", "geometry": junction_sidewalk},
                ]
            }
        ],
        strip_zones={},
    )

    scene = street_layout._build_osm_base_scene(placement_ctx)
    curb_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("curb_")
    ]

    assert curb_meshes
    assert max(float(mesh.bounds[1][0]) for mesh in curb_meshes) > 1.8


def test_osm_curb_and_sidewalk_own_disjoint_top_surfaces():
    trimesh = pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")
    shapely_ops = pytest.importorskip("shapely.ops")

    carriageway = shapely_geometry.box(-10.0, -2.0, 10.0, 2.0)
    sidewalk_zone = shapely_geometry.box(-10.0, 2.0, 10.0, 5.0).union(
        shapely_geometry.box(-10.0, -5.0, 10.0, -2.0)
    )
    placement_ctx = SimpleNamespace(
        carriageway=carriageway,
        sidewalk_zone=sidewalk_zone,
        road_arm_geometries=[carriageway],
        junction_geometries=[],
        strip_zones={},
    )

    scene = street_layout._build_osm_base_scene(placement_ctx)

    def projected_top(prefix):
        polygons = []
        for node_name in scene.graph.nodes_geometry:
            if not str(node_name).startswith(prefix):
                continue
            transform, geometry_name = scene.graph[node_name]
            mesh = scene.geometry[geometry_name].copy()
            mesh.apply_transform(transform)
            top_y = float(mesh.vertices[:, 1].max())
            for face in mesh.faces:
                vertices = mesh.vertices[face]
                if float(np.max(np.abs(vertices[:, 1] - top_y))) > 1e-6:
                    continue
                polygon = shapely_geometry.Polygon([(float(x), float(z)) for x, _y, z in vertices])
                if polygon.is_valid and polygon.area > 1e-10:
                    polygons.append(polygon)
        return shapely_ops.unary_union(polygons)

    curb_top = projected_top("curb_")
    sidewalk_top = projected_top("sidewalk_")
    assert curb_top.area > 0.0
    assert sidewalk_top.area > 0.0
    assert curb_top.intersection(sidewalk_top).area <= 1e-4
    assert placement_ctx.surface_geometry_qa["curb_sidewalk_overlap_area_m2"] <= 1e-4
    assert placement_ctx.surface_geometry_qa["mesh_boundary_clearance_m"] == pytest.approx(0.0)
    assert placement_ctx.surface_geometry_qa["needle_top_face_count"] == 0
    assert placement_ctx.surface_geometry_qa["short_boundary_edge_count"] == 0
    assert placement_ctx.surface_geometry_qa["road_junction_seam_gap_area_m2"] <= 1e-4
    assert placement_ctx.surface_geometry_qa["context_ground_exposure_inside_row_m2"] <= 1e-4
    assert placement_ctx.surface_geometry_qa["rendered_surface_uncovered_area_m2"] <= 1e-4
    assert max(float(mesh.bounds[1][1]) for mesh in [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("curb_")
    ]) == pytest.approx(street_layout.DEFAULT_CURB_REVEAL_M)


def test_osm_base_scene_renders_bus_bay_surface_and_markings():
    pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")
    import trimesh

    bus_bay = shapely_geometry.Polygon(
        [
            (0.0, -6.6),
            (40.0, -6.6),
            (32.0, -8.6),
            (8.0, -8.6),
            (0.0, -6.6),
        ]
    )
    transit_pad = shapely_geometry.box(8.0, -10.2, 32.0, -8.6)
    placement_ctx = SimpleNamespace(
        carriageway=shapely_geometry.box(0.0, -6.6, 40.0, 6.6),
        sidewalk_zone=shapely_geometry.box(0.0, -10.2, 40.0, -6.6),
        road_arm_geometries=[],
        junction_geometries=[],
        strip_zones={},
        carriageway_width_m=13.2,
        road_reference=SimpleNamespace(coords=[(0.0, 0.0), (40.0, 0.0)], width_m=13.2, highway_type="primary"),
        detailed_strip_profiles=[
            {"side": "center", "kind": "drive_lane", "inner_m": -6.6, "outer_m": -3.3},
            {"side": "center", "kind": "drive_lane", "inner_m": -3.3, "outer_m": 0.0},
            {"side": "center", "kind": "drive_lane", "inner_m": 0.0, "outer_m": 3.3},
            {"side": "center", "kind": "drive_lane", "inner_m": 3.3, "outer_m": 6.6},
        ],
        surface_annotations=[
            {
                "surface_id": "bus_bay",
                "kind": "bus_lane_widening",
                "surface_role": "bus_lane",
                "geometry": bus_bay,
                "material": {"preset": "bus_lane_green"},
            },
            {
                "surface_id": "transit_pad",
                "kind": "transit_pad",
                "surface_role": "transit_pad",
                "geometry": transit_pad,
                "material": {"preset": "transit_pad_plaza"},
            },
        ],
    )

    try:
        trimesh.creation.extrude_polygon(shapely_geometry.Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]), 0.01)
    except ValueError as exc:
        if "No available triangulation engine" in str(exc):
            pytest.skip("Trimesh triangulation engine is unavailable in this environment")
        raise

    tracker = create_scene_texture_tracker("solid_color_legacy")
    scene = street_layout._build_osm_base_scene(
        placement_ctx,
        config=SimpleNamespace(urban_lane_edge_mode="always"),
        texture_mode="solid_color_legacy",
        texture_tracker=tracker,
    )

    node_names = {str(name) for name in scene.graph.nodes_geometry}
    vehicle_surface_zone = placement_ctx.carriageway.union(bus_bay)

    assert any(name.startswith("surface_annotation_bus_bay") for name in node_names)
    assert any(name.startswith("surface_annotation_bus_bay_marking") for name in node_names)
    assert not any(name.startswith("surface_annotation_transit_pad") for name in node_names)

    def _vertices_for(prefix: str) -> np.ndarray:
        meshes = [
            scene.geometry[scene.graph[node_name][1]]
            for node_name in scene.graph.nodes_geometry
            if str(node_name).startswith(prefix)
        ]
        if not meshes:
            return np.empty((0, 3))
        return np.vstack([np.asarray(mesh.vertices) for mesh in meshes if len(mesh.vertices)])

    bus_bay_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("surface_annotation_bus_bay_")
        and not str(node_name).startswith("surface_annotation_bus_bay_marking")
    ]
    bus_bay_vertices = np.vstack([np.asarray(mesh.vertices) for mesh in bus_bay_meshes if len(mesh.vertices)])
    assert bus_bay_vertices.size
    assert float(bus_bay_vertices[:, 1].min()) == pytest.approx(street_layout.BUS_BAY_SURFACE_TOP_Y_M)
    assert float(bus_bay_vertices[:, 1].max()) == pytest.approx(street_layout.BUS_BAY_SURFACE_TOP_Y_M)
    assert float(bus_bay_vertices[:, 0].min()) == pytest.approx(bus_bay.bounds[0])
    assert float(bus_bay_vertices[:, 2].min()) == pytest.approx(bus_bay.bounds[1])
    assert float(bus_bay_vertices[:, 0].max()) == pytest.approx(bus_bay.bounds[2])
    assert float(bus_bay_vertices[:, 2].max()) == pytest.approx(bus_bay.bounds[3])
    bus_bay_color = np.asarray(bus_bay_meshes[0].visual.material.baseColorFactor, dtype=float)
    if float(bus_bay_color.max()) <= 1.0:
        bus_bay_color = bus_bay_color * 255.0
    assert bus_bay_color[:4] == pytest.approx((65, 68, 72, 255))
    assert tracker.surface_role_counts.get("bus_lane", 0) == 0
    carriageway_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("carriageway")
    ]
    assert carriageway_meshes
    bus_bay_connection_zone = bus_bay.buffer(0.02)
    for mesh in carriageway_meshes:
        vertices = np.asarray(mesh.vertices)
        if not len(vertices):
            continue
        mesh_plan_bounds = shapely_geometry.box(
            float(vertices[:, 0].min()),
            float(vertices[:, 2].min()),
            float(vertices[:, 0].max()),
            float(vertices[:, 2].max()),
        )
        if mesh_plan_bounds.intersects(bus_bay_connection_zone):
            assert np.all(np.asarray(mesh.face_normals)[:, 1] > 0.5)
    sidewalk_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("sidewalk_")
    ]
    assert sidewalk_meshes
    assert any(str(name).startswith("sidewalk_sidewall_") for name in node_names)
    sidewalk_top_vertices = np.vstack(
        [
            np.asarray(scene.geometry[scene.graph[node_name][1]].vertices)
            for node_name in scene.graph.nodes_geometry
            if str(node_name).startswith("sidewalk_")
            and not str(node_name).startswith("sidewalk_sidewall_")
            and len(scene.geometry[scene.graph[node_name][1]].vertices)
        ]
    )
    if sidewalk_top_vertices.size:
        vehicle_surface_interior = vehicle_surface_zone.buffer(-0.01)
        assert not any(
            vehicle_surface_interior.covers(shapely_geometry.Point(float(vertex[0]), float(vertex[2])))
            for vertex in sidewalk_top_vertices
        )

    marking_vertices = _vertices_for("surface_annotation_bus_bay_marking")
    assert marking_vertices.size
    marking_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("surface_annotation_bus_bay_marking")
    ]
    assert len(marking_meshes) >= 3
    marking_plan_extents = [max(float(mesh.extents[0]), float(mesh.extents[2])) for mesh in marking_meshes]
    assert max(marking_plan_extents) <= 6.2
    assert min(marking_plan_extents) > 0.3
    lane_edge_vertices = _vertices_for("lane_edge_")
    assert lane_edge_vertices.size
    bus_bay_exclusion = bus_bay.buffer(0.02)
    assert not any(
        bus_bay_exclusion.covers(shapely_geometry.Point(float(vertex[0]), float(vertex[2])))
        for vertex in lane_edge_vertices
    )
    center_span = (marking_vertices[:, 0] > 10.0) & (marking_vertices[:, 0] < 30.0)
    old_edge_marking = center_span & (marking_vertices[:, 2] > -6.75) & (marking_vertices[:, 2] < -6.45)
    moved_edge_marking = (
        (marking_vertices[:, 0] > 7.5)
        & (marking_vertices[:, 0] < 32.5)
        & (marking_vertices[:, 2] > -8.75)
        & (marking_vertices[:, 2] < -8.45)
    )
    assert np.any(old_edge_marking)
    assert not np.any(moved_edge_marking)

    curb_vertices = _vertices_for("curb_")
    if curb_vertices.size:
        curb_center_span = (curb_vertices[:, 0] > 10.0) & (curb_vertices[:, 0] < 30.0)
        old_edge_curb = curb_center_span & (curb_vertices[:, 2] > -6.75) & (curb_vertices[:, 2] < -6.45)
        moved_edge_curb = (
            (curb_vertices[:, 0] > 7.5)
            & (curb_vertices[:, 0] < 32.5)
            & (curb_vertices[:, 2] > -8.75)
            & (curb_vertices[:, 2] < -8.45)
        )
        assert not np.any(old_edge_curb)
        assert np.any(moved_edge_curb)
        curb_points = [
            shapely_geometry.Point(float(vertex[0]), float(vertex[2]))
            for vertex in curb_vertices
        ]
        left_taper = shapely_geometry.LineString([(0.0, -6.6), (8.0, -8.6)])
        right_taper = shapely_geometry.LineString([(40.0, -6.6), (32.0, -8.6)])
        assert any(left_taper.buffer(0.18).covers(point) for point in curb_points)
        assert any(right_taper.buffer(0.18).covers(point) for point in curb_points)


def test_road_reference_markings_stay_on_matching_road_arm_surface():
    pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")
    import trimesh

    full_coords = [(0.0, 0.0), (60.0, -36.0)]
    allowed_coords = [(0.0, 0.0), (32.0, -19.2)]
    full_length = float(np.hypot(60.0, -36.0))
    allowed_length = float(np.hypot(32.0, -19.2))
    road_width = 13.2
    allowed_arm = shapely_geometry.LineString(allowed_coords).buffer(road_width * 0.5, cap_style="flat")
    sidewalk_zone = allowed_arm.buffer(3.0).difference(allowed_arm)
    placement_ctx = SimpleNamespace(
        carriageway=allowed_arm,
        sidewalk_zone=sidewalk_zone,
        road_arm_geometries=[allowed_arm],
        junction_geometries=[],
        strip_zones={},
        carriageway_width_m=road_width,
        road_reference=SimpleNamespace(coords=full_coords, width_m=road_width, highway_type="primary"),
        road_references=[SimpleNamespace(coords=full_coords, width_m=road_width, highway_type="primary")],
        detailed_strip_profiles=[
            {"side": "center", "kind": "drive_lane", "inner_m": -6.6, "outer_m": -3.3},
            {"side": "center", "kind": "drive_lane", "inner_m": -3.3, "outer_m": 0.0},
            {"side": "center", "kind": "drive_lane", "inner_m": 0.0, "outer_m": 3.3},
            {"side": "center", "kind": "drive_lane", "inner_m": 3.3, "outer_m": 6.6},
        ],
        surface_annotations=[],
    )

    try:
        trimesh.creation.extrude_polygon(
            shapely_geometry.Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]),
            0.01,
        )
    except ValueError as exc:
        if "No available triangulation engine" in str(exc):
            pytest.skip("Trimesh triangulation engine is unavailable in this environment")
        raise

    scene = street_layout._build_osm_base_scene(
        placement_ctx,
        texture_mode="solid_color_legacy",
        texture_tracker=create_scene_texture_tracker("solid_color_legacy"),
    )
    marking_centers = []
    marking_axes = []
    for node_name in scene.graph.nodes_geometry:
        name = str(node_name)
        if not (name.startswith("centerline_mark_0") or name.startswith("lane_edge_0")):
            continue
        vertices = np.asarray(scene.geometry[scene.graph[node_name][1]].vertices)
        if len(vertices):
            xz_vertices = vertices[:, [0, 2]]
            marking_centers.append(xz_vertices.mean(axis=0))
            centered_vertices = xz_vertices - xz_vertices.mean(axis=0)
            covariance = centered_vertices.T @ centered_vertices
            eigen_values, eigen_vectors = np.linalg.eigh(covariance)
            marking_axes.append(eigen_vectors[:, int(np.argmax(eigen_values))])

    assert marking_centers
    forward = np.array([60.0 / full_length, -36.0 / full_length], dtype=float)
    projections = [float(np.dot(center, forward)) for center in marking_centers]
    assert max(projections) <= allowed_length + 1.5
    assert all(abs(float(np.dot(axis, forward))) > 0.95 for axis in marking_axes)
    assert all(
        allowed_arm.buffer(0.25).covers(shapely_geometry.Point(float(center[0]), float(center[1])))
        for center in marking_centers
    )


def test_structure_lane_markings_have_visible_geometry():
    trimesh = pytest.importorskip("trimesh")

    scene = trimesh.Scene()
    street_layout._add_centerline_markings(
        scene,
        road_length_m=36.0,
        road_width_m=12.4,
        road_center_x_m=0.0,
        road_center_z_m=0.0,
        road_yaw_deg=0.0,
        lane_count=4,
        color=(245, 245, 245, 255),
        roughness=0.30,
        texture_mode="solid_color_legacy",
    )
    street_layout._add_lane_edge_markings(
        scene,
        road_length_m=36.0,
        road_center_x_m=0.0,
        road_center_z_m=0.0,
        road_yaw_deg=0.0,
        detailed_strip_profiles=[
            {"side": "center", "kind": "drive_lane", "inner_m": -6.2, "outer_m": -3.1},
            {"side": "center", "kind": "drive_lane", "inner_m": -3.1, "outer_m": 0.0},
            {"side": "center", "kind": "drive_lane", "inner_m": 0.0, "outer_m": 3.1},
            {"side": "center", "kind": "drive_lane", "inner_m": 3.1, "outer_m": 6.2},
        ],
        edge_color=(230, 200, 50, 255),
        roughness=0.30,
        texture_mode="solid_color_legacy",
    )

    def _meshes_for(prefix: str) -> list:
        return [
            scene.geometry[scene.graph[node_name][1]]
            for node_name in scene.graph.nodes_geometry
            if str(node_name).startswith(prefix)
        ]

    lane_mark_meshes = _meshes_for("centerline_mark")
    lane_edge_meshes = _meshes_for("lane_edge")
    assert lane_mark_meshes
    assert lane_edge_meshes
    assert min(float(mesh.bounds[0][1]) for mesh in lane_mark_meshes) == pytest.approx(street_layout.LANE_MARK_Y_MIN_M)
    assert min(float(mesh.bounds[0][1]) for mesh in lane_edge_meshes) == pytest.approx(street_layout.LANE_EDGE_MARK_Y_MIN_M)
    assert max(float(mesh.extents[2]) for mesh in lane_mark_meshes) == pytest.approx(street_layout.LANE_MARK_WIDTH_M)
    assert max(float(mesh.extents[2]) for mesh in lane_edge_meshes) == pytest.approx(street_layout.LANE_EDGE_MARK_WIDTH_M)


def test_drive_lane_internal_offsets_skip_median_boundaries():
    profiles = [
        {"side": "center", "kind": "drive_lane", "inner_m": -6.25, "outer_m": -3.25},
        {"side": "center", "kind": "drive_lane", "inner_m": -3.25, "outer_m": -0.25},
        {"side": "center", "kind": "median", "inner_m": -0.25, "outer_m": 0.25},
        {"side": "center", "kind": "drive_lane", "inner_m": 0.25, "outer_m": 3.25},
        {"side": "center", "kind": "drive_lane", "inner_m": 3.25, "outer_m": 6.25},
    ]

    assert street_layout._drive_lane_boundary_offsets(profiles) == [-6.25, -3.25, -0.25, 0.25, 3.25, 6.25]
    assert street_layout._drive_lane_internal_offsets(profiles) == [-3.25, 3.25]


def test_crossing_like_surface_annotation_clips_to_carriageway_and_marks_top():
    pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    placement_ctx = SimpleNamespace(
        carriageway=shapely_geometry.box(0.0, -6.0, 24.0, 6.0),
        sidewalk_zone=shapely_geometry.box(0.0, -10.0, 24.0, 10.0),
        road_arm_geometries=[],
        junction_geometries=[],
        strip_zones={},
        surface_annotations=[
            {
                "surface_id": "school_crossing",
                "kind": "colored_pavement",
                "surface_role": "colored_pavement",
                "label": "学校门前彩色过街",
                "geometry": shapely_geometry.box(6.0, -9.0, 18.0, 9.0),
                "material": {"preset": "raised_crossing_warm"},
            },
            {
                "surface_id": "school_crossing_refuge",
                "kind": "safety_island",
                "surface_role": "safety_island",
                "label": "学校过街安全岛",
                "geometry": shapely_geometry.box(10.0, -0.6, 14.0, 0.6),
                "material": {"preset": "safety_island_concrete"},
            },
        ],
    )

    scene = street_layout._build_osm_base_scene(placement_ctx)

    def _vertices_for(prefix: str) -> np.ndarray:
        meshes = [
            scene.geometry[scene.graph[node_name][1]]
            for node_name in scene.graph.nodes_geometry
            if str(node_name).startswith(prefix)
        ]
        if not meshes:
            return np.empty((0, 3))
        return np.vstack([np.asarray(mesh.vertices) for mesh in meshes if len(mesh.vertices)])

    crossing_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("surface_annotation_school_crossing_")
        and not str(node_name).startswith("surface_annotation_school_crossing_refuge")
    ]
    crossing_vertices = np.vstack([np.asarray(mesh.vertices) for mesh in crossing_meshes if len(mesh.vertices)])
    assert crossing_vertices.size
    assert float(crossing_vertices[:, 2].min()) == pytest.approx(-6.0)
    assert float(crossing_vertices[:, 2].max()) == pytest.approx(6.0)
    assert float(crossing_vertices[:, 1].max()) == pytest.approx(street_layout.SURFACE_CROSSING_TOP_Y_M)

    stripe_vertices = _vertices_for("surface_annotation_crossing_marking_school_crossing")
    assert stripe_vertices.size
    assert float(stripe_vertices[:, 1].max()) == pytest.approx(street_layout.CROSSING_STRIPE_TOP_Y_M)

    refuge_vertices = _vertices_for("surface_annotation_school_crossing_refuge")
    assert refuge_vertices.size
    assert float(refuge_vertices[:, 1].max()) == pytest.approx(street_layout.CENTER_ISLAND_TOP_Y_M)


def test_sidewalk_render_zone_is_clipped_out_of_carriageway():
    pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    placement_ctx = SimpleNamespace(
        carriageway=shapely_geometry.box(-5.0, -1.0, 5.0, 1.0),
        sidewalk_zone=shapely_geometry.box(-2.0, -2.0, 2.0, 2.0),
        road_arm_geometries=[],
        junction_geometries=[],
        strip_zones={},
        surface_annotations=[],
    )

    scene = street_layout._build_osm_base_scene(placement_ctx)
    sidewalk_vertices = np.vstack([
        np.asarray(scene.geometry[scene.graph[node_name][1]].vertices)
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("sidewalk_")
    ])

    assert sidewalk_vertices.size
    inside_carriageway = (
        (sidewalk_vertices[:, 0] > -1.9)
        & (sidewalk_vertices[:, 0] < 1.9)
        & (sidewalk_vertices[:, 2] > -0.9)
        & (sidewalk_vertices[:, 2] < 0.9)
    )
    assert not np.any(inside_carriageway)


def test_center_median_renders_as_yellow_lane_mark_fill():
    pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    placement_ctx = SimpleNamespace(
        carriageway=shapely_geometry.box(-5.0, -2.0, 5.0, 2.0),
        sidewalk_zone=shapely_geometry.MultiPolygon(),
        road_arm_geometries=[],
        junction_geometries=[],
        strip_zones={"center_median": shapely_geometry.box(-5.0, -0.25, 5.0, 0.25)},
        surface_annotations=[],
    )

    scene = street_layout._build_osm_base_scene(
        placement_ctx,
        texture_mode="solid_color_legacy",
    )
    center_median_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("center_median_")
    ]
    center_median_vertices = np.vstack([np.asarray(mesh.vertices) for mesh in center_median_meshes])

    assert center_median_vertices.size
    material_color = np.asarray(center_median_meshes[0].visual.material.baseColorFactor, dtype=float)
    assert material_color[:4] == pytest.approx(street_layout.CENTER_PAINTED_MEDIAN_COLOR)
    assert float(center_median_vertices[:, 1].min()) == pytest.approx(street_layout.LANE_MARK_Y_MIN_M)
    assert float(center_median_vertices[:, 1].max()) == pytest.approx(street_layout.CENTER_PAINTED_MEDIAN_TOP_Y_M)
    assert float(center_median_vertices[:, 1].max()) < street_layout.CENTER_ISLAND_TOP_Y_M


def test_center_median_marking_stops_at_junction_surface():
    pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    junction_vehicle = shapely_geometry.box(-1.0, -2.0, 1.0, 2.0)
    placement_ctx = SimpleNamespace(
        carriageway=shapely_geometry.box(-8.0, -2.0, 8.0, 2.0),
        sidewalk_zone=shapely_geometry.MultiPolygon(),
        road_arm_geometries=[],
        junction_geometries=[
            {
                "normalized_surface_patches": [
                    {"surface_role": "carriageway", "geometry": junction_vehicle},
                ]
            }
        ],
        strip_zones={"center_median": shapely_geometry.box(-8.0, -0.25, 8.0, 0.25)},
        surface_annotations=[],
    )

    scene = street_layout._build_osm_base_scene(
        placement_ctx,
        texture_mode="solid_color_legacy",
    )
    center_median_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("center_median_")
    ]

    assert len(center_median_meshes) >= 2
    for mesh in center_median_meshes:
        min_x = float(mesh.bounds[0][0])
        max_x = float(mesh.bounds[1][0])
        assert max_x <= -1.30 or min_x >= 1.30


def test_center_median_suppresses_overlapping_centerline_dashes_only():
    pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    placement_ctx = SimpleNamespace(
        carriageway=shapely_geometry.box(-20.0, -6.2, 20.0, 6.2),
        sidewalk_zone=shapely_geometry.MultiPolygon(),
        road_arm_geometries=[],
        junction_geometries=[],
        strip_zones={"center_median": shapely_geometry.box(-20.0, -0.25, 20.0, 0.25)},
        surface_annotations=[],
        carriageway_width_m=12.4,
        detailed_strip_profiles=[
            {"side": "center", "kind": "drive_lane", "inner_m": -6.2, "outer_m": -3.1},
            {"side": "center", "kind": "drive_lane", "inner_m": -3.1, "outer_m": 0.0},
            {"side": "center", "kind": "drive_lane", "inner_m": 0.0, "outer_m": 3.1},
            {"side": "center", "kind": "drive_lane", "inner_m": 3.1, "outer_m": 6.2},
        ],
    )

    scene = street_layout._build_osm_base_scene(
        placement_ctx,
        texture_mode="solid_color_legacy",
    )
    centerline_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("centerline_mark_")
    ]

    assert centerline_meshes
    assert not any(
        float(mesh.bounds[0][2]) < 0.25 and float(mesh.bounds[1][2]) > -0.25
        for mesh in centerline_meshes
    )
    assert any(float(mesh.bounds[0][2]) < -2.9 and float(mesh.bounds[1][2]) > -3.3 for mesh in centerline_meshes)
    assert any(float(mesh.bounds[0][2]) < 3.3 and float(mesh.bounds[1][2]) > 2.9 for mesh in centerline_meshes)


def test_surface_annotation_transit_pad_derives_required_bus_stop_slot():
    shapely_geometry = pytest.importorskip("shapely.geometry")

    placement_ctx = SimpleNamespace(
        surface_annotations=[
            {
                "surface_id": "scenario_03_bus_lane_widening",
                "kind": "bus_lane_widening",
                "surface_role": "bus_lane",
                "centerline_id": "main_axis",
                "station_start_m": 72.0,
                "station_end_m": 112.0,
                "lateral_start_m": -8.6,
                "lateral_end_m": -6.6,
                "geometry": shapely_geometry.Polygon(
                    [
                        (0.0, -6.6),
                        (40.0, -6.6),
                        (32.0, -8.6),
                        (8.0, -8.6),
                        (0.0, -6.6),
                    ]
                ),
            },
            {
                "surface_id": "scenario_03_transit_pad",
                "kind": "transit_pad",
                "surface_role": "transit_pad",
                "centerline_id": "main_axis",
                "station_start_m": 80.0,
                "station_end_m": 104.0,
                "lateral_start_m": -10.2,
                "lateral_end_m": -8.6,
                "geometry": shapely_geometry.box(8.0, -10.2, 32.0, -8.6),
            },
        ],
    )

    slots = street_layout._surface_annotation_bus_stop_slot_plans(
        placement_ctx=placement_ctx,
        theme_segments=[],
        road_segment_graph=None,
    )

    assert len(slots) == 1
    slot = slots[0]
    assert slot.category == "bus_stop"
    assert slot.required is True
    assert slot.anchor_poi_type == "bus_stop"
    assert slot.side == "right"
    assert slot.anchor_position_xz is not None
    assert shapely_geometry.Point(slot.anchor_position_xz).within(placement_ctx.surface_annotations[1]["geometry"])


def test_osm_normalized_crosswalk_uses_preserved_horizontal_axes():
    pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    placement_ctx = SimpleNamespace(
        carriageway=shapely_geometry.Polygon(),
        sidewalk_zone=shapely_geometry.Polygon(),
        road_arm_geometries=[],
        junction_geometries=[
            {
                "normalized_surface_patches": [
                    {
                        "surface_role": "crossing",
                        "geometry": shapely_geometry.box(0.0, 0.0, 4.0, 10.0),
                        "horizontal_axes": [[1.0, 0.0], [0.0, 1.0]],
                    }
                ]
            }
        ],
        strip_zones={},
    )

    scene = street_layout._build_osm_base_scene(placement_ctx)
    stripe_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("junction_normalized_crossing_")
    ]

    assert stripe_meshes
    for mesh in stripe_meshes:
        x_extent = float(mesh.bounds[1][0] - mesh.bounds[0][0])
        z_extent = float(mesh.bounds[1][2] - mesh.bounds[0][2])
        assert x_extent == pytest.approx(4.0, abs=0.05)
        assert z_extent <= 0.7


def test_osm_center_grass_belt_renders_as_flowerbed():
    pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    carriageway = shapely_geometry.box(-8.0, -3.0, 8.0, 3.0)
    grass_belt = shapely_geometry.box(-8.0, -0.62, 8.0, 0.62)
    placement_ctx = SimpleNamespace(
        carriageway=carriageway,
        sidewalk_zone=shapely_geometry.Polygon(),
        road_arm_geometries=[carriageway],
        junction_geometries=[],
        strip_zones={"center_grass_belt": grass_belt},
    )

    scene = street_layout._build_osm_base_scene(placement_ctx)
    soil_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("center_grass_belt_soil_")
    ]
    curb_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("center_grass_belt_curb_")
    ]

    assert soil_meshes
    assert curb_meshes
    assert max(float(mesh.bounds[1][1]) for mesh in soil_meshes) == pytest.approx(
        street_layout.CENTER_PLANTING_SOIL_TOP_Y_M
    )
    assert max(float(mesh.bounds[1][1]) for mesh in curb_meshes) == pytest.approx(
        street_layout.CENTER_FLOWERBED_CURB_TOP_Y_M
    )
    assert min(float(mesh.bounds[0][1]) for mesh in soil_meshes) >= -1e-6
    assert max(float(mesh.bounds[1][2]) - float(mesh.bounds[0][2]) for mesh in soil_meshes) == pytest.approx(1.0)


def test_osm_plain_center_median_renders_as_yellow_lane_mark(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")
    import trimesh

    orig_extrude_polygon = trimesh.creation.extrude_polygon

    def _fallback_extrude_polygon(polygon, height):
        try:
            return orig_extrude_polygon(polygon, height)
        except ValueError as exc:
            if "No available triangulation engine" not in str(exc):
                raise
            min_x, min_z, max_x, max_z = polygon.bounds
            x_span = float(max(max_x - min_x, 1e-6))
            z_span = float(max(max_z - min_z, 1e-6))
            mesh = trimesh.creation.box(extents=(x_span, z_span, height))
            mesh.apply_translation(
                [
                    (min_x + max_x) / 2.0,
                    (min_z + max_z) / 2.0,
                    height / 2.0,
                ]
            )
            return mesh

    monkeypatch.setattr(trimesh.creation, "extrude_polygon", _fallback_extrude_polygon)

    carriageway = shapely_geometry.box(-8.0, -3.0, 8.0, 3.0)
    median = shapely_geometry.box(-8.0, -0.15, 8.0, 0.15)
    tracker = create_scene_texture_tracker("solid_color_legacy")
    placement_ctx = SimpleNamespace(
        carriageway=carriageway,
        sidewalk_zone=shapely_geometry.Polygon(),
        road_arm_geometries=[carriageway],
        junction_geometries=[],
        strip_zones={"center_median": median},
    )

    scene = street_layout._build_osm_base_scene(
        placement_ctx,
        texture_mode="solid_color_legacy",
        texture_tracker=tracker,
    )
    median_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("center_median_")
    ]

    assert median_meshes
    assert max(float(mesh.bounds[1][1]) for mesh in median_meshes) == pytest.approx(
        street_layout.CENTER_PAINTED_MEDIAN_TOP_Y_M
    )
    material_color = np.asarray(median_meshes[0].visual.material.baseColorFactor, dtype=float)
    assert material_color[:4] == pytest.approx(street_layout.CENTER_PAINTED_MEDIAN_COLOR)
    assert tracker.surface_role_counts.get("lane_mark", 0) >= 1
    assert tracker.surface_role_counts.get("safety_island", 0) == 0
    assert tracker.surface_role_counts.get("median_green", 0) == 0


def test_osm_center_grass_belt_flowerbed_narrow_fallback():
    pytest.importorskip("trimesh")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    carriageway = shapely_geometry.box(-8.0, -3.0, 8.0, 3.0)
    grass_belt = shapely_geometry.box(-8.0, -0.08, 8.0, 0.08)
    placement_ctx = SimpleNamespace(
        carriageway=carriageway,
        sidewalk_zone=shapely_geometry.Polygon(),
        road_arm_geometries=[carriageway],
        junction_geometries=[],
        strip_zones={"center_grass_belt": grass_belt},
    )

    scene = street_layout._build_osm_base_scene(placement_ctx)
    soil_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("center_grass_belt_soil_")
    ]

    assert soil_meshes
    assert max(float(mesh.bounds[1][1]) for mesh in soil_meshes) == pytest.approx(
        street_layout.CENTER_PLANTING_SOIL_TOP_Y_M
    )


def test_base_scene_center_grass_belt_uses_flowerbed_parts():
    pytest.importorskip("trimesh")

    scene = street_layout._build_base_scene(
        length_m=40.0,
        road_width_m=8.0,
        left_side_width_m=2.5,
        right_side_width_m=2.5,
        street_program=SimpleNamespace(
            bands=(
                SimpleNamespace(
                    name="center_grass_belt",
                    kind="grass_belt",
                    side="center",
                    width_m=1.24,
                    z_center_m=0.0,
                ),
            )
        ),
    )

    soil_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("center_grass_belt_soil")
    ]
    curb_meshes = [
        scene.geometry[scene.graph[node_name][1]]
        for node_name in scene.graph.nodes_geometry
        if str(node_name).startswith("center_grass_belt_curb")
    ]

    assert soil_meshes
    assert len(curb_meshes) == 2
    assert max(float(mesh.bounds[1][1]) for mesh in soil_meshes) == pytest.approx(
        street_layout.CENTER_PLANTING_SOIL_TOP_Y_M
    )
    assert max(float(mesh.bounds[1][1]) for mesh in curb_meshes) == pytest.approx(
        street_layout.CENTER_FLOWERBED_CURB_TOP_Y_M
    )
    assert max(float(mesh.bounds[1][2]) - float(mesh.bounds[0][2]) for mesh in soil_meshes) == pytest.approx(1.0)


def test_base_scene_plain_median_uses_neutral_surface_role():
    pytest.importorskip("trimesh")

    tracker = create_scene_texture_tracker("solid_color_legacy")
    scene = street_layout._build_base_scene(
        length_m=40.0,
        road_width_m=8.0,
        left_side_width_m=2.5,
        right_side_width_m=2.5,
        street_program=SimpleNamespace(
            bands=(
                SimpleNamespace(
                    name="center_median",
                    kind="median",
                    side="center",
                    width_m=0.3,
                    z_center_m=0.0,
                ),
            )
        ),
        texture_mode="solid_color_legacy",
        texture_tracker=tracker,
    )

    assert any(str(node_name).startswith("road_center_median") for node_name in scene.graph.nodes_geometry)
    assert tracker.surface_role_counts.get("safety_island", 0) >= 1
    assert tracker.surface_role_counts.get("median_green", 0) == 0


def test_base_scene_adds_centerline_markings():
    pytest.importorskip("trimesh")

    scene = street_layout._build_base_scene(
        length_m=60.0,
        road_width_m=8.0,
        left_side_width_m=2.5,
        right_side_width_m=2.5,
        street_program=SimpleNamespace(lane_count=2),
    )

    node_names = set(scene.graph.nodes_geometry)
    assert any(name.startswith("centerline_mark_") for name in node_names)


def test_centerline_markings_render_for_width_based_roads():
    trimesh = pytest.importorskip("trimesh")

    scene = trimesh.Scene()
    street_layout._add_centerline_markings(
        scene,
        road_length_m=60.0,
        road_width_m=6.0,
        road_center_x_m=0.0,
        road_center_z_m=0.0,
        road_yaw_deg=0.0,
        lane_count=None,
        color=(245, 245, 245, 255),
        roughness=0.30,
    )

    node_names = set(scene.graph.nodes_geometry)
    assert any(name.startswith("centerline_mark_") for name in node_names)


def test_centerline_markings_follow_road_reference_polyline():
    trimesh = pytest.importorskip("trimesh")

    scene = trimesh.Scene()
    street_layout._add_centerline_markings(
        scene,
        road_length_m=20.0,
        road_width_m=6.0,
        road_center_x_m=0.0,
        road_center_z_m=0.0,
        road_yaw_deg=0.0,
        lane_count=None,
        road_coords=((10.0, 5.0), (20.0, 15.0)),
        color=(245, 245, 245, 255),
        roughness=0.30,
    )

    geometries = list(scene.geometry.values())
    assert geometries
    centers = [mesh.bounds.mean(axis=0) for mesh in geometries]
    assert all(center[0] > 5.0 for center in centers)
    assert all(center[2] > 0.0 for center in centers)


def test_run_street_compose_auto_selects_stable_poi_rich_road_by_seed(tmp_path: Path, monkeypatch):
    pytest.importorskip("gradio")
    def fake_compose(**kwargs):
        config = kwargs["config"]
        captured["selected_road_osm_id"] = config.selected_road_osm_id
        captured["aoi_bbox"] = config.aoi_bbox
        effective_counts = effective_counts_by_osm[int(config.selected_road_osm_id)]
        layout_path.write_text(
            json.dumps(
                {
                    "summary": {
                        "instance_count": 0,
                        "dropped_slots": 0,
                        "selected_road_effective_poi_count": sum(effective_counts.values()),
                        "spatial_context": {
                            "entrance_points_xz": [[0.0, 0.0]] * effective_counts["entrance"],
                            "bus_stop_points_xz": [[1.0, 0.0]] * effective_counts["bus_stop"],
                            "fire_points_xz": [[2.0, 0.0]] * effective_counts["fire"],
                        },
                    },
                    "placements": [],
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        return StreetComposeResult(
            query="urban street",
            instance_count=0,
            dropped_slots=0,
            placements=[],
            outputs={
                "scene_glb": str(glb_path),
                "scene_ply": "",
                "scene_layout": str(layout_path),
            },
        )

    monkeypatch.setattr(app, "compose_street_scene", fake_compose)
    monkeypatch.setattr(app, "_probe_discovered_road_effective_poi_counts", lambda row, **kwargs: effective_counts_by_osm[int(row["osm_id"])])
    summary, _rows, _layout_json, _model_path, _files = app.run_street_compose(
        dataset_profile="real",
        query="urban street",
        real_manifest_text=str(tmp_path / "real_assets_manifest.jsonl"),
        artifacts_dir_text=str(artifacts_dir),
        model_name="openai/clip-vit-base-patch32",
        model_dir_text="",
        local_files_only=True,
        device="cpu",
        street_length_m=80.0,
        street_road_width_m=8.0,
        street_sidewalk_width_m=2.5,
        street_lane_count=2,
        street_density=1.0,
        street_seed=0,
        street_topk_per_category=20,
        street_max_trials_per_slot=30,
        export_format="glb",
        m5_layout_mode="osm",
        m5_constraint_mode="off",
        m5_bbox_min_lon=113.2660,
        m5_bbox_min_lat=23.1280,
        m5_bbox_max_lon=113.2710,
        m5_bbox_max_lat=23.1325,
        road_selection="primary_road",
    )

    assert captured["selected_road_osm_id"] == 201
    assert captured["aoi_bbox"] == (113.2000, 23.1000, 113.2100, 23.1100)
    assert "selected_road_osm_id: 201" in summary
    assert "selected_road_discovered_poi_count: 2" in summary
    assert "selected_road_effective_poi_count: 2" in summary

    summary_seed_0_again, *_ = app.run_street_compose(
        dataset_profile="real",
        query="urban street",
        real_manifest_text=str(tmp_path / "real_assets_manifest.jsonl"),
        artifacts_dir_text=str(artifacts_dir),
        model_name="openai/clip-vit-base-patch32",
        model_dir_text="",
        local_files_only=True,
        device="cpu",
        street_length_m=80.0,
        street_road_width_m=8.0,
        street_sidewalk_width_m=2.5,
        street_lane_count=2,
        street_density=1.0,
        street_seed=0,
        street_topk_per_category=20,
        street_max_trials_per_slot=30,
        export_format="glb",
        m5_layout_mode="osm",
        m5_constraint_mode="off",
        m5_bbox_min_lon=113.2660,
        m5_bbox_min_lat=23.1280,
        m5_bbox_max_lon=113.2710,
        m5_bbox_max_lat=23.1325,
        road_selection="primary_road",
    )
    assert "selected_road_osm_id: 201" in summary_seed_0_again

    summary_seed_1, *_ = app.run_street_compose(
        dataset_profile="real",
        query="urban street",
        real_manifest_text=str(tmp_path / "real_assets_manifest.jsonl"),
        artifacts_dir_text=str(artifacts_dir),
        model_name="openai/clip-vit-base-patch32",
        model_dir_text="",
        local_files_only=True,
        device="cpu",
        street_length_m=80.0,
        street_road_width_m=8.0,
        street_sidewalk_width_m=2.5,
        street_lane_count=2,
        street_density=1.0,
        street_seed=1,
        street_topk_per_category=20,
        street_max_trials_per_slot=30,
        export_format="glb",
        m5_layout_mode="osm",
        m5_constraint_mode="off",
        m5_bbox_min_lon=113.2660,
        m5_bbox_min_lat=23.1280,
        m5_bbox_max_lon=113.2710,
        m5_bbox_max_lat=23.1325,
        road_selection="primary_road",
    )
    assert "selected_road_osm_id: 202" in summary_seed_1
    assert "selected_road_osm_id: 999" not in summary_seed_1


def test_run_street_compose_skips_discovered_road_that_loses_poi_after_compose_filter(tmp_path: Path, monkeypatch):
    pytest.importorskip("gradio")
    def fake_compose(**kwargs):
        config = kwargs["config"]
        captured["selected_road_osm_id"] = config.selected_road_osm_id
        layout_path.write_text(
            json.dumps(
                {
                    "summary": {
                        "instance_count": 0,
                        "dropped_slots": 0,
                        "selected_road_effective_poi_count": 3,
                        "spatial_context": {
                            "entrance_points_xz": [[0.0, 0.0], [1.0, 0.0]],
                            "bus_stop_points_xz": [[2.0, 0.0]],
                            "fire_points_xz": [],
                        },
                    },
                    "placements": [],
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        return StreetComposeResult(
            query="urban street",
            instance_count=0,
            dropped_slots=0,
            placements=[],
            outputs={"scene_glb": str(glb_path), "scene_ply": "", "scene_layout": str(layout_path)},
        )

    monkeypatch.setattr(app, "compose_street_scene", fake_compose)
    monkeypatch.setattr(
        app,
        "_probe_discovered_road_effective_poi_counts",
        lambda row, **kwargs: (
            {"entrance": 0, "bus_stop": 0, "fire": 0}
            if int(row["osm_id"]) == 201
            else {"entrance": 2, "bus_stop": 1, "fire": 0}
        ),
    )

    summary, *_ = app.run_street_compose(
        dataset_profile="real",
        query="urban street",
        real_manifest_text=str(tmp_path / "real_assets_manifest.jsonl"),
        artifacts_dir_text=str(artifacts_dir),
        model_name="openai/clip-vit-base-patch32",
        model_dir_text="",
        local_files_only=True,
        device="cpu",
        street_length_m=80.0,
        street_road_width_m=8.0,
        street_sidewalk_width_m=2.5,
        street_lane_count=2,
        street_density=1.0,
        street_seed=1,
        street_topk_per_category=20,
        street_max_trials_per_slot=30,
        export_format="glb",
        m5_layout_mode="osm",
        m5_constraint_mode="off",
        m5_bbox_min_lon=113.2660,
        m5_bbox_min_lat=23.1280,
        m5_bbox_max_lon=113.2710,
        m5_bbox_max_lat=23.1325,
        road_selection="primary_road",
    )

    assert captured["selected_road_osm_id"] == 202
    assert "selected_road_osm_id: 202" in summary


def test_run_street_compose_auto_discovers_when_cached_roads_missing(tmp_path: Path, monkeypatch):
    pytest.importorskip("gradio")
    def fake_compose(**kwargs):
        config = kwargs["config"]
        captured["selected_road_osm_id"] = config.selected_road_osm_id
        captured["aoi_bbox"] = config.aoi_bbox
        effective_counts = effective_counts_by_osm[int(config.selected_road_osm_id)]
        layout_path.write_text(
            json.dumps(
                {
                    "summary": {
                        "instance_count": 0,
                        "dropped_slots": 0,
                        "selected_road_effective_poi_count": sum(effective_counts.values()),
                        "spatial_context": {
                            "entrance_points_xz": [[0.0, 0.0]] * effective_counts["entrance"],
                            "bus_stop_points_xz": [[2.0, 0.0]] * effective_counts["bus_stop"],
                            "fire_points_xz": [[3.0, 0.0]] * effective_counts["fire"],
                        },
                    },
                    "placements": [],
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        return StreetComposeResult(
            query="urban street",
            instance_count=0,
            dropped_slots=0,
            placements=[],
            outputs={"scene_glb": str(glb_path), "scene_ply": "", "scene_layout": str(layout_path)},
        )

    monkeypatch.setattr(app, "compose_street_scene", fake_compose)
    monkeypatch.setattr(
        app,
        "discover_poi_roads",
        lambda city, cache_dir: [
            DiscoveredRoad(city_name_en="adhoc", osm_id=501, highway_type="service", road_length_m=120.0, poi_count=2, poi_types={"entrance": 2}, bbox=(113.20, 23.10, 113.21, 23.11)),
            DiscoveredRoad(city_name_en="adhoc", osm_id=502, highway_type="secondary", road_length_m=150.0, poi_count=3, poi_types={"entrance": 2, "bus_stop": 1}, bbox=(113.26, 23.12, 113.27, 23.13)),
        ],
    )
    monkeypatch.setattr(
        app,
        "_probe_discovered_road_effective_poi_counts",
        lambda row, **kwargs: effective_counts_by_osm[int(row["osm_id"])],
    )

    summary, *_ = app.run_street_compose(
        dataset_profile="real",
        query="urban street",
        real_manifest_text=str(tmp_path / "real_assets_manifest.jsonl"),
        artifacts_dir_text=str(artifacts_dir),
        model_name="openai/clip-vit-base-patch32",
        model_dir_text="",
        local_files_only=True,
        device="cpu",
        street_length_m=80.0,
        street_road_width_m=8.0,
        street_sidewalk_width_m=2.5,
        street_lane_count=2,
        street_density=1.0,
        street_seed=0,
        street_topk_per_category=20,
        street_max_trials_per_slot=30,
        export_format="glb",
        m5_layout_mode="osm",
        m5_constraint_mode="off",
        m5_bbox_min_lon=113.2660,
        m5_bbox_min_lat=23.1280,
        m5_bbox_max_lon=113.2710,
        m5_bbox_max_lat=23.1325,
        road_selection="primary_road",
    )

    assert captured["selected_road_osm_id"] == 501
    assert captured["aoi_bbox"] == (113.20, 23.10, 113.21, 23.11)
    assert "road_source: auto_discovered" in summary
    assert "selected_road_discovered_poi_count: 2" in summary
    assert "selected_road_effective_poi_count: 2" in summary


def test_run_street_compose_summary_and_asset_usage_extract_show_objaverse_counts(tmp_path: Path, monkeypatch):
    pytest.importorskip("gradio")
    def fake_compose(**kwargs):
        return StreetComposeResult(
            query="urban street",
            instance_count=4,
            dropped_slots=0,
            placements=[],
            outputs={"scene_glb": str(glb_path), "scene_ply": "", "scene_layout": str(layout_path)},
        )

    monkeypatch.setattr(app, "compose_street_scene", fake_compose)

    summary, _rows, layout_json, _model_path, _files = app.run_street_compose(
        dataset_profile="real",
        query="urban street",
        real_manifest_text=str(tmp_path / "real_assets_manifest.jsonl"),
        artifacts_dir_text=str(tmp_path / "artifacts"),
        model_name="openai/clip-vit-base-patch32",
        model_dir_text="",
        local_files_only=True,
        device="cpu",
        street_length_m=80.0,
        street_road_width_m=8.0,
        street_sidewalk_width_m=2.5,
        street_lane_count=2,
        street_density=1.0,
        street_seed=0,
        street_topk_per_category=20,
        street_max_trials_per_slot=30,
        export_format="glb",
        m5_layout_mode="template",
        m5_constraint_mode="off",
    )
    usage_json, usage_rows = app._extract_asset_usage_summary(layout_json)

    assert "asset_source_counts: Objaverse=2, Parametric=1, Procedural=1" in summary
    assert "objaverse_instances: 2" in summary
    parsed_usage = json.loads(usage_json)
    assert parsed_usage["objaverse_instance_count"] == 2
    assert usage_rows[0][0] == "Objaverse"
    assert usage_rows[0][2] == 2


def test_run_street_compose_rediscover_when_cached_metadata_mismatches(tmp_path: Path, monkeypatch):
    pytest.importorskip("gradio")
    def fake_compose(**kwargs):
        config = kwargs["config"]
        layout_path.write_text(
            json.dumps(
                {
                    "summary": {
                        "instance_count": 0,
                        "dropped_slots": 0,
                        "selected_road_effective_poi_count": 2,
                        "spatial_context": {
                            "entrance_points_xz": [[0.0, 0.0], [1.0, 0.0]],
                            "bus_stop_points_xz": [],
                            "fire_points_xz": [],
                        },
                    },
                    "placements": [],
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        return StreetComposeResult(
            query="urban street",
            instance_count=0,
            dropped_slots=0,
            placements=[],
            outputs={"scene_glb": str(glb_path), "scene_ply": "", "scene_layout": str(layout_path)},
        )

    def fake_discover(city, cache_dir):
        calls["discover"] += 1
        return [
            DiscoveredRoad(city_name_en="adhoc", osm_id=502, highway_type="secondary", road_length_m=150.0, poi_count=2, poi_types={"entrance": 2}, bbox=(113.26, 23.12, 113.27, 23.13)),
        ]

    monkeypatch.setattr(app, "compose_street_scene", fake_compose)
    monkeypatch.setattr(app, "discover_poi_roads", fake_discover)
    monkeypatch.setattr(app, "_probe_discovered_road_effective_poi_counts", lambda row, **kwargs: {"entrance": 2, "bus_stop": 0, "fire": 0})

    summary, *_ = app.run_street_compose(
        dataset_profile="real",
        query="urban street",
        real_manifest_text=str(tmp_path / "real_assets_manifest.jsonl"),
        artifacts_dir_text=str(artifacts_dir),
        model_name="openai/clip-vit-base-patch32",
        model_dir_text="",
        local_files_only=True,
        device="cpu",
        street_length_m=80.0,
        street_road_width_m=8.0,
        street_sidewalk_width_m=2.5,
        street_lane_count=2,
        street_density=1.0,
        street_seed=0,
        street_topk_per_category=20,
        street_max_trials_per_slot=30,
        export_format="glb",
        m5_layout_mode="osm",
        m5_constraint_mode="off",
        m5_bbox_min_lon=113.2660,
        m5_bbox_min_lat=23.1280,
        m5_bbox_max_lon=113.2710,
        m5_bbox_max_lat=23.1325,
        road_selection="primary_road",
    )

    assert calls["discover"] == 1
    assert "selected_road_osm_id: 502" in summary


def test_run_street_compose_errors_when_auto_discovery_finds_no_poi_rich_roads(tmp_path: Path, monkeypatch):
    pytest.importorskip("gradio")
def test_street_compose_empty_category_pool_fails_cleanly(tmp_path: Path):
    pytest.importorskip("trimesh")
    mesh_path = tmp_path / "mesh.glb"
    _make_mesh(mesh_path, kind="box")
    manifest = tmp_path / "real_assets_manifest.jsonl"
    _write_manifest(
        manifest,
        [
            {
                "asset_id": "x_01",
                "category": "unknown",
                "text_desc": "unknown object",
                "mesh_path": str(mesh_path),
                "latent_path": str(tmp_path / "x_01.pt"),
                "license": "cc-by",
                "source": "test",
                "split": "train",
            }
        ],
    )
    with pytest.raises(RuntimeError, match="No supported categories found"):
        compose_street_scene(
            config=replace(_build_config(seed=1), curated_street_assets_profile="disabled"),
            manifest_path=manifest,
            artifacts_dir=tmp_path / "artifacts",
            local_files_only=True,
            device="cpu",
            out_dir=tmp_path / "artifacts",
        )


def test_softmax_weighted_sampling_prefers_high_score():
    rng = random.Random(123)
    candidates = [
        RetrievalHit(asset_id="lamp_hi", score=0.99),
        RetrievalHit(asset_id="lamp_mid", score=0.50),
        RetrievalHit(asset_id="lamp_low", score=0.10),
    ]
    asset_by_id = {
        "lamp_hi": {"asset_id": "lamp_hi", "category": "lamp"},
        "lamp_mid": {"asset_id": "lamp_mid", "category": "lamp"},
        "lamp_low": {"asset_id": "lamp_low", "category": "lamp"},
    }
    index = _UnitFakeIndexStore(candidates)
    counts = {"lamp_hi": 0, "lamp_mid": 0, "lamp_low": 0}
    for _ in range(300):
        row, _score, source = street_layout._pick_category_candidate(
            query="street",
            category="lamp",
            topk=3,
            embedder=_UnitFakeEmbedder(),
            index_store=index,
            asset_by_id=asset_by_id,
            category_pool=[asset_by_id["lamp_hi"], asset_by_id["lamp_mid"], asset_by_id["lamp_low"]],
            used_asset_ids=set(),
            rng=rng,
        )
        counts[row["asset_id"]] += 1
        assert source == "faiss_softmax"
    assert counts["lamp_hi"] > counts["lamp_mid"] > counts["lamp_low"]


def test_no_repeat_within_category_before_exhaustion():
    rng = random.Random(7)
    candidates = [
        RetrievalHit(asset_id="bench_a", score=0.9),
        RetrievalHit(asset_id="bench_b", score=0.85),
        RetrievalHit(asset_id="bench_c", score=0.8),
    ]
    asset_by_id = {hit.asset_id: {"asset_id": hit.asset_id, "category": "bench"} for hit in candidates}
    used: set[str] = set()
    picked = []
    for _ in range(3):
        row, _score, source = street_layout._pick_category_candidate(
            query="street",
            category="bench",
            topk=3,
            embedder=_UnitFakeEmbedder(),
            index_store=_UnitFakeIndexStore(candidates),
            asset_by_id=asset_by_id,
            category_pool=list(asset_by_id.values()),
            used_asset_ids=used,
            rng=rng,
        )
        picked.append(row["asset_id"])
        used.add(row["asset_id"])
        assert source == "faiss_softmax"
    assert len(set(picked)) == 3


def test_relaxed_repeat_after_exhaustion_fill_priority():
    rng = random.Random(11)
    candidates = [
        RetrievalHit(asset_id="tree_a", score=0.9),
        RetrievalHit(asset_id="tree_b", score=0.8),
    ]
    asset_by_id = {hit.asset_id: {"asset_id": hit.asset_id, "category": "tree"} for hit in candidates}
    used: set[str] = set()
    sources: list[str] = []
    for _ in range(4):
        row, _score, source = street_layout._pick_category_candidate(
            query="street",
            category="tree",
            topk=2,
            embedder=_UnitFakeEmbedder(),
            index_store=_UnitFakeIndexStore(candidates),
            asset_by_id=asset_by_id,
            category_pool=list(asset_by_id.values()),
            used_asset_ids=used,
            rng=rng,
        )
        used.add(row["asset_id"])
        sources.append(source)
    assert "faiss_relaxed_repeat" in sources
    assert len(sources) == 4


def test_scene_layout_contains_diversity_metrics(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    result = compose_street_scene(
        config=_build_config(seed=42),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )
    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    assert summary["unique_asset_count"] >= 1
    assert 0.0 <= summary["diversity_ratio"] <= 1.0
    assert isinstance(summary["per_category_unique"], dict)
    assert isinstance(summary["selection_source_counts"], dict)


def test_scene_layout_contains_parametric_provenance_counts(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    rows[0]["generator_type"] = "parametric_v1"
    rows[0]["source"] = "parametric_generated"
    rows[1]["generator_type"] = "parametric_v1"
    rows[1]["source"] = "parametric_generated"
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    result = compose_street_scene(
        config=StreetComposeConfig(
            query="modern clean urban street",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=42,
            topk_per_category=20,
            max_trials_per_slot=30,
            asset_curation_mode="parametric_first",
        ),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )
    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]

    assert summary["asset_curation_mode"] == "parametric_first"
    assert "asset_generator_type_counts" in summary
    assert int(summary["parametric_instance_count"]) == int(summary["asset_generator_type_counts"].get("parametric", 0))
    assert int(summary["parametric_instance_count"]) >= 1


def test_scene_layout_contains_asset_source_usage_summary(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    rows[0]["source"] = "objaverse_import"
    rows[0]["generator_type"] = "objaverse_v1"
    rows[1]["source"] = "objaverse_import"
    rows[1]["generator_type"] = "objaverse_v1"
    rows[2]["source"] = "parametric_generated"
    rows[2]["generator_type"] = "parametric_v1"
    rows[3]["source"] = "procedural_generated"
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    result = compose_street_scene(
        config=StreetComposeConfig(
            query="street furniture with benches lamps and bins",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=42,
            topk_per_category=20,
            max_trials_per_slot=30,
        ),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )
    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]

    assert "asset_source_counts" in summary
    assert int(summary["asset_source_counts"].get("objaverse_import", 0)) >= 1
    assert "asset_source_unique_counts" in summary
    assert isinstance(summary.get("asset_usage_by_source", []), list)
    assert any(item.get("source") == "objaverse_import" for item in summary.get("asset_usage_by_source", []))


def test_fixed_hq_profile_builds_curated_asset_allowlists(tmp_path: Path):
    rows = _build_real_rows(tmp_path / "data")
    rows.append(
        _asset_row(
            "lamp_allowlist_alt",
            "lamp",
            source="urbanverse",
            quality_tier=3,
            scene_eligible=True,
        )
    )
    rows.append(
        _asset_row(
            "003e74743d454448abf11fd78164a75d",
            "lamp",
            source="urbanverse",
            quality_tier=3,
            scene_eligible=True,
        )
    )
    for row in rows:
        if row["category"] in {"lamp", "trash", "bollard", "tree"}:
            row["quality_tier"] = 3
            row["scene_eligible"] = True
        if row["category"] == "tree":
            row["source"] = "external_import"
            row["quality_notes"] = ["tree_upright_validated", "scene_ready"]

    category_to_rows = {category: [] for category in street_layout.DEFAULT_CATEGORIES}
    for row in rows:
        category_to_rows[str(row["category"])].append(row)

    allowlists = street_layout._curated_allowlist_ids_by_category(
        category_to_rows,
        config=_build_config(seed=42),
    )

    assert set(allowlists["lamp"]) == {"lamp_modern_production", "lamp_allowlist_alt"}
    assert "003e74743d454448abf11fd78164a75d" not in allowlists["lamp"]
    assert allowlists["trash"] == ["objaverse_trash_f16b7d84113d4cba869412ee95769910"]
    assert "tree_01" in allowlists["tree"]


def test_scene_layout_contains_presentation_views_and_metrics(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("matplotlib")
    rows = _build_real_rows(tmp_path / "data")
    rows[0]["style_tags"] = ["civic", "clean", "formal"]
    rows[0]["quality_tier"] = 3
    rows[0]["hero_asset"] = True
    rows[1]["style_tags"] = ["modern", "metal", "clean"]
    rows[1]["quality_tier"] = 3
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    result = compose_street_scene(
        config=StreetComposeConfig(
            query="civic clean boulevard",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=42,
            topk_per_category=20,
            max_trials_per_slot=20,
            style_preset="civic_clean_v1",
            beauty_mode="presentation_v1",
            render_preset="jury_default_v1",
            asset_curation_mode="curated_first",
        ),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    assert summary["style_preset"] == "civic_clean_v1"
    assert summary["beauty_mode"] == "presentation_v1"
    assert summary["render_preset_used"] == "jury_default_v1"
    assert summary["final_render_style"] == "jury_default"
    assert 0.0 <= float(summary["presentation_score"]) <= 1.0
    assert 0.0 <= float(summary["style_coherence"]) <= 1.0
    assert "composition_report" in summary
    render_views = summary.get("render_views", [])
    assert len(render_views) == 6
    assert [render_views[0]["name"], render_views[1]["name"]] == [
        "final_plan_watercolor",
        "final_oblique_45_watercolor",
    ]
    for view in render_views:
        assert Path(view["path"]).exists()


def test_osm_compose_outputs_theme_segments_and_surrounding_buildings(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data", include_buildings=True)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    import roadgen3d.osm_ingest as osm_ingest

    monkeypatch.setattr(osm_ingest, "fetch_osm_data", lambda **kwargs: _build_osm_response(include_building=True))
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])
    monkeypatch.setattr(street_layout, "_load_building_manifest", lambda _path: [])

    result = compose_street_scene(
        config=_build_osm_config(tmp_path, seed=19),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    building_group = [placement for placement in result.placements if placement.placement_group == "building"]
    furniture_group = [placement for placement in result.placements if placement.placement_group == "street_furniture"]

    assert len(summary["theme_segments"]) >= 2
    assert {segment["theme_name"] for segment in summary["theme_segments"]} >= {"commercial", "transit"}
    assert payload["building_footprints"] == []
    assert payload["building_placements"]
    assert payload["generated_lots"]
    assert payload["zoning_grid"]
    assert furniture_group
    assert building_group
    assert all(placement.selection_source == "building_asset" for placement in building_group)
    assert summary["building_generation_mode"] == "grid_growth"
    assert summary["land_use_asymmetry_strength"] == pytest.approx(0.0)
    assert summary["building_front_setback_min_m"] == pytest.approx(DEFAULT_BUILDING_FRONT_SETBACK_MIN_M)
    assert summary["building_front_setback_max_m"] == pytest.approx(DEFAULT_BUILDING_FRONT_SETBACK_MAX_M)
    assert summary["zoning_granularity"] == "fine"
    assert summary["streetwall_continuity"] == pytest.approx(0.95)
    assert summary["infill_policy"] == "aggressive"
    assert summary["building_balance_policy"] == "road_clearance_balanced_sides"
    assert summary["building_balance_ok"] is True
    assert summary["frontage_balance_gap"] <= 0.10
    assert summary["buildable_frontage_by_side"]["left"] > 0.0
    assert summary["buildable_frontage_by_side"]["right"] > 0.0
    assert summary["building_land_policy"] == "road_clearance_fill_v1"
    assert summary["grass_policy"] == "continuous_non_road_underlay_v1"
    assert summary["auto_building_scaling"] is False
    assert summary["street_facade_count"] > 0
    assert summary["interior_fill_count"] > 0
    assert {lot["source"] for lot in payload["generated_lots"]} == {"road_clearance_fill"}
    assert summary["street_furniture_side_counts"]["left"] > 0
    assert summary["street_furniture_side_counts"]["right"] > 0
    assert summary["street_furniture_balance_ok"] is True
    assert summary["zoning_preview_mode"] == "parcel_first"
    assert summary["frontage_cell_count"] > len(summary["theme_segments"])
    assert summary["building_retrieval_coverage"]["footprint_count"] == 0
    assert summary["building_retrieval_coverage"]["placed_count"] == len(payload["building_placements"])
    assert summary["zoning_preview_summary"]["cell_count"] == len(payload["zoning_grid"])
    assert summary["zoning_preview_summary"]["zoning_preview_mode"] == "parcel_first"
    assert summary["zoning_preview_summary"]["frontage_cell_count"] > len(summary["theme_segments"])
    assert summary["zoning_preview_summary"]["building_buffer_gap_ratio"] <= 0.10
    assert summary["zoning_preview_summary"]["streetwall_reference_gap_ratio"] <= 0.10
    assert summary["zoning_preview_summary"]["side_land_use_counts"]["left"]
    assert summary["zoning_preview_summary"]["side_land_use_counts"]["right"]
    assert {"left_building_buffer", "left_sidewalk", "carriageway", "right_sidewalk", "right_building_buffer"} <= {
        cell["lane_role"] for cell in payload["zoning_grid"]
    }
    assert {cell["theme_name"] for cell in payload["zoning_grid"]} >= {"commercial", "transit"}
    assert all("land_use_type" in cell and "buildable" in cell and "lot_id" in cell for cell in payload["zoning_grid"])
    assert not any(cell["footprint_ids"] for cell in payload["zoning_grid"] if "building_buffer" in cell["lane_role"])
    assert summary["infill_footprint_count"] == 0
    assert summary["building_summary"]["real_footprint_count"] == 0
    assert summary["building_summary"]["infill_footprint_count"] == 0
    assert summary["door_enabled"] is True
    assert summary["door_strategy"] == "attached_3d_v1"
    assert summary["door_policy"] == "procedural_fallback_only"
    assert summary["door_count"] == 0
    assert summary["building_summary"]["door_count"] == 0
    assert summary["door_required_count"] == 0
    assert summary["door_skipped_existing_asset_count"] == len(payload["building_placements"])
    assert summary["building_summary"]["door_skipped_existing_asset_count"] == len(payload["building_placements"])
    assert summary["door_count_by_side"] == {}
    assert summary["door_missing_building_count"] == 0
    assert summary["building_summary"]["door_missing_reason_counts"] == {}
    assert summary["frontage_coverage_by_side"]["left"]["coverage_ratio"] >= 0.65
    assert summary["frontage_coverage_by_side"]["right"]["coverage_ratio"] >= 0.65
    assert summary["frontage_gap_stats_by_side"]["left"]["gap_count"] >= 0
    assert len(payload["generated_lots"]) < summary["land_use_summary"]["buildable_cell_count"]
    assert summary["building_summary"]["frontage_cell_count"] == summary["frontage_cell_count"]
    assert all(
        len(plan.get("scale_xyz", [])) == 3
        and plan["scale_xyz"][0] == pytest.approx(plan["scale_xyz"][1])
        and plan["scale_xyz"][0] == pytest.approx(plan["scale_xyz"][2])
        for plan in payload["building_placements"]
    )
    assert all(plan["asset_scale_mode"] == "building_real_preserve" for plan in payload["building_placements"])
    assert all(float(plan["scale"]) == pytest.approx(1.0) for plan in payload["building_placements"])
    for plan in payload["building_placements"]:
        assert plan["final_size_m"]["width_m"] == pytest.approx(plan["native_size_m"]["width_m"])
        assert plan["final_size_m"]["depth_m"] == pytest.approx(plan["native_size_m"]["depth_m"])
    building_bboxes = [plan["bbox_xz"] for plan in payload["building_placements"]]
    for idx, bbox in enumerate(building_bboxes):
        for other in building_bboxes[idx + 1 :]:
            assert _bbox_overlap_area_values(bbox, other) <= 0.05
    assert summary["building_summary"]["building_collision_policy"] == "reject_overlapping_aabb_v1"
    assert summary["building_asset_rejected_size_mismatch_count"] >= 0
    assert "building_asset_rejected_size_mismatch_count" in summary["asset_scale_summary"]["_diagnostics"]
    assert all(not bool(plan["door_added"]) for plan in payload["building_placements"])
    assert all(plan["door_missing_reason"] == "real_building_asset_has_native_door" for plan in payload["building_placements"])


def test_osm_scene_layout_contains_cumulative_production_steps(tmp_path: Path, monkeypatch):
    trimesh = pytest.importorskip("trimesh")
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")
    pytest.importorskip("matplotlib")

    rows = _build_real_rows(tmp_path / "data", include_buildings=True)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    import roadgen3d.osm_ingest as osm_ingest

    monkeypatch.setattr(
        osm_ingest,
        "fetch_osm_data",
        lambda **kwargs: _build_osm_response(include_building=True, include_bus_stop=True, include_fire_hydrant=True),
    )
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])

    result = compose_street_scene(
        config=_build_osm_config(tmp_path, seed=19),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    steps = payload["production_steps"]
    summary = payload["summary"]
    step_by_id = {step["step_id"]: step for step in steps}
    building_ids = {
        placement.instance_id
        for placement in result.placements
        if placement.placement_group == "building"
    }

    assert [step["step_id"] for step in steps] == [
        "road_base",
        "land_use_zoning",
        "buildings",
        "poi_context",
        "furniture_anchor",
        "furniture_required",
        "furniture_optional",
        "scene_preview",
    ]
    assert int(summary["production_step_count"]) == 8
    assert summary["final_production_step_id"] == "scene_preview"
    assert summary["production_step_ids"] == [step["step_id"] for step in steps]
    assert summary["scene_texture_mode"] == "topdown_tiles_v1"
    assert summary["scene_texture_pack"] == "topdown_tiles_v1"
    assert summary["scene_texture_fallback_used"] is False
    assert summary["scene_texture_missing_assets"] == []
    assert Path(payload["outputs"]["production_steps_dir"]).exists()
    assert Path(payload["outputs"]["production_steps_manifest"]).exists()
    assert all(Path(step["glb_path"]).exists() for step in steps)
    assert all(step["scene_texture_mode"] == "topdown_tiles_v1" for step in steps)
    assert all(bool(step["textured_base_enabled"]) for step in steps)
    assert step_by_id["road_base"]["counts"]["street_furniture_count"] == 0
    assert step_by_id["buildings"]["counts"]["street_furniture_count"] == 0
    assert set(step_by_id["buildings"]["visible_instance_ids"]) == building_ids
    assert step_by_id["poi_context"]["companion_path"]
    assert Path(step_by_id["poi_context"]["companion_path"]).exists()
    assert step_by_id["land_use_zoning"]["companion_path"]
    assert Path(step_by_id["land_use_zoning"]["companion_path"]).exists()
    assert int(step_by_id["poi_context"]["counts"]["poi_point_count"]) > 0
    assert (
        int(step_by_id["furniture_anchor"]["counts"]["visible_instance_count"])
        <= int(step_by_id["furniture_required"]["counts"]["visible_instance_count"])
        <= int(step_by_id["furniture_optional"]["counts"]["visible_instance_count"])
    )
    loaded_scene = trimesh.load(Path(result.outputs["scene_glb"]), force="scene")
    assert _has_embedded_texture(loaded_scene) is True


def test_scene_preview_production_companion_prefers_final_render_view(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data", include_buildings=True)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    import roadgen3d.osm_ingest as osm_ingest

    monkeypatch.setattr(
        osm_ingest,
        "fetch_osm_data",
        lambda **kwargs: _build_osm_response(include_building=True, include_bus_stop=True, include_fire_hydrant=True),
    )

    final_companion = (tmp_path / "artifacts" / "presentation_views" / "final_oblique_45_axonometric.png").resolve()
    final_companion.parent.mkdir(parents=True, exist_ok=True)
    final_companion.write_bytes(b"png")
    monkeypatch.setattr(
        street_layout,
        "render_presentation_views",
        lambda *args, **kwargs: [
            {
                "name": "final_oblique_45_axonometric",
                "title": "Final Oblique 45 Axonometric",
                "path": str(final_companion),
            }
        ],
    )

    result = compose_street_scene(
        config=_build_osm_config(tmp_path, seed=23),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    step_by_id = {step["step_id"]: step for step in payload["production_steps"]}
    assert step_by_id["scene_preview"]["companion_path"] == str(final_companion)
    assert Path(payload["outputs"]["production_steps_manifest"]).exists()


def test_osm_compose_building_fallback_survives_missing_assets_and_footprints(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data", include_buildings=False)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    import roadgen3d.osm_ingest as osm_ingest

    monkeypatch.setattr(osm_ingest, "fetch_osm_data", lambda **kwargs: _build_osm_response(include_building=False))
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])
    monkeypatch.setattr(street_layout, "_load_building_manifest", lambda _path: [])

    result = compose_street_scene(
        config=replace(_build_osm_config(tmp_path, seed=23), curated_street_assets_profile="disabled"),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    building_group = [placement for placement in result.placements if placement.placement_group == "building"]
    building_plans = payload["building_placements"]

    assert building_group
    assert building_plans
    assert all(placement.asset_id.startswith("building_fallback_") for placement in building_group)
    assert all(placement.selection_source == "procedural_fallback" for placement in building_group)
    assert all(plan["asset_scale_mode"] == "procedural_fallback_fit" for plan in building_plans)
    assert summary["building_summary"]["fallback_count"] > 0
    assert summary["procedural_building_fallback_count"] == summary["building_summary"]["fallback_count"]
    assert summary["building_generation_mode"] == "grid_growth"
    assert summary["building_summary"]["real_footprint_count"] == 0
    assert summary["building_summary"]["infill_footprint_count"] == 0
    assert summary["building_summary"]["door_count"] == len(payload["building_placements"])
    assert summary["building_summary"]["door_policy"] == "procedural_fallback_only"
    assert summary["building_summary"]["door_required_count"] == len(payload["building_placements"])
    assert summary["building_summary"]["door_skipped_existing_asset_count"] == 0
    assert summary["building_summary"]["door_missing_building_count"] == 0
    assert summary["building_balance_ok"] is True
    assert summary["street_furniture_balance_ok"] is True
    assert any(plan["selection_source"] == "procedural_fallback" for plan in building_plans)
    assert all(bool(plan["door_added"]) for plan in building_plans)
    assert payload["zoning_grid"]
    assert payload["generated_lots"]
    assert summary["zoning_preview_summary"]["occupied_building_cells"] > 0
    assert summary["frontage_parcel_count"] == summary["street_facade_count"]
    assert summary["building_land_policy"] == "road_clearance_fill_v1"
    assert summary["zoning_preview_mode"] == "parcel_first"
    assert summary["frontage_cell_count"] > len(summary["theme_segments"])


def test_analytical_diorama_visual_style_metadata_uses_procedural_buildings(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data", include_buildings=True)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    import roadgen3d.osm_ingest as osm_ingest

    monkeypatch.setattr(osm_ingest, "fetch_osm_data", lambda **kwargs: _build_osm_response(include_building=False))
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])

    result = compose_street_scene(
        config=replace(
            _build_osm_config(tmp_path, seed=37, surrounding_building_mode="grid_growth"),
            style_preset="analytical_diorama_v1",
            beauty_mode="presentation_v1",
            render_preset="axonometric_board_v1",
            asset_curation_mode="curated_first",
            scene_texture_mode="topdown_tiles_v1",
        ),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    visual_style = payload["visual_style"]
    environment_state = payload["environment_state"]

    assert payload["schema_version"] == SCENE_LAYOUT_SCHEMA_VERSION
    assert visual_style["preset"] == "analytical_diorama_v1"
    assert visual_style["lighting_preset"] == "analytical_diorama"
    assert visual_style["material_finish_version"] == "analytical_diorama_finish_v1"
    assert visual_style["building_profile"]["mode"] == "procedural_background"
    assert summary["visual_style_preset"] == "analytical_diorama_v1"
    assert summary["visual_lighting_preset"] == "analytical_diorama"
    assert summary["environment_system"]["layer"] == "environment_runtime_v1"
    assert summary["environment_system"]["sun_model"] == "artistic_day_cycle"
    assert summary["environment_system"]["runtime_only"] is True
    assert environment_state["weather_mode"] in {"clear", "overcast", "rain", "fog"}
    assert environment_state["time_of_day_hours"] == 14.0
    assert environment_state["sun_cycle_enabled"] is False
    assert summary["scene_texture_pack"] == "topdown_tiles_v1"
    assert summary["visual_surface_role_count"]["carriageway"] > 0
    assert summary["visual_surface_role_count"]["sidewalk"] > 0
    assert payload["building_placements"]
    assert all(plan["selection_source"] == "procedural_fallback" for plan in payload["building_placements"])
    assert summary["procedural_building_fallback_count"] == summary["building_summary"]["fallback_count"]
    assert summary["procedural_building_fallback_count"] == len(payload["building_placements"])


def test_graph_template_building_regions_keep_auto_land_use_generation(tmp_path: Path, monkeypatch):
    trimesh = pytest.importorskip("trimesh")
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data", include_buildings=True)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])

    bridge = _build_graph_template_bridge_with_building_regions()
    config = StreetComposeConfig(
        query="campus gateway street",
        length_m=96.0,
        road_width_m=18.0,
        sidewalk_width_m=3.0,
        lane_count=2,
        density=1.0,
        seed=31,
        topk_per_category=20,
        max_trials_per_slot=20,
        layout_mode="graph_template",
        constraint_mode="off",
        curated_street_assets_profile="disabled",
        enable_surrounding_buildings=True,
        surrounding_building_mode="grid_growth",
        building_search_topk=3,
    )

    result = compose_street_scene(
        config=config,
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
        road_segment_graph_override=bridge.road_segment_graph,
        projected_features_override=bridge.projected_features,
        placement_context_override=bridge.placement_context,
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    region_ids = {region["region_id"] for region in bridge.placement_context.building_regions}
    step_ids = [step["step_id"] for step in payload["production_steps"]]

    assert summary["building_generation_mode_requested"] == "grid_growth"
    assert summary["building_generation_mode"] == "grid_growth"
    assert summary["building_generation_mode_used"] == "grid_growth"
    assert summary["building_generation_fallback_reason"] == ""
    assert summary["building_footprint_count"] == 0
    assert summary["building_region_count"] == len(region_ids)
    assert payload["generated_lots"]
    assert payload["zoning_grid"]
    assert "land_use_zoning" in step_ids
    assert summary["frontage_parcel_count"] == summary["street_facade_count"]
    assert summary["building_land_policy"] == "road_clearance_fill_v1"
    assert summary["grass_policy"] == "continuous_non_road_underlay_v1"
    assert summary["zoning_preview_mode"] == "parcel_first"
    assert summary["zoning_preview_summary"]["auto_land_use_enabled"] is True
    assert summary["zoning_preview_summary"]["auto_land_use_mode"] == "road_buffer"
    assert summary["zoning_preview_summary"]["frontage_parcel_count"] == summary["street_facade_count"]
    assert summary["land_use_summary"]["buildable_cell_count"] > 0
    assert summary["building_summary"]["lot_count"] == len(payload["generated_lots"])
    assert summary["building_summary"]["target_type"] == "lot"
    assert summary["building_summary"]["region_direct_mode"] is False
    assert summary["building_summary"]["building_region_count"] == len(region_ids)
    assert summary["infill_footprint_count"] == 0
    assert summary["building_summary"]["infill_footprint_count"] == 0
    assert payload["building_footprints"] == []
    lot_ids = {lot["lot_id"] for lot in payload["generated_lots"]}
    assert all(lot["source"] == "road_clearance_fill" for lot in payload["generated_lots"])
    assert all(placement.anchor_geom_id in lot_ids for placement in result.placements if placement.placement_group == "building")
    assert all(plan["anchor_geom_id"] in lot_ids for plan in payload["building_placements"])
    assert all(str(plan["placement_strategy"]).startswith("road_clearance_") for plan in payload["building_placements"])
    assert summary["zoning_preview_summary"]["building_region_count"] == len(region_ids)
    assert summary["zoning_preview_summary"]["active_building_region_count"] == len(region_ids)
    loaded_scene = trimesh.load(Path(result.outputs["scene_glb"]), force="scene")
    assert not any("zoning_proxy" in str(node_name) for node_name in loaded_scene.graph.nodes_geometry)
    assert any("building_land_grass" in str(node_name) for node_name in loaded_scene.graph.nodes_geometry)


def test_osm_compose_grid_growth_generates_lots_and_lot_based_buildings(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data", include_buildings=True)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    import roadgen3d.osm_ingest as osm_ingest

    monkeypatch.setattr(osm_ingest, "fetch_osm_data", lambda **kwargs: _build_osm_response(include_building=True))
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])
    monkeypatch.setattr(street_layout, "_load_building_manifest", lambda _path: [])

    result = compose_street_scene(
        config=_build_osm_config(tmp_path, seed=29, surrounding_building_mode="grid_growth"),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    lot_ids = {lot["lot_id"] for lot in payload["generated_lots"]}

    assert summary["building_generation_mode"] == "grid_growth"
    assert payload["building_footprints"] == []
    assert payload["generated_lots"]
    assert summary["lot_generation_summary"]["lot_count"] == len(payload["generated_lots"])
    assert summary["frontage_parcel_count"] == summary["street_facade_count"]
    assert summary["building_summary"]["frontage_parcel_count"] == summary["street_facade_count"]
    assert summary["building_land_policy"] == "road_clearance_fill_v1"
    assert summary["grass_policy"] == "continuous_non_road_underlay_v1"
    assert summary["auto_building_scaling"] is False
    assert summary["land_use_summary"]["buildable_cell_count"] > 0
    assert summary["building_retrieval_coverage"]["lot_count"] == len(payload["generated_lots"])
    assert len(payload["generated_lots"]) < summary["land_use_summary"]["buildable_cell_count"]
    assert summary["frontage_cell_count"] > len(summary["theme_segments"])
    assert summary["zoning_preview_summary"]["side_land_use_counts"]["left"]
    assert summary["zoning_preview_summary"]["side_land_use_counts"]["right"]
    assert all(plan["anchor_geom_id"] in lot_ids for plan in payload["building_placements"])
    assert all(placement.anchor_geom_id in lot_ids for placement in result.placements if placement.placement_group == "building")
    assert any(str(cell.get("lot_id", "") or "") for cell in payload["zoning_grid"] if bool(cell.get("buildable", False)))
    assert all("building_buffer" in cell["lane_role"] for cell in payload["zoning_grid"] if str(cell.get("lot_id", "") or ""))
    assert all(str(plan["placement_strategy"]).startswith("road_clearance_") for plan in payload["building_placements"])
    assert all(DEFAULT_BUILDING_FRONT_SETBACK_MIN_M <= float(plan["front_setback_m"]) <= DEFAULT_BUILDING_FRONT_SETBACK_MAX_M for plan in payload["building_placements"])
    assert all(str(lot["placement_strategy"]).startswith("road_clearance_") for lot in payload["generated_lots"])
    assert {lot["source"] for lot in payload["generated_lots"]} == {"road_clearance_fill"}
    assert all(DEFAULT_BUILDING_FRONT_SETBACK_MIN_M <= float(lot["front_setback_m"]) <= DEFAULT_BUILDING_FRONT_SETBACK_MAX_M for lot in payload["generated_lots"])
    assert all(lot["placement_xz"] == lot["center_xz"] for lot in payload["generated_lots"])
    assert summary["building_balance_ok"] is True
    assert summary["frontage_coverage_by_side"]["left"]["coverage_ratio"] >= 0.65
    assert summary["frontage_coverage_by_side"]["right"]["coverage_ratio"] >= 0.65
    assert summary["frontage_balance_gap"] <= 0.10


def test_osm_compose_grid_growth_falls_back_without_building_assets(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data", include_buildings=False)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    import roadgen3d.osm_ingest as osm_ingest

    monkeypatch.setattr(osm_ingest, "fetch_osm_data", lambda **kwargs: _build_osm_response(include_building=True))
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])
    monkeypatch.setattr(street_layout, "_load_building_manifest", lambda _path: [])

    result = compose_street_scene(
        config=replace(
            _build_osm_config(tmp_path, seed=37, surrounding_building_mode="grid_growth"),
            curated_street_assets_profile="disabled",
        ),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    building_group = [placement for placement in result.placements if placement.placement_group == "building"]

    assert payload["generated_lots"]
    assert building_group
    assert all(placement.asset_id.startswith("building_fallback_lot_") for placement in building_group)
    assert all(plan["selection_source"] == "procedural_fallback" for plan in payload["building_placements"])
    assert summary["building_summary"]["fallback_count"] == len(payload["building_placements"])
    assert summary["building_generation_mode"] == "grid_growth"
    assert summary["frontage_parcel_count"] == summary["street_facade_count"]
    assert summary["building_land_policy"] == "road_clearance_fill_v1"


def test_osm_bus_stop_anchor_relaxes_when_exact_anchor_is_blocked(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data", include_buildings=False)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    import roadgen3d.osm_ingest as osm_ingest

    monkeypatch.setattr(
        osm_ingest,
        "fetch_osm_data",
        lambda **kwargs: _build_osm_response(
            include_building=False,
            include_bus_stop=True,
            include_fire_hydrant=False,
        ),
    )
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])
    original_iter = street_layout._iter_slot_candidate_groups

    def fake_iter_slot_candidate_groups(**kwargs):
        slot = kwargs["slot"]
        if (
            kwargs["category"] == "bus_stop"
            and str(getattr(slot, "anchor_poi_type", "") or "") == "bus_stop"
        ):
            sampled_pose = street_layout._sample_pose_osm_for_segment(
                kwargs["category"],
                kwargs["placement_ctx"],
                kwargs["rng"],
                segment_node=kwargs["segment_node"],
                slot_side=str(getattr(slot, "side", "") or ""),
                band_width_m=float(kwargs["band_width_m"]),
                anchor_position_xz=None,
            )
            assert sampled_pose is not None
            sample_x, sample_z, sample_yaw = sampled_pose
            return (
                (
                    {
                        "tier": "tier_1_exact",
                        "point_xz": (float(getattr(slot, "x_center_m")), 999.0),
                        "yaw_deg": 0.0,
                        "anchor_distance_m": 0.0,
                    },
                ),
                (
                    {
                        "tier": "tier_2_ring",
                        "point_xz": (float(sample_x), float(sample_z)),
                        "yaw_deg": float(sample_yaw),
                        "anchor_distance_m": 1.2,
                    },
                ),
            )
        return original_iter(**kwargs)

    monkeypatch.setattr(street_layout, "_iter_slot_candidate_groups", fake_iter_slot_candidate_groups)

    result = compose_street_scene(
        config=_build_osm_config(tmp_path, seed=31),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    anchor_bus_stops = [
        placement
        for placement in result.placements
        if placement.category == "bus_stop" and placement.anchor_poi_type == "bus_stop"
    ]

    assert anchor_bus_stops
    assert all(placement.placement_status == "anchored_relaxed" for placement in anchor_bus_stops)
    assert all(0.75 < float(placement.anchor_distance_m) <= 8.0 for placement in anchor_bus_stops)
    assert summary["anchor_resolution_summary"]["anchored_relaxed"] >= 1
    assert summary["required_slot_realization_rate"] > 0.0
    placed_bus_stop_slot_ids = {str(placement.slot_id) for placement in anchor_bus_stops}
    assert all(
        str(diagnostic.get("slot_id", "")) not in placed_bus_stop_slot_ids
        for diagnostic in payload["unplaced_slot_diagnostics"]
    )


def test_osm_required_anchor_failure_degrades_to_diagnostics(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data", include_buildings=False)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    import roadgen3d.osm_ingest as osm_ingest

    monkeypatch.setattr(
        osm_ingest,
        "fetch_osm_data",
        lambda **kwargs: _build_osm_response(
            include_building=False,
            include_bus_stop=True,
            include_fire_hydrant=False,
        ),
    )
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])

    original_evaluate = street_layout._evaluate_slot_candidate

    def fake_evaluate_slot_candidate(**kwargs):
        slot = kwargs["slot"]
        if (
            kwargs["category"] == "bus_stop"
            and str(getattr(slot, "anchor_poi_type", "") or "") == "bus_stop"
        ):
            return None, "overlap_blocked"
        return original_evaluate(**kwargs)

    monkeypatch.setattr(street_layout, "_evaluate_slot_candidate", fake_evaluate_slot_candidate)

    result = compose_street_scene(
        config=_build_osm_config(tmp_path, seed=37),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]

    assert result.placements
    assert summary["unplaced_required_slot_count"] >= 1
    assert 0.0 <= float(summary["required_slot_realization_rate"]) < 1.0
    assert summary["placement_force_model"]["version"] == "placement_field_v1"
    assert payload["unplaced_slot_diagnostics"]
    assert any(
        diag["category"] == "bus_stop" and diag["failure_reason"] == "overlap_blocked"
        for diag in payload["unplaced_slot_diagnostics"]
    )
