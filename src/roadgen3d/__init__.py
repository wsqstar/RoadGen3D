"""RoadGen3D backend package."""

from .decoder import PlaceholderVoxelDecoder
from .decoder_shapee import ShapeEDecoder, ShapeEDecoderError
from .embedder import ClipTextEmbedder, ModelLoadError
from .eval_metrics import (
    aggregate_scene_rows,
    compare_mode_reports,
    compute_dropped_slot_rate,
    compute_latency_ms_per_instance,
    compute_overlap_rate,
    evaluate_topk_category_hits,
)
from .index_store import FaissIndexStore
from .latent_store import LatentStore, load_asset_records
from .layout_features import CandidateDescriptor, PolicyFeatureContext, build_candidate_feature
from .layout_policy import LayoutPolicyMLP, LayoutPolicyRuntime, PolicyTrainConfig
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
    "CandidateDescriptor",
    "ClipTextEmbedder",
    "FaissIndexStore",
    "LayoutPolicyMLP",
    "LayoutPolicyRuntime",
    "LatentStore",
    "M1Pipeline",
    "ModelLoadError",
    "PipelineResult",
    "PolicyFeatureContext",
    "PolicyTrainConfig",
    "PlaceholderVoxelDecoder",
    "RetrievalHit",
    "ShapeEDecoder",
    "ShapeEDecoderError",
    "StreetComposeConfig",
    "StreetComposeResult",
    "StreetPlacement",
    "aggregate_scene_rows",
    "build_candidate_feature",
    "compare_mode_reports",
    "compute_dropped_slot_rate",
    "compute_latency_ms_per_instance",
    "compute_overlap_rate",
    "compose_street_scene",
    "evaluate_topk_category_hits",
    "export_voxel_meshes",
    "load_asset_records",
]
