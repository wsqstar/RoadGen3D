"""Asset decomposition: multi-box decomposition for tight collision detection.

This module decomposes complex 3D assets (from UrbanVerse/Objaverse) into
multiple tightly-fitting bounding boxes. This enables more accurate and compact
packing during street furniture placement.

Example: An L-shaped bench is decomposed into:
  - Box 1: Seat panel
  - Box 2: Backrest
  - Box 3-4: Legs

Instead of one large AABB that wastes space, we get multiple precise boxes
that allow other assets to pack closer together.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

import numpy as np

try:
    import trimesh
except ImportError:
    trimesh = None  # type: ignore

# Directory for cached decomposition results
DECOMPOSITION_CACHE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "real" / "latents"


@dataclass(frozen=True)
class SubBox:
    """A single sub-box within a decomposed asset.
    
    Coordinates are local to the asset's center (XZ plane).
    """
    local_x: float      # Center X offset from asset origin
    local_z: float      # Center Z offset from asset origin
    width_m: float      # Extent along X axis
    depth_m: float      # Extent along Z axis

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, float]) -> "SubBox":
        return cls(
            local_x=float(d["local_x"]),
            local_z=float(d["local_z"]),
            width_m=float(d["width_m"]),
            depth_m=float(d["depth_m"]),
        )


@dataclass(frozen=True)
class DecomposedAsset:
    """A 3D asset decomposed into multiple tight-fitting bounding boxes."""
    asset_id: str
    category: str
    boxes: List[SubBox]
    
    # Original single AABB for fast coarse collision detection
    outer_half_x: float
    outer_half_z: float
    
    # Center of the asset (used as origin for local_x/local_z)
    origin_x: float = 0.0
    origin_z: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "category": self.category,
            "boxes": [b.to_dict() for b in self.boxes],
            "outer_half_x": self.outer_half_x,
            "outer_half_z": self.outer_half_z,
            "origin_x": self.origin_x,
            "origin_z": self.origin_z,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecomposedAsset":
        return cls(
            asset_id=str(d["asset_id"]),
            category=str(d["category"]),
            boxes=[SubBox.from_dict(b) for b in d["boxes"]],
            outer_half_x=float(d["outer_half_x"]),
            outer_half_z=float(d["outer_half_z"]),
            origin_x=float(d.get("origin_x", 0.0)),
            origin_z=float(d.get("origin_z", 0.0)),
        )

    def get_cache_path(self) -> Path:
        """Get the path where this decomposition should be cached."""
        return DECOMPOSITION_CACHE_DIR / f"{self.asset_id}_boxes.json"

    def save_cache(self) -> None:
        """Save decomposition to JSON cache file."""
        DECOMPOSITION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = self.get_cache_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_cache(cls, asset_id: str) -> Optional["DecomposedAsset"]:
        """Load decomposition from JSON cache file if it exists."""
        path = DECOMPOSITION_CACHE_DIR / f"{asset_id}_boxes.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


def decompose_mesh_to_boxes(
    mesh_path: str,
    asset_id: str,
    category: str,
    method: str = "connected_components",
    merge_threshold: float = 0.15,
) -> DecomposedAsset:
    """Decompose a GLB mesh into multiple tight-fitting bounding boxes.
    
    Args:
        mesh_path: Path to the GLB file
        asset_id: Unique asset identifier
        category: Asset category (bench, lamp, tree, etc.)
        method: Decomposition method ('connected_components' or 'grid')
        merge_threshold: Minimum distance (m) to merge nearby boxes
        
    Returns:
        DecomposedAsset with list of SubBox objects
    """
    if trimesh is None:
        raise RuntimeError("trimesh is required for asset decomposition")
    
    # Load mesh
    mesh_or_scene = trimesh.load(mesh_path, force="scene")
    
    # Collect all geometry
    if isinstance(mesh_or_scene, trimesh.Scene):
        meshes = []
        for geom in mesh_or_scene.geometry.values():
            if hasattr(geom, "vertices") and len(geom.vertices) > 0:
                meshes.append(geom)
        if not meshes:
            # Fallback to single AABB
            return _create_single_aabb(mesh_or_scene, asset_id, category)
        combined = trimesh.util.concatenate(meshes)
    else:
        combined = mesh_or_scene
    
    # Get outer bounds
    outer_bounds = combined.bounds
    outer_span = outer_bounds[1] - outer_bounds[0]
    outer_half_x = float(max(outer_span[0] / 2.0, 1e-3))
    outer_half_z = float(max(outer_span[2] / 2.0, 1e-3))
    origin_x = float((outer_bounds[0][0] + outer_bounds[1][0]) / 2.0)
    origin_z = float((outer_bounds[0][2] + outer_bounds[1][2]) / 2.0)
    
    # Decompose based on method
    if method == "connected_components":
        boxes = _decompose_connected_components(combined, merge_threshold)
    elif method == "grid":
        boxes = _decompose_grid(combined, merge_threshold)
    else:
        boxes = _decompose_connected_components(combined, merge_threshold)
    
    # If decomposition resulted in only one box, just use the outer AABB
    if len(boxes) <= 1:
        return DecomposedAsset(
            asset_id=asset_id,
            category=category,
            boxes=[SubBox(local_x=0.0, local_z=0.0, width_m=outer_half_x*2, depth_m=outer_half_z*2)],
            outer_half_x=outer_half_x,
            outer_half_z=outer_half_z,
            origin_x=origin_x,
            origin_z=origin_z,
        )
    
    return DecomposedAsset(
        asset_id=asset_id,
        category=category,
        boxes=boxes,
        outer_half_x=outer_half_x,
        outer_half_z=outer_half_z,
        origin_x=origin_x,
        origin_z=origin_z,
    )


def _decompose_connected_components(
    mesh: "trimesh.Trimesh",
    merge_threshold: float,
) -> List[SubBox]:
    """Decompose mesh into boxes based on spatially connected components.
    
    This works by:
    1. Splitting the mesh into disconnected components (if any)
    2. For each component, compute its tight AABB
    3. Merge components that are very close together
    """
    if trimesh is None:
        raise RuntimeError("trimesh is required")
    
    # Split into disconnected components
    components = mesh.split(only_watertight=False)
    
    if not components:
        # Single component
        bounds = mesh.bounds
        span = bounds[1] - bounds[0]
        center = (bounds[0] + bounds[1]) / 2.0
        return [SubBox(
            local_x=float(center[0]),
            local_z=float(center[2]),
            width_m=float(max(span[0], 1e-3)),
            depth_m=float(max(span[2], 1e-3)),
        )]
    
    # Compute AABB for each component
    component_boxes: List[Tuple[float, float, float, float, float, float]] = []
    for comp in components:
        if comp.is_empty or len(comp.vertices) == 0:
            continue
        bounds = comp.bounds
        # bounds is [[min_x, min_y, min_z], [max_x, max_y, max_z]]
        component_boxes.append((
            float(bounds[0][0]),  # min_x
            float(bounds[0][2]),  # min_z
            float(bounds[1][0]),  # max_x
            float(bounds[1][2]),  # max_z
            float((bounds[0][0] + bounds[1][0]) / 2.0),  # center_x
            float((bounds[0][2] + bounds[1][2]) / 2.0),  # center_z
        ))
    
    if not component_boxes:
        # Fallback
        bounds = mesh.bounds
        span = bounds[1] - bounds[0]
        center = (bounds[0] + bounds[1]) / 2.0
        return [SubBox(
            local_x=float(center[0]),
            local_z=float(center[2]),
            width_m=float(max(span[0], 1e-3)),
            depth_m=float(max(span[2], 1e-3)),
        )]
    
    # Merge nearby components
    merged_boxes = _merge_nearby_boxes(component_boxes, merge_threshold)
    
    # Convert to SubBox format
    # Calculate global center as origin
    all_min_x = min(b[0] for b in component_boxes)
    all_max_x = max(b[2] for b in component_boxes)
    all_min_z = min(b[1] for b in component_boxes)
    all_max_z = max(b[3] for b in component_boxes)
    origin_x = (all_min_x + all_max_x) / 2.0
    origin_z = (all_min_z + all_max_z) / 2.0
    
    boxes = []
    for min_x, min_z, max_x, max_z, cx, cz in merged_boxes:
        boxes.append(SubBox(
            local_x=float(cx - origin_x),
            local_z=float(cz - origin_z),
            width_m=float(max(max_x - min_x, 1e-3)),
            depth_m=float(max(max_z - min_z, 1e-3)),
        ))
    
    return boxes


def _merge_nearby_boxes(
    boxes: List[Tuple[float, float, float, float, float, float]],
    threshold: float,
) -> List[Tuple[float, float, float, float, float, float]]:
    """Iteratively merge boxes that overlap or are within threshold distance."""
    if len(boxes) <= 1:
        return boxes
    
    merged = True
    while merged:
        merged = False
        new_boxes = []
        used = [False] * len(boxes)
        
        for i in range(len(boxes)):
            if used[i]:
                continue
            current = boxes[i]
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                other = boxes[j]
                
                # Check if boxes should be merged
                if _boxes_should_merge(current, other, threshold):
                    # Merge them
                    merged_box = (
                        min(current[0], other[0]),  # min_x
                        min(current[1], other[1]),  # min_z
                        max(current[2], other[2]),  # max_x
                        max(current[3], other[3]),  # max_z
                        0.0,  # center_x (recalculated below)
                        0.0,  # center_z (recalculated below)
                    )
                    # Recalculate centers
                    merged_box = (
                        merged_box[0],
                        merged_box[1],
                        merged_box[2],
                        merged_box[3],
                        (merged_box[0] + merged_box[2]) / 2.0,
                        (merged_box[1] + merged_box[3]) / 2.0,
                    )
                    new_boxes.append(merged_box)
                    used[i] = True
                    used[j] = True
                    merged = True
                    break
            if not used[i]:
                new_boxes.append(current)
        
        boxes = new_boxes
    
    return boxes


def _boxes_should_merge(
    box1: Tuple[float, float, float, float, float, float],
    box2: Tuple[float, float, float, float, float, float],
    threshold: float,
) -> bool:
    """Check if two AABB should be merged based on proximity."""
    # Calculate distance between boxes
    # Boxes overlap or touch if:
    #   max(min1, min2) <= min(max1, max2) + threshold
    overlap_x = min(box1[2], box2[2]) >= max(box1[0], box2[0]) - threshold
    overlap_z = min(box1[3], box2[3]) >= max(box1[1], box2[1]) - threshold
    
    # Also check centroid distance
    centroid_dist_x = abs(box1[4] - box2[4])
    centroid_dist_z = abs(box1[5] - box2[5])
    
    return (overlap_x and overlap_z) or (centroid_dist_x < threshold and centroid_dist_z < threshold)


def _decompose_grid(
    mesh: "trimesh.Trimesh",
    merge_threshold: float,
) -> List[SubBox]:
    """Decompose mesh using a grid-based approach.
    
    This divides the mesh into a grid and marks cells that contain geometry.
    Useful for complex meshes where connected components don't work well.
    """
    if trimesh is None:
        raise RuntimeError("trimesh is required")
    
    bounds = mesh.bounds
    span = bounds[1] - bounds[0]
    
    # Determine grid resolution based on mesh size
    grid_size_x = max(2, int(span[0] / merge_threshold))
    grid_size_z = max(2, int(span[2] / merge_threshold))
    
    # Clamp to reasonable size
    grid_size_x = min(grid_size_x, 10)
    grid_size_z = min(grid_size_z, 10)
    
    cell_width = span[0] / grid_size_x
    cell_depth = span[2] / grid_size_z
    
    boxes = []
    for i in range(grid_size_x):
        for j in range(grid_size_z):
            min_x = bounds[0][0] + i * cell_width
            min_z = bounds[0][2] + j * cell_depth
            max_x = min_x + cell_width
            max_z = min_z + cell_depth
            
            # Check if this cell contains any vertices
            center_x = (min_x + max_x) / 2.0
            center_z = (min_z + max_z) / 2.0
            
            # Simple check: is the cell center close to any vertex?
            vertices = mesh.vertices
            dists = np.sqrt((vertices[:, 0] - center_x)**2 + (vertices[:, 2] - center_z)**2)
            
            if np.min(dists) < merge_threshold:
                boxes.append(SubBox(
                    local_x=float(center_x),
                    local_z=float(center_z),
                    width_m=float(cell_width),
                    depth_m=float(cell_depth),
                ))
    
    if not boxes:
        # Fallback to single box
        return [SubBox(
            local_x=0.0,
            local_z=0.0,
            width_m=float(max(span[0], 1e-3)),
            depth_m=float(max(span[2], 1e-3)),
        )]
    
    return boxes


def _create_single_aabb(
    mesh_or_scene: Any,
    asset_id: str,
    category: str,
) -> DecomposedAsset:
    """Create a single AABB decomposition (fallback)."""
    if isinstance(mesh_or_scene, trimesh.Scene):
        bounds = np.asarray(mesh_or_scene.bounds, dtype=np.float64)
    else:
        bounds = np.asarray(mesh_or_scene.bounds, dtype=np.float64)
    
    span = bounds[1] - bounds[0]
    center = (bounds[0] + bounds[1]) / 2.0
    
    return DecomposedAsset(
        asset_id=asset_id,
        category=category,
        boxes=[SubBox(
            local_x=0.0,
            local_z=0.0,
            width_m=float(max(span[0], 1e-3)),
            depth_m=float(max(span[2], 1e-3)),
        )],
        outer_half_x=float(max(span[0] / 2.0, 1e-3)),
        outer_half_z=float(max(span[2] / 2.0, 1e-3)),
        origin_x=float(center[0]),
        origin_z=float(center[2]),
    )


def batch_decompose_assets(
    manifest_path: str,
    output_dir: Optional[str] = None,
    force_recompute: bool = False,
) -> Dict[str, DecomposedAsset]:
    """Batch decompose all assets from a manifest file.
    
    Args:
        manifest_path: Path to JSONL manifest file
        output_dir: Directory to save cache files
        force_recompute: If True, recompute even if cache exists
        
    Returns:
        Dictionary mapping asset_id to DecomposedAsset
    """
    global DECOMPOSITION_CACHE_DIR
    if output_dir:
        DECOMPOSITION_CACHE_DIR = Path(output_dir)
    
    results: Dict[str, DecomposedAsset] = {}
    
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            asset_id = entry["asset_id"]
            category = entry.get("category", "unknown")
            mesh_path = entry.get("mesh_path", "")
            
            if not mesh_path or not Path(mesh_path).exists():
                continue
            
            # Check cache
            if not force_recompute:
                cached = DecomposedAsset.load_cache(asset_id)
                if cached is not None:
                    results[asset_id] = cached
                    continue
            
            # Decompose
            try:
                result = decompose_mesh_to_boxes(mesh_path, asset_id, category)
                result.save_cache()
                results[asset_id] = result
                print(f"Decomposed {asset_id} ({category}): {len(result.boxes)} boxes")
            except Exception as e:
                print(f"Failed to decompose {asset_id}: {e}")
    
    return results
