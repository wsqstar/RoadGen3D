#!/usr/bin/env python3
"""Full pipeline test: load tree, normalize, place in scene, export, verify"""
import sys, math, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import trimesh
import numpy as np

ASSET_ID = "objaverse_tree_843278c62cb9494bafda67e7c14c5707"
MESH_PATH = Path(f"/Users/shiqi/Coding/github/GIStudio/RoadGen3D/data/real/meshes/{ASSET_ID}.glb")

print("=" * 80)
print(f"FULL PIPELINE ANALYSIS: {ASSET_ID}")
print("=" * 80)

# Step 1: Load raw GLB
raw = trimesh.load(MESH_PATH, force="scene")
raw_bounds = np.asarray(raw.bounds, dtype=np.float64)
print(f"\n--- STEP 1: RAW GLB ---")
print(f"Raw bounds min: {raw_bounds[0]}")
print(f"Raw bounds max: {raw_bounds[1]}")
print(f"Raw height: {raw_bounds[1][1] - raw_bounds[0][1]:.3f}m")
print(f"Geometries: {len(raw.geometry)}")

# Step 2: Print per-geometry local bounds (no scene transforms)
print(f"\n--- STEP 2: PER-GEOMETRY LOCAL BOUNDS (first 5) ---")
for i, (name, geom) in enumerate(list(raw.geometry.items())[:5]):
    if hasattr(geom, 'bounds'):
        b = np.asarray(geom.bounds)
        print(f"  {name}: Y=[{b[0][1]:.3f}, {b[1][1]:.3f}] h={b[1][1]-b[0][1]:.3f}")

# Step 3: Print scene graph node transforms
print(f"\n--- STEP 3: SCENE GRAPH TRANSFORMS (first 5 geometry nodes) ---")
for i, node in enumerate(list(raw.graph.nodes_geometry)[:5]):
    transform, geom_name = raw.graph[node]
    # Extract translation from 4x4 matrix
    tx, ty, tz = transform[:3, 3]
    # Extract scale from matrix columns
    sx = np.linalg.norm(transform[:3, 0])
    sy = np.linalg.norm(transform[:3, 1])
    sz = np.linalg.norm(transform[:3, 2])
    print(f"  Node {i}: translate=[{tx:.4f}, {ty:.4f}, {tz:.4f}] scale=[{sx:.4f}, {sy:.4f}, {sz:.4f}]")
    
    # Compute WORLD position of this geometry
    local_geom = raw.geometry[geom_name]
    if hasattr(local_geom, 'bounds'):
        lb = np.asarray(local_geom.bounds)
        # World min/max = local + translation (ignoring rotation for simplicity)
        world_min_y = lb[0][1] * sy + ty
        world_max_y = lb[1][1] * sy + ty
        print(f"    Local Y: [{lb[0][1]:.3f}, {lb[1][1]:.3f}] -> World Y: [{world_min_y:.3f}, {world_max_y:.3f}]")

# Step 4: Simulate normalization (apply_translation)
print(f"\n--- STEP 4: AFTER NORMALIZATION ---")
normalized = raw.copy()
min_y_val = float(raw_bounds[0][1])
max_y_val = float(raw_bounds[1][1])
height = max_y_val - min_y_val
span_x = float(raw_bounds[1][0] - raw_bounds[0][0])
span_z = float(raw_bounds[1][2] - raw_bounds[0][2])
span_xz = max(span_x, span_z)

has_disjoint = height > 3.0 and height > span_xz * 2.5 and min_y_val < -0.1
print(f"Height={height:.3f} span_xz={span_xz:.3f} min_y={min_y_val:.3f}")
print(f"has_disjoint_geometry = {has_disjoint}")

if has_disjoint:
    all_verts = []
    for geom in normalized.geometry.values():
        if hasattr(geom, 'vertices'):
            all_verts.append(np.asarray(geom.vertices))
    if all_verts:
        all_v = np.vstack(all_verts)
        ground_level = float(np.percentile(all_v[:, 1], 5))
        print(f"ground_level (5th percentile): {ground_level:.3f}")
        if abs(ground_level) > 1e-6:
            normalized.apply_translation([0.0, -ground_level, 0.0])
elif abs(min_y_val) > 1e-6:
    normalized.apply_translation([0.0, -min_y_val, 0.0])

norm_bounds = np.asarray(normalized.bounds, dtype=np.float64)
print(f"Normalized bounds: [{norm_bounds[0][1]:.3f}, {norm_bounds[1][1]:.3f}]")
native_height = norm_bounds[1][1] - norm_bounds[0][1]
print(f"Normalized native height: {native_height:.3f}m")

# Step 5: Simulate placement (scale + rotation + translation)
print(f"\n--- STEP 5: AFTER PLACEMENT (scale=7.0, y=sidewalk=0.15) ---")
placed = normalized.copy()
CANONICAL_SCALE = 7.0
SIDEWALK_Y = 0.15

placed.apply_scale(CANONICAL_SCALE)
# Position at origin for simplicity
placed.apply_translation([0.0, SIDEWALK_Y, 0.0])

placed_bounds = np.asarray(placed.bounds, dtype=np.float64)
print(f"Placed bounds: [{placed_bounds[0][1]:.3f}, {placed_bounds[1][1]:.3f}]")
print(f"Placed height: {placed_bounds[1][1] - placed_bounds[0][1]:.3f}m")

# Step 6: Export and verify
print(f"\n--- STEP 6: EXPORT & VERIFY ---")
export_path = Path("/tmp/test_tree_placed.glb")
placed.export(export_path)
print(f"Exported to: {export_path}")
print(f"File size: {export_path.stat().st_size / 1024:.1f} KB")

# Re-load and verify
reloaded = trimesh.load(export_path, force="scene")
re_bounds = np.asarray(reloaded.bounds, dtype=np.float64)
print(f"Reloaded bounds: [{re_bounds[0][1]:.3f}, {re_bounds[1][1]:.3f}]")
print(f"Reloaded height: {re_bounds[1][1] - re_bounds[0][1]:.3f}m")
print(f"Geometries in exported file: {len(reloaded.geometry)}")

# Count total vertices
total_verts = sum(
    len(g.vertices) for g in reloaded.geometry.values() if hasattr(g, 'vertices')
)
total_faces = sum(
    len(g.faces) for g in reloaded.geometry.values() if hasattr(g, 'faces')
)
print(f"Total vertices: {total_verts}, Total faces: {total_faces}")

print("\n" + "=" * 80)
if placed_bounds[1][1] - placed_bounds[0][1] > 5.0:
    print("OK: Tree appears to have reasonable height after placement")
else:
    print("PROBLEM: Tree is too short after placement!")
print("=" * 80)
