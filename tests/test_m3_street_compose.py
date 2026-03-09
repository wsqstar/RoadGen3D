from __future__ import annotations

import json
import random
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

from roadgen3d.types import RetrievalHit, StreetComposeConfig, StreetComposeResult, StreetPlacement
from roadgen3d.street_layout import compose_street_scene
import roadgen3d.street_layout as street_layout


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

    monkeypatch.setattr(app, "compose_street_scene", fake_compose)
    summary, rows, layout_json, model_path, files = app.run_street_compose(
        dataset_profile="real",
        query="urban street",
        real_manifest_text=str(tmp_path / "real_assets_manifest.jsonl"),
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
    assert layout_json
    assert any(str(path).endswith("scene_layout.json") for path in files)


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
