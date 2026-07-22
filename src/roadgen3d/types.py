"""Shared datatypes for RoadGen3D pipelines."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_BUILDING_FRONT_SETBACK_MIN_M = 0.25
DEFAULT_BUILDING_FRONT_SETBACK_MAX_M = 0.75


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
    layout_mode: str = "template"  # "template" | "osm" | "osm_multiblock" | "metaurban" | "graph_template" | "reference_annotation"
    constraint_mode: str = "soft"  # "off" | "soft"
    aoi_bbox: Optional[Tuple[float, ...]] = None  # (min_lon, min_lat, max_lon, max_lat)
    osm_cache_dir: str = "artifacts/m5/osm_cache"
    constraint_weight: float = 0.45
    constraint_veto_threshold: float = 0.95
    poi_rule_set: str = "entrance_fire_bus_stop_v1"
    road_selection: str = "walkable_neighborhood"  # "all" | "primary_road" | "longest" | "walkable_neighborhood"
    selected_road_osm_id: Optional[int] = None
    selected_road_discovered_poi_count: Optional[int] = None
    selected_road_discovered_poi_score: Optional[float] = None
    selected_road_discovered_core_poi_count: Optional[int] = None
    osm_semantic_mode: str = "landuse_rules_v1"
    skeleton_design_profile: str = ""
    skeleton_design_profile_source: str = ""
    skeleton_design_profile_confidence: float = 0.0
    skeleton_design_profile_reasons: Tuple[str, ...] = ()
    street_furniture_profile: str = ""
    street_furniture_profile_source: str = ""
    street_furniture_profile_confidence: float = 0.0
    street_furniture_profile_reasons: Tuple[str, ...] = ()
    osm_multiblock_max_roads: int = 12
    osm_multiblock_max_extent_m: float = 350.0
    width_budget_mode: str = "expand_total_width"
    sidewalk_distribution: str = "per_side"
    poi_fit_mode: str = "hard_containment"
    base_lane_width_m: Optional[float] = None
    furnishing_width_m: Optional[float] = None
    beauty_mode: str = "presentation_v1"
    style_preset: str = "civic_clean_v1"
    render_preset: str = "axonometric_board_v1"
    topdown_render_mode: str = "design_tiles_v1"  # "legacy_vector" | "design_tiles_v1"
    scene_texture_mode: str = "topdown_tiles_v1"  # "topdown_tiles_v1" | "solid_color_legacy"
    topdown_canvas_px: int = 2048
    asset_curation_mode: str = "scene_ready_first"
    asset_scale_mode: str = "canonical_v1"  # "canonical_v1" | "native_raw"
    curated_street_assets_profile: str = "fixed_hq_v1"  # "fixed_hq_v1" | "disabled"

    # -- Deterministic street-surface geometry policy --
    junction_corner_radius_mode: str = "auto"  # "auto" | "fixed"
    junction_corner_radius_m: Optional[float] = None
    junction_corner_min_radius_m: float = 3.0
    junction_corner_max_radius_m: float = 8.0
    junction_precision_grid_m: float = 0.001
    junction_seam_extension_m: float = 0.02
    junction_curve_max_angle_deg: float = 2.0
    junction_curve_max_chord_m: float = 0.25
    junction_marking_setback_m: float = 0.5
    urban_lane_edge_mode: str = "explicit_only"  # "explicit_only" | "always"
    curb_width_m: float = 0.12
    curb_reveal_m: float = 0.15
    curb_top_mode: str = "flush_with_sidewalk"
    median_enabled: bool = False
    median_kind: str = "raised"  # "raised" | "planted"
    median_width_m: float = 2.0
    bus_stop_enabled: bool = False
    bus_stop_placement: str = "curbside"  # "curbside" | "bay"
    curb_ramp_enabled: bool = False
    curb_ramp_side: str = "right"  # "left" | "right"
    curb_ramp_position_ratio: float = 0.5
    furniture_style: str = "civic_clean"

    # -- Neuralsymbolic v1 fields --
    program_generator: str = "heuristic_v1"
    design_rule_profile: str = "balanced_complete_street_v1"
    city_context: str = "generic_city"
    target_street_type: str = "mixed_use"
    layout_solver: str = "hybrid_milp_v1"
    objective_profile: str = "balanced"  # "balanced" | "greening" | "commerce" | "transit"
    ped_demand_level: str = "medium"  # "low" | "medium" | "high"
    bike_demand_level: str = "low"  # "low" | "medium" | "high"
    transit_demand_level: str = "medium"  # "low" | "medium" | "high"
    vehicle_demand_level: str = "medium"  # "low" | "medium" | "high"
    allow_solver_fallback: bool = True
    segment_length_m: float = 12.0
    osm_short_road_policy: str = "semantic"  # "semantic" | "default_style"
    osm_short_road_min_length_m: float = 0.0
    osm_context_fit_mode: str = "auto_design"  # "off" | "report" | "auto_design"
    bus_stop_eligible_road_names: Tuple[str, ...] = ()
    max_bus_stops_per_scene: int = 0  # <= 0 means no cap/demo injection
    allow_demo_bus_stop_when_osm_absent: bool = False
    enable_surrounding_buildings: bool = True
    surrounding_building_mode: str = "grid_growth"
    building_search_topk: int = 5
    building_representation: str = "asset"  # "asset" | "transparent_massing"
    theme_inference_mode: str = "deterministic_auto"
    theme_vocab_name: str = "fixed_v1"
    building_height_mode: str = "theme_random"  # "class_only" | "theme_random"
    building_height_profile: str = "urban_default_v1"
    land_use_asymmetry_strength: float = 0.0
    left_right_bias: float = 0.0
    auto_land_use_mode: str = "road_buffer"  # "road_buffer" | "off"
    land_use_buffer_m: float = 35.0
    min_land_use_polygon_area_m2: float = 12.0
    max_frontage_lot_length_m: float = 18.0
    building_front_setback_min_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MIN_M
    building_front_setback_max_m: float = DEFAULT_BUILDING_FRONT_SETBACK_MAX_M
    zoning_granularity: str = "fine"  # "coarse" | "balanced" | "fine"
    streetwall_continuity: float = 0.95
    building_density: float = 0.55
    building_max_per_100m: float = 10.0
    infill_policy: str = "aggressive"  # "off" | "large_gap_only" | "balanced" | "aggressive"
    tree_species_policy: str = "per_theme_single_species"  # "per_theme_single_species" | "free_mixed"
    furniture_balance_policy: str = "overall_balanced"  # "overall_balanced" | "side_biased_legacy"
    street_furniture_distribution_policy: str = "road_uniform_v1"  # "road_uniform_v1" | "legacy"
    placement_logging_mode: str = "full_with_ui_summary"  # "off" | "summary_only" | "full_with_ui_summary"
    max_styles_per_category: int = 3  # <= 0 disables the per-scene category style cap
    amenity_coverage_mode: str = "try"  # "off" | "try"
    minimum_category_presence: Tuple[str, ...] = ("trash", "bench", "lamp")
    optional_category_presence: Tuple[str, ...] = ("mailbox", "hydrant")
    furniture_category_parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)

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
    objective_profile: str = "balanced"
    throughput_requirements: Dict[str, float] = field(default_factory=dict)
    band_bounds: Dict[str, Dict[str, float]] = field(default_factory=dict)
    topology_requirements: Dict[str, Any] = field(default_factory=dict)
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
class BandSolution:
    """Resolved width solution for one functional band."""

    band_name: str
    band_kind: str
    side: str
    width_m: float
    min_width_m: float
    max_width_m: float
    slack_m: float = 0.0
    objective_weight: float = 0.0
    active_constraint_names: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["active_constraint_names"] = list(self.active_constraint_names)
        return payload


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
    band_solutions: Tuple[BandSolution, ...]
    slot_plans: Tuple[LayoutSlotPlan, ...]
    rule_evaluations: Tuple[RuleEvaluation, ...]
    edits: Tuple[LayoutEdit, ...]
    conflicts: Tuple[LayoutConflict, ...]
    topology_validity: float
    cross_section_feasibility: float
    rule_satisfaction_rate: float
    editability: float
    conflict_explainability: float
    active_constraints: Tuple[str, ...] = ()
    throughput_feasibility: Dict[str, Any] = field(default_factory=dict)
    objective_profile: str = "balanced"
    objective_score_breakdown: Dict[str, float] = field(default_factory=dict)
    backend_requested: str = "banded"
    backend_used: str = "banded"
    fallback_reason: str = ""
    road_segment_graph_summary: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resolved_program": self.resolved_program.to_dict(),
            "band_solutions": [band.to_dict() for band in self.band_solutions],
            "slot_plans": [slot.to_dict() for slot in self.slot_plans],
            "rule_evaluations": [evaluation.to_dict() for evaluation in self.rule_evaluations],
            "edits": [edit.to_dict() for edit in self.edits],
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "topology_validity": float(self.topology_validity),
            "cross_section_feasibility": float(self.cross_section_feasibility),
            "rule_satisfaction_rate": float(self.rule_satisfaction_rate),
            "editability": float(self.editability),
            "conflict_explainability": float(self.conflict_explainability),
            "active_constraints": list(self.active_constraints),
            "throughput_feasibility": dict(self.throughput_feasibility),
            "objective_profile": self.objective_profile,
            "objective_score_breakdown": dict(self.objective_score_breakdown),
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
class RoadSegmentCrossSectionStrip:
    """One ordered strip in a segment-level street cross section."""

    strip_id: str
    zone: str
    kind: str
    width_m: float
    direction: str = "none"
    order_index: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strip_id": self.strip_id,
            "zone": self.zone,
            "kind": self.kind,
            "width_m": float(self.width_m),
            "direction": self.direction,
            "order_index": int(self.order_index),
        }


@dataclass(frozen=True)
class RoadSegmentFurnitureInstance:
    """One street-furniture instance anchored to a segment cross section."""

    instance_id: str
    centerline_id: str
    strip_id: str
    kind: str
    station_m: float
    lateral_offset_m: float
    yaw_deg: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "centerline_id": self.centerline_id,
            "strip_id": self.strip_id,
            "kind": self.kind,
            "station_m": float(self.station_m),
            "lateral_offset_m": float(self.lateral_offset_m),
            "yaw_deg": float(self.yaw_deg) if self.yaw_deg is not None else None,
        }


@dataclass(frozen=True)
class RoadSegmentMetaUrbanAssetHint:
    """MetaUrban-style asset/category hints derived from one street strip."""

    strip_id: str
    zone: str
    strip_kind: str
    metaurban_zone: str
    display_label: str
    suggested_assets: Tuple[str, ...] = ()
    placement_hint: str = ""
    asset_source: str = "metaurban_asset_config"
    asset_directory_status: str = "hook_only"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strip_id": self.strip_id,
            "zone": self.zone,
            "strip_kind": self.strip_kind,
            "metaurban_zone": self.metaurban_zone,
            "display_label": self.display_label,
            "suggested_assets": list(self.suggested_assets),
            "placement_hint": self.placement_hint,
            "asset_source": self.asset_source,
            "asset_directory_status": self.asset_directory_status,
        }


@dataclass(frozen=True)
class RoadSegmentJunctionApproachSplit:
    """One road approach boundary owned by a junction."""

    boundary_id: str
    road_id: int
    centerline_id: str
    start_xy: Tuple[float, float]
    end_xy: Tuple[float, float]
    center_xy: Tuple[float, float]
    exit_distance_m: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "road_id": int(self.road_id),
            "centerline_id": self.centerline_id,
            "start_xy": list(self.start_xy),
            "end_xy": list(self.end_xy),
            "center_xy": list(self.center_xy),
            "exit_distance_m": float(self.exit_distance_m),
        }


@dataclass(frozen=True)
class RoadSegmentJunctionFootPoint:
    """Centerline foot point on an approach boundary."""

    foot_id: str
    road_id: int
    centerline_id: str
    xy: Tuple[float, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "foot_id": self.foot_id,
            "road_id": int(self.road_id),
            "centerline_id": self.centerline_id,
            "xy": list(self.xy),
        }


@dataclass(frozen=True)
class RoadSegmentJunctionControlPoint:
    """Derived control point for sub-lane corner construction."""

    control_id: str
    road_id: int
    centerline_id: str
    strip_kind: str
    strip_zone: str
    point_kind: str
    xy: Tuple[float, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "control_id": self.control_id,
            "road_id": int(self.road_id),
            "centerline_id": self.centerline_id,
            "strip_kind": self.strip_kind,
            "strip_zone": self.strip_zone,
            "point_kind": self.point_kind,
            "xy": list(self.xy),
        }


@dataclass(frozen=True)
class RoadSegmentJunction:
    """Explicit junction metadata carried alongside the road graph."""

    junction_id: str
    kind: str
    anchor_xy: Tuple[float, float]
    connected_road_ids: Tuple[int, ...] = ()
    connected_centerline_ids: Tuple[str, ...] = ()
    crosswalk_depth_m: float = 3.0
    source_mode: str = "explicit"
    approach_split_lines: Tuple[RoadSegmentJunctionApproachSplit, ...] = ()
    skeleton_foot_points: Tuple[RoadSegmentJunctionFootPoint, ...] = ()
    sub_lane_control_points: Tuple[RoadSegmentJunctionControlPoint, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "junction_id": self.junction_id,
            "kind": self.kind,
            "anchor_xy": list(self.anchor_xy),
            "connected_road_ids": [int(item) for item in self.connected_road_ids],
            "connected_centerline_ids": [str(item) for item in self.connected_centerline_ids],
            "crosswalk_depth_m": float(self.crosswalk_depth_m),
            "source_mode": self.source_mode,
            "approach_split_lines": [item.to_dict() for item in self.approach_split_lines],
            "skeleton_foot_points": [item.to_dict() for item in self.skeleton_foot_points],
            "sub_lane_control_points": [item.to_dict() for item in self.sub_lane_control_points],
        }


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
    road_width_m: float = 0.0
    lane_profile: Dict[str, int] = field(default_factory=dict)
    cross_section_strips: Tuple[RoadSegmentCrossSectionStrip, ...] = ()
    cross_section_width_m: float = 0.0
    street_furniture_instances: Tuple[RoadSegmentFurnitureInstance, ...] = ()
    metaurban_asset_hints: Tuple[RoadSegmentMetaUrbanAssetHint, ...] = ()
    start_junction_id: str = ""
    end_junction_id: str = ""
    semantic_profile_id: str = ""
    semantic_reasons: Tuple[str, ...] = ()
    semantic_confidence: float = 0.0
    semantic_block_id: str = ""
    skeleton_design_profile: str = ""
    skeleton_design_profile_source: str = ""
    skeleton_design_profile_confidence: float = 0.0
    skeleton_design_profile_reasons: Tuple[str, ...] = ()

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
            "road_width_m": float(self.road_width_m),
            "lane_profile": {str(key): int(value) for key, value in self.lane_profile.items()},
            "cross_section_strips": [strip.to_dict() for strip in self.cross_section_strips],
            "cross_section_width_m": float(self.cross_section_width_m),
            "street_furniture_instances": [
                instance.to_dict()
                for instance in self.street_furniture_instances
            ],
            "metaurban_asset_hints": [
                hint.to_dict()
                for hint in self.metaurban_asset_hints
            ],
            "start_junction_id": self.start_junction_id,
            "end_junction_id": self.end_junction_id,
            "semantic_profile_id": self.semantic_profile_id,
            "semantic_reasons": list(self.semantic_reasons),
            "semantic_confidence": float(self.semantic_confidence),
            "semantic_block_id": self.semantic_block_id,
            "skeleton_design_profile": self.skeleton_design_profile or self.semantic_profile_id,
            "skeleton_design_profile_source": self.skeleton_design_profile_source,
            "skeleton_design_profile_confidence": float(self.skeleton_design_profile_confidence or self.semantic_confidence),
            "skeleton_design_profile_reasons": list(self.skeleton_design_profile_reasons or self.semantic_reasons),
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
    junctions: Tuple[RoadSegmentJunction, ...] = ()
    mode: str = "osm"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "junctions": [junction.to_dict() for junction in self.junctions],
        }

    def summary(self) -> Dict[str, Any]:
        road_ids = {int(node.road_id) for node in self.nodes}
        carriageway_widths_by_road: Dict[int, float] = {}
        cross_section_widths_by_road: Dict[int, float] = {}
        metaurban_asset_hint_count = 0
        for node in self.nodes:
            width_m = float(getattr(node, "road_width_m", 0.0) or 0.0)
            if width_m <= 0.0:
                continue
            lane_profile = getattr(node, "lane_profile", {}) or {}
            if lane_profile and int(lane_profile.get("total_lane_count", 0)) <= 0:
                continue
            carriageway_widths_by_road.setdefault(int(node.road_id), width_m)
            cross_section_width_m = float(getattr(node, "cross_section_width_m", 0.0) or 0.0)
            if cross_section_width_m > 0.0:
                cross_section_widths_by_road.setdefault(int(node.road_id), cross_section_width_m)
            metaurban_asset_hint_count += len(getattr(node, "metaurban_asset_hints", ()) or ())
        unique_widths = list(carriageway_widths_by_road.values())
        unique_cross_section_widths = list(cross_section_widths_by_road.values())
        junction_kinds = [str(junction.kind) for junction in self.junctions]
        return {
            "mode": self.mode,
            "segment_count": len(self.nodes),
            "edge_count": len(self.edges),
            "junction_segment_count": sum(1 for node in self.nodes if node.is_junction),
            "graph_junction_count": len(self.junctions),
            "graph_t_junction_count": sum(1 for kind in junction_kinds if kind == "t_junction"),
            "graph_cross_junction_count": sum(1 for kind in junction_kinds if kind == "cross_junction"),
            "avg_segment_length_m": (
                sum(float(node.length_m) for node in self.nodes) / len(self.nodes)
                if self.nodes
                else 0.0
            ),
            "road_count": len(carriageway_widths_by_road) if carriageway_widths_by_road else len(road_ids),
            "min_road_width_m": min(unique_widths) if unique_widths else 0.0,
            "max_road_width_m": max(unique_widths) if unique_widths else 0.0,
            "avg_road_width_m": (
                sum(unique_widths) / len(unique_widths)
                if unique_widths
                else 0.0
            ),
            "min_cross_section_width_m": (
                min(unique_cross_section_widths)
                if unique_cross_section_widths
                else 0.0
            ),
            "max_cross_section_width_m": (
                max(unique_cross_section_widths)
                if unique_cross_section_widths
                else 0.0
            ),
            "avg_cross_section_width_m": (
                sum(unique_cross_section_widths) / len(unique_cross_section_widths)
                if unique_cross_section_widths
                else 0.0
            ),
            "metaurban_asset_hint_count": int(metaurban_asset_hint_count),
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
    semantic_profile_ids: Tuple[str, ...] = ()
    design_rule_profile: str = ""
    style_preset: str = ""
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["segment_ids"] = list(self.segment_ids)
        payload["dominant_poi_types"] = list(self.dominant_poi_types)
        payload["semantic_profile_ids"] = list(self.semantic_profile_ids)
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
    land_use_type: str = ""
    side: str = ""
    height_class: str = "midrise"
    target_height_m: float = 0.0
    anchor_geom_id: str = ""
    size_class: str = "medium"
    street_edge_xz: Tuple[float, float] = (0.0, 0.0)
    placement_xz: Tuple[float, float] = (0.0, 0.0)
    front_setback_m: float = 0.0
    placement_strategy: str = "footprint_centroid"
    building_depth_m: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["polygon_xz"] = [list(point) for point in self.polygon_xz]
        payload["centroid_xz"] = list(self.centroid_xz)
        payload["street_edge_xz"] = list(self.street_edge_xz)
        payload["placement_xz"] = list(self.placement_xz)
        return payload


@dataclass(frozen=True)
class GeneratedLot:
    """One generated lot derived from zoning-grid cells."""

    lot_id: str
    polygon_xz: Tuple[Tuple[float, float], ...]
    center_xz: Tuple[float, float]
    side: str
    land_use_type: str
    theme_id: str
    frontage_width_m: float
    depth_m: float
    height_class: str = "midrise"
    target_height_m: float = 0.0
    yaw_deg: float = 0.0
    source: str = "grid_growth"
    cell_ids: Tuple[str, ...] = ()
    segment_ids: Tuple[str, ...] = ()
    street_edge_xz: Tuple[float, float] = (0.0, 0.0)
    placement_xz: Tuple[float, float] = (0.0, 0.0)
    front_setback_m: float = 0.0
    placement_strategy: str = "lot_center"
    building_depth_m: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["polygon_xz"] = [list(point) for point in self.polygon_xz]
        payload["center_xz"] = list(self.center_xz)
        payload["street_edge_xz"] = list(self.street_edge_xz)
        payload["placement_xz"] = list(self.placement_xz)
        payload["cell_ids"] = list(self.cell_ids)
        payload["segment_ids"] = list(self.segment_ids)
        return payload


@dataclass(frozen=True)
class BuildingPlacementPlan:
    """Resolved building placement derived from a footprint and retrieval result."""

    instance_id: str
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
    side: str = ""
    land_use_type: str = ""
    street_edge_xz: Tuple[float, float] = (0.0, 0.0)
    placement_xz: Tuple[float, float] = (0.0, 0.0)
    anchor_geom_id: str = ""
    retrieval_score: float = 0.0
    fallback_reason: str = ""
    target_height_m: float = 0.0
    placement_strategy: str = ""
    front_setback_m: float = 0.0
    asset_scale_mode: str = ""
    orientation_policy: str = ""
    desired_front_yaw_deg: float = 0.0
    canonical_front: str = "+Z"
    asset_yaw_offset_deg: float = 0.0
    road_tangent_yaw_deg: float = 0.0
    native_size_m: Dict[str, float] = field(default_factory=dict)
    final_size_m: Dict[str, float] = field(default_factory=dict)
    raw_size_m: Dict[str, float] = field(default_factory=dict)
    metric_size_m: Dict[str, float] = field(default_factory=dict)
    source_scale: float = 1.0
    source_scale_source: str = ""
    source_scale_confidence: str = ""
    source_scale_rejected_reason: str = ""
    door_added: bool = False
    door_facing: str = ""
    door_center_local_x: float = 0.0
    door_width_m: float = 0.0
    door_height_m: float = 0.0
    door_dims_m: Dict[str, float] = field(default_factory=dict)
    door_center_world_xyz: List[float] = field(default_factory=list)
    door_missing_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["street_edge_xz"] = list(self.street_edge_xz)
        payload["placement_xz"] = list(self.placement_xz)
        return payload


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
    required: bool = False
    theme_id: str = ""
    anchor_poi_type: str = ""
    anchor_target_xz: Optional[Tuple[float, float]] = None
    anchor_distance_m: float = -1.0
    placement_energy: float = 0.0
    placement_status: str = ""
    anchor_geom_id: str = ""
    orientation_policy: str = ""
    desired_front_yaw_deg: float = 0.0
    canonical_front: str = "+Z"
    asset_yaw_offset_deg: float = 0.0
    road_tangent_yaw_deg: float = 0.0
    scale_xyz: List[float] = field(default_factory=list)
    native_size_m: Dict[str, float] = field(default_factory=dict)
    canonical_target: Dict[str, float] = field(default_factory=dict)
    asset_scale_mode: str = ""
    scale_fallback_used: bool = False
    source_scale: float = 1.0
    source_scale_source: str = ""
    source_scale_confidence: str = ""
    source_scale_rejected_reason: str = ""
    raw_size_m: Dict[str, float] = field(default_factory=dict)
    metric_size_m: Dict[str, float] = field(default_factory=dict)
    final_size_m: Dict[str, float] = field(default_factory=dict)
    scale_gate_failed: bool = False
    scale_gate_reason: str = ""

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
        if self.anchor_target_xz is not None:
            payload["anchor_target_xz"] = list(self.anchor_target_xz)
        return payload


@dataclass(frozen=True)
class ProductionStepRecord:
    """One cumulative production-step snapshot for street preview."""

    step_id: str
    index: int
    title: str
    glb_path: str
    companion_path: str = ""
    scene_texture_mode: str = "topdown_tiles_v1"
    textured_base_enabled: bool = False
    visible_instance_ids: Tuple[str, ...] = ()
    delta_instance_ids: Tuple[str, ...] = ()
    counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["visible_instance_ids"] = list(self.visible_instance_ids)
        payload["delta_instance_ids"] = list(self.delta_instance_ids)
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
