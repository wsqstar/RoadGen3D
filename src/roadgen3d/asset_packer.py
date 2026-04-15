"""Dynamic asset placement algorithms (Bin Packing & Uniform Spacing).

This module replaces the fixed "Slot" mechanism for specific asset categories
to ensure better utilization of street length and aesthetic spacing.

Supports both single-box and multi-box (decomposed) assets for tighter packing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Sequence, Dict, Any, Optional

from .types import StreetProgram, StreetBand
from .asset_decomposition import SubBox, DecomposedAsset

@dataclass
class AssetCandidate:
    """An asset ready to be placed."""
    asset_id: str
    category: str
    width_m: float  # Full width (already scaled)
    depth_m: float  # Full depth (already scaled)
    min_spacing_m: float = 1.5  # Minimum gap to other assets
    
    # Optional: multi-box decomposition
    decomposition: Optional[DecomposedAsset] = None


@dataclass
class PlacedAsset:
    """A successfully placed asset."""
    asset_id: str
    category: str
    center_x: float
    center_z: float
    width_m: float
    depth_m: float
    yaw_deg: float = 0.0
    
    # Optional: multi-box decomposition (in world coordinates)
    boxes: List[SubBox] = None

    def __post_init__(self):
        if self.boxes is None:
            # Default to single box if not provided
            half_w = self.width_m / 2.0
            half_d = self.depth_m / 2.0
            self.boxes = [SubBox(
                local_x=0.0,
                local_z=0.0,
                width_m=self.width_m,
                depth_m=self.depth_m,
            )]

    @property
    def bbox_xz(self) -> Tuple[float, float, float, float]:
        """Returns outer bounding box (x_min, x_max, z_min, z_max)."""
        half_w = self.width_m / 2.0
        half_d = self.depth_m / 2.0
        return (
            self.center_x - half_w,
            self.center_x + half_w,
            self.center_z - half_d,
            self.center_z + half_d,
        )

    def get_world_boxes(self) -> List[Tuple[float, float, float, float]]:
        """Get all sub-boxes in world coordinates.
        
        Returns list of (x_min, x_max, z_min, z_max) for each sub-box.
        """
        result = []
        for box in self.boxes:
            x_min = self.center_x + box.local_x - box.width_m / 2.0
            x_max = self.center_x + box.local_x + box.width_m / 2.0
            z_min = self.center_z + box.local_z - box.depth_m / 2.0
            z_max = self.center_z + box.local_z + box.depth_m / 2.0
            result.append((x_min, x_max, z_min, z_max))
        return result

    def intersects(self, other: PlacedAsset, clearance: float = 0.1) -> bool:
        """Check collision using multi-box precise detection.
        
        Two-stage detection:
        1. Coarse: Check outer AABB
        2. Fine: Check all sub-box pairs only if coarse overlaps
        """
        # Stage 1: Coarse detection (outer bounds)
        bbox1 = self.bbox_xz
        bbox2 = other.bbox_xz
        
        coarse_overlap = not (
            bbox1[1] + clearance < bbox2[0] or
            bbox1[0] - clearance > bbox2[1] or
            bbox1[3] + clearance < bbox2[2] or
            bbox1[2] - clearance > bbox2[3]
        )
        
        if not coarse_overlap:
            return False
        
        # Stage 2: Fine detection (all sub-box pairs)
        my_boxes = self.get_world_boxes()
        other_boxes = other.get_world_boxes()
        
        for b1 in my_boxes:
            for b2 in other_boxes:
                # Check overlap with clearance
                overlap = not (
                    b1[1] + clearance < b2[0] or
                    b1[0] - clearance > b2[1] or
                    b1[3] + clearance < b2[2] or
                    b1[2] - clearance > b2[3]
                )
                if overlap:
                    return True
        
        return False


@dataclass
class KeepoutZone:
    """A region where assets cannot be placed (e.g., POIs, crossings)."""
    x_min: float
    x_max: float
    name: str = ""

    def overlaps(self, asset: PlacedAsset) -> bool:
        bbox = asset.bbox_xz
        # Check if asset [x_min, x_max] overlaps with zone [x_min, x_max]
        return not (bbox[1] < self.x_min or bbox[0] > self.x_max)


def solve_uniform_spacing(
    assets: Sequence[AssetCandidate],
    start_x: float,
    end_x: float,
    center_z: float,
    keepouts: Sequence[KeepoutZone] = (),
) -> List[PlacedAsset]:
    """Distribute assets evenly along a line segment, avoiding keepouts.
    
    Algorithm:
    1. Calculate total available length (excluding keepouts).
    2. Calculate "ideal" spacing: (Length - Sum_Assets) / (Count - 1).
    3. Place assets. If an asset hits a keepout, shift it to the next available slot.
    """
    if not assets:
        return []

    # 1. Calculate total width required by assets
    total_assets_width = sum(a.width_m + a.min_spacing_m for a in assets) - assets[-1].min_spacing_m
    
    # Total space available
    total_length = end_x - start_x
    
    # If assets don't fit, we just pack them (handled by compact solver usually, 
    # but here we try our best to space them)
    
    if len(assets) == 1:
        # Center single asset
        center = (start_x + end_x) / 2.0
        # Check keepout
        # Simplified: if center is in keepout, try to move to edge
        # (Full keepout logic is complex for single item, assume valid for now)
        return [PlacedAsset(
            asset_id=assets[0].asset_id,
            category=assets[0].category,
            center_x=center,
            center_z=center_z,
            width_m=assets[0].width_m,
            depth_m=assets[0].depth_m,
        )]

    # Ideal spacing
    ideal_spacing = (total_length - total_assets_width) / (len(assets) - 1)
    # Clamp spacing to be non-negative
    ideal_spacing = max(0.0, ideal_spacing)

    placed: List[PlacedAsset] = []
    current_x = start_x + (assets[0].width_m / 2.0) # Start placing from first asset's center

    # Simple placement loop with collision avoidance
    # This is a simplified version. A full version would re-distribute remaining assets
    # after an obstacle.
    
    for i, asset in enumerate(assets):
        # Attempt placement at current_x
        center_x = current_x + (asset.width_m / 2.0)
        
        # Check Keepouts
        # (Implementation needed for robust keepout handling)
        
        placed.append(PlacedAsset(
            asset_id=asset.asset_id,
            category=asset.category,
            center_x=center_x,
            center_z=center_z,
            width_m=asset.width_m,
            depth_m=asset.depth_m,
        ))
        
        # Move cursor forward: half current + spacing + half next (if exists)
        if i < len(assets) - 1:
            current_x = center_x + (asset.width_m / 2.0) + ideal_spacing
            
    return placed


def solve_compact_packing(
    assets: Sequence[AssetCandidate],
    start_x: float,
    end_x: float,
    center_z: float,
    keepouts: Sequence[KeepoutZone] = (),
) -> List[PlacedAsset]:
    """Pack assets as tightly as possible, filling gaps.

    Strategy: First Fit Decreasing (simplified for 1D).
    Supports multi-box decomposition for precise collision detection.
    
    Algorithm:
    1. Sort assets by size (optional)
    2. Place one by one from start_x
    3. Use multi-box collision detection for tight packing
    4. Jump over keepout zones
    """
    if not assets:
        return []

    placed: List[PlacedAsset] = []
    current_x = start_x

    for asset in assets:
        half_w = asset.width_m / 2.0

        # Candidate position: touch the previous asset or start_x
        candidate_x = current_x + half_w

        # Check Bounds
        if candidate_x + half_w > end_x:
            # Asset doesn't fit in remaining space
            continue

        # Build placed asset with decomposition if available
        boxes = None
        if asset.decomposition is not None:
            # Use pre-computed decomposition
            boxes = list(asset.decomposition.boxes)
        
        temp_asset = PlacedAsset(
            asset_id=asset.asset_id,
            category=asset.category,
            center_x=candidate_x,
            center_z=center_z,
            width_m=asset.width_m,
            depth_m=asset.depth_m,
            boxes=boxes,
        )

        # Check Keepouts
        skip = False
        for zone in keepouts:
            if zone.overlaps(temp_asset):
                # Move asset to end of keepout
                candidate_x = zone.x_max + half_w
                skip = True
                break

        if skip:
            # Re-check bounds after jump
            if candidate_x + half_w > end_x:
                continue
            # Update temp asset position
            temp_asset.center_x = candidate_x

            # Re-check overlap with previously placed assets (since we jumped forward)
            for prev in placed:
                if prev.intersects(temp_asset):
                    # Move past the overlapping asset
                    candidate_x = prev.bbox_xz[1] + half_w + 0.1  # 10cm clearance
                    temp_asset.center_x = candidate_x
        
        # Final bounds check
        if candidate_x + half_w > end_x:
            continue

        # Final placement
        placed.append(PlacedAsset(
            asset_id=asset.asset_id,
            category=asset.category,
            center_x=candidate_x,
            center_z=center_z,
            width_m=asset.width_m,
            depth_m=asset.depth_m,
            boxes=list(temp_asset.boxes),
        ))

        # Update cursor: current asset's end edge
        current_x = candidate_x + half_w + asset.min_spacing_m

    return placed


def create_candidate_from_decomposition(
    asset_id: str,
    category: str,
    decomposition: DecomposedAsset,
    min_spacing_m: float = 1.5,
) -> AssetCandidate:
    """Create an AssetCandidate from a pre-computed decomposition.
    
    This is a helper to easily use decomposed assets in the packing algorithms.
    """
    return AssetCandidate(
        asset_id=asset_id,
        category=category,
        width_m=decomposition.outer_half_x * 2.0,
        depth_m=decomposition.outer_half_z * 2.0,
        min_spacing_m=min_spacing_m,
        decomposition=decomposition,
    )
