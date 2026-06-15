from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.design_matrix import DesignMatrixService, _glb_has_street_furniture
from web.api.main import create_app


class _FakeScenarioDesignService:
    def __init__(self, *, preview_layout_path: str = "") -> None:
        self.preview_layout_path = preview_layout_path

    def list_scenarios(self) -> dict:
        return {
            "items": [
                {
                    "scenario_id": f"scenario_{index:02d}",
                    "title_zh": f"方案 {index}",
                    "enabled": True,
                    "query": f"scenario {index}",
                    "preview_layout_path": self.preview_layout_path,
                    "compose_config_patch": {
                        "road_width_m": 8.0 + index,
                        "skeleton_design_profile": "transit_priority" if index == 3 else "walkable_commercial",
                    },
                }
                for index in range(1, 8)
            ]
        }


def _service(tmp_path: Path, *, preview_layout_path: str = "") -> DesignMatrixService:
    return DesignMatrixService(
        design_service=object(),
        scenario_design_service=_FakeScenarioDesignService(preview_layout_path=preview_layout_path),
        artifact_root=tmp_path / "matrix_artifacts",
        recent_roots=(tmp_path,),
        cache_for_viewer=False,
    )


def test_design_matrix_inventory_builds_stable_72_cell_grid(tmp_path: Path) -> None:
    service = _service(tmp_path)

    payload = service.inventory({
        "graph_template_id": "hkust_gz_gate",
        "custom_structure": {
            "scenario_id": "draft_bus_stop",
            "title_zh": "临时结构",
            "query": "add a bus stop",
            "template_patch": {"operations": []},
        },
        "custom_furniture": {
            "prompt": "more seating and softer lighting",
            "compose_config_patch": {"street_furniture_profile": "pedestrian_friendly"},
        },
    })

    assert len(payload["rows"]) == 9
    assert len(payload["columns"]) == 8
    assert len(payload["cells"]) == 72
    keys = [cell["cell_key"] for cell in payload["cells"]]
    assert len(keys) == len(set(keys))
    assert payload["cells"][0]["cell_key"] == service.inventory({
        "graph_template_id": "hkust_gz_gate",
        "custom_structure": {
            "scenario_id": "draft_bus_stop",
            "title_zh": "临时结构",
            "query": "add a bus stop",
            "template_patch": {"operations": []},
        },
        "custom_furniture": {
            "prompt": "more seating and softer lighting",
            "compose_config_patch": {"street_furniture_profile": "pedestrian_friendly"},
        },
    })["cells"][0]["cell_key"]


def test_design_matrix_recent_scan_keeps_latest_layout_per_cell(tmp_path: Path) -> None:
    service = _service(tmp_path)
    inventory = service.inventory({"graph_template_id": "hkust_gz_gate"})
    target = next(
        cell
        for cell in inventory["cells"]
        if cell["structure_key"] == "scenario:scenario_03" and cell["furniture_key"] == "preset:transit_priority"
    )
    old_layout = _write_matrix_layout(
        tmp_path / "old" / "scene_layout.json",
        target["metadata"],
        b"old",
        placements=[{"category": "tree"}],
    )
    new_layout = _write_matrix_layout(
        tmp_path / "new" / "scene_layout.json",
        target["metadata"],
        b"new",
        placements=[{"category": "tree"}],
    )
    os.utime(old_layout, (1000, 1000))
    os.utime(new_layout, (2000, 2000))

    refreshed = service.inventory({"graph_template_id": "hkust_gz_gate"})
    refreshed_cell = next(cell for cell in refreshed["cells"] if cell["cell_key"] == target["cell_key"])

    assert refreshed_cell["status"] == "ready"
    assert refreshed_cell["layout_path"] == str(new_layout)


def test_design_matrix_no_furniture_materializes_buildings_step_only(tmp_path: Path) -> None:
    source_layout = _write_source_layout(tmp_path / "source" / "scene_layout.json")
    service = _service(tmp_path, preview_layout_path=str(source_layout))

    result = service.prepare_generate({
        "graph_template_id": "hkust_gz_gate",
        "structure_key": "scenario:scenario_03",
        "furniture_key": "none",
    })

    assert result["mode"] == "materialized"
    layout_path = Path(result["layout_path"])
    payload = json.loads(layout_path.read_text(encoding="utf-8"))
    assert payload["production_steps"] == []
    assert payload["summary"]["design_matrix_cell"]["furniture_key"] == "none"
    assert payload["summary"]["street_furniture_profile"] == "none"
    assert payload["placements"] == []
    assert payload["scene_graph"]["filters"]["categories"] == ["building"]
    assert all(node.get("category") != "bench" for node in payload["scene_graph"]["nodes"])
    assert all(node.get("category") != "tree" for node in payload["scene_graph"]["nodes"])
    assert Path(payload["outputs"]["scene_glb"]).exists()
    assert len(list(layout_path.parent.glob("*.glb"))) == 1


