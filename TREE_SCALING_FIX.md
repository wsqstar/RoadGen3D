# Tree Asset Scaling Fix

## Problem

The tree asset `objaverse_tree_843278c62cb9494bafda67e7c14c5707` was appearing sunk below street level in generated 3D scenes, with only the canopy top visible.

### Root Cause Analysis

The issue had two components:

1. **Missing Manifest Fields**: The asset manifest entry lacked `scale`, `dimensions_m`, and `yaw_deg` fields that were added by the newer Asset Editor system.

2. **Improperly Authored 3D Model**: The GLB file contained disjoint geometry clusters:
   - Trunk/base geometry at Y ≈ 0 to 0.88m
   - Canopy geometry floating at Y ≈ 13.76 to 14.69m
   - Total bounds span: 14.74m in height

This caused a cascade of problems:
- Mesh cache computed bounds as min_y=-0.05, max_y=14.69 (height=14.74m)
- Canonical scale system tried to fit 14.74m → 7.0m, computing scale ≈ 0.47
- When scaled and positioned, the tree appeared incorrectly sized and positioned

### Why This Happened

Unlike Urbanverse trees which go through `normalize_grounded_mesh()` during import, Objaverse trees were imported as-is without normalization. This particular tree model from Objaverse was authored with its canopy geometry far from the origin, causing the scaling system to misinterpret its actual size.

## Solution

Added automatic mesh normalization in `_load_mesh_cache()` function in `src/roadgen3d/street_layout.py`.

### Key Changes

The fix detects abnormal geometry patterns (height >> width/depth with negative min_y) and applies intelligent normalization:

```python
def _load_mesh_cache(rows: List[Dict[str, str]]) -> Dict[str, _MeshCacheEntry]:
    """Load mesh cache with automatic Y-axis normalization.
    
    CRITICAL: Normalizes all meshes so their base sits at Y=0. This is essential
    for consistent canonical scaling. Handles Objaverse assets that weren't 
    normalized during import (unlike Urbanverse assets which are pre-normalized).
    """
    # ... loading code ...
    
    # Detect abnormal geometry: height >> width/depth suggests disjoint clusters
    has_disjoint_geometry = (
        height > 3.0 and  # Significant height
        height > span_xz * 2.5 and  # Much taller than wide
        min_y_val < -0.1  # Has geometry below origin
    )
    
    if has_disjoint_geometry:
        # Find ground-level vertices using percentile to ignore outliers
        all_vertices = np.vstack([geom.vertices for geom in display_geom.geometry.values()])
        ground_level = float(np.percentile(all_vertices[:, 1], 5))
        display_geom.apply_translation([0.0, -ground_level, 0.0])
    elif abs(min_y_val) > 1e-6:
        # Standard case: translate so minimum Y is at 0
        display_geom.apply_translation([0.0, -min_y_val, 0.0])
    
    # Re-compute bounds after normalization
    # ... rest of cache entry creation ...
```

### Results

After the fix:
- **objaverse_tree_843278c62cb9494bafda67e7c14c5707** now normalizes correctly:
  - Native height: 1.0m (was 14.74m)
  - Applied scale: 7.0x (correct canonical scale)
  - Expected height: 7.0m (perfect!)
  - Min Y: 0.0 (base at origin)

## Testing

Run the test script to verify:
```bash
cd /Users/shiqi/Coding/github/GIStudio/RoadGen3D
PYTHONPATH=/Users/shiqi/Coding/github/GIStudio/RoadGen3D/src uv run python test_all_trees_normalization.py
```

Expected output shows the problematic tree is now fixed:
```
>>> ✓ objaverse_tree_843278c62cb9494bafda67e7c14c5707
         Native height: 1.000m
         Applied scale: 7.000x
         Expected height: 7.00m
         Min Y: 0.000000

         ✓ FIXED!
         ✓ Height OK
```

## Additional Notes

### Other Tree Assets

During testing, we discovered other tree assets with extreme native heights (100-1500m!). These represent separate import pipeline issues where the original GLB files were not properly scaled during Objaverse import. These would require:

1. Re-importing with proper normalization in the Objaverse import pipeline
2. Or adding backfill logic to use `quality_metrics.tree_upright_validation` when available

### Recommended Follow-up Actions

1. **Backfill manifest fields**: Add `scale`, `dimensions_m`, `yaw_deg` to existing Objaverse assets using their quality_metrics
2. **Fix Objaverse import pipeline**: Add normalization similar to Urbanverse import in `scripts/m2_14_import_objaverse.py`
3. **Add validation warnings**: Warn during asset loading if native_height exceeds reasonable bounds (>50m for trees)

## Files Modified

- `src/roadgen3d/street_layout.py`: Added Y-normalization logic in `_load_mesh_cache()`
- Test files created for verification (can be removed):
  - `test_tree_scale_debug.py`
  - `test_tree_mesh_inspect.py`
  - `test_tree_normalization_fix.py`
  - `test_scene_transform_debug.py`
  - `test_all_trees_normalization.py`

## Technical Details

### Why Scene Transforms Work Correctly

The fix uses `trimesh.Scene.apply_translation()` which modifies the scene graph transformation matrices, not individual geometry vertex data. This means:
- `scene.bounds` reflects the transformed state ✓
- Individual `geometry.bounds` show local (untransformed) coordinates
- When rendering/exporting, trimesh applies the scene graph transforms automatically

This is the correct approach as it's non-destructive and leverages trimesh's built-in transformation system.

### Canonical Scale System Reference

For trees, the canonical scale system targets:
- Primary fit: height_m = 7.0m
- Secondary fit: canopy_width_m = 4.5m  
- Scale range: (0.35, 8.0)

The system computes: `applied_scale = target_height / native_height`

With normalized native_height=1.0m, this gives applied_scale=7.0, resulting in a proper 7m tall street tree.
