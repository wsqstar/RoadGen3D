from __future__ import annotations

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

from roadgen3d.scene_graph_viz import (
    build_scene_graph,
    compute_scene_graph_heatmap,
    ensure_scene_graph,
    plot_scene_graph,
)
from roadgen3d.types import RoadSegmentEdge, RoadSegmentGraph, RoadSegmentNode


def _sample_layout_payload() -> dict:
    return {
        "config": {
            "length_m": 60.0,
            "road_width_m": 8.0,
            "sidewalk_width_m": 2.5,
            "poi_rule_set": "multitype_street_poi_v2",
        },
        "summary": {
            "length_m": 60.0,
            "road_width_m": 8.0,
            "sidewalk_width_m": 2.5,
            "left_clear_path_width_m": 2.4,
            "right_clear_path_width_m": 2.2,
            "left_furnishing_width_m": 1.2,
            "right_furnishing_width_m": 1.0,
            "osm_geometry": {
                "aoi_bbox_m": [-30.0, -10.0, 30.0, 10.0],
            },
            "spatial_context": {
                "junction_points_xz": [[-15.0, 0.0], [15.0, 0.0]],
                "entrance_points_xz": [[-10.0, 3.8]],
                "bus_stop_points_xz": [[8.0, 3.6]],
                "fire_points_xz": [[15.0, -3.2]],
                "poi_points_by_type_xz": {
                    "entrance": [[-10.0, 3.8]],
                    "bus_stop": [[8.0, 3.6]],
                    "fire_hydrant": [[15.0, -3.2]],
                    "crossing": [[0.0, 0.2]],
                    "traffic_signals": [[0.0, 2.8]],
                    "parking_entrance": [[-18.0, -3.5]],
                    "subway_entrance": [[5.0, 3.9]],
                    "post_box": [[-4.0, 3.4]],
                    "waste_basket": [[11.0, 3.2]],
                    "bollard": [[1.0, -3.6]],
                },
                "road_half_width_m": 4.0,
                "length_m": 60.0,
            },
            "poi_exclusion_zones": [
                {"poi_type": "entrance", "position_xz": [-10.0, 3.8], "radius_m": 1.5, "rule_name": "entrance_clearance"},
                {"poi_type": "crossing", "position_xz": [0.0, 0.2], "radius_m": 1.8, "rule_name": "crossing_keep_clear"},
                {"poi_type": "fire_hydrant", "position_xz": [15.0, -3.2], "radius_m": 1.8, "rule_name": "fire_access"},
            ],
            "poi_conflict_assets": [
                {
                    "instance_id": "inst_0003",
                    "slot_id": "slot_bench_003",
                    "category": "bench",
                    "position_xz": [-9.5, 3.5],
                    "violated_rules": ["entrance_clearance"],
                    "constraint_penalty": 0.74,
                }
            ],
        },
        "solver": {
            "slot_plans": [
                {
                    "slot_id": "slot_bus_stop_001",
                    "category": "bus_stop",
                    "band_name": "right_transit_edge",
                    "x_center_m": 8.0,
                    "z_center_m": 3.6,
                    "spacing_m": 10.0,
                    "side": "left",
                    "priority": 1.0,
                    "required": True,
                    "anchor_poi_type": "bus_stop",
                    "anchor_position_xz": [8.0, 3.6],
                },
                {
                    "slot_id": "slot_mailbox_002",
                    "category": "mailbox",
                    "band_name": "left_furnishing",
                    "x_center_m": -4.0,
                    "z_center_m": 3.3,
                    "spacing_m": 8.0,
                    "side": "left",
                    "priority": 0.9,
                    "required": True,
                    "anchor_poi_type": "post_box",
                    "anchor_position_xz": [-4.0, 3.4],
                },
                {
                    "slot_id": "slot_bench_003",
                    "category": "bench",
                    "band_name": "left_furnishing",
                    "x_center_m": -9.5,
                    "z_center_m": 3.5,
                    "spacing_m": 8.0,
                    "side": "left",
                    "priority": 0.7,
                    "required": False,
                    "anchor_poi_type": "",
                    "anchor_position_xz": None,
                },
            ],
        },
        "placements": [
            {
                "instance_id": "inst_0001",
                "slot_id": "slot_bus_stop_001",
                "asset_id": "bus_stop_01",
                "category": "bus_stop",
                "score": 0.98,
                "position_xyz": [8.0, 0.0, 3.6],
                "yaw_deg": 0.0,
                "scale": 1.0,
                "bbox_xz": [7.0, 9.0, 3.0, 4.2],
                "selection_source": "rule",
                "constraint_penalty": 0.0,
                "feasibility_score": 1.0,
                "violated_rules": [],
            },
            {
                "instance_id": "inst_0002",
                "slot_id": "slot_mailbox_002",
                "asset_id": "mailbox_01",
                "category": "mailbox",
                "score": 0.92,
                "position_xyz": [-4.1, 0.0, 3.2],
                "yaw_deg": 0.0,
                "scale": 1.0,
                "bbox_xz": [-4.5, -3.7, 2.8, 3.6],
                "selection_source": "rule",
                "constraint_penalty": 0.0,
                "feasibility_score": 1.0,
                "violated_rules": [],
            },
            {
                "instance_id": "inst_0003",
                "slot_id": "slot_bench_003",
                "asset_id": "bench_01",
                "category": "bench",
                "score": 0.81,
                "position_xyz": [-9.5, 0.0, 3.5],
                "yaw_deg": 0.0,
                "scale": 1.0,
                "bbox_xz": [-10.3, -8.7, 3.0, 4.0],
                "selection_source": "rule",
                "constraint_penalty": 0.74,
                "feasibility_score": 0.48,
                "violated_rules": ["entrance_clearance"],
            },
        ],
    }


