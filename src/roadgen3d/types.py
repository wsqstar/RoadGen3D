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

    # -- Neuralsymbolic v1 fields --
    program_generator: str = "heuristic_v1"
    design_rule_profile: str = "balanced_complete_street_v1"
    city_context: str = "generic_city"
    target_street_type: str = "mixed_use"
    layout_solver: str = "banded"

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
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["bands"] = [band.to_dict() for band in self.bands]
        payload["control_points"] = list(self.control_points)
        payload["design_goals"] = list(self.design_goals)
        payload["notes"] = list(self.notes)
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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LayoutSolverInput:
    """Input to the constrained layout solver."""

    program: StreetProgram
    config: StreetComposeConfig
    available_categories: Tuple[str, ...]
    constraint_set: ConstraintSet

    def to_dict(self) -> Dict[str, Any]:
        return {
            "program": self.program.to_dict(),
            "config": self.config.to_dict(),
            "available_categories": list(self.available_categories),
            "constraint_set": self.constraint_set.to_dict(),
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

    # -- M5 constraint fields --
    constraint_penalty: float = 0.0
    feasibility_score: float = 1.0
    violated_rules: Tuple[str, ...] = ()

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
