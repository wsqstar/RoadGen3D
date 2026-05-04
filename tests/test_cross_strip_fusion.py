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
                        {"kind": "frontage_reserve", "width_m": 2.0},
                    ],
                    "right": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                        {"kind": "frontage_reserve", "width_m": 2.0},
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
                        {"kind": "frontage_reserve", "width_m": 2.0},
                    ],
                    "right": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                        {"kind": "frontage_reserve", "width_m": 2.0},
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
                        {"kind": "frontage_reserve", "width_m": 2.0},
                    ],
                    "right": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                        {"kind": "frontage_reserve", "width_m": 2.0},
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
                        {"kind": "frontage_reserve", "width_m": 2.0},
                    ],
                    "right": [
                        {"kind": "nearroad_furnishing", "width_m": 1.5},
                        {"kind": "clear_sidewalk", "width_m": 2.5},
                        {"kind": "frontage_reserve", "width_m": 2.0},
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
        from shapely.geometry import Point

        arms = self._standard_cross_arms()
        result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        polygon = result.carriageway_core_polygon
        assert polygon.is_valid, "Carriageway core should be valid"
        assert polygon.geom_type == "Polygon", "Should be a Polygon"
        assert len(list(polygon.exterior.coords)) >= 12, "Core should be a merged straight-through throat surface"
        for arm in result.arms:
            profile_offset = arm.carriageway_half_width_m + sum(max(float(value), 0.0) for value in arm.strip_widths_by_kind.values())
            depth = max(3.0 + arm.carriageway_half_width_m, arm.carriageway_half_width_m * 2.4, profile_offset * 1.35, 4.0)
            mouth_center = (
                arm.tangent[0] * depth,
                arm.tangent[1] * depth,
            )
            assert polygon.buffer(1e-6).covers(Point(*mouth_center))
        assert not polygon.buffer(1e-6).covers(Point(10.8, 10.8)), "Core should not be the old diagonal convex hull"

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
        assert "frontage_reserve" in result.fused_corner_strips

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

    def test_carriageway_aprons_fill_road_side_corner_pockets(self):
        """Road surface should continue to the inner curb curve at each corner."""
        from roadgen3d.junction_surface_normalization import normalize_junction_surface_geometry

        arms = self._standard_cross_arms()
        result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        assert len(result.carriageway_apron_patch_records) == 4
        assert all(patch["geometry"].area > 1.0 for patch in result.carriageway_apron_patch_records)

        geometry = cross_strip_fusion_to_junction_geometry(result)
        apron_sources = [
            patch for patch in geometry["canonical_surface_patches"]
            if patch.get("source_kind") == "roadpen_style_carriageway_apron"
        ]
        assert len(apron_sources) == 4

        normalized = normalize_junction_surface_geometry(geometry)
        carriageway = [
            patch for patch in normalized["normalized_surface_patches"]
            if patch["surface_role"] == "carriageway"
        ]
        assert len(carriageway) == 1
        assert carriageway[0]["geometry"].area > result.carriageway_core_polygon.area

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
        # RoadPen-style side connectors need both adjacent side bands. Missing
        # strips should skip the corner rather than inventing a triangular fill.
        assert result.fused_corner_strips == {}
        assert result.debug_info["corner_connector_patch_count"] == 0
        assert result.debug_info["endpoint_fill_patch_count"] == 0

    def test_roadpen_style_corner_connectors_keep_provenance(self):
        """Corner connectors should carry from/to strip metadata for diagnostics."""
        arms = self._standard_cross_arms()
        result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        assert result.debug_info["generation_mode"] == "roadpen_style_junction_fusion_v1"
        assert result.fused_corner_patch_records
        sidewalk_records = [
            record for record in result.fused_corner_patch_records
            if record["strip_kind"] == "clear_sidewalk"
        ]
        assert len(sidewalk_records) == 4
        for record in sidewalk_records:
            assert record["generation_mode"] == "roadpen_style_lane_connector"
            assert record["from_centerline_id"]
            assert record["to_centerline_id"]
            assert record["from_strip_id"].startswith("left_")
            assert record["to_strip_id"].startswith("right_")
            assert record["geometry"].area > 0

    def test_corner_chamfer_uses_shared_reference_turn(self):
        """All side strips in a quadrant share one reference turn skeleton."""
        arms = self._standard_cross_arms()
        result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        assert result.debug_info["corner_chamfer_mode"] == "diagonal_depth"
        assert result.debug_info["corner_chamfer_depth_m"] == pytest.approx(1.0)
        first_quadrant = [
            record for record in result.fused_corner_patch_records
            if record["quadrant_id"] == "test_cross_quadrant_00"
        ]
        assert {record["strip_kind"] for record in first_quadrant} == {
            "nearroad_furnishing",
            "clear_sidewalk",
            "frontage_reserve",
        }
        reference_q_values = {record["reference_q_m"] for record in first_quadrant}
        radius_values = {record["fillet_radius_m"] for record in first_quadrant}
        setback_values = {record["tangent_setback_m"] for record in first_quadrant}
        assert len(reference_q_values) == 1
        assert len(radius_values) == 1
        assert len(setback_values) == 1
        assert next(iter(radius_values)) >= 1.0 / (math.sqrt(2.0) - 1.0)
        assert all(record["effective_chamfer_depth_m"] >= record["chamfer_depth_m"] for record in first_quadrant)

    def test_endpoint_fill_patches_connect_chamfers_to_straight_strips(self):
        """Each side connector should have from/to endpoint fill surfaces."""
        arms = self._standard_cross_arms()
        result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        assert result.debug_info["endpoint_fill_patch_count"] == 24
        assert len(result.fused_corner_patch_records) == 12
        assert len(result.endpoint_fill_patch_records) == 24
        connectors = {
            record["patch_id"]: record["geometry"]
            for record in result.fused_corner_patch_records
        }
        for fill in result.endpoint_fill_patch_records:
            assert fill["generation_mode"] == "roadpen_style_endpoint_fill"
            assert fill["endpoint_role"] in {"from", "to"}
            assert fill["paired_connector_id"] in connectors
            assert fill["geometry"].area > 0
            contact = fill["geometry"].intersection(connectors[fill["paired_connector_id"]])
            assert contact.area > 0 or contact.length > 0
            assert fill["fill_length_m"] == pytest.approx(
                max(fill["tangent_setback_m"], fill["chamfer_depth_m"] * 2.0) + 0.25,
                abs=1e-3,
            )

        geometry = cross_strip_fusion_to_junction_geometry(result)
        endpoint_sources = [
            patch for patch in geometry["canonical_surface_patches"]
            if patch.get("source_kind") == "roadpen_style_endpoint_fill"
        ]
        assert len(endpoint_sources) == 24

    def test_roadpen_style_sidewalk_connectors_do_not_cover_junction_anchor(self):
        """Sidewalk corner surfaces should stay outside the carriageway center."""
        from shapely.geometry import Point

        arms = self._standard_cross_arms()
        result = build_cross_strip_fusion(
            junction_id="test_cross",
            anchor_xy=(0.0, 0.0),
            arms=arms,
        )

        anchor = Point(0.0, 0.0)
        for record in result.fused_corner_patch_records:
            assert not record["geometry"].buffer(1e-6).covers(anchor)


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
