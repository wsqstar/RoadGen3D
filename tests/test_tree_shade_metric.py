from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL_ENGINE_EXT = ROOT / "src" / "roadgen3d" / "eval_engine_ext"
if str(EVAL_ENGINE_EXT) not in sys.path:
    sys.path.insert(0, str(EVAL_ENGINE_EXT))

from road_metrics.core.config import WalkabilityConfig  # noqa: E402
from road_metrics.extractors.trees import TreeData  # noqa: E402
from road_metrics.metrics.tree_shade import (  # noqa: E402
    TREE_SHADE_METHOD,
    project_canopy_shadows,
)
from road_metrics.metrics.walkability import compute_walkability  # noqa: E402


def _tree(
    instance_id: str,
    *,
    x: float = 0.0,
    z: float = 0.0,
    canopy_width_m: float = 2.0,
    height_m: float = 8.0,
) -> dict:
    return {
        "instance_id": instance_id,
        "category": "tree",
        "position_xyz": [x, 0.0, z],
        "final_size_m": {
            "canopy_width_m": canopy_width_m,
            "height_m": height_m,
        },
    }


def _walkability(placements: list[dict], *, config: WalkabilityConfig) -> object:
    return compute_walkability(
        placements=placements,
        length_m=20.0,
        road_width_m=8.0,
        sidewalk_width_m=3.0,
        config=config,
    )


def test_tree_height_and_sun_elevation_shift_and_elongate_projected_shadow() -> None:
    short = TreeData.from_placements([_tree("short", height_m=4.0)])
    tall = TreeData.from_placements([_tree("tall", height_m=10.0)])

    short_shadow = project_canopy_shadows(
        short,
        sun_azimuth_deg=180.0,
        sun_elevation_deg=45.0,
        canopy_center_height_ratio=0.70,
        canopy_vertical_ratio=0.25,
    )[0]
    tall_shadow = project_canopy_shadows(
        tall,
        sun_azimuth_deg=180.0,
        sun_elevation_deg=45.0,
        canopy_center_height_ratio=0.70,
        canopy_vertical_ratio=0.25,
    )[0]
    low_sun_shadow = project_canopy_shadows(
        tall,
        sun_azimuth_deg=180.0,
        sun_elevation_deg=15.0,
        canopy_center_height_ratio=0.70,
        canopy_vertical_ratio=0.25,
    )[0]

    assert tall_shadow.center_z > short_shadow.center_z
    assert tall_shadow.parallel_radius_m > short_shadow.parallel_radius_m
    assert low_sun_shadow.center_z > tall_shadow.center_z
    assert low_sun_shadow.parallel_radius_m > tall_shadow.parallel_radius_m
    assert low_sun_shadow.perpendicular_radius_m == pytest.approx(
        tall_shadow.perpendicular_radius_m
    )


def test_overlapping_crowns_are_unioned_on_local_sidewalk_grid() -> None:
    tree = _tree("tree-1", z=5.5, canopy_width_m=4.0, height_m=6.0)
    duplicate = {**tree, "instance_id": "tree-2"}
    config = WalkabilityConfig(tree_sun_elevation_deg=89.9)

    one_tree = _walkability([tree], config=config)
    two_overlapping_trees = _walkability([tree, duplicate], config=config)

    assert one_tree.tree_shade > 0.0
    assert two_overlapping_trees.tree_shade == one_tree.tree_shade
    assert two_overlapping_trees.metadata["tree_shade_metadata"]["overlap_treatment"] == (
        "sampled_shadow_union"
    )


def test_road_center_crown_counts_only_when_projected_shadow_reaches_sidewalk() -> None:
    road_center_tree = _tree("road-center", z=0.0, canopy_width_m=2.0, height_m=8.0)
    overhead_sun = WalkabilityConfig(
        tree_sun_azimuth_deg=180.0,
        tree_sun_elevation_deg=89.9,
    )
    angled_sun = WalkabilityConfig(
        tree_sun_azimuth_deg=180.0,
        tree_sun_elevation_deg=45.0,
    )

    overhead_result = _walkability([road_center_tree], config=overhead_sun)
    angled_result = _walkability([road_center_tree], config=angled_sun)

    assert overhead_result.tree_shade == 0.0
    assert angled_result.tree_shade > 0.0


def test_network_projected_shadow_proxy_is_bounded_and_zero_area_is_explicit() -> None:
    trees = [
        _tree(f"tree-{index}", x=float(index), canopy_width_m=8.0, height_m=12.0)
        for index in range(30)
    ]
    config = WalkabilityConfig(network_mode=True, tree_sun_elevation_deg=1.0)

    result = _walkability(trees, config=config)
    zero_area = compute_walkability(
        placements=trees,
        length_m=20.0,
        road_width_m=8.0,
        sidewalk_width_m=0.0,
        config=config,
    )

    assert 0.0 <= result.tree_shade <= 1.0
    assert result.tree_shade == 1.0
    assert result.metadata["tree_shade_method"] == TREE_SHADE_METHOD
    assert result.metadata["tree_shade_mode"] == "network_projected_shadow_area_proxy"
    assert result.metadata["tree_shade_is_proxy"] is True
    assert zero_area.tree_shade == 0.0
    assert (
        zero_area.metadata["tree_shade_metadata"]["estimated_network_sidewalk_area_m2"]
        == 0.0
    )


def test_tree_shade_metadata_exposes_solar_parameters_and_dimension_evidence() -> None:
    tree = _tree("tree", z=5.0, canopy_width_m=3.0, height_m=7.0)
    config = WalkabilityConfig(
        tree_sun_azimuth_deg=135.0,
        tree_sun_elevation_deg=30.0,
        tree_canopy_center_height_ratio=0.65,
        tree_canopy_vertical_ratio=0.20,
    )

    result = _walkability([tree], config=config)
    metadata = result.metadata["tree_shade_metadata"]

    assert metadata["method"] == "solar_canopy_projection_v1"
    assert metadata["sun_azimuth_deg"] == 135.0
    assert metadata["sun_elevation_deg"] == 30.0
    assert metadata["canopy_center_height_ratio"] == 0.65
    assert metadata["canopy_vertical_ratio"] == 0.20
    assert metadata["canopy_width_sources"] == {"final_size_m": 1}
    assert metadata["tree_height_sources"] == {"final_size_m": 1}
    assert metadata["evidence_basis"] == (
        "tree_positions_extracted_or_imputed_canopy_width_tree_height_and_configured_sun"
    )
