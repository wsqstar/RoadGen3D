"""Tests for cross_strip_fusion module."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import math
import pytest

from roadgen3d.cross_strip_fusion import (
    _angle_bisector,
    _normalize_vector,
    _unit_vector_from_angle,
    _distance,
    _point_along_line,
    CARRIAGEWAY_STRIP_KINDS,
    CORNER_FUSION_STRIP_KINDS,
    build_cross_strip_fusion,
    cross_strip_fusion_to_junction_geometry,
)


class TestVectorHelpers:
    """Test vector helper functions."""

    def test_normalize_vector_valid(self):
        """Test normalizing a valid vector."""
        result = _normalize_vector((3.0, 4.0))
        assert result is not None
        assert abs(result[0] - 0.6) < 1e-9
        assert abs(result[1] - 0.8) < 1e-9

    def test_normalize_vector_zero(self):
        """Test normalizing a zero vector returns None."""
        result = _normalize_vector((0.0, 0.0))
        assert result is None

    def test_unit_vector_from_angle(self):
        """Test creating unit vector from angle."""
        # 0 degrees = east
        vec = _unit_vector_from_angle(0.0)
        assert abs(vec[0] - 1.0) < 1e-9
        assert abs(vec[1] - 0.0) < 1e-9

        # 90 degrees = north
        vec = _unit_vector_from_angle(90.0)
        assert abs(vec[0] - 0.0) < 1e-9
        assert abs(vec[1] - 1.0) < 1e-9

    def test_distance(self):
        """Test Euclidean distance calculation."""
        assert abs(_distance((0.0, 0.0), (3.0, 4.0)) - 5.0) < 1e-9

    def test_point_along_line(self):
        """Test computing point along a line."""
        origin = (0.0, 0.0)
        direction = (1.0, 0.0)
        result = _point_along_line(origin, direction, 5.0)
        assert abs(result[0] - 5.0) < 1e-9
        assert abs(result[1] - 0.0) < 1e-9

    def test_angle_bisector_perpendicular(self):
        """Test angle bisector for perpendicular arms (90 degrees)."""
        # East and North arms
        tangent_a = (1.0, 0.0)  # East
        tangent_b = (0.0, 1.0)  # North
        bisector = _angle_bisector(tangent_a, tangent_b)

        # Bisector should point NE (45 degrees)
        expected = (1.0 / math.sqrt(2), 1.0 / math.sqrt(2))
        assert abs(bisector[0] - expected[0]) < 1e-9
        assert abs(bisector[1] - expected[1]) < 1e-9


class TestStripKindConstants:
    """Test strip kind constants."""

    def test_carriageway_strip_kinds(self):
        """Test carriageway strip kinds are defined correctly."""
        assert "drive_lane" in CARRIAGEWAY_STRIP_KINDS
        assert "bus_lane" in CARRIAGEWAY_STRIP_KINDS
        assert "bike_lane" in CARRIAGEWAY_STRIP_KINDS
        assert "parking_lane" in CARRIAGEWAY_STRIP_KINDS
        # Non-vehicle strips should NOT be in carriageway
        assert "clear_sidewalk" not in CARRIAGEWAY_STRIP_KINDS
        assert "nearroad_furnishing" not in CARRIAGEWAY_STRIP_KINDS

    def test_corner_fusion_strip_kinds(self):
        """Test corner fusion strip kinds are defined correctly."""
        assert "nearroad_furnishing" in CORNER_FUSION_STRIP_KINDS
        assert "clear_sidewalk" in CORNER_FUSION_STRIP_KINDS
        assert "frontage_reserve" in CORNER_FUSION_STRIP_KINDS
        # Vehicle strips should NOT be in corner fusion
        assert "drive_lane" not in CORNER_FUSION_STRIP_KINDS


class TestBuildCrossStripFusion:
    """Test the main cross strip fusion generation."""

    def _standard_cross_arms(self) -> list:
        """Create standard 4-arm cross junction arms."""
        # N, E, S, W arms at 0, 90, 180, 270 degrees
        return [
            {
                "road_id": 1,
                "centerline_id": "road_north",
                "angle_deg": 0.0,
                "carriageway_width_m": 8.0,
                "side_strip_layouts": {
                    "left": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                    ],
                    "right": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                    ],
                },
            },
            {
                "road_id": 2,
                "centerline_id": "road_east",
                "angle_deg": 90.0,
                "carriageway_width_m": 8.0,
                "side_strip_layouts": {
                    "left": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                    ],
                    "right": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                    ],
                },
            },
            {
                "road_id": 3,
                "centerline_id": "road_south",
                "angle_deg": 180.0,
                "carriageway_width_m": 8.0,
                "side_strip_layouts": {
                    "left": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                    ],
                    "right": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                    ],
                },
            },
            {
                "road_id": 4,
                "centerline_id": "road_west",
                "angle_deg": 270.0,
                "carriageway_width_m": 8.0,
                "side_strip_layouts": {
                    "left": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                    ],
                    "right": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                    ],
                },
            },
        ]

    def test_basic_cross_junction_generation(self):
        """Test basic cross junction geometry generation."""
        arms = self._standard_cross_arms()
        result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        assert result.junction_id == "test_cross"
        assert result.kind == "cross_junction"
        assert len(result.arms) == 4
        assert len(result.corners) == 4
        assert result.carriageway_core_polygon is not None
        assert result.carriageway_core_polygon.area > 0

    def test_carriageway_core_is_valid_polygon(self):
        """Test that carriageway core is a valid polygon."""
        arms = self._standard_cross_arms()
        result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        polygon = result.carriageway_core_polygon
        assert polygon.is_valid, "Carriageway core should be valid"
        assert polygon.geom_type == "Polygon", "Should be a Polygon"

    def test_fused_corner_strips_generated(self):
        """Test that fused corner strips are generated for each kind."""
        arms = self._standard_cross_arms()
        result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        # Check that corner strips are generated
        assert "nearroad_furnishing" in result.fused_corner_strips
        assert "clear_sidewalk" in result.fused_corner_strips
        # frontage_reserve not in test arms, so should not be present

        # Check fused strips are valid
        for kind, polygon in result.fused_corner_strips.items():
            assert polygon is not None
            assert not polygon.is_empty

    def test_fused_corner_strips_not_empty(self):
        """Test that fused corner strips have non-zero area."""
        arms = self._standard_cross_arms()
        result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        for kind, polygon in result.fused_corner_strips.items():
            area = float(polygon.area)
            assert area > 0, f"{kind} should have non-zero area"

    def test_debug_info_contains_stats(self):
        """Test that debug info contains expected statistics."""
        arms = self._standard_cross_arms()
        result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        assert result.debug_info["arm_count"] == 4
        assert result.debug_info["corner_count"] == 4
        assert "carriageway_core_area_m2" in result.debug_info
        assert result.debug_info["carriageway_core_area_m2"] > 0

    def test_cross_strip_fusion_to_junction_geometry(self):
        """Test conversion to junction geometry format."""
        arms = self._standard_cross_arms()
        fusion_result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        geometry = cross_strip_fusion_to_junction_geometry(fusion_result)

        assert geometry["junction_id"] == "test_cross"
        assert geometry["kind"] == "cross_junction"
        assert "carriageway_core" in geometry
        assert "nearroad_corner_patches" in geometry
        assert "sidewalk_corner_patches" in geometry

    def test_missing_strips_handled_gracefully(self):
        """Test that missing strip widths are handled gracefully."""
        arms = [
            {
                "road_id": 1,
                "centerline_id": "road_north",
                "angle_deg": 0.0,
                "carriageway_width_m": 8.0,
                # No side_strip_layouts
            },
            {
                "road_id": 2,
                "centerline_id": "road_east",
                "angle_deg": 90.0,
                "carriageway_width_m": 8.0,
                "side_strip_layouts": {
                    "left": [
                        {"kind": "clear_sidewalk", "width_m": 2.0},
                    ],
                    "right": [],
                },
            },
            {
                "road_id": 3,
                "centerline_id": "road_south",
                "angle_deg": 180.0,
                "carriageway_width_m": 8.0,
            },
            {
                "road_id": 4,
                "centerline_id": "road_west",
                "angle_deg": 270.0,
                "carriageway_width_m": 8.0,
            },
        ]

        result = build_cross_strip_fusion(
            junction_id="test_partial",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        # Should still produce valid geometry
        assert result.carriageway_core_polygon.is_valid
        # Only clear_sidewalk should be present (from road_east)
        assert "clear_sidewalk" in result.fused_corner_strips


class TestArmSorting:
    """Test arm sorting by angle."""

    def test_arms_sorted_by_angle(self):
        """Test that arms are sorted by angle."""
        arms = [
            {"road_id": 3, "centerline_id": "road_south", "angle_deg": 180.0, "carriageway_width_m": 8.0},
            {"road_id": 1, "centerline_id": "road_north", "angle_deg": 0.0, "carriageway_width_m": 8.0},
            {"road_id": 4, "centerline_id": "road_west", "angle_deg": 270.0, "carriageway_width_m": 8.0},
            {"road_id": 2, "centerline_id": "road_east", "angle_deg": 90.0, "carriageway_width_m": 8.0},
        ]

        result = build_cross_strip_fusion(
            junction_id="test_sort",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        # Check arms are sorted: N(0), E(90), S(180), W(270)
        angles = [arm.angle_deg for arm in result.arms]
        assert angles == [0.0, 90.0, 180.0, 270.0]

    def test_corners_between_sorted_arms(self):
        """Test that corners are created between adjacent sorted arms."""
        arms = [
            {"road_id": 1, "centerline_id": "road_north", "angle_deg": 0.0, "carriageway_width_m": 8.0},
            {"road_id": 2, "centerline_id": "road_east", "angle_deg": 90.0, "carriageway_width_m": 8.0},
            {"road_id": 3, "centerline_id": "road_south", "angle_deg": 180.0, "carriageway_width_m": 8.0},
            {"road_id": 4, "centerline_id": "road_west", "angle_deg": 270.0, "carriageway_width_m": 8.0},
        ]

        result = build_cross_strip_fusion(
            junction_id="test_corners",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        assert len(result.corners) == 4

        # First corner should be between N and E arms
        corner_0 = result.corners[0]
        assert corner_0.arm_a.centerline_id == "road_north"
        assert corner_0.arm_b.centerline_id == "road_east"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
