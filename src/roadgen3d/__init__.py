"""RoadGen3D backend package."""

from .compliance_eval import compute_compliance, evaluate_compliance_batch
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
from .osm_ingest import fetch_osm_data, parse_osm_features, project_to_local
from .pipeline import M1Pipeline
from .placement_zones import PlacementContext, build_placement_context
from .poi_rules import ConstraintResult, load_rule_set, score_placement
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
    "ConstraintResult",
    "FaissIndexStore",
    "LayoutPolicyMLP",
    "LayoutPolicyRuntime",
    "LatentStore",
    "M1Pipeline",
    "ModelLoadError",
    "PipelineResult",
    "PlacementContext",
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
    "build_placement_context",
    "compare_mode_reports",
    "compute_compliance",
    "compute_dropped_slot_rate",
    "compute_latency_ms_per_instance",
    "compute_overlap_rate",
    "compose_street_scene",
    "evaluate_compliance_batch",
    "evaluate_topk_category_hits",
    "export_voxel_meshes",
    "fetch_osm_data",
    "load_asset_records",
    "load_rule_set",
    "parse_osm_features",
    "project_to_local",
    "score_placement",
]
