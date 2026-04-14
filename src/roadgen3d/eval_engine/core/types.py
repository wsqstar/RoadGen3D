"""Universal types for the evaluation engine.

This module defines all data types used by the evaluation engine.
It has NO dependencies on other RoadGen3D modules, making the engine fully decoupled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class SceneLayout:
    """Standard scene layout input format.

    This is the ONLY input format the evaluation engine depends on.
    It matches the scene_layout.json format produced by RoadGen3D.
    """

    length_m: float
    """Street segment length in meters."""

    road_width_m: float
    """Total road width (carriageway + sidewalks) in meters."""

    sidewalk_width_m: float
    """Single sidewalk width in meters."""

    left_clear_path_width_m: Optional[float] = None
    """Left sidewalk clear path width (auto-computed from bbox if None)."""

    right_clear_path_width_m: Optional[float] = None
    """Right sidewalk clear path width (auto-computed from bbox if None)."""

    left_furnishing_width_m: float = 0.0
    """Left furnishing zone width in meters."""

    right_furnishing_width_m: float = 0.0
    """Right furnishing zone width in meters."""

    lane_count: int = 2
    """Number of vehicle lanes."""

    density: float = 1.0
    """Overall furniture density scale (0-1)."""

    entrance_count: int = 0
    """Number of building entrances."""

    mean_entrance_openness: float = 1.0
    """Mean entrance openness (0-1)."""

    mean_noise_shielding: float = 0.0
    """Mean noise shielding level (0-1)."""

    bus_stop_points_xz: List[List[float]] = field(default_factory=list)
    """Bus stop coordinates [[x, z], ...]."""

    poi_points_by_type_xz: Dict[str, List[List[float]]] = field(default_factory=dict)
    """POI points by type {type: [[x, z], ...]}."""

    land_use_summary: Dict[str, float] = field(default_factory=dict)
    """Land use counts {type: count}."""

    placements: List[Dict[str, Any]] = field(default_factory=list)
    """List of placed assets with full metadata."""

    @classmethod
    def from_layout_payload(cls, payload: Mapping[str, Any]) -> "SceneLayout":
        """Create SceneLayout from scene_layout.json payload.

        This is the primary factory method for parsing RoadGen3D output.
        """
        summary = dict(payload.get("summary", {}) or {})
        config = dict(payload.get("config", {}) or {})
        placements = list(payload.get("placements", []) or [])

        # Extract spatial context
        spatial_ctx = summary.get("spatial_context", {}) or {}
        bus_stops = spatial_ctx.get("bus_stop_points_xz", []) or []
        poi_points = spatial_ctx.get("poi_points_by_type_xz", {}) or {}

        return cls(
            length_m=float(summary.get("length_m", config.get("length_m", 80.0)) or 80.0),
            road_width_m=float(
                summary.get("road_width_m", config.get("road_width_m", summary.get("carriageway_width_m", 8.0))) or 8.0
            ),
            sidewalk_width_m=float(summary.get("sidewalk_width_m", config.get("sidewalk_width_m", 2.5)) or 2.5),
            left_clear_path_width_m=_safe_float(summary.get("left_clear_path_width_m")),
            right_clear_path_width_m=_safe_float(summary.get("right_clear_path_width_m")),
            left_furnishing_width_m=float(summary.get("left_furnishing_width_m", 0.0) or 0.0),
            right_furnishing_width_m=float(summary.get("right_furnishing_width_m", 0.0) or 0.0),
            lane_count=int(config.get("lane_count", summary.get("lane_count", 2)) or 2),
            density=float(config.get("density", summary.get("density", 1.0)) or 1.0),
            entrance_count=int(summary.get("entrance_count", 0) or 0),
            mean_entrance_openness=float(summary.get("mean_entrance_openness", 1.0) or 1.0),
            mean_noise_shielding=float(summary.get("mean_noise_shielding", 0.0) or 0.0),
            bus_stop_points_xz=[list(pt) for pt in bus_stops],
            poi_points_by_type_xz={k: [list(pt) for pt in v] for k, v in poi_points.items()},
            land_use_summary=dict(summary.get("land_use_summary", {}) or {}),
            placements=placements,
        )


@dataclass
class WalkabilityIndicators:
    """Complete walkability evaluation results."""

    # 11底层指标
    sid_clr: float = 0.0
    """Sidewalk clear width (0-1)."""

    clear_cont: float = 0.0
    """Clear path continuity (0-1)."""

    furn_d: float = 0.0
    """Furniture density (0-1)."""

    light_uni: float = 0.0
    """Light uniformity (0-1)."""

    tree_shade: float = 0.0
    """Tree shade fraction (0-1)."""

    buffer_ratio: float = 0.0
    """Buffer zone ratio (0-1)."""

    transit_prox: float = 0.0
    """Transit proximity (0-1)."""

    cross_prov: float = 0.0
    """Crossing provision (0-1)."""

    entr_dens: float = 0.0
    """Entrance density (0-1)."""

    poi_mix: float = 0.0
    """POI mix diversity (0-1)."""

    micro_env: float = 0.0
    """Micro-environment comfort (0-1)."""

    # 三大支柱聚合
    protection: float = 0.0
    """Protection pillar score (0-1)."""

    comfort: float = 0.0
    """Comfort pillar score (0-1)."""

    delight: float = 0.0
    """Delight pillar score (0-1)."""

    # 综合指数
    walkability_index: float = 0.0
    """Overall walkability index (0-1)."""

    # 诊断信息
    top_contributors: List[Dict[str, Any]] = field(default_factory=list)
    """Top-3 indicators whose +0.1 improvement would most increase W."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Additional metadata (length, widths, etc.)."""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "indicators": {
                "SID_CLR": self.sid_clr,
                "CLEAR_CONT": self.clear_cont,
                "FURN_D": self.furn_d,
                "LIGHT_UNI": self.light_uni,
                "TREE_SHADE": self.tree_shade,
                "BUFFER_RATIO": self.buffer_ratio,
                "TRANSIT_PROX": self.transit_prox,
                "CROSS_PROV": self.cross_prov,
                "ENTR_DENS": self.entr_dens,
                "POI_MIX": self.poi_mix,
                "MICRO_ENV": self.micro_env,
            },
            "pillar_scores": {
                "Protection": self.protection,
                "Comfort": self.comfort,
                "Delight": self.delight,
            },
            "walkability_index": self.walkability_index,
            "top_contributors": self.top_contributors,
            "metadata": self.metadata,
        }


