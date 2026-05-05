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
    placements = []
    for idx in range(4):
        x = -42.0 + idx * 28.0
        z = 7.0 if idx % 2 == 0 else -7.0
        placements.append({
            "instance_id": f"bench_{idx:02d}",
            "asset_id": "bench_modern_production",
            "category": "bench",
            "position_xyz": [x, 0.0, z],
            "bbox_xz": [x - 1.0, x + 1.0, z - 0.4, z + 0.4],
            "yaw_deg": 0.0,
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
        "placements": placements,
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


def test_review_expanded_capture_target_planner_adds_human_and_building_views():
    plan = plan_capture_targets(_layout_payload(), profile="review_expanded")

    assert len(plan["targets"]) == 40
    kinds = {target["kind"] for target in plan["targets"]}
    assert {"bench_eye", "junction_pedestrian", "rooftop", "window_view"}.issubset(kinds)


def test_capture_success_patches_layout_and_deletes_non_retained_glb(tmp_path: Path, monkeypatch):
    layout_path = tmp_path / "scene_layout.json"
    glb_path = tmp_path / "scene.glb"
    glb_path.write_bytes(b"glb")
    payload = _layout_payload(building_count=2)
    payload["outputs"] = {"scene_glb": str(glb_path), "scene_layout": str(layout_path)}
    layout_path.write_text(json.dumps(payload), encoding="utf-8")
    stale_image = tmp_path / "view_captures" / "stale.png"
    stale_image.parent.mkdir()
    stale_image.write_bytes(b"old")

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
    assert not stale_image.exists()
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


def test_rendered_views_for_evaluation_selects_representative_3d_captures(tmp_path: Path):
    capture_dir = tmp_path / "view_captures"
    capture_dir.mkdir()
    capture_views = []
    kinds = [
        "street",
        "junction_pedestrian",
        "junction_pedestrian",
        "bench_eye",
        "window_view",
        "rooftop",
        "overview",
        "junction",
        "building",
        *("street" for _ in range(31)),
    ]
    for index, kind in enumerate(kinds, start=1):
        path = capture_dir / f"{index:02d}_{kind}.png"
        path.write_bytes(f"{kind}-{index}".encode("utf-8"))
        capture_views.append({
            "view_id": f"{kind}_{index}",
            "label": f"{kind} view {index}",
            "kind": kind,
            "priority": 100 - index,
            "path": str(path),
            "camera": [float(index), 1.5, 0.0],
            "target": [float(index), 0.0, 4.0],
        })
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(
        json.dumps({
            "summary": {
                "render_views_3d": capture_views,
                "render_views": [],
            }
        }),
        encoding="utf-8",
    )

    views = _rendered_views_for_evaluation(str(layout_path))

    assert len(views) == 8
    selected_kinds = [view["kind"] for view in views]
    assert selected_kinds[:7] == [
        "street",
        "junction_pedestrian",
        "junction_pedestrian",
        "bench_eye",
        "window_view",
        "rooftop",
        "overview",
    ]
    assert selected_kinds[7] == "junction"
    assert views[0]["camera"] == [1.0, 1.5, 0.0]
    assert all(view["image_data_url"].startswith("data:image/png;base64,") for view in views)


def test_rendered_views_for_evaluation_falls_back_to_legacy_render_views(tmp_path: Path):
    legacy = tmp_path / "legacy.png"
    legacy.write_bytes(b"legacy")
    layout_path = tmp_path / "scene_layout.json"
    layout_path.write_text(
        json.dumps({
            "summary": {
                "render_views_3d": [],
                "render_views": [
                    {"name": "debug_side", "path": str(tmp_path / "missing.png")},
                    {"name": "final_legacy", "title": "Final legacy", "path": str(legacy)},
                ],
            }
        }),
        encoding="utf-8",
    )

    views = _rendered_views_for_evaluation(str(layout_path), limit=8)

    assert [view["view_id"] for view in views] == ["final_legacy"]
    assert views[0]["label"] == "Final legacy"
