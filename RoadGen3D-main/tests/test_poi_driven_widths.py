from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.cross_section_synthesis import synthesize_poi_driven_cross_section
from roadgen3d.osm_ingest import OsmRoad, ProjectedFeatures
from roadgen3d.placement_zones import evaluate_projected_road_context
from roadgen3d.types import StreetComposeConfig

pytest.importorskip("gradio")
import scripts.m1_gradio_app as app


def test_poi_driven_cross_section_expands_only_poi_heavy_side():
    road = OsmRoad(
        osm_id=1,
        highway_type="secondary",
        coords=[(-20.0, 0.0), (20.0, 0.0)],
        width_m=8.0,
    )

    result = synthesize_poi_driven_cross_section(
        roads=[road],
        poi_points_by_type={
            "entrance": [(0.0, 7.5), (4.0, 7.0)],
            "bus_stop": [(10.0, 6.8)],
        },
        road_width_m=8.0,
        lane_count=2,
        sidewalk_seed_width_m=2.5,
        base_lane_width_m=None,
        min_clear_path_width_m=2.4,
        left_furnishing_min_width_m=1.0,
        right_furnishing_min_width_m=1.0,
    )

    assert result.poi_fit_feasible is True
    assert result.left_sidewalk_width_m > result.right_sidewalk_width_m
    assert result.required_left_width_m > result.required_right_width_m
    assert result.width_expanded is True


def test_poi_driven_cross_section_respects_explicit_base_lane_width():
    road = OsmRoad(
        osm_id=2,
        highway_type="primary",
        coords=[(-30.0, 0.0), (30.0, 0.0)],
        width_m=12.0,
    )

    result = synthesize_poi_driven_cross_section(
        roads=[road],
        poi_points_by_type={"entrance": [(0.0, 5.8)]},
        road_width_m=10.5,
        lane_count=2,
        sidewalk_seed_width_m=2.5,
        base_lane_width_m=3.0,
        min_clear_path_width_m=2.4,
        left_furnishing_min_width_m=1.0,
        right_furnishing_min_width_m=1.0,
    )

    assert result.carriageway_width_m == pytest.approx(6.0)
    assert "reallocated" in result.width_reallocation_reason


def test_evaluate_projected_road_context_uses_dynamic_sidewalk_widths():
    projected = ProjectedFeatures(
        roads=[
            OsmRoad(
                osm_id=10,
                highway_type="secondary",
                coords=[(-25.0, 0.0), (25.0, 0.0)],
                width_m=8.0,
            )
        ],
        poi_points_by_type={
            "entrance": [(0.0, 6.5)],
            "post_box": [(5.0, 5.6)],
        },
        bbox_m=(-30.0, -12.0, 30.0, 12.0),
        origin_utm=(0.0, 0.0),
        utm_epsg=32649,
    )
    config = StreetComposeConfig(
        query="walkable street",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=1,
        topk_per_category=10,
        max_trials_per_slot=10,
        layout_mode="osm",
        constraint_mode="off",
        aoi_bbox=(113.0, 23.0, 113.01, 23.01),
        selected_road_osm_id=10,
    )

    _filtered, placement_ctx, poi_counts = evaluate_projected_road_context(projected, config)

    assert placement_ctx.poi_fit_feasible is True
    assert placement_ctx.left_clear_path_width_m > 2.4
    assert placement_ctx.left_sidewalk_zone is not None
    assert poi_counts["entrance"] == 1
    assert poi_counts["post_box"] == 1


def test_auto_discovered_road_selection_skips_poi_fit_infeasible_candidate(tmp_path: Path, monkeypatch):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    discovered_path = artifacts_dir.parent / "m5" / "discovered_poi_roads.jsonl"
    discovered_path.parent.mkdir(parents=True, exist_ok=True)
    discovered_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "osm_id": 101,
                        "bbox": [113.0, 23.0, 113.01, 23.01],
                        "poi_count": 3,
                        "poi_score": 3.2,
                        "core_poi_count": 2,
                        "poi_types": {"entrance": 2, "bus_stop": 1},
                    }
                ),
                json.dumps(
                    {
                        "osm_id": 202,
                        "bbox": [113.1, 23.1, 113.11, 23.11],
                        "poi_count": 3,
                        "poi_score": 3.4,
                        "core_poi_count": 2,
                        "poi_types": {"entrance": 2, "bus_stop": 1},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "_discovered_cache_matches", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        app,
        "_probe_discovered_road_context_metrics",
        lambda row, **kwargs: {
            "poi_counts": {"entrance": 2, "bus_stop": 1} if int(row["osm_id"]) == 101 else {"entrance": 2, "bus_stop": 1},
            "poi_fit_feasible": int(row["osm_id"]) == 202,
            "poi_fit_report": {"road": int(row["osm_id"])},
            "required_left_width_m": 3.0,
            "required_right_width_m": 3.0,
            "row_width_m": 15.0,
        },
    )

    selected, auto_discovered, probe_metrics = app._select_auto_discovered_road(
        artifacts_dir=artifacts_dir,
        osm_cache_dir=tmp_path / "osm_cache",
        aoi_bbox=(113.0, 23.0, 113.2, 23.2),
        seed=7,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        road_selection="primary_road",
    )

    assert auto_discovered is False
    assert int(selected["osm_id"]) == 202
    assert probe_metrics["poi_fit_feasible"] is True


def test_auto_discovered_road_selection_prefers_walkable_neighborhood_types(tmp_path: Path, monkeypatch):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    discovered_path = artifacts_dir.parent / "m5" / "discovered_poi_roads.jsonl"
    discovered_path.parent.mkdir(parents=True, exist_ok=True)
    discovered_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "osm_id": 101,
                        "bbox": [113.0, 23.0, 113.01, 23.01],
                        "highway_type": "secondary",
                        "road_length_m": 120.0,
                        "poi_count": 3,
                        "poi_score": 3.2,
                        "core_poi_count": 2,
                        "poi_types": {"entrance": 2, "bus_stop": 1},
                    }
                ),
                json.dumps(
                    {
                        "osm_id": 202,
                        "bbox": [113.1, 23.1, 113.11, 23.11],
                        "highway_type": "tertiary",
                        "road_length_m": 110.0,
                        "poi_count": 3,
                        "poi_score": 3.0,
                        "core_poi_count": 2,
                        "poi_types": {"entrance": 2, "bus_stop": 1},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "_discovered_cache_matches", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        app,
        "_probe_discovered_road_context_metrics",
        lambda row, **kwargs: {
            "poi_counts": {"entrance": 2, "bus_stop": 1},
            "poi_fit_feasible": True,
            "poi_fit_report": {"road": int(row["osm_id"])},
            "required_left_width_m": 2.8,
            "required_right_width_m": 2.6,
            "row_width_m": 14.0,
        },
    )

    selected, auto_discovered, probe_metrics = app._select_auto_discovered_road(
        artifacts_dir=artifacts_dir,
        osm_cache_dir=tmp_path / "osm_cache",
        aoi_bbox=(113.0, 23.0, 113.2, 23.2),
        seed=7,
        road_width_m=7.0,
        sidewalk_width_m=2.4,
        lane_count=2,
        road_selection="walkable_neighborhood",
    )

    assert auto_discovered is False
    assert int(selected["osm_id"]) == 202
    assert str(selected["highway_type"]) == "tertiary"
    assert probe_metrics["poi_fit_feasible"] is True
