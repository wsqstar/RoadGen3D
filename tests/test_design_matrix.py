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

from roadgen3d.services.design_matrix import DesignMatrixService
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
    old_layout = _write_matrix_layout(tmp_path / "old" / "scene_layout.json", target["metadata"], b"old")
    new_layout = _write_matrix_layout(tmp_path / "new" / "scene_layout.json", target["metadata"], b"new")
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
    assert Path(payload["outputs"]["scene_glb"]).exists()
    assert len(list(layout_path.parent.glob("*.glb"))) == 1


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
    assert request["draft"]["compose_config_patch"]["street_furniture_profile"] == "transit_priority"


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


def _write_matrix_layout(path: Path, metadata: dict, glb_bytes: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    glb = path.parent / "scene.glb"
    glb.write_bytes(glb_bytes)
    path.write_text(
        json.dumps({
            "summary": {"design_matrix_cell": metadata},
            "outputs": {"scene_glb": str(glb)},
            "production_steps": [],
        }),
        encoding="utf-8",
    )
    return path


def _write_source_layout(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    final_glb = path.parent / "final.glb"
    buildings_glb = path.parent / "buildings.glb"
    final_glb.write_bytes(b"final")
    buildings_glb.write_bytes(b"buildings")
    path.write_text(
        json.dumps({
            "summary": {"instance_count": 12, "production_step_count": 1},
            "outputs": {"scene_glb": str(final_glb)},
            "production_steps": [
                {"step_id": "buildings", "title": "Buildings", "glb_path": str(buildings_glb)}
            ],
        }),
        encoding="utf-8",
    )
    return path