def test_design_matrix_no_furniture_skips_contaminated_buildings_step(tmp_path: Path) -> None:
    source_layout = _write_contaminated_no_furniture_source_layout(tmp_path / "source" / "scene_layout.json")
    service = _service(tmp_path, preview_layout_path=str(source_layout))

    result = service.prepare_generate({
        "graph_template_id": "hkust_gz_gate",
        "structure_key": "scenario:scenario_03",
        "furniture_key": "none",
    })

    payload = json.loads(Path(result["layout_path"]).read_text(encoding="utf-8"))
    source_road_base = source_layout.parent / "road_base.glb"
    copied_glb = Path(payload["outputs"]["scene_glb"])
    assert payload["summary"]["design_matrix_cell"]["materialized_from_step"] == "road_base"
    assert copied_glb.read_bytes() == source_road_base.read_bytes()


def test_design_matrix_inventory_ignores_contaminated_no_furniture_ready_cell(tmp_path: Path) -> None:
    source_layout = _write_source_layout(tmp_path / "source" / "scene_layout.json")
    service = _service(tmp_path, preview_layout_path=str(source_layout))
    inventory = service.inventory({"graph_template_id": "hkust_gz_gate"})
    target = next(
        cell
        for cell in inventory["cells"]
        if cell["structure_key"] == "scenario:scenario_03" and cell["furniture_key"] == "none"
    )
    _write_matrix_layout(tmp_path / "ready" / "scene_layout.json", target["metadata"], _minimal_glb(["inst_001_Bench_Bench_Metal"]))

    refreshed = service.inventory({"graph_template_id": "hkust_gz_gate"})
    refreshed_cell = next(cell for cell in refreshed["cells"] if cell["cell_key"] == target["cell_key"])

    assert refreshed_cell["status"] != "ready"
    assert refreshed_cell["layout_path"] == ""


def test_design_matrix_no_furniture_glb_check_ignores_sky_dome_asset_name(tmp_path: Path) -> None:
    glb_path = tmp_path / "structure_preview.glb"
    glb_path.write_bytes(_minimal_glb(["objaverse_tree_a90b8cca57b44f5492e796cf94d64e80-sky-dome.glb"]))

    assert _glb_has_street_furniture(glb_path) is False


def test_design_matrix_scene_job_request_disables_extra_glbs_and_carries_metadata(tmp_path: Path) -> None:
    service = _service(tmp_path)

    prepared = service.prepare_generate({
        "graph_template_id": "hkust_gz_gate",
        "structure_key": "scenario:scenario_03",
        "furniture_key": "preset:transit_priority",
    })

    assert prepared["mode"] == "job"
    request = prepared["scene_job_request"]
    options = request["generation_options"]
    assert options["build_production_artifacts"] is False
    assert options["render_presentation_artifacts"] is False
    assert options["capture_3d_views"] is False
    assert options["export_format"] == "glb"
    assert options["design_matrix_cell"]["structure_key"] == "scenario:scenario_03"
    assert options["design_matrix_cell"]["furniture_key"] == "preset:transit_priority"
    compose_patch = request["draft"]["compose_config_patch"]
    assert compose_patch["street_furniture_profile"] == "transit_priority"
    assert compose_patch["furniture_balance_policy"] == "overall_balanced"
    assert compose_patch["street_furniture_distribution_policy"] == "road_uniform_v1"
    assert "tree" in compose_patch["minimum_category_presence"]
    assert "tree" not in compose_patch["optional_category_presence"]


def test_design_matrix_non_none_furniture_requests_include_tree(tmp_path: Path) -> None:
    service = _service(tmp_path)

    for column in service.furniture_options():
        if column.key == "none" or not column.enabled:
            continue
        prepared = service.prepare_generate({
            "graph_template_id": "hkust_gz_gate",
            "structure_key": "scenario:scenario_03",
            "furniture_key": column.key,
        })
        compose_patch = prepared["scene_job_request"]["draft"]["compose_config_patch"]
        assert "tree" in compose_patch["minimum_category_presence"]
        assert "tree" not in compose_patch["optional_category_presence"]