def _sample_road_graph() -> RoadSegmentGraph:
    return RoadSegmentGraph(
        nodes=(
            RoadSegmentNode(
                segment_id="seg_0000",
                road_id=1,
                start_xy=(-20.0, 0.0),
                end_xy=(-5.0, 0.0),
                center_xy=(-12.5, 0.0),
                length_m=15.0,
                is_junction=True,
            ),
            RoadSegmentNode(
                segment_id="seg_0001",
                road_id=1,
                start_xy=(-5.0, 0.0),
                end_xy=(10.0, 0.0),
                center_xy=(2.5, 0.0),
                length_m=15.0,
            ),
            RoadSegmentNode(
                segment_id="seg_0002",
                road_id=1,
                start_xy=(10.0, 0.0),
                end_xy=(20.0, 0.0),
                center_xy=(15.0, 0.0),
                length_m=10.0,
                is_junction=True,
            ),
        ),
        edges=(
            RoadSegmentEdge(edge_id="edge_0000", from_segment_id="seg_0000", to_segment_id="seg_0001"),
            RoadSegmentEdge(edge_id="edge_0001", from_segment_id="seg_0001", to_segment_id="seg_0002"),
        ),
        mode="osm",
    )


def _nearest_index(values: list[float], target: float) -> int:
    return min(range(len(values)), key=lambda idx: abs(float(values[idx]) - float(target)))


def test_build_scene_graph_contains_required_node_and_edge_types():
    payload = _sample_layout_payload()
    scene_graph = build_scene_graph(payload, road_segment_graph=_sample_road_graph())

    node_types = {node["node_type"] for node in scene_graph["nodes"]}
    edge_types = {edge["edge_type"] for edge in scene_graph["edges"]}

    assert {"road_segment", "poi", "slot_plan", "placement"} <= node_types
    assert {"road_connects", "slot_on_segment", "placement_realizes_slot", "slot_anchors_poi"} <= edge_types
    assert any(edge["edge_type"] == "slot_anchors_poi" for edge in scene_graph["edges"])
    assert all(placement["slot_id"] for placement in payload["placements"])


