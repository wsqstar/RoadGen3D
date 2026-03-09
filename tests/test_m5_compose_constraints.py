"""Tests for M5 constraint integration in street_layout compose."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.types import RetrievalHit, StreetComposeConfig, StreetComposeResult
from roadgen3d.street_layout import compose_street_scene
import roadgen3d.street_layout as street_layout


# ---------------------------------------------------------------------------
# Shared helpers (same pattern as test_m3_street_compose)
# ---------------------------------------------------------------------------


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


def _build_real_rows(base_dir: Path) -> list[dict[str, object]]:
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
    for asset_id, category in categories:
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
    return rows


def _setup_fake_retrieval(monkeypatch, asset_ids: list[str]) -> None:
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


def _setup_fake_osm(monkeypatch) -> None:
    import roadgen3d.osm_ingest as osm_ingest

    projected = osm_ingest.ProjectedFeatures(
        roads=[
            osm_ingest.OsmRoad(osm_id=101, highway_type="primary", coords=[(-25.0, 10.0), (25.0, 10.0)], width_m=12.0),
            osm_ingest.OsmRoad(osm_id=202, highway_type="service", coords=[(-25.0, -5.0), (25.0, -5.0)], width_m=6.0),
        ],
        entrances=[(0.0, -1.5), (8.0, -1.5)],
        bus_stops=[(10.0, -1.0)],
        fire_points=[(-10.0, -1.0)],
        bbox_m=(-30.0, -12.0, 30.0, 15.0),
        origin_utm=(0.0, 0.0),
        utm_epsg=32649,
    )

    monkeypatch.setattr(osm_ingest, "fetch_osm_data", lambda **kwargs: {"elements": []})
    monkeypatch.setattr(osm_ingest, "parse_osm_features", lambda raw: osm_ingest.OsmFeatures())
    monkeypatch.setattr(osm_ingest, "project_to_local", lambda features, bbox: projected)


def _setup_fake_osm_with_extended_pois(monkeypatch) -> None:
    import roadgen3d.osm_ingest as osm_ingest

    projected = osm_ingest.ProjectedFeatures(
        roads=[
            osm_ingest.OsmRoad(osm_id=303, highway_type="secondary", coords=[(-25.0, -4.0), (25.0, -4.0)], width_m=8.0),
        ],
        entrances=[(0.0, -1.5)],
        bus_stops=[],
        fire_points=[],
        poi_points_by_type={
            "entrance": [(0.0, -1.5)],
            "crossing": [(-12.0, -1.0)],
            "post_box": [(4.0, -1.2)],
            "waste_basket": [(8.0, -1.0), (9.5, -1.0)],
            "bollard": [(14.0, -0.8), (15.0, -0.8), (16.0, -0.8)],
        },
        bbox_m=(-30.0, -12.0, 30.0, 15.0),
        origin_utm=(0.0, 0.0),
        utm_epsg=32649,
    )

    monkeypatch.setattr(osm_ingest, "fetch_osm_data", lambda **kwargs: {"elements": []})
    monkeypatch.setattr(osm_ingest, "parse_osm_features", lambda raw: osm_ingest.OsmFeatures())
    monkeypatch.setattr(osm_ingest, "project_to_local", lambda features, bbox: projected)


# ---------------------------------------------------------------------------
# Template mode backward compatibility
# ---------------------------------------------------------------------------


def test_template_backward_compat(tmp_path: Path, monkeypatch):
    """layout_mode=template + constraint_mode=off produces same structure as M4."""
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    config = StreetComposeConfig(
        query="modern clean urban street",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
        layout_mode="template",
        constraint_mode="off",
    )
    result = compose_street_scene(
        config=config,
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

    # All placements should have default constraint fields
    for p in result.placements:
        assert p.constraint_penalty == 0.0
        assert p.feasibility_score == 1.0
        assert p.violated_rules == ()


# ---------------------------------------------------------------------------
# Soft constraint adds fields
# ---------------------------------------------------------------------------


def test_soft_constraint_fields_present(tmp_path: Path, monkeypatch):
    """constraint_mode=soft (template mode) should have constraint fields in output."""
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    config = StreetComposeConfig(
        query="modern clean urban street",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
        layout_mode="template",
        constraint_mode="soft",  # soft but with template → empty POI → no penalty
    )
    result = compose_street_scene(
        config=config,
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
        export_format="both",
        out_dir=tmp_path / "artifacts",
    )
    assert result.instance_count > 0

    # In template + soft, POI context is empty so penalty should be 0
    for p in result.placements:
        assert p.constraint_penalty == 0.0
        assert p.feasibility_score == 1.0
        assert p.violated_rules == ()


# ---------------------------------------------------------------------------
# Summary has compliance keys
# ---------------------------------------------------------------------------


def test_summary_has_compliance_keys(tmp_path: Path, monkeypatch):
    """scene_layout.json summary should contain M5 compliance fields."""
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    config = StreetComposeConfig(
        query="modern clean urban street",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
        constraint_mode="soft",
    )
    result = compose_street_scene(
        config=config,
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
        export_format="both",
        out_dir=tmp_path / "artifacts",
    )

    layout_path = result.outputs.get("scene_layout", "")
    assert layout_path
    layout_data = json.loads(Path(layout_path).read_text(encoding="utf-8"))
    summary = layout_data["summary"]

    # M5 compliance fields must be present
    assert "layout_mode" in summary
    assert "constraint_mode" in summary
    assert "compliance_rate_total" in summary
    assert "violations_total" in summary
    assert "rule_violation_counts" in summary
    assert "avg_constraint_penalty" in summary
    assert "avg_feasibility_score" in summary


# ---------------------------------------------------------------------------
# Constraint mode off: default values
# ---------------------------------------------------------------------------


def test_constraint_off_default_values(tmp_path: Path, monkeypatch):
    """With constraint_mode=off, new fields should have their defaults."""
    pytest.importorskip("trimesh")
    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])

    config = StreetComposeConfig(
        query="modern clean urban street",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
        constraint_mode="off",
    )
    result = compose_street_scene(
        config=config,
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
        export_format="both",
        out_dir=tmp_path / "artifacts",
    )
    for p in result.placements:
        assert p.constraint_penalty == 0.0
        assert p.feasibility_score == 1.0
        assert p.violated_rules == ()

    # Check no overlap (AABB constraint still active)
    bboxes = [p.bbox_xz for p in result.placements]
    for i, a in enumerate(bboxes):
        for j, b in enumerate(bboxes):
            if j <= i:
                continue
            intersects = not (a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2])
            assert not intersects, f"overlap between {i} and {j}"


def test_osm_mode_preserves_poi_counts_when_constraint_off(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("shapely")
    pytest.importorskip("gradio")
    import scripts.m1_gradio_app as app

    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])
    _setup_fake_osm(monkeypatch)

    config = StreetComposeConfig(
        query="transit street with poi",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
        layout_mode="osm",
        constraint_mode="off",
        aoi_bbox=(113.2660, 23.1280, 113.2710, 23.1325),
        selected_road_osm_id=202,
        road_selection="primary_road",
        osm_cache_dir=str(tmp_path / "osm_cache"),
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

    layout_data = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = layout_data["summary"]
    spatial_ctx = summary["spatial_context"]
    street_program = layout_data["street_program"]
    solver = layout_data["solver"]
    assert len(spatial_ctx["entrance_points_xz"]) == 2
    assert len(spatial_ctx["bus_stop_points_xz"]) == 1
    assert len(spatial_ctx["fire_points_xz"]) == 1
    assert summary.get("poi_exclusion_zones", []) == []
    assert summary["selected_road_effective_poi_count"] == 4
    assert summary["selected_road_effective_poi_score"] == pytest.approx(4.8)
    assert summary["selected_road_core_poi_count"] == 4
    assert summary["observed_poi_counts"] == {"entrance": 2, "bus_stop": 1, "fire_hydrant": 1}
    assert street_program["observed_poi_counts"] == {"entrance": 2, "bus_stop": 1, "fire_hydrant": 1}
    assert street_program["furniture_requirements"]["bus_stop"] >= 1
    assert street_program["furniture_requirements"]["hydrant"] >= 1
    assert "transit_stop" in street_program["control_points"]
    assert any(
        slot["category"] == "bus_stop" and slot.get("anchor_poi_type") == "bus_stop"
        for slot in solver["slot_plans"]
    )
    assert any(
        slot["category"] == "hydrant" and slot.get("anchor_poi_type") == "fire_hydrant"
        for slot in solver["slot_plans"]
    )
    assert any(placement["category"] == "bus_stop" for placement in layout_data["placements"])
    assert any(placement["category"] == "hydrant" for placement in layout_data["placements"])

    program_summary = json.loads(app._extract_program_summary(json.dumps(layout_data)))
    assert program_summary["poi_counts"] == {"entrance": 2, "bus_stop": 1, "fire_hydrant": 1}
    assert program_summary["observed_poi_counts"] == {"entrance": 2, "bus_stop": 1, "fire_hydrant": 1}
    assert program_summary["total_poi_points"] == 4
    assert program_summary["selected_road_effective_poi_count"] == 4
    assert program_summary["selected_road_effective_poi_score"] == pytest.approx(4.8)
    assert program_summary["exclusion_zone_count"] == 0


def test_osm_mode_soft_constraint_adds_exclusion_zones_but_keeps_poi_counts(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("shapely")
    pytest.importorskip("gradio")
    import scripts.m1_gradio_app as app

    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])
    _setup_fake_osm(monkeypatch)

    config = StreetComposeConfig(
        query="transit street with poi",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
        layout_mode="osm",
        constraint_mode="soft",
        aoi_bbox=(113.2660, 23.1280, 113.2710, 23.1325),
        selected_road_osm_id=202,
        road_selection="primary_road",
        osm_cache_dir=str(tmp_path / "osm_cache"),
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

    layout_data = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = layout_data["summary"]
    spatial_ctx = summary["spatial_context"]
    street_program = layout_data["street_program"]
    assert len(spatial_ctx["entrance_points_xz"]) == 2
    assert len(spatial_ctx["bus_stop_points_xz"]) == 1
    assert len(spatial_ctx["fire_points_xz"]) == 1
    assert len(summary.get("poi_exclusion_zones", [])) > 0
    assert street_program["observed_poi_counts"] == {"entrance": 2, "bus_stop": 1, "fire_hydrant": 1}
    assert street_program["furniture_requirements"]["bus_stop"] >= 1
    assert street_program["furniture_requirements"]["hydrant"] >= 1

    program_summary = json.loads(app._extract_program_summary(json.dumps(layout_data)))
    assert program_summary["poi_counts"] == {"entrance": 2, "bus_stop": 1, "fire_hydrant": 1}
    assert program_summary["observed_poi_counts"] == {"entrance": 2, "bus_stop": 1, "fire_hydrant": 1}
    assert program_summary["total_poi_points"] == 4
    assert program_summary["selected_road_effective_poi_count"] == 4
    assert program_summary["selected_road_effective_poi_score"] == pytest.approx(4.8)
    assert program_summary["exclusion_zone_count"] > 0


def test_osm_mode_fails_when_poi_backed_category_missing_from_inventory(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("shapely")

    rows = [row for row in _build_real_rows(tmp_path / "data") if row["category"] != "bus_stop"]
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])
    _setup_fake_osm(monkeypatch)

    config = StreetComposeConfig(
        query="transit street with poi",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
        layout_mode="osm",
        constraint_mode="off",
        aoi_bbox=(113.2660, 23.1280, 113.2710, 23.1325),
        selected_road_osm_id=202,
        road_selection="primary_road",
        osm_cache_dir=str(tmp_path / "osm_cache"),
    )

    with pytest.raises(RuntimeError, match="bus_stop POIs"):
        compose_street_scene(
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


def test_extended_asset_backed_pois_bind_to_program_and_slots(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])
    _setup_fake_osm_with_extended_pois(monkeypatch)

    config = StreetComposeConfig(
        query="neighborhood street with access and street furniture",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=7,
        topk_per_category=20,
        max_trials_per_slot=30,
        layout_mode="osm",
        constraint_mode="soft",
        aoi_bbox=(113.2660, 23.1280, 113.2710, 23.1325),
        selected_road_osm_id=303,
        road_selection="primary_road",
        osm_cache_dir=str(tmp_path / "osm_cache"),
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

    layout_data = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    street_program = layout_data["street_program"]
    solver = layout_data["solver"]

    assert street_program["observed_poi_counts"]["post_box"] == 1
    assert street_program["observed_poi_counts"]["waste_basket"] == 2
    assert street_program["observed_poi_counts"]["bollard"] == 3
    assert street_program["furniture_requirements"]["mailbox"] >= 1
    assert street_program["furniture_requirements"]["trash"] >= 1
    assert street_program["furniture_requirements"]["bollard"] >= 1
    assert "crossing" in street_program["control_points"]

    slot_plans = solver["slot_plans"]
    assert any(slot["category"] == "mailbox" and slot.get("anchor_poi_type") == "post_box" for slot in slot_plans)
    assert any(slot["category"] == "trash" and slot.get("anchor_poi_type") == "waste_basket" for slot in slot_plans)
    assert any(slot["category"] == "bollard" and slot.get("anchor_poi_type") == "bollard" for slot in slot_plans)


# ---------------------------------------------------------------------------
# Placement to_dict includes violated_rules as list
# ---------------------------------------------------------------------------


def test_placement_to_dict_violated_rules_is_list():
    """StreetPlacement.to_dict() should convert violated_rules tuple to list."""
    from roadgen3d.types import StreetPlacement

    p = StreetPlacement(
        instance_id="inst_0001",
        asset_id="bench_01",
        category="bench",
        score=0.9,
        position_xyz=[1.0, 0.0, 2.0],
        yaw_deg=90.0,
        scale=1.0,
        bbox_xz=[0.5, 1.5, 1.5, 2.5],
        selection_source="faiss_softmax",
        constraint_penalty=0.5,
        feasibility_score=0.6,
        violated_rules=("entrance_clearance",),
    )
    d = p.to_dict()
    assert isinstance(d["violated_rules"], list)
    assert d["violated_rules"] == ["entrance_clearance"]
    assert d["constraint_penalty"] == 0.5
    assert d["feasibility_score"] == 0.6
