from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import roadgen3d.street_layout as street_layout


FOUR_LANE_PROFILES = [
    {"side": "center", "kind": "drive_lane", "inner_m": -7.0, "outer_m": -3.5},
    {"side": "center", "kind": "drive_lane", "inner_m": -3.5, "outer_m": 0.0},
    {"side": "center", "kind": "drive_lane", "inner_m": 0.0, "outer_m": 3.5},
    {"side": "center", "kind": "drive_lane", "inner_m": 3.5, "outer_m": 7.0},
]


def test_lane_edge_markings_follow_curved_polyline(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, object]] = []

    def fake_add_road_box(_scene, **kwargs):
        calls.append(dict(kwargs))

    monkeypatch.setattr(street_layout, "_add_road_box", fake_add_road_box)

    street_layout._add_lane_edge_markings(
        object(),
        road_length_m=20.0,
        road_center_x_m=0.0,
        road_center_z_m=0.0,
        road_yaw_deg=0.0,
        detailed_strip_profiles=FOUR_LANE_PROFILES,
        road_coords=((0.0, 0.0), (10.0, 0.0), (10.0, 10.0)),
        node_name_prefix="test_lane_edge",
    )

    assert len(calls) > 12
    assert {round(float(call["local_z_m"]), 1) for call in calls} == {-3.5, 3.5}
    assert all(float(call["local_x_m"]) == pytest.approx(0.0) for call in calls)
    assert all(call["surface_role"] == "lane_edge_mark" for call in calls)
    assert all(float(call["length_m"]) == pytest.approx(1.8) for call in calls)
    assert any(round(float(call["road_yaw_deg"])) == 0 for call in calls)
    assert any(round(float(call["road_yaw_deg"])) == 90 for call in calls)
    assert any(float(call["road_center_x_m"]) == pytest.approx(10.0) and float(call["road_center_z_m"]) > 0.0 for call in calls)


def test_lane_edge_markings_straight_fallback_uses_lateral_offsets(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, object]] = []

    def fake_add_road_box(_scene, **kwargs):
        calls.append(dict(kwargs))

    monkeypatch.setattr(street_layout, "_add_road_box", fake_add_road_box)

    street_layout._add_lane_edge_markings(
        object(),
        road_length_m=30.0,
        road_center_x_m=2.0,
        road_center_z_m=3.0,
        road_yaw_deg=15.0,
        detailed_strip_profiles=FOUR_LANE_PROFILES,
        road_coords=(),
    )

    assert len(calls) == 2
    assert {round(float(call["local_z_m"]), 1) for call in calls} == {-3.5, 3.5}
    assert all(float(call["local_x_m"]) == pytest.approx(0.0) for call in calls)
    assert all(float(call["length_m"]) == pytest.approx(30.0) for call in calls)