def test_design_matrix_balanced_complete_preview_uses_compact_density_and_expires_dense_ready_cell(tmp_path: Path) -> None:
    service = _service(tmp_path)

    prepared = service.prepare_generate({
        "graph_template_id": "hkust_gz_gate",
        "structure_key": "scenario:scenario_06",
        "furniture_key": "preset:balanced_complete",
    })
    compose_patch = prepared["scene_job_request"]["draft"]["compose_config_patch"]

    assert compose_patch["furniture_balance_policy"] == "overall_balanced"
    assert compose_patch["density"] == 0.22
    assert compose_patch["street_furniture_distribution_policy"] == "road_uniform_v1"
    assert "tree" in compose_patch["minimum_category_presence"]

    inventory = service.inventory({"graph_template_id": "hkust_gz_gate"})
    target = next(
        cell
        for cell in inventory["cells"]
        if cell["structure_key"] == "scenario:scenario_06" and cell["furniture_key"] == "preset:balanced_complete"
    )
    _write_matrix_layout(
        tmp_path / "dense" / "scene_layout.json",
        target["metadata"],
        _minimal_glb(["scene"]),
        config={
            "density": 0.6,
            "furniture_balance_policy": "overall_balanced",
            "street_furniture_distribution_policy": "road_uniform_v1",
        },
        placements=[{"category": "tree"}] + [{"category": "lamp"} for _ in range(48)],
    )

    refreshed = service.inventory({"graph_template_id": "hkust_gz_gate"})
    refreshed_cell = next(cell for cell in refreshed["cells"] if cell["cell_key"] == target["cell_key"])

    assert refreshed_cell["status"] != "ready"
    assert refreshed_cell["layout_path"] == ""


def test_design_matrix_inventory_accepts_furniture_ready_cell_without_tree(tmp_path: Path) -> None:
    service = _service(tmp_path)
    inventory = service.inventory({"graph_template_id": "hkust_gz_gate"})
    target = next(
        cell
        for cell in inventory["cells"]
        if cell["structure_key"] == "scenario:scenario_03" and cell["furniture_key"] == "preset:transit_priority"
    )
    ready_layout = _write_matrix_layout(
        tmp_path / "ready" / "scene_layout.json",
        target["metadata"],
        _minimal_glb(["scene"]),
        placements=[{"category": "lamp"}],
    )
    os.utime(ready_layout, (2000, 2000))

    refreshed = service.inventory({"graph_template_id": "hkust_gz_gate"})
    refreshed_cell = next(cell for cell in refreshed["cells"] if cell["cell_key"] == target["cell_key"])

    assert refreshed_cell["status"] == "ready"
    assert refreshed_cell["layout_path"] == str(ready_layout)


def test_design_matrix_inventory_expires_furniture_cell_without_any_furniture(tmp_path: Path) -> None:
    service = _service(tmp_path)
    inventory = service.inventory({"graph_template_id": "hkust_gz_gate"})
    target = next(
        cell
        for cell in inventory["cells"]
        if cell["structure_key"] == "scenario:scenario_03" and cell["furniture_key"] == "preset:transit_priority"
    )
    stale_layout = _write_matrix_layout(
        tmp_path / "stale" / "scene_layout.json",
        target["metadata"],
        _minimal_glb(["scene"]),
        placements=[{"category": "building"}],
    )
    os.utime(stale_layout, (2000, 2000))

    refreshed = service.inventory({"graph_template_id": "hkust_gz_gate"})
    refreshed_cell = next(cell for cell in refreshed["cells"] if cell["cell_key"] == target["cell_key"])

    assert refreshed_cell["status"] != "ready"
    assert refreshed_cell["layout_path"] == ""


def test_design_matrix_balanced_complete_prefers_current_distribution_policy(tmp_path: Path) -> None:
    service = _service(tmp_path)
    inventory = service.inventory({"graph_template_id": "hkust_gz_gate"})
    target = next(
        cell
        for cell in inventory["cells"]
        if cell["structure_key"] == "scenario:scenario_06" and cell["furniture_key"] == "preset:balanced_complete"
    )
    legacy_layout = _write_matrix_layout(
        tmp_path / "legacy" / "scene_layout.json",
        target["metadata"],
        _minimal_glb(["scene"]),
        config={"density": 0.22, "furniture_balance_policy": "side_biased_legacy"},
        placements=[{"category": "tree"}],
    )
    ready_layout = _write_matrix_layout(
        tmp_path / "ready" / "scene_layout.json",
        target["metadata"],
        _minimal_glb(["scene"]),
        config={
            "density": 0.22,
            "furniture_balance_policy": "overall_balanced",
            "street_furniture_distribution_policy": "road_uniform_v1",
        },
        placements=[{"category": "bench"}],
    )
    os.utime(legacy_layout, (3000, 3000))
    os.utime(ready_layout, (2000, 2000))

    refreshed = service.inventory({"graph_template_id": "hkust_gz_gate"})
    refreshed_cell = next(cell for cell in refreshed["cells"] if cell["cell_key"] == target["cell_key"])

    assert refreshed_cell["status"] == "ready"
    assert refreshed_cell["layout_path"] == str(ready_layout)


