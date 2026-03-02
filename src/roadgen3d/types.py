"""Shared datatypes for RoadGen3D pipelines."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class AssetRecord:
    """Metadata describing one retrievable 3D asset latent."""

    asset_id: str
    description: str
    latent_path: str


@dataclass(frozen=True)
class RetrievalHit:
    """One FAISS search result."""

    asset_id: str
    score: float


@dataclass(frozen=True)
class PipelineResult:
    """Top-level output for the milestone-1 end-to-end run."""

    query: str
    top_hit: RetrievalHit
    latent_shape: List[int]
    voxel_shape: List[int]
    occupied_voxels: int
    outputs: Dict[str, str]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["top_hit"] = asdict(self.top_hit)
        return payload


@dataclass(frozen=True)
class StreetComposeConfig:
    """Configuration for M3 street composition."""

    query: str
    length_m: float
    road_width_m: float
    sidewalk_width_m: float
    lane_count: int
    density: float
    seed: int
    topk_per_category: int
    max_trials_per_slot: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StreetPlacement:
    """One placed instance in the composed street scene."""

    instance_id: str
    asset_id: str
    category: str
    score: float
    position_xyz: List[float]
    yaw_deg: float
    scale: float
    bbox_xz: List[float]  # [xmin, xmax, zmin, zmax]
    selection_source: str  # faiss_softmax | faiss_relaxed_repeat | policy_* | fallback_pool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StreetComposeResult:
    """Top-level output for M3 street composition."""

    query: str
    instance_count: int
    dropped_slots: int
    placements: List[StreetPlacement]
    outputs: Dict[str, str]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["placements"] = [placement.to_dict() for placement in self.placements]
        return payload
