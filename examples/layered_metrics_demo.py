"""Example: Using the layered architecture for flexible metric composition."""

import sys
from pathlib import Path

# Add submodule to path
ROOT = Path(__file__).resolve().parents[1]
SUBMODULE = ROOT / "src" / "roadgen3d" / "eval_engine_ext"
if str(SUBMODULE) not in sys.path:
    sys.path.insert(0, str(SUBMODULE))

from road_metrics.base_metrics.core import (
    compute_spatial_uniformity_1d,
    compute_adequacy,
    normalize_density,
)
from road_metrics.extractors.furniture import FurnitureData
from road_metrics.composers.furniture import compose_furniture_density_score

print("=== Layered Architecture Demo ===\n")

# ============================================================================
# Example 1: Reuse base metrics for ANY spatial analysis
# ============================================================================
print("1. Reusing base metrics for different analyses:")

# Analyze bus stop distribution
bus_stops = [10, 50, 90, 130, 170]
bus_uniformity = compute_spatial_uniformity_1d(bus_stops)
print(f"   Bus stop uniformity: {bus_uniformity:.3f}")

# Analyze bench distribution
benches = [20, 25, 100, 105, 180]
bench_uniformity = compute_spatial_uniformity_1d(benches)
print(f"   Bench uniformity: {bench_uniformity:.3f}")

# Analyze tree distribution
trees = [30, 60, 90, 120, 150]
tree_uniformity = compute_spatial_uniformity_1d(trees)
print(f"   Tree uniformity: {tree_uniformity:.3f}")

# ============================================================================
# Example 2: Compose metrics with custom weights
# ============================================================================
print("\n2. Composing furniture density with custom weights:")

# Scenario A: Count-focused (good for small streets)
count_focused = compose_furniture_density_score(
    count_density_score=0.8,
    area_density_score=0.4,
    weights=None,  # Default: 0.4 count, 0.6 area
)
print(f"   Count-focused score: {count_focused:.3f}")

# Scenario B: Area-focused (good for parks with large features)
from road_metrics.composers.furniture import FurnitureDensityWeights
area_focused = compose_furniture_density_score(
    count_density_score=0.8,
    area_density_score=0.4,
    weights=FurnitureDensityWeights(count_weight=0.2, area_weight=0.8),
)
print(f"   Area-focused score: {area_focused:.3f}")

# ============================================================================
# Example 3: Extract and analyze independently
# ============================================================================
print("\n3. Extracting data for independent analysis:")

# Mock placements
mock_placements = [
    {"category": "bench", "position_xyz": [10, 0, -2], "bbox_xz": [9, 11, -2.5, -1.5]},
    {"category": "lamp", "position_xyz": [30, 0, -3], "bbox_xz": [28, 32, -3.5, -2.5]},
    {"category": "tree", "position_xyz": [50, 0, -2], "native_size_m": {"canopy_width_m": 4.0}},
]

furniture = FurnitureData.from_placements(mock_placements)
print(f"   Furniture count: {furniture.count}")
print(f"   Total footprint area: {furniture.total_footprint_area:.2f} m²")
print(f"   By category: {list(furniture.by_category.keys())}")

print("\n✅ Layered architecture enables flexible composition!")
