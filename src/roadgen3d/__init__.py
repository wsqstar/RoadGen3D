"""RoadGen3D backend package."""

from .decoder import PlaceholderVoxelDecoder
from .decoder_shapee import ShapeEDecoder, ShapeEDecoderError
from .embedder import ClipTextEmbedder, ModelLoadError
from .index_store import FaissIndexStore
from .latent_store import LatentStore, load_asset_records
from .pipeline import M1Pipeline
from .street_layout import compose_street_scene
from .types import (
    AssetRecord,
    PipelineResult,
    RetrievalHit,
    StreetComposeConfig,
    StreetComposeResult,
    StreetPlacement,
)
from .voxel_export import export_voxel_meshes

__all__ = [
    "AssetRecord",
    "ClipTextEmbedder",
    "FaissIndexStore",
    "LatentStore",
    "M1Pipeline",
    "ModelLoadError",
    "PipelineResult",
    "PlaceholderVoxelDecoder",
    "RetrievalHit",
    "ShapeEDecoder",
    "ShapeEDecoderError",
    "StreetComposeConfig",
    "StreetComposeResult",
    "StreetPlacement",
    "compose_street_scene",
    "export_voxel_meshes",
    "load_asset_records",
]
