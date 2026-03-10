"""Shared datatypes for RoadGen3D pipelines."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


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
    """Configuration for street composition and the neuralsymbolic v1 pipeline."""

    query: str
    length_m: float
    road_width_m: float
    sidewalk_width_m: float
    lane_count: int
    density: float
    seed: int
    topk_per_category: int
    max_trials_per_slot: int

    # -- M5 fields (all have defaults for backward compat) --
    layout_mode: str = "template"  # "template" | "osm"
    constraint_mode: str = "soft"  # "off" | "soft"
    aoi_bbox: Optional[Tuple[float, ...]] = None  # (min_lon, min_lat, max_lon, max_lat)
    osm_cache_dir: str = "artifacts/m5/osm_cache"
    constraint_weight: float = 0.45
    constraint_veto_threshold: float = 0.95
    poi_rule_set: str = "entrance_fire_bus_stop_v1"
    road_selection: str = "primary_road"  # "all" | "primary_road" | "longest"
    selected_road_osm_id: Optional[int] = None
    selected_road_discovered_poi_count: Optional[int] = None
    selected_road_discovered_poi_score: Optional[float] = None
    selected_road_discovered_core_poi_count: Optional[int] = None
    width_budget_mode: str = "expand_total_width"
    sidewalk_distribution: str = "per_side"
    poi_fit_mode: str = "hard_containment"
    base_lane_width_m: Optional[float] = None
    beauty_mode: str = "presentation_v1"
    style_preset: str = "civic_clean_v1"
    render_preset: str = "jury_default_v1"
    asset_curation_mode: str = "curated_first"

    # -- Neuralsymbolic v1 fields --
    program_generator: str = "heuristic_v1"
    design_rule_profile: str = "balanced_complete_street_v1"
    city_context: str = "generic_city"
    target_street_type: str = "mixed_use"
    layout_solver: str = "banded"
    allow_solver_fallback: bool = True
    segment_length_m: float = 12.0
    enable_surrounding_buildings: bool = True
    building_search_topk: int = 5
    theme_inference_mode: str = "deterministic_auto"
    theme_vocab_name: str = "fixed_v1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StreetBand:
    """One functional band in the street cross section."""

    name: str
    kind: str
    side: str
    width_m: float
    z_center_m: float
    allowed_categories: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["allowed_categories"] = list(self.allowed_categories)
        return payload


@dataclass(frozen=True)
class StreetProgram:
    """Structured street intent between text conditions and 3D realization."""

    query: str
    road_type: str
    city_context: str
    target_standard: str
    lane_count: int
    cross_section_type: str
    road_width_m: float
    sidewalk_width_m: float
    furnishing_width_m: float
    bands: Tuple[StreetBand, ...]
    furniture_requirements: Dict[str, int]
    control_points: Tuple[str, ...]
    design_goals: Tuple[str, ...]
    context_conditions: Dict[str, str]
    observed_poi_counts: Dict[str, int] = field(default_factory=dict)
    reserved_band_categories: Dict[str, str] = field(default_factory=dict)
    design_goal_weights: Dict[str, float] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()
    left_clear_path_width_m: float = 0.0
    right_clear_path_width_m: float = 0.0
    left_furnishing_width_m: float = 0.0
    right_furnishing_width_m: float = 0.0
    row_width_m: float = 0.0
    width_expanded: bool = False
    width_reallocation_reason: str = ""
    poi_fit_feasible: bool = True
    poi_fit_report: Dict[str, Any] = field(default_factory=dict)
    theme_segments: Tuple["ThemeSegment", ...] = ()
    building_strategy_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["bands"] = [band.to_dict() for band in self.bands]
        payload["control_points"] = list(self.control_points)
        payload["design_goals"] = list(self.design_goals)
        payload["notes"] = list(self.notes)
        payload["theme_segments"] = [segment.to_dict() for segment in self.theme_segments]
        return payload


@dataclass(frozen=True)
class DesignRuleSpec:
    """Declarative design rule for compiling a street program into a layout."""

    name: str
    description: str
    target: str
    mode: str = "hard"
    operator: str = ">="
    value: Any = None
    weight: float = 1.0
    applies_to: Tuple[str, ...] = ()
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["applies_to"] = list(self.applies_to)
        return payload


@dataclass(frozen=True)
class ConstraintSet:
    """Named collection of declarative design rules."""

    name: str
    description: str
    rules: Tuple[DesignRuleSpec, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "rules": [rule.to_dict() for rule in self.rules],
        }


@dataclass(frozen=True)
class InventorySummary:
    """Compact summary of the asset inventory available to program/solver runtimes."""

    category_counts: Dict[str, int]
    asset_ids_by_category: Dict[str, Tuple[str, ...]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category_counts": dict(self.category_counts),
            "asset_ids_by_category": {
                key: list(value)
                for key, value in self.asset_ids_by_category.items()
            },
        }


@dataclass(frozen=True)
class ProgramGenerationInput:
    """Input to heuristic or learned street-program generation."""

    query: str
    compose_config: StreetComposeConfig
    available_categories: Tuple[str, ...]
    constraint_profile: str
    placement_context: object | None = None
    inventory_summary: Optional[InventorySummary] = None
    road_segment_graph: object | None = None
    poi_context: object | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "compose_config": self.compose_config.to_dict(),
            "available_categories": list(self.available_categories),
            "constraint_profile": self.constraint_profile,
            "inventory_summary": self.inventory_summary.to_dict() if self.inventory_summary is not None else None,
        }


@dataclass(frozen=True)
class ProgramGenerationResult:
    """Output of program generation including runtime metadata."""

    program: StreetProgram
    backend_requested: str
    backend_used: str
    fallback_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "program": self.program.to_dict(),
            "backend_requested": self.backend_requested,
            "backend_used": self.backend_used,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True)
class RuleEvaluation:
    """Status of one design rule after compiling/solving a layout."""

    rule_name: str
    status: str
    mode: str
    score: float
    explanation: str
    affected_categories: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["affected_categories"] = list(self.affected_categories)
        return payload


@dataclass(frozen=True)
class LayoutEdit:
    """One explainable edit introduced by the layout solver."""

    action: str
    target: str
    before: str
    after: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LayoutConflict:
    """One unresolved conflict between rules, inventory, and layout feasibility."""

    rule_name: str
    severity: str
    message: str
    affected_target: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LayoutSlotPlan:
    """One solver-produced layout slot before asset realization."""

    slot_id: str
    category: str
    band_name: str
    x_center_m: float
    z_center_m: float
    spacing_m: float
    side: str
    priority: float
    required: bool = False
    anchor_poi_type: str = ""
    anchor_position_xz: Optional[Tuple[float, float]] = None
    theme_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LayoutSolverInput:
    """Input to the constrained layout solver."""

    program: StreetProgram
    config: StreetComposeConfig
    available_categories: Tuple[str, ...]
    constraint_set: ConstraintSet
    placement_context: object | None = None
    inventory_summary: Optional[InventorySummary] = None
    road_segment_graph: object | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "program": self.program.to_dict(),
            "config": self.config.to_dict(),
            "available_categories": list(self.available_categories),
            "constraint_set": self.constraint_set.to_dict(),
            "inventory_summary": self.inventory_summary.to_dict() if self.inventory_summary is not None else None,
        }


@dataclass(frozen=True)
class LayoutSolverResult:
    """Output of the constrained layout solver."""

    resolved_program: StreetProgram
    slot_plans: Tuple[LayoutSlotPlan, ...]
    rule_evaluations: Tuple[RuleEvaluation, ...]
    edits: Tuple[LayoutEdit, ...]
    conflicts: Tuple[LayoutConflict, ...]
    topology_validity: float
    cross_section_feasibility: float
    rule_satisfaction_rate: float
    editability: float
    conflict_explainability: float
    backend_requested: str = "banded"
    backend_used: str = "banded"
    fallback_reason: str = ""
    road_segment_graph_summary: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resolved_program": self.resolved_program.to_dict(),
            "slot_plans": [slot.to_dict() for slot in self.slot_plans],
            "rule_evaluations": [evaluation.to_dict() for evaluation in self.rule_evaluations],
            "edits": [edit.to_dict() for edit in self.edits],
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "topology_validity": float(self.topology_validity),
            "cross_section_feasibility": float(self.cross_section_feasibility),
            "rule_satisfaction_rate": float(self.rule_satisfaction_rate),
            "editability": float(self.editability),
            "conflict_explainability": float(self.conflict_explainability),
            "backend_requested": self.backend_requested,
            "backend_used": self.backend_used,
            "fallback_reason": self.fallback_reason,
            "road_segment_graph_summary": self.road_segment_graph_summary,
        }


@dataclass(frozen=True)
class RoadSegmentBand:
    """One usable functional band on a road segment."""

    band_id: str
    segment_id: str
    side: str
    kind: str
    width_m: float
    allowed_categories: Tuple[str, ...] = ()
    nearest_poi_types: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["allowed_categories"] = list(self.allowed_categories)
        payload["nearest_poi_types"] = list(self.nearest_poi_types)
        return payload


@dataclass(frozen=True)
class RoadSegmentNode:
    """One segment on a road polyline graph."""

    segment_id: str
    road_id: int
    start_xy: Tuple[float, float]
    end_xy: Tuple[float, float]
    center_xy: Tuple[float, float]
    length_m: float
    is_junction: bool = False
    is_accessible: bool = True
    highway_type: str = ""
    poi_types: Tuple[str, ...] = ()
    bands: Tuple[RoadSegmentBand, ...] = ()
    station_start_m: float = 0.0
    station_end_m: float = 0.0
    station_center_m: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "road_id": int(self.road_id),
            "start_xy": list(self.start_xy),
            "end_xy": list(self.end_xy),
            "center_xy": list(self.center_xy),
            "length_m": float(self.length_m),
            "is_junction": bool(self.is_junction),
            "is_accessible": bool(self.is_accessible),
            "highway_type": self.highway_type,
            "poi_types": list(self.poi_types),
            "bands": [band.to_dict() for band in self.bands],
            "station_start_m": float(self.station_start_m),
            "station_end_m": float(self.station_end_m),
            "station_center_m": float(self.station_center_m),
        }


@dataclass(frozen=True)
class RoadSegmentEdge:
    """Adjacency relation between two road graph segments."""

    edge_id: str
    from_segment_id: str
    to_segment_id: str
    weight: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoadSegmentGraph:
    """Discrete graph summary used by OSM segment-level layout solving."""

    nodes: Tuple[RoadSegmentNode, ...]
    edges: Tuple[RoadSegmentEdge, ...]
    mode: str = "osm"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    def summary(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "segment_count": len(self.nodes),
            "edge_count": len(self.edges),
            "junction_segment_count": sum(1 for node in self.nodes if node.is_junction),
            "avg_segment_length_m": (
                sum(float(node.length_m) for node in self.nodes) / len(self.nodes)
                if self.nodes
                else 0.0
            ),
        }


@dataclass(frozen=True)
class ThemeSegment:
    """One contiguous themed portion of a selected road."""

    theme_id: str
    theme_name: str
    x_start_m: float
    x_end_m: float
    center_x_m: float
    length_m: float
    segment_ids: Tuple[str, ...] = ()
    dominant_poi_types: Tuple[str, ...] = ()
    design_rule_profile: str = ""
    style_preset: str = ""
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["segment_ids"] = list(self.segment_ids)
        payload["dominant_poi_types"] = list(self.dominant_poi_types)
        payload["notes"] = list(self.notes)
        return payload


@dataclass(frozen=True)
class BuildingFootprint:
    """One surrounding-building footprint aligned to a road theme zone."""

    footprint_id: str
    source: str
    polygon_xz: Tuple[Tuple[float, float], ...]
    centroid_xz: Tuple[float, float]
    frontage_width_m: float
    depth_m: float
    yaw_deg: float
    theme_id: str
    height_class: str = "midrise"
    anchor_geom_id: str = ""
    size_class: str = "medium"

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["polygon_xz"] = [list(point) for point in self.polygon_xz]
        payload["centroid_xz"] = list(self.centroid_xz)
        return payload


@dataclass(frozen=True)
class BuildingPlacementPlan:
    """Resolved building placement derived from a footprint and retrieval result."""

    footprint_id: str
    theme_id: str
    asset_id: str
    selection_source: str
    position_xyz: List[float]
    yaw_deg: float
    scale: float
    scale_xyz: List[float]
    bbox_xz: List[float]
    frontage_width_m: float
    depth_m: float
    anchor_geom_id: str = ""
    retrieval_score: float = 0.0
    fallback_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkspaceReadiness:
    """Read-only status summary for one-click workspace preparation."""

    manifest_ok: bool
    latents_ok: bool
    index_ok: bool
    osm_cache_ok: bool
    missing_items: Tuple[str, ...] = ()
    recommended_next_action: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["missing_items"] = list(self.missing_items)
        return payload


@dataclass(frozen=True)
class StepResult:
    """One step in workspace preparation."""

    step: str
    status: str
    message: str
    outputs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PrepareWorkspaceResult:
    """Aggregated result for one-click workspace preparation."""

    summary: str
    readiness: WorkspaceReadiness
    steps: Tuple[StepResult, ...]
    discovered_roads_rows: Tuple[Tuple[str, ...], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "readiness": self.readiness.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "discovered_roads_rows": [list(row) for row in self.discovered_roads_rows],
        }


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
    slot_id: str = ""
    placement_group: str = "street_furniture"
    theme_id: str = ""
    anchor_geom_id: str = ""
    scale_xyz: List[float] = field(default_factory=list)

    # -- M5 constraint fields --
    constraint_penalty: float = 0.0
    feasibility_score: float = 1.0
    violated_rules: Tuple[str, ...] = ()

    # -- M8 spatial distance fields --
    dist_to_road_edge_m: float = -1.0
    dist_to_nearest_junction_m: float = -1.0
    dist_to_nearest_entrance_m: float = -1.0

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["violated_rules"] = list(self.violated_rules)
        return payload


@dataclass(frozen=True)
class StreetComposeResult:
    """Top-level output for street composition."""

    query: str
    instance_count: int
    dropped_slots: int
    placements: List[StreetPlacement]
    outputs: Dict[str, str]
    street_program: Optional[StreetProgram] = None
    solver_result: Optional[LayoutSolverResult] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["placements"] = [placement.to_dict() for placement in self.placements]
        payload["street_program"] = self.street_program.to_dict() if self.street_program is not None else None
        payload["solver_result"] = self.solver_result.to_dict() if self.solver_result is not None else None
        return payload


# ---------------------------------------------------------------------------
# Entrance analysis types (M7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlacedAsset:
    """Lightweight record of an already-placed asset for entrance analysis."""

    position_xz: Tuple[float, float]
    category: str
    bbox_xz: Tuple[float, float, float, float]  # (x_min, x_max, z_min, z_max)
    bbox_radius: float  # max(half_x, half_z)


@dataclass(frozen=True)
class EntranceAssessment:
    """Evaluation result for a single entrance point."""

    entrance_xz: Tuple[float, float]
    openness_score: float  # [0, 1]
    shielding_score: float  # [0, 1]
    blocked_angle_deg: float
    shielding_ray_hits: int
    shielding_ray_total: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SceneEntranceReport:
    """Scene-level entrance openness and noise shielding summary."""

    assessments: Tuple[EntranceAssessment, ...]
    mean_openness: float
    mean_shielding: float
    min_openness: float
    entrances_below_openness_threshold: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mean_openness": self.mean_openness,
            "mean_shielding": self.mean_shielding,
            "min_openness": self.min_openness,
            "entrances_below_openness_threshold": self.entrances_below_openness_threshold,
            "assessments": [a.to_dict() for a in self.assessments],
        }
