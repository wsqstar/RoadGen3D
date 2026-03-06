"""RoadGen3D backend package."""

from .design_rules import extend_constraint_set, list_constraint_profiles, load_constraint_set
from .compliance_eval import compute_compliance, evaluate_compliance_batch
from .decoder import PlaceholderVoxelDecoder
from .decoder_shapee import ShapeEDecoder, ShapeEDecoderError
from .embedder import ClipTextEmbedder, ModelLoadError
from .eval_metrics import (
    aggregate_scene_rows,
    compare_mode_reports,
    compute_cross_section_feasibility,
    compute_dropped_slot_rate,
    compute_editability,
    compute_explainability,
    compute_latency_ms_per_instance,
    compute_overlap_rate,
    compute_rule_satisfaction_rate,
    compute_topology_validity,
    evaluate_topk_category_hits,
)
from .index_store import FaissIndexStore
from .latent_store import LatentStore, load_asset_records
from .layout_features import CandidateDescriptor, PolicyFeatureContext, build_candidate_feature
from .layout_policy import LayoutPolicyMLP, LayoutPolicyRuntime, PolicyTrainConfig
from .layout_solver import LayoutSolverRuntime, solve_layout
from .milp_solver import solve_candidate_assignment
from .osm_ingest import fetch_osm_data, parse_osm_features, project_to_local
from .osm_segment_graph import build_segment_graph
from .pipeline import M1Pipeline
from .placement_zones import PlacementContext, build_placement_context
from .poi_rules import ConstraintResult, load_rule_set, score_placement
from .program_generator import (
    ProgramGeneratorRuntime,
    ProgramTrainConfig,
    split_program_samples_by_scene,
    train_program_generator,
    vectorize_program_input,
)
from .street_layout import compose_street_scene
from .street_program import infer_street_program
from .types import (
    AssetRecord,
    ConstraintSet,
    DesignRuleSpec,
    InventorySummary,
    LayoutConflict,
    LayoutEdit,
    LayoutSlotPlan,
    LayoutSolverInput,
    LayoutSolverResult,
    PipelineResult,
    PrepareWorkspaceResult,
    ProgramGenerationInput,
    ProgramGenerationResult,
    RetrievalHit,
    RoadSegmentBand,
    RoadSegmentEdge,
    RoadSegmentGraph,
    RoadSegmentNode,
    RuleEvaluation,
    StepResult,
    StreetBand,
    StreetComposeConfig,
    StreetComposeResult,
    StreetPlacement,
    StreetProgram,
    WorkspaceReadiness,
)
from .voxel_export import export_voxel_meshes

__all__ = [
    "AssetRecord",
    "CandidateDescriptor",
    "ClipTextEmbedder",
    "ConstraintSet",
    "ConstraintResult",
    "DesignRuleSpec",
    "FaissIndexStore",
    "LayoutPolicyMLP",
    "LayoutPolicyRuntime",
    "LayoutSolverRuntime",
    "LayoutConflict",
    "LayoutEdit",
    "LayoutSlotPlan",
    "LayoutSolverInput",
    "LayoutSolverResult",
    "LatentStore",
    "M1Pipeline",
    "ModelLoadError",
    "PipelineResult",
    "PlacementContext",
    "PolicyFeatureContext",
    "PolicyTrainConfig",
    "PrepareWorkspaceResult",
    "PlaceholderVoxelDecoder",
    "ProgramGenerationInput",
    "ProgramGenerationResult",
    "ProgramGeneratorRuntime",
    "ProgramTrainConfig",
    "RetrievalHit",
    "RoadSegmentBand",
    "RoadSegmentEdge",
    "RoadSegmentGraph",
    "RoadSegmentNode",
    "ShapeEDecoder",
    "ShapeEDecoderError",
    "RuleEvaluation",
    "StepResult",
    "StreetBand",
    "StreetComposeConfig",
    "StreetComposeResult",
    "StreetPlacement",
    "StreetProgram",
    "WorkspaceReadiness",
    "aggregate_scene_rows",
    "build_candidate_feature",
    "build_placement_context",
    "build_segment_graph",
    "compare_mode_reports",
    "compute_compliance",
    "compute_cross_section_feasibility",
    "compute_dropped_slot_rate",
    "compute_editability",
    "compute_explainability",
    "compute_latency_ms_per_instance",
    "compute_overlap_rate",
    "compute_rule_satisfaction_rate",
    "compute_topology_validity",
    "compose_street_scene",
    "extend_constraint_set",
    "evaluate_compliance_batch",
    "evaluate_topk_category_hits",
    "export_voxel_meshes",
    "fetch_osm_data",
    "infer_street_program",
    "InventorySummary",
    "list_constraint_profiles",
    "load_asset_records",
    "load_constraint_set",
    "load_rule_set",
    "parse_osm_features",
    "project_to_local",
    "score_placement",
    "solve_candidate_assignment",
    "solve_layout",
    "split_program_samples_by_scene",
    "train_program_generator",
    "vectorize_program_input",
]
