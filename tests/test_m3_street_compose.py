from __future__ import annotations

import inspect
import json
import random
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.types import (
    DEFAULT_BUILDING_FRONT_SETBACK_MAX_M,
    DEFAULT_BUILDING_FRONT_SETBACK_MIN_M,
    LayoutSlotPlan,
    RetrievalHit,
    StreetComposeConfig,
    StreetComposeResult,
    StreetPlacement,
)
from roadgen3d.asset_scale import compute_asset_scale
from roadgen3d.street_layout import compose_street_scene
import roadgen3d.street_layout as street_layout
from roadgen3d.poi_rules import PoiContext
from roadgen3d.scene_textures import apply_default_scene_texture, create_scene_texture_tracker


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
        ("lamp_01", "lamp"),
        ("trash_01", "trash"),
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
    _write_manifest(
        manifest,
        [
            {
                "asset_id": "lamp_scene_ready",
                "category": "lamp",
                "text_desc": "scene ready lamp",
                "mesh_path": str(tmp_path / "meshes" / "lamp_scene_ready.glb"),
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
    import scripts.m1_gradio_app as app

    glb_path = (tmp_path / "scene.glb").resolve()
    ply_path = (tmp_path / "scene.ply").resolve()
    layout_path = (tmp_path / "scene_layout.json").resolve()
    glb_path.write_bytes(b"glb")
    ply_path.write_bytes(b"ply")
    layout_payload = {"summary": {"instance_count": 1, "dropped_slots": 0}, "placements": []}
    layout_path.write_text(json.dumps(layout_payload), encoding="utf-8")

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


def test_run_street_compose_defaults_shift_to_walkable_narrow_street():
    pytest.importorskip("gradio")
    import scripts.m1_gradio_app as app

    signature = inspect.signature(app.run_street_compose)

    assert signature.parameters["street_road_width_m"].default == pytest.approx(7.0)
    assert signature.parameters["street_sidewalk_width_m"].default == pytest.approx(2.4)
    assert signature.parameters["road_selection"].default == "walkable_neighborhood"
    assert signature.parameters["asset_scale_mode"].default == "canonical_v1"
    assert signature.parameters["tree_species_policy"].default == "per_theme_single_species"
    assert signature.parameters["furniture_balance_policy"].default == "overall_balanced"
    assert signature.parameters["placement_logging_mode"].default == "full_with_ui_summary"


def test_street_compose_gradio_callback_propagates_objective_and_demand_controls(tmp_path: Path, monkeypatch):
    pytest.importorskip("gradio")
    import scripts.m1_gradio_app as app

    glb_path = (tmp_path / "scene.glb").resolve()
    layout_path = (tmp_path / "scene_layout.json").resolve()
    placement_log_path = (tmp_path / "placement_decisions.jsonl").resolve()
    glb_path.write_bytes(b"glb")
    placement_log_path.write_text("{}", encoding="utf-8")
    captured_config: dict[str, object] = {}
    layout_payload = {
        "summary": {
            "instance_count": 1,
            "dropped_slots": 0,
            "objective_profile": "commerce",
            "ped_demand_level": "high",
            "bike_demand_level": "medium",
            "transit_demand_level": "low",
            "vehicle_demand_level": "medium",
            "solver_backend_requested": "hybrid_milp_v1",
            "solver_backend_used": "hybrid_milp_v1",
            "cross_section_type": "complete_street",
            "style_preset": "civic_clean_v1",
            "asset_curation_mode": "scene_ready_first",
            "asset_scale_mode": "native_raw",
            "selected_highway_type": "tertiary",
            "road_selection_requested": "walkable_neighborhood",
            "road_selection_used": "walkable_neighborhood",
            "parametric_instance_count": 0,
            "production_step_count": 4,
            "presentation_score": 0.42,
            "land_use_asymmetry_strength": 0.55,
            "left_right_bias": -0.25,
            "building_front_setback_min_m": 1.1,
            "building_front_setback_max_m": 1.9,
            "zoning_granularity": "fine",
            "streetwall_continuity": 0.9,
            "infill_policy": "balanced",
            "frontage_parcel_count": 12,
            "infill_footprint_count": 3,
            "tree_species_policy": "per_theme_single_species",
            "furniture_balance_policy": "overall_balanced",
            "placement_logging_mode": "full_with_ui_summary",
            "tree_asset_by_theme": {"theme_000": "tree_02"},
            "street_furniture_core_side_counts": {"left": 2, "right": 2},
            "street_furniture_core_categories_by_side": {
                "left": ["lamp", "tree"],
                "right": ["bench", "tree"],
            },
            "placement_log_path": str(placement_log_path),
            "placement_log_reason_counts": {"feasible_candidate_selected": 4},
        },
        "placements": [],
    }
    layout_path.write_text(json.dumps(layout_payload), encoding="utf-8")

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
    import scripts.m1_gradio_app as app

    payload = {
        "config": {"layout_mode": "osm"},
        "summary": {
            "layout_mode": "osm",
            "solver_backend_requested": "hybrid_milp_v1",
            "solver_backend_used": "banded",
            "objective_profile": "greening",
            "solver_fallback_reason": "hybrid_milp_v1 produced no feasible slot assignment; fallback to banded",
        },
        "solver": {
            "backend_requested": "hybrid_milp_v1",
            "backend_used": "banded",
            "objective_profile": "greening",
            "fallback_reason": "hybrid_milp_v1 produced no feasible slot assignment; fallback to banded",
            "active_constraints": [f"constraint_{idx}" for idx in range(12)],
            "objective_score_breakdown": {"total_width_score": 8.0},
            "throughput_feasibility": {
                "overall_satisfied": False,
                "by_mode": {
                    "ped_clear_path": {"required": 2.4, "actual": 1.8, "satisfied": False},
                    "vehicle_carriageway": {"required": 6.0, "actual": 8.0, "satisfied": True},
                },
            },
            "band_solutions": [
                {
                    "band_name": "left_clear_path",
                    "band_kind": "clear_path",
                    "side": "left",
                    "width_m": 2.2,
                    "min_width_m": 1.8,
                    "max_width_m": 3.2,
                    "slack_m": 1.0,
                    "objective_weight": 1.1,
                    "active_constraint_names": ["left:min"],
                },
                {
                    "band_name": "left_clear_path",
                    "band_kind": "clear_path",
                    "side": "left",
                    "width_m": 2.6,
                    "min_width_m": 1.8,
                    "max_width_m": 3.4,
                    "slack_m": 0.8,
                    "objective_weight": 1.0,
                    "active_constraint_names": ["left:min", "ped:throughput"],
                },
                {
                    "band_name": "carriageway",
                    "band_kind": "carriageway",
                    "side": "",
                    "width_m": 8.0,
                    "min_width_m": 6.0,
                    "max_width_m": 10.0,
                    "slack_m": 2.0,
                    "objective_weight": 1.0,
                    "active_constraint_names": ["vehicle:throughput"],
                },
            ],
        },
    }

    fig, summary_json = app._extract_solver_diagnostics(json.dumps(payload, ensure_ascii=True))
    summary = json.loads(summary_json)

    assert summary["band_view"] == "OSM aggregated band view"
    assert summary["band_row_count"] == 2
    assert summary["fallback_reason"].endswith("fallback to banded")
    assert summary["active_constraint_count"] == 12
    assert summary["active_constraints_display"][-1] == "+2 more"
    assert summary["overall_throughput_satisfied"] is False
    assert summary["throughput_feasibility"]["by_mode"]["ped_clear_path"]["satisfied"] is False
    assert fig is not None


def test_extract_placement_decision_summary_reads_new_fields():
    pytest.importorskip("gradio")
    import scripts.m1_gradio_app as app

    payload = {
        "summary": {
            "tree_species_policy": "per_theme_single_species",
            "tree_asset_by_theme": {"theme_000": "tree_02"},
            "tree_theme_reselection_count": 1,
            "furniture_balance_policy": "overall_balanced",
            "street_furniture_side_counts": {"left": 4, "right": 5},
            "street_furniture_core_side_counts": {"left": 2, "right": 3},
            "street_furniture_core_categories_by_side": {
                "left": ["lamp", "tree"],
                "right": ["bench", "tree"],
            },
            "street_furniture_core_category_count_by_side": {"left": 2, "right": 2},
            "street_furniture_balance_ok": True,
            "street_furniture_balance_reason": "",
            "balance_repair_summary": {"attempt_count": 2, "success_count": 1},
            "placement_logging_mode": "full_with_ui_summary",
            "placement_log_path": "/tmp/placement_decisions.jsonl",
            "placement_log_summary": {"event_count": 12},
            "placement_log_reason_counts": {"feasible_candidate_selected": 5},
        }
    }

    summary = json.loads(app._extract_placement_decision_summary(json.dumps(payload)))

    assert summary["tree_species_policy"] == "per_theme_single_species"
    assert summary["tree_asset_by_theme"]["theme_000"] == "tree_02"
    assert summary["street_furniture_core_side_counts"]["left"] == 2
    assert summary["street_furniture_core_categories_by_side"]["right"] == ["bench", "tree"]
    assert summary["placement_log_path"] == "/tmp/placement_decisions.jsonl"
    assert summary["placement_log_reason_counts"]["feasible_candidate_selected"] == 5


def test_extract_street_scale_summary_reports_scale_and_road_selection():
    pytest.importorskip("gradio")
    import scripts.m1_gradio_app as app

    summary_json = app._extract_street_scale_summary(
        json.dumps(
            {
                "summary": {
                    "asset_scale_mode": "canonical_v1",
                    "selected_highway_type": "tertiary",
                    "road_selection_requested": "walkable_neighborhood",
                    "road_selection_used": "walkable_neighborhood",
                    "road_width_m": 7.0,
                    "sidewalk_width_m": 2.4,
                    "carriageway_width_m": 7.0,
                    "asset_scale_summary": {"tree": {"count": 2, "median_scale": 3.5}},
                }
            },
            ensure_ascii=True,
        )
    )

    payload = json.loads(summary_json)
    assert payload["asset_scale_mode"] == "canonical_v1"
    assert payload["selected_highway_type"] == "tertiary"
    assert payload["road_selection_requested"] == "walkable_neighborhood"
    assert payload["asset_scale_summary"]["tree"]["median_scale"] == 3.5


def test_extract_cross_section_preview_builds_template_cross_section():
    pytest.importorskip("gradio")
    import scripts.m1_gradio_app as app

    payload = {
        "config": {"layout_mode": "template"},
        "summary": {
            "layout_mode": "template",
            "solver_backend_used": "hybrid_milp_v1",
            "objective_profile": "balanced",
        },
        "solver": {
            "backend_used": "hybrid_milp_v1",
            "objective_profile": "balanced",
            "band_solutions": [
                {
                    "band_name": "left_furnishing",
                    "band_kind": "furnishing",
                    "side": "left",
                    "width_m": 1.4,
                    "min_width_m": 1.0,
                    "max_width_m": 2.2,
                },
                {
                    "band_name": "left_clear_path",
                    "band_kind": "clear_path",
                    "side": "left",
                    "width_m": 2.2,
                    "min_width_m": 1.8,
                    "max_width_m": 3.0,
                },
                {
                    "band_name": "carriageway",
                    "band_kind": "carriageway",
                    "side": "center",
                    "width_m": 8.0,
                    "min_width_m": 6.0,
                    "max_width_m": 8.0,
                },
                {
                    "band_name": "right_clear_path",
                    "band_kind": "clear_path",
                    "side": "right",
                    "width_m": 2.2,
                    "min_width_m": 1.8,
                    "max_width_m": 3.0,
                },
                {
                    "band_name": "right_furnishing",
                    "band_kind": "furnishing",
                    "side": "right",
                    "width_m": 1.4,
                    "min_width_m": 1.0,
                    "max_width_m": 2.2,
                },
            ],
        },
    }

    fig, summary_json = app._extract_cross_section_preview(json.dumps(payload, ensure_ascii=True))
    summary = json.loads(summary_json)

    assert summary["view_mode"] == "template"
    assert summary["data_source"] == "solver_bands"
    assert summary["band_count"] == 5
    assert summary["total_width_m"] == pytest.approx(15.2)
    assert fig is not None
    assert fig.layout.title.text == "Cross-Section Preview"


def test_extract_cross_section_preview_builds_osm_aggregated_cross_section():
    pytest.importorskip("gradio")
    import scripts.m1_gradio_app as app

    payload = {
        "config": {"layout_mode": "osm"},
        "summary": {
            "layout_mode": "osm",
            "solver_backend_used": "banded",
            "objective_profile": "greening",
        },
        "solver": {
            "backend_used": "banded",
            "objective_profile": "greening",
            "band_solutions": [
                {
                    "band_name": "left_furnishing",
                    "band_kind": "furnishing",
                    "side": "left",
                    "width_m": 1.2,
                    "min_width_m": 0.9,
                    "max_width_m": 2.0,
                },
                {
                    "band_name": "left_furnishing",
                    "band_kind": "furnishing",
                    "side": "left",
                    "width_m": 1.6,
                    "min_width_m": 1.0,
                    "max_width_m": 2.3,
                },
                {
                    "band_name": "left_clear_path",
                    "band_kind": "clear_path",
                    "side": "left",
                    "width_m": 2.4,
                    "min_width_m": 1.8,
                    "max_width_m": 3.0,
                },
                {
                    "band_name": "carriageway",
                    "band_kind": "carriageway",
                    "side": "center",
                    "width_m": 8.0,
                    "min_width_m": 6.0,
                    "max_width_m": 8.5,
                },
                {
                    "band_name": "right_clear_path",
                    "band_kind": "clear_path",
                    "side": "right",
                    "width_m": 2.1,
                    "min_width_m": 1.8,
                    "max_width_m": 3.0,
                },
                {
                    "band_name": "right_transit_edge",
                    "band_kind": "transit_edge",
                    "side": "right",
                    "width_m": 1.5,
                    "min_width_m": 1.2,
                    "max_width_m": 2.4,
                },
            ],
        },
    }

    fig, summary_json = app._extract_cross_section_preview(json.dumps(payload, ensure_ascii=True))
    summary = json.loads(summary_json)

    assert summary["view_mode"] == "osm_aggregated"
    assert summary["band_view"] == "OSM aggregated band view"
    assert summary["data_source"] == "solver_bands"
    assert summary["band_count"] == 5
    assert fig is not None


def test_extract_solver_diagnostics_tolerates_legacy_layout_payload():
    pytest.importorskip("gradio")
    import scripts.m1_gradio_app as app

    fig, summary_json = app._extract_solver_diagnostics(json.dumps({"summary": {"layout_mode": "template"}}, ensure_ascii=True))
    summary = json.loads(summary_json)

    assert summary["band_view"] == "Template band view"
    assert summary["band_row_count"] == 0
    assert summary["throughput_mode_count"] == 0
    assert summary["fallback_reason"] == "no fallback"
    assert summary["active_constraints_display"] == []
    if fig is not None:
        assert fig.layout.title.text == "Solver Diagnostics"


def test_extract_cross_section_preview_tolerates_legacy_layout_payload():
    pytest.importorskip("gradio")
    import scripts.m1_gradio_app as app

    payload = {
        "summary": {
            "layout_mode": "template",
            "left_clear_path_width_m": 2.0,
            "left_furnishing_width_m": 1.2,
            "road_width_m": 8.0,
            "right_furnishing_width_m": 1.2,
            "right_clear_path_width_m": 2.0,
        }
    }
    fig, summary_json = app._extract_cross_section_preview(json.dumps(payload, ensure_ascii=True))
    summary = json.loads(summary_json)

    assert summary["data_source"] == "summary_fields"
    assert summary["band_count"] == 5
    assert summary["total_width_m"] == pytest.approx(14.4)
    if fig is not None:
        assert fig.layout.title.text == "Cross-Section Preview"


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
        ),
    )

    assert row["asset_id"] == "tree_lowpoly"
    assert score > 0.91
    assert source == "faiss_softmax"


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
    assert abs(float(candidates[0]["yaw_deg"]) - 90.0) <= 1e-6


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


def test_run_street_compose_auto_selects_stable_poi_rich_road_by_seed(tmp_path: Path, monkeypatch):
    pytest.importorskip("gradio")
    import scripts.m1_gradio_app as app

    monkeypatch.setattr(app, "ROOT", tmp_path)
    artifacts_dir = tmp_path / "artifacts" / "real"
    discovered_dir = tmp_path / "artifacts" / "m5"
    discovered_dir.mkdir(parents=True, exist_ok=True)
    records = [
        {"osm_id": 999, "bbox": [120.0, 30.0, 120.1, 30.1], "highway_type": "primary", "road_length_m": 123.0, "poi_count": 1, "poi_types": {"entrance": 1, "bus_stop": 0, "fire_hydrant": 0}},
        {"osm_id": 201, "bbox": [113.2000, 23.1000, 113.2100, 23.1100], "highway_type": "service", "road_length_m": 140.0, "poi_count": 2, "poi_types": {"entrance": 2, "bus_stop": 0, "fire_hydrant": 0}},
        {"osm_id": 202, "bbox": [113.2660, 23.1280, 113.2710, 23.1325], "highway_type": "secondary", "road_length_m": 150.0, "poi_count": 3, "poi_types": {"entrance": 2, "bus_stop": 1, "fire_hydrant": 0}},
        {"osm_id": 203, "bbox": [113.3000, 23.1400, 113.3100, 23.1500], "highway_type": "tertiary", "road_length_m": 160.0, "poi_count": 4, "poi_types": {"entrance": 2, "bus_stop": 1, "fire_hydrant": 1}},
    ]
    (discovered_dir / "discovered_poi_roads.jsonl").write_text(
        "\n".join(json.dumps(record, ensure_ascii=True) for record in records) + "\n",
        encoding="utf-8",
    )
    app._write_discovered_roads_metadata(
        app._discovered_metadata_path(discovered_dir / "discovered_poi_roads.jsonl"),
        (113.2660, 23.1280, 113.2710, 23.1325),
    )

    glb_path = (tmp_path / "scene.glb").resolve()
    layout_path = (tmp_path / "scene_layout.json").resolve()
    glb_path.write_bytes(b"glb")

    captured: dict[str, object] = {}
    effective_counts_by_osm = {
        201: {"entrance": 2, "bus_stop": 0, "fire": 0},
        202: {"entrance": 2, "bus_stop": 1, "fire": 0},
        203: {"entrance": 2, "bus_stop": 1, "fire": 1},
    }

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
    import scripts.m1_gradio_app as app

    monkeypatch.setattr(app, "ROOT", tmp_path)
    artifacts_dir = tmp_path / "artifacts" / "real"
    discovered_dir = tmp_path / "artifacts" / "m5"
    discovered_dir.mkdir(parents=True, exist_ok=True)
    records = [
        {"osm_id": 201, "bbox": [113.2000, 23.1000, 113.2100, 23.1100], "highway_type": "service", "road_length_m": 140.0, "poi_count": 2, "poi_types": {"entrance": 2, "bus_stop": 0, "fire_hydrant": 0}},
        {"osm_id": 202, "bbox": [113.2660, 23.1280, 113.2710, 23.1325], "highway_type": "secondary", "road_length_m": 150.0, "poi_count": 3, "poi_types": {"entrance": 2, "bus_stop": 1, "fire_hydrant": 0}},
    ]
    discovered_path = discovered_dir / "discovered_poi_roads.jsonl"
    discovered_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=True) for record in records) + "\n",
        encoding="utf-8",
    )
    app._write_discovered_roads_metadata(
        app._discovered_metadata_path(discovered_path),
        (113.2660, 23.1280, 113.2710, 23.1325),
    )

    glb_path = (tmp_path / "scene.glb").resolve()
    layout_path = (tmp_path / "scene_layout.json").resolve()
    glb_path.write_bytes(b"glb")
    captured: dict[str, object] = {}

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
    import scripts.m1_gradio_app as app
    from roadgen3d.road_discovery import DiscoveredRoad

    monkeypatch.setattr(app, "ROOT", tmp_path)
    artifacts_dir = tmp_path / "artifacts" / "real"
    glb_path = (tmp_path / "scene.glb").resolve()
    layout_path = (tmp_path / "scene_layout.json").resolve()
    glb_path.write_bytes(b"glb")

    captured: dict[str, object] = {}
    effective_counts_by_osm = {
        501: {"entrance": 2, "bus_stop": 0, "fire": 0},
        502: {"entrance": 2, "bus_stop": 1, "fire": 0},
    }

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
    import scripts.m1_gradio_app as app

    glb_path = (tmp_path / "scene.glb").resolve()
    layout_path = (tmp_path / "scene_layout.json").resolve()
    glb_path.write_bytes(b"glb")
    layout_payload = {
        "summary": {
            "instance_count": 4,
            "dropped_slots": 0,
            "asset_source_counts": {
                "objaverse_import": 2,
                "procedural_generated": 1,
                "parametric_generated": 1,
            },
            "asset_source_unique_counts": {
                "objaverse_import": 2,
                "procedural_generated": 1,
                "parametric_generated": 1,
            },
            "asset_generator_type_counts": {
                "objaverse_v1": 2,
                "legacy": 1,
                "parametric": 1,
            },
            "asset_usage_by_source": [
                {
                    "source": "objaverse_import",
                    "instance_count": 2,
                    "unique_asset_count": 2,
                    "categories": ["bench", "lamp"],
                    "generator_types": ["objaverse_v1"],
                    "asset_ids": ["objaverse_bench_x", "objaverse_lamp_y"],
                },
                {
                    "source": "procedural_generated",
                    "instance_count": 1,
                    "unique_asset_count": 1,
                    "categories": ["tree"],
                    "generator_types": ["legacy"],
                    "asset_ids": ["tree_01"],
                },
            ],
        },
        "placements": [],
    }
    layout_path.write_text(json.dumps(layout_payload, ensure_ascii=True), encoding="utf-8")

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
    import scripts.m1_gradio_app as app
    from roadgen3d.road_discovery import DiscoveredRoad

    monkeypatch.setattr(app, "ROOT", tmp_path)
    artifacts_dir = tmp_path / "artifacts" / "real"
    discovered_dir = tmp_path / "artifacts" / "m5"
    discovered_dir.mkdir(parents=True, exist_ok=True)
    discovered_path = discovered_dir / "discovered_poi_roads.jsonl"
    discovered_path.write_text(
        json.dumps(
            {"osm_id": 101, "bbox": [120.0, 30.0, 120.1, 30.1], "highway_type": "service", "road_length_m": 120.0, "poi_count": 2, "poi_types": {"entrance": 2}},
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    app._write_discovered_roads_metadata(
        app._discovered_metadata_path(discovered_path),
        (120.0, 30.0, 120.1, 30.1),
    )

    glb_path = (tmp_path / "scene.glb").resolve()
    layout_path = (tmp_path / "scene_layout.json").resolve()
    glb_path.write_bytes(b"glb")
    calls = {"discover": 0}

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
    import scripts.m1_gradio_app as app

    monkeypatch.setattr(app, "ROOT", tmp_path)
    artifacts_dir = tmp_path / "artifacts" / "real"
    monkeypatch.setattr(app, "discover_poi_roads", lambda city, cache_dir: [])

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

    assert "No POI-rich roads found" in summary


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
            config=_build_config(seed=1),
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
    assert 0.0 <= float(summary["presentation_score"]) <= 1.0
    assert 0.0 <= float(summary["style_coherence"]) <= 1.0
    assert "composition_report" in summary
    render_views = summary.get("render_views", [])
    assert len(render_views) == 4
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
    assert all(placement.asset_id in {"building_01", "building_02"} for placement in building_group)
    assert all(placement.selection_source == "building_asset" for placement in building_group)
    assert summary["building_generation_mode"] == "grid_growth"
    assert summary["land_use_asymmetry_strength"] == pytest.approx(0.0)
    assert summary["building_front_setback_min_m"] == pytest.approx(DEFAULT_BUILDING_FRONT_SETBACK_MIN_M)
    assert summary["building_front_setback_max_m"] == pytest.approx(DEFAULT_BUILDING_FRONT_SETBACK_MAX_M)
    assert summary["zoning_granularity"] == "fine"
    assert summary["streetwall_continuity"] == pytest.approx(0.95)
    assert summary["infill_policy"] == "aggressive"
    assert summary["building_balance_policy"] == "balanced_default"
    assert summary["building_balance_ok"] is True
    assert summary["frontage_balance_gap"] <= 0.10
    assert summary["buildable_frontage_by_side"]["left"] > 0.0
    assert summary["buildable_frontage_by_side"]["right"] > 0.0
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
    assert summary["frontage_coverage_by_side"]["left"]["coverage_ratio"] >= 0.65
    assert summary["frontage_coverage_by_side"]["right"]["coverage_ratio"] >= 0.65
    assert summary["frontage_gap_stats_by_side"]["left"]["gap_count"] >= 0
    assert len(payload["generated_lots"]) >= summary["land_use_summary"]["buildable_cell_count"]
    assert summary["building_summary"]["frontage_cell_count"] == summary["frontage_cell_count"]


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
    loaded_zoning = trimesh.load(Path(step_by_id["land_use_zoning"]["glb_path"]), force="scene")
    assert _has_embedded_texture(loaded_zoning) is True


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
    summary = payload["summary"]
    building_group = [placement for placement in result.placements if placement.placement_group == "building"]
    building_plans = payload["building_placements"]

    assert building_group
    assert building_plans
    assert all(placement.asset_id.startswith("building_fallback_") for placement in building_group)
    assert all(placement.selection_source == "procedural_fallback" for placement in building_group)
    assert summary["building_summary"]["fallback_count"] > 0
    assert summary["building_generation_mode"] == "grid_growth"
    assert summary["building_summary"]["real_footprint_count"] == 0
    assert summary["building_summary"]["infill_footprint_count"] == 0
    assert summary["building_balance_ok"] is True
    assert summary["street_furniture_balance_ok"] is True
    assert any(plan["selection_source"] == "procedural_fallback" for plan in building_plans)
    assert payload["zoning_grid"]
    assert payload["generated_lots"]
    assert summary["zoning_preview_summary"]["occupied_building_cells"] > 0
    assert summary["frontage_parcel_count"] == len(payload["generated_lots"])
    assert summary["zoning_preview_mode"] == "parcel_first"
    assert summary["frontage_cell_count"] > len(summary["theme_segments"])


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
    assert summary["frontage_parcel_count"] == len(payload["generated_lots"])
    assert summary["building_summary"]["frontage_parcel_count"] == len(payload["generated_lots"])
    assert summary["land_use_summary"]["buildable_cell_count"] > 0
    assert summary["building_retrieval_coverage"]["lot_count"] == len(payload["generated_lots"])
    assert len(payload["generated_lots"]) >= summary["land_use_summary"]["buildable_cell_count"]
    assert summary["frontage_cell_count"] > len(summary["theme_segments"])
    assert summary["zoning_preview_summary"]["side_land_use_counts"]["left"]
    assert summary["zoning_preview_summary"]["side_land_use_counts"]["right"]
    assert all(plan["anchor_geom_id"] in lot_ids for plan in payload["building_placements"])
    assert all(placement.anchor_geom_id in lot_ids for placement in result.placements if placement.placement_group == "building")
    assert any(str(cell.get("lot_id", "") or "") for cell in payload["zoning_grid"] if bool(cell.get("buildable", False)))
    assert all("building_buffer" in cell["lane_role"] for cell in payload["zoning_grid"] if str(cell.get("lot_id", "") or ""))
    assert all(plan["placement_strategy"] in {"frontage_setback", "frontage_clamped"} for plan in payload["building_placements"])
    assert all(DEFAULT_BUILDING_FRONT_SETBACK_MIN_M <= float(plan["front_setback_m"]) <= DEFAULT_BUILDING_FRONT_SETBACK_MAX_M for plan in payload["building_placements"])
    assert all(lot["placement_strategy"] in {"frontage_setback", "frontage_clamped"} for lot in payload["generated_lots"])
    assert all(DEFAULT_BUILDING_FRONT_SETBACK_MIN_M <= float(lot["front_setback_m"]) <= DEFAULT_BUILDING_FRONT_SETBACK_MAX_M for lot in payload["generated_lots"])
    assert any(lot["placement_xz"] != lot["center_xz"] for lot in payload["generated_lots"])
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

    result = compose_street_scene(
        config=_build_osm_config(tmp_path, seed=37, surrounding_building_mode="grid_growth"),
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
    assert summary["frontage_parcel_count"] == len(payload["generated_lots"])


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
    assert payload["unplaced_slot_diagnostics"] == []


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