def test_design_matrix_api_routes_delegate_to_service() -> None:
    app = create_app(design_service=_ApiDesignService())
    app.state.design_matrix_service = _ApiMatrixService()
    client = TestClient(app)

    inventory_response = client.post("/api/design/matrix/inventory", json={"graph_template_id": "hkust_gz_gate"})
    assert inventory_response.status_code == 200
    assert inventory_response.json()["cells"][0]["cell_key"] == "demo-cell"

    generate_response = client.post(
        "/api/design/matrix/cells/generate",
        json={
            "graph_template_id": "hkust_gz_gate",
            "structure_key": "base",
            "furniture_key": "none",
        },
    )
    assert generate_response.status_code == 200
    assert generate_response.json()["mode"] == "materialized"
    assert generate_response.json()["layout_path"] == "/tmp/matrix_layout.json"


class _ApiDesignService:
    default_pdf_path = Path("/tmp/guide.pdf")
    default_artifact_dir = Path("/tmp/knowledge")


class _ApiMatrixService:
    def inventory(self, payload: dict) -> dict:
        return {
            "schema_version": "design_matrix_inventory_v1",
            "graph_template_id": payload["graph_template_id"],
            "rows": [],
            "columns": [],
            "cells": [{"cell_key": "demo-cell", "status": "ready"}],
        }

    def prepare_generate(self, payload: dict) -> dict:
        return {
            "mode": "materialized",
            "layout_path": "/tmp/matrix_layout.json",
            "scene_glb_path": "/tmp/matrix_scene.glb",
            "cell": {"cell_key": "demo-cell", "status": "ready"},
        }


def _write_matrix_layout(
    path: Path,
    metadata: dict,
    glb_bytes: bytes,
    *,
    config: dict | None = None,
    placements: list[dict] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    glb = path.parent / "scene.glb"
    glb.write_bytes(glb_bytes)
    path.write_text(
        json.dumps({
            "summary": {"design_matrix_cell": metadata},
            "outputs": {"scene_glb": str(glb)},
            "production_steps": [],
            "config": config or {},
            "placements": placements or [],
        }),
        encoding="utf-8",
    )
    return path


def _write_source_layout(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    final_glb = path.parent / "final.glb"
    buildings_glb = path.parent / "buildings.glb"
    final_glb.write_bytes(_minimal_glb(["final_scene"]))
    buildings_glb.write_bytes(_minimal_glb(["building_mass"]))
    path.write_text(
        json.dumps({
            "summary": {"instance_count": 12, "production_step_count": 1},
            "outputs": {"scene_glb": str(final_glb)},
            "placements": [{"instance_id": "inst_bench", "category": "bench"}],
            "scene_graph": {
                "nodes": [
                    {"node_id": "road:1", "category": ""},
                    {"node_id": "asset:bench", "category": "bench"},
                    {"node_id": "building:1", "category": "building"},
                ],
                "edges": [
                    {"edge_type": "road_connects", "source_id": "road:1", "target_id": "building:1"},
                    {"edge_type": "placement_realizes_slot", "source_id": "asset:bench", "target_id": "road:1"},
                ],
                "filters": {"categories": ["bench", "building"], "edge_types": ["road_connects", "placement_realizes_slot"]},
                "heatmap_defaults": {"default_category": "bench"},
            },
            "production_steps": [
                {"step_id": "buildings", "title": "Buildings", "glb_path": str(buildings_glb)}
            ],
        }),
        encoding="utf-8",
    )
    return path


def _write_contaminated_no_furniture_source_layout(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    final_glb = path.parent / "final.glb"
    buildings_glb = path.parent / "buildings.glb"
    road_base_glb = path.parent / "road_base.glb"
    final_glb.write_bytes(_minimal_glb(["inst_001_Bench_Bench_Metal"]))
    buildings_glb.write_bytes(_minimal_glb(["inst_002_tree_oak"]))
    road_base_glb.write_bytes(_minimal_glb(["clean_road_base"]))
    path.write_text(
        json.dumps({
            "summary": {"instance_count": 12, "production_step_count": 2},
            "outputs": {"scene_glb": str(final_glb)},
            "production_steps": [
                {"step_id": "buildings", "title": "Buildings", "glb_path": str(buildings_glb)},
                {"step_id": "road_base", "title": "Road Base", "glb_path": str(road_base_glb)},
            ],
        }),
        encoding="utf-8",
    )
    return path


def _minimal_glb(names: list[str]) -> bytes:
    payload = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(names)))}],
        "nodes": [{"name": name} for name in names],
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    raw += b" " * ((4 - len(raw) % 4) % 4)
    total_length = 12 + 8 + len(raw)
    return (
        b"glTF"
        + (2).to_bytes(4, "little")
        + total_length.to_bytes(4, "little")
        + len(raw).to_bytes(4, "little")
        + b"JSON"
        + raw
    )