def test_heatmap_semantics_are_stable_across_categories():
    payload = _sample_layout_payload()

    bench = compute_scene_graph_heatmap(payload, "bench")
    tree = compute_scene_graph_heatmap(payload, "tree")
    xs = bench["x"]
    zs = bench["z"]
    bus_ix = _nearest_index(xs, 8.0)
    bus_iz = _nearest_index(zs, 3.6)
    entrance_ix = _nearest_index(xs, -10.0)
    entrance_iz = _nearest_index(zs, 3.8)
    far_ix = _nearest_index(xs, 22.0)
    far_iz = _nearest_index(zs, -6.0)

    assert bench["attraction"][bus_iz, bus_ix] > bench["attraction"][far_iz, far_ix]
    assert tree["repulsion"][entrance_iz, entrance_ix] > tree["repulsion"][far_iz, far_ix]
    assert bench["combined"][bus_iz, bus_ix] > 0.0
    assert bench["combined"][entrance_iz, entrance_ix] < 0.0

    for category in ("bus_stop", "mailbox", "hydrant", "trash", "bollard", "bench", "lamp", "tree"):
        heatmap = compute_scene_graph_heatmap(payload, category)
        assert np.isfinite(heatmap["attraction"]).any(), category
        assert np.isfinite(heatmap["repulsion"]).any(), category
        assert np.isfinite(heatmap["combined"]).any(), category


def test_plot_scene_graph_filters_layers_and_heatmap_toggle():
    pytest.importorskip("plotly")
    payload = _sample_layout_payload()
    payload["scene_graph"] = build_scene_graph(payload, road_segment_graph=_sample_road_graph())

    fig = plot_scene_graph(
        payload,
        node_layers=["road_segment", "poi", "placement"],
        poi_types=list(payload["scene_graph"]["filters"]["poi_types"]),
        categories=list(payload["scene_graph"]["filters"]["categories"]),
        edge_types=["road_connects", "poi_near_segment", "placement_realizes_slot"],
        heatmap_category="bench",
        heatmap_layer="combined",
        show_heatmap=True,
        heatmap_opacity=0.55,
    )
    names = [str(getattr(trace, "name", "") or "") for trace in fig.data]
    assert not any(name.startswith("slot:") for name in names)

    fig_filtered = plot_scene_graph(
        payload,
        node_layers=["road_segment", "poi", "slot_plan", "placement"],
        poi_types=["entrance"],
        categories=list(payload["scene_graph"]["filters"]["categories"]),
        edge_types=list(payload["scene_graph"]["filters"]["edge_types"]),
        heatmap_category="bench",
        heatmap_layer="combined",
        show_heatmap=True,
        heatmap_opacity=0.55,
    )
    filtered_names = [str(getattr(trace, "name", "") or "") for trace in fig_filtered.data]
    assert "poi:bus_stop" not in filtered_names
    assert "slot_anchors_poi" not in filtered_names

    fig_no_heatmap = plot_scene_graph(
        payload,
        node_layers=["road_segment", "poi", "slot_plan", "placement"],
        poi_types=list(payload["scene_graph"]["filters"]["poi_types"]),
        categories=list(payload["scene_graph"]["filters"]["categories"]),
        edge_types=list(payload["scene_graph"]["filters"]["edge_types"]),
        heatmap_category="bench",
        heatmap_layer="combined",
        show_heatmap=False,
        heatmap_opacity=0.55,
    )
    assert not any(trace.type == "heatmap" for trace in fig_no_heatmap.data)
    assert any(getattr(trace, "name", "") == "road_segment" for trace in fig_no_heatmap.data)


def test_legacy_payload_without_scene_graph_can_still_render_minimal_graph():
    pytest.importorskip("plotly")
    payload = _sample_layout_payload()
    graph = ensure_scene_graph(payload)

    assert graph["nodes"]
    assert graph["edges"]
    assert any(node["node_type"] == "road_segment" for node in graph["nodes"])

    figure = plot_scene_graph(
        payload,
        node_layers=["road_segment", "poi", "slot_plan", "placement"],
        poi_types=list(graph["filters"]["poi_types"]),
        categories=list(graph["filters"]["categories"]),
        edge_types=list(graph["filters"]["edge_types"]),
        heatmap_category="bench",
        heatmap_layer="combined",
        show_heatmap=True,
        heatmap_opacity=0.55,
    )
    assert figure is not None