@dataclass
class SafetyReport:
    """Safety evaluation report."""

    features: Dict[str, float] = field(default_factory=dict)
    """Extracted safety features."""

    structural_score: float = 0.0
    """Structural safety score (no LLM)."""

    llm_scores: Optional[Dict[str, Any]] = None
    """LLM-based sub-dimension scores."""

    final_score: float = 0.0
    """Final safety score (structural or LLM-enhanced)."""

    llm_required: bool = True
    """Whether LLM evaluation is required."""

    needs_review: bool = False
    """Whether human review is recommended."""

    diagnosis: Dict[str, Any] = field(default_factory=dict)
    """Weakest dimension diagnosis."""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "features": self.features,
            "structural_score": self.structural_score,
            "llm_scores": self.llm_scores,
            "final_score": self.final_score,
            "llm_required": self.llm_required,
            "needs_review": self.needs_review,
            "diagnosis": self.diagnosis,
        }


@dataclass
class BeautyReport:
    """Beauty/aesthetics evaluation report."""

    features: Dict[str, float] = field(default_factory=dict)
    """Extracted beauty features."""

    structural_score: float = 0.0
    """Structural beauty score (no LLM)."""

    llm_scores: Optional[Dict[str, Any]] = None
    """LLM-based sub-dimension scores."""

    final_score: float = 0.0
    """Final beauty score (structural or LLM-enhanced)."""

    llm_required: bool = True
    """Whether LLM evaluation is required."""

    needs_review: bool = False
    """Whether human review is recommended."""

    diagnosis: Dict[str, Any] = field(default_factory=dict)
    """Weakest dimension diagnosis."""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "features": self.features,
            "structural_score": self.structural_score,
            "llm_scores": self.llm_scores,
            "final_score": self.final_score,
            "llm_required": self.llm_required,
            "needs_review": self.needs_review,
            "diagnosis": self.diagnosis,
        }


@dataclass
class AudioProfile:
    """Scene ambient audio profile."""

    traffic: float = 0.0
    """Traffic noise volume (0-1)."""

    nature: float = 0.0
    """Nature sounds volume (0-1)."""

    urban: float = 0.0
    """Urban ambience volume (0-1)."""

    transit: float = 0.0
    """Transit-related sounds volume (0-1)."""

    point_sources: List[Dict[str, Any]] = field(default_factory=list)
    """Positional sound emitters (bus stops, etc.)."""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ambient": {
                "traffic": self.traffic,
                "nature": self.nature,
                "urban": self.urban,
                "transit": self.transit,
            },
            "point_sources": self.point_sources,
        }


@dataclass
class EvaluationResult:
    """Complete evaluation result."""

    walkability: WalkabilityIndicators
    safety: SafetyReport
    beauty: BeautyReport
    audio: Optional[AudioProfile] = None

    # 综合评分
    evaluation_score: float = 0.0
    """Combined score: 0.45*W + 0.35*S + 0.20*B."""

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "walkability": self.walkability.to_dict(),
            "safety": self.safety.to_dict(),
            "beauty": self.beauty.to_dict(),
            "evaluation_score": self.evaluation_score,
        }
        if self.audio:
            result["audio"] = self.audio.to_dict()
        return result


def _safe_float(value: Any) -> Optional[float]:
    """Safely convert to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
