from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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
    assert {round(float(call["local_z_m"]), 1) for call in calls} == {-7.0, 7.0}
    assert all(float(call["local_x_m"]) == pytest.approx(0.0) for call in calls)
    assert all(call["surface_role"] == "lane_edge_mark" for call in calls)
    assert all(float(call["length_m"]) == pytest.approx(2.0) for call in calls)
    assert any(round(float(call["road_yaw_deg"])) == 0 for call in calls)
    assert any(round(float(call["road_yaw_deg"])) == -90 for call in calls)
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
    assert {round(float(call["local_z_m"]), 1) for call in calls} == {-7.0, 7.0}
    assert all(float(call["local_x_m"]) == pytest.approx(0.0) for call in calls)
    assert all(float(call["length_m"]) == pytest.approx(30.0) for call in calls)


def test_default_lane_edge_policy_suppresses_ordinary_curbed_urban_road():
    road = SimpleNamespace(highway_type="secondary", tags={})

    assert street_layout._road_requires_lane_edge_marking(
        road,
        FOUR_LANE_PROFILES,
        mode="explicit_only",
    ) is False


@pytest.mark.parametrize(
    ("road", "profiles"),
    [
        (SimpleNamespace(highway_type="motorway", tags={}), FOUR_LANE_PROFILES),
        (SimpleNamespace(highway_type="secondary", tags={"shoulder": "yes"}), FOUR_LANE_PROFILES),
        (
            SimpleNamespace(highway_type="secondary", tags={}),
            [*FOUR_LANE_PROFILES, {"side": "right", "kind": "bike_lane", "inner_m": 7.0, "outer_m": 8.8}],
        ),
    ],
)
def test_explicit_or_special_road_semantics_keep_lane_edges(road, profiles):
    assert street_layout._road_requires_lane_edge_marking(
        road,
        profiles,
        mode="explicit_only",
    ) is True


def test_continuous_marking_ribbons_are_clipped_before_junction():
    shapely_geometry = pytest.importorskip("shapely.geometry")

    exclusion = shapely_geometry.box(-2.0, -5.0, 2.0, 5.0)
    allowed = shapely_geometry.box(-20.0, -7.0, 20.0, 7.0)
    geometry = street_layout._lane_edge_marking_ribbon_geometry(
        road_coords=((-20.0, 0.0), (20.0, 0.0)),
        road_width_m=14.0,
        detailed_strip_profiles=FOUR_LANE_PROFILES,
        allowed_geometries=[allowed],
        exclusion_geometries=[exclusion],
    )

    assert not geometry.is_empty
    assert geometry.intersection(exclusion).area == pytest.approx(0.0, abs=1e-9)
    assert geometry.bounds[0] >= allowed.bounds[0]
    assert geometry.bounds[2] <= allowed.bounds[2]
