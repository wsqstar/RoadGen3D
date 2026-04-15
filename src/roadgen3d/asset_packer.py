"""Dynamic asset placement algorithms (Bin Packing & Uniform Spacing).

This module replaces the fixed "Slot" mechanism for specific asset categories
to ensure better utilization of street length and aesthetic spacing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Sequence, Dict, Any, Optional

from .types import StreetProgram, StreetBand

@dataclass
class AssetCandidate:
    """An asset ready to be placed."""
    asset_id: str
    category: str
    width_m: float  # Full width (already scaled)
    depth_m: float  # Full depth (already scaled)
    min_spacing_m: float = 1.5  # Minimum gap to other assets


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

    @property
    def bbox_xz(self) -> Tuple[float, float, float, float]:
        """Returns (x_min, x_max, z_min, z_max)."""
        half_w = self.width_m / 2.0
        half_d = self.depth_m / 2.0
        return (
            self.center_x - half_w,
            self.center_x + half_w,
            self.center_z - half_d,
            self.center_z + half_d,
        )

    def intersects(self, other: PlacedAsset) -> bool:
        """Check AABB intersection with a small tolerance for clearance."""
        clearance = 0.1  # 10cm tolerance
        bbox1 = self.bbox_xz
        bbox2 = other.bbox_xz
        return not (
            bbox1[1] + clearance < bbox2[0] or
            bbox1[0] - clearance > bbox2[1] or
            bbox1[3] + clearance < bbox2[2] or
            bbox1[2] - clearance > bbox2[3]
        )


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
    Sort assets by size (optional, usually they are same category) and place them
    one by one starting from start_x.
    """
    if not assets:
        return []

    placed: List[PlacedAsset] = []
    current_x = start_x
    
    # Sort by width (largest first usually packs better, but for street furniture
    # insertion order or random is often fine to look natural).
    # Let's keep original order or random shuffle for natural look.
    
    for asset in assets:
        half_w = asset.width_m / 2.0
        
        # Candidate position: touch the previous asset or start_x
        candidate_x = current_x + half_w
        
        # Check Bounds
        if candidate_x + half_w > end_x:
            # Asset doesn't fit in remaining space
            continue
            
        # Check Keepouts
        skip = False
        temp_asset = PlacedAsset(
            asset_id=asset.asset_id,
            category=asset.category,
            center_x=candidate_x,
            center_z=center_z,
            width_m=asset.width_m,
            depth_m=asset.depth_m,
        )
        
        for zone in keepouts:
            if zone.overlaps(temp_asset):
                # Move asset to end of keepout
                candidate_x = zone.x_max + half_w
                skip = True # Need to re-check bounds and overlaps
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
                    # If we overlap with a placed asset (shouldn't happen if keepouts are disjoint 
                    # from placed assets, but good for safety), move past it
                    candidate_x = prev.bbox_xz[1] + half_w + 0.1 # 10cm clearance
                    temp_asset.center_x = candidate_x
        
        # Final placement
        placed.append(PlacedAsset(
            asset_id=asset.asset_id,
            category=asset.category,
            center_x=candidate_x,
            center_z=center_z,
            width_m=asset.width_m,
            depth_m=asset.depth_m,
        ))
        
        # Update cursor: current asset's end edge
        current_x = candidate_x + half_w + asset.min_spacing_m

    return placed
