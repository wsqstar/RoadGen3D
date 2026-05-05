from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.capture_3d import capture_views_for_layout, plan_capture_targets  # noqa: E402
from roadgen3d.services.branch_runs import _rendered_views_for_evaluation  # noqa: E402


def _layout_payload(building_count: int = 30) -> dict:
    footprints = []
    for idx in range(building_count):
        x = -45.0 + idx * 4.0
        z = 14.0 if idx % 2 == 0 else -14.0
        footprints.append({
            "footprint_id": f"region_{idx:02d}",
            "source": "building_region",
            "centroid_xz": [x, z],
            "polygon_xz": [[x - 1, z - 1], [x + 1, z - 1], [x + 1, z + 1], [x - 1, z + 1]],
            "side": "right" if z > 0 else "left",
            "target_height_m": 12.0,
        })
    return {
        "config": {"length_m": 120.0},
        "summary": {
            "spatial_context": {
                "junction_points_xz": [[-36.0, 0.0], [-12.0, 0.0], [12.0, 0.0], [36.0, 0.0]],
                "entrance_points_xz": [[-50.0, 6.0], [50.0, -6.0]],
                "road_half_width_m": 4.5,
            }
        },
        "placements": [],
        "building_footprints": footprints,
        "outputs": {},
    }


def test_review_24_capture_target_planner_is_deterministic_and_budgeted():
    payload = _layout_payload()
    first = plan_capture_targets(payload, profile="review_24")
    second = plan_capture_targets(payload, profile="review_24")

    assert first == second
    assert len(first["targets"]) == 24
    assert first["skipped_targets"]
    kinds = [target["kind"] for target in first["targets"]]
    assert "pedestrian" in kinds
    assert kinds.count("junction") == 4
    assert "overview" in kinds
    assert "building" in kinds


def test_capture_success_patches_layout_and_deletes_non_retained_glb(tmp_path: Path, monkeypatch):
    layout_path = tmp_path / "scene_layout.json"
    glb_path = tmp_path / "scene.glb"
    glb_path.write_bytes(b"glb")
    payload = _layout_payload(building_count=2)
    payload["outputs"] = {"scene_glb": str(glb_path), "scene_layout": str(layout_path)}
    layout_path.write_text(json.dumps(payload), encoding="utf-8")

    def fake_capture(**kwargs):
        out_dir = Path(kwargs["out_dir"])
        targets = json.loads(Path(kwargs["target_file"]).read_text(encoding="utf-8"))["targets"]
        views = []
        for index, target in enumerate(targets[:3]):
            image_path = out_dir / f"{index:02d}_{target['target_id']}.png"
            image_path.write_bytes(b"png")
            views.append({
                "target_id": target["target_id"],
                "path": str(image_path),
                "width": kwargs["width"],
                "height": kwargs["height"],
            })
        return {"views": views}

    monkeypatch.setattr("roadgen3d.capture_3d._run_playwright_capture", fake_capture)

    result = capture_views_for_layout(
        layout_path=layout_path,
        scene_glb_path=glb_path,
        options={
            "capture_profile": "quick_12",
            "capture_resolution": [320, 180],
            "retain_glb_policy": "top_k",
        },
    )

    assert result.status == "succeeded"
    assert result.view_count == 3
    assert result.glb_deleted is True
    assert not glb_path.exists()
    patched = json.loads(layout_path.read_text(encoding="utf-8"))
    assert patched["outputs"]["scene_glb"] == ""
    assert Path(patched["outputs"]["capture_manifest"]).exists()
    assert len(patched["summary"]["render_views_3d"]) == 3
    assert patched["summary"]["capture_3d"]["status"] == "succeeded"


def test_capture_failure_keeps_glb_and_records_warning(tmp_path: Path, monkeypatch):
    layout_path = tmp_path / "scene_layout.json"
    glb_path = tmp_path / "scene.glb"
    glb_path.write_bytes(b"glb")
    payload = _layout_payload(building_count=1)
    payload["outputs"] = {"scene_glb": str(glb_path)}
    layout_path.write_text(json.dumps(payload), encoding="utf-8")

    def fail_capture(**_kwargs):
        raise RuntimeError("playwright unavailable")

    monkeypatch.setattr("roadgen3d.capture_3d._run_playwright_capture", fail_capture)

    result = capture_views_for_layout(
        layout_path=layout_path,
        scene_glb_path=glb_path,
        options={"capture_failure_policy": "warn"},
    )

    assert result.status == "failed"
    assert glb_path.exists()
    patched = json.loads(layout_path.read_text(encoding="utf-8"))
    assert patched["outputs"]["scene_glb"] == str(glb_path)
    assert patched["summary"]["capture_3d"]["error"] == "playwright unavailable"


def test_rendered_views_for_evaluation_prefers_backend_3d_captures(tmp_path: Path):
    capture_dir = tmp_path / "view_captures"
    capture_dir.mkdir()
    overview = capture_dir / "overview.png"
    street = capture_dir / "street.png"
    junction = capture_dir / "junction.png"
    overview.write_bytes(b"overview")
    street.write_bytes(b"street")
    junction.write_bytes(b"junction")
    legacy = tmp_path / "legacy.png"
    legacy.write_bytes(b"legacy")
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(
        json.dumps({
            "summary": {
                "render_views_3d": [
                    {"view_id": "overview_top", "kind": "overview", "priority": 80, "path": str(overview)},
                    {"view_id": "street_1", "kind": "street", "priority": 70, "path": str(street)},
                    {"view_id": "junction_1", "kind": "junction", "priority": 90, "path": str(junction)},
                ],
                "render_views": [
                    {"name": "final_legacy", "path": str(legacy)},
                ],
            }
        }),
        encoding="utf-8",
    )

    views = _rendered_views_for_evaluation(str(layout_path), limit=3)

    assert [view["view_id"] for view in views] == ["street_1", "junction_1", "overview_top"]
    assert all(view["image_data_url"].startswith("data:image/png;base64,") for view in views)
