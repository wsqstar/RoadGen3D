"""Evaluation engine configuration with universal parameters.

All thresholds, weights, and constants are configurable via EvalConfig.
This enables independent tuning without code changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping


@dataclass
class WalkabilityConfig:
    """Walkability evaluation parameters."""

    # 支柱权重
    protection_weight: float = 0.40
    comfort_weight: float = 0.35
    delight_weight: float = 0.25

    # 净空宽度阈值 (米)
    clear_width_min: float = 1.8
    """Minimum acceptable clear width (wheelchair passage)."""

    clear_width_ideal: float = 3.2
    """Ideal clear width for full score."""

    # 家具密度 (每平方米)
    amenity_density_ideal: float = 0.15
    """Ideal furniture area per meter of street."""

    # 绿化遮荫
    default_canopy_size_m: tuple = (3.6, 3.6)
    """Fallback canopy dimensions when asset metadata unavailable."""

    # 过街设施 (米/个)
    crossing_spacing_m: float = 80.0
    """Target spacing between crossings."""

    # 入口密度 (每平方米)
    entrance_density_ideal: float = 0.04
    """Ideal entrance density per meter."""

    # 微环境权重
    micro_env_tree_weight: float = 0.5
    micro_env_noise_weight: float = 0.3
    micro_env_openness_weight: float = 0.2

    # 交通可达性
    transit_decay_m: float = 60.0
    """Exponential decay constant for transit proximity."""

    @property
    def pillar_weights(self) -> Dict[str, float]:
        return {
            "Protection": self.protection_weight,
            "Comfort": self.comfort_weight,
            "Delight": self.delight_weight,
        }


@dataclass
class SafetyConfig:
    """Safety evaluation parameters."""

    # 结构化评分权重 (无LLM)
    cross_prov_weight: float = 0.15
    light_uni_weight: float = 0.15
    buffer_ratio_weight: float = 0.10
    bollard_density_weight: float = 0.10
    visibility_weight: float = 0.10

    # LLM增强评分权重
    llm_weight: float = 0.60
    llm_cross_prov_weight: float = 0.15
    llm_light_uni_weight: float = 0.15
    llm_buffer_ratio_weight: float = 0.10

    # 护柱密度 (个/米)
    bollard_density_ideal: float = 0.15
    """Ideal bollard density per meter."""

    # LLM方差阈值 (触发人工审查)
    llm_stddev_threshold: float = 0.20
    """Standard deviation threshold on 0-1 scale."""


@dataclass
class BeautyConfig:
    """Beauty/aesthetics evaluation parameters."""

    # 结构化评分权重 (无LLM)
    presentation_weight: float = 0.40
    active_front_weight: float = 0.10
    anchor_poi_weight: float = 0.10
    visual_clutter_weight: float = 0.10

    # LLM增强评分权重
    llm_weight: float = 0.40
    llm_presentation_weight: float = 0.40
    llm_active_front_weight: float = 0.10
    llm_anchor_poi_weight: float = 0.10

    # 活跃界面
    active_frontage_span_m: float = 4.0
    """Assumed width per active frontage."""

    active_frontage_ratio_ideal: float = 0.70
    """Ideal active frontage ratio."""

    # 锚点POI密度 (每平方米)
    anchor_poi_density_ideal: float = 0.12
    """Ideal weighted POI density per meter."""

    # LLM方差阈值
    llm_stddev_threshold: float = 0.20


@dataclass
class AudioConfig:
    """Audio profile generation parameters."""

    # 交通音量权重
    lane_count_weight: float = 0.4
    road_width_weight: float = 0.3
    vehicle_demand_weight: float = 0.3

    lane_count_max: int = 6
    road_width_max_m: float = 20.0

    # 自然音权重
    green_density_weight: float = 0.5
    vehicle_demand_inverse_weight: float = 0.2

    # 城市音权重
    density_weight: float = 0.3
    ped_demand_weight: float = 0.3
    building_weight: float = 0.2
    bike_demand_weight: float = 0.2

    building_count_max: int = 10

    # 公交音权重
    bus_stop_weight: float = 0.3
    transit_demand_weight: float = 0.5

    # 点声源半径
    bus_stop_radius_m: float = 15.0


@dataclass
class AggregationConfig:
    """Overall score aggregation parameters."""

    walkability_weight: float = 0.45
    safety_weight: float = 0.35
    beauty_weight: float = 0.20


@dataclass
class EvalConfig:
    """Master evaluation configuration.

    All evaluation parameters are configurable through this single object.
    Enables independent tuning, A/B testing, and scenario-specific configs.
    """

    walkability: WalkabilityConfig = field(default_factory=WalkabilityConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    beauty: BeautyConfig = field(default_factory=BeautyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    aggregation: AggregationConfig = field(default_factory=AggregationConfig)

    # 全局开关
    enable_llm_eval: bool = False
    enable_audio_profile: bool = True
    enable_top_contributors: bool = True
    top_k_contributors: int = 3

    @classmethod
    def default(cls) -> "EvalConfig":
        """Return default configuration matching current eval_quality.py behavior."""
        return cls()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalConfig":
        """Create config from nested dictionary.

        Example:
            config = EvalConfig.from_dict({
                "walkability": {"protection_weight": 0.45},
                "aggregation": {"walkability_weight": 0.50},
                "enable_llm_eval": True,
            })
        """
        config = cls()

        if "walkability" in data:
            config.walkability = WalkabilityConfig(**data["walkability"])
        if "safety" in data:
            config.safety = SafetyConfig(**data["safety"])
        if "beauty" in data:
            config.beauty = BeautyConfig(**data["beauty"])
        if "audio" in data:
            config.audio = AudioConfig(**data["audio"])
        if "aggregation" in data:
            config.aggregation = AggregationConfig(**data["aggregation"])

        if "enable_llm_eval" in data:
            config.enable_llm_eval = bool(data["enable_llm_eval"])
        if "enable_audio_profile" in data:
            config.enable_audio_profile = bool(data["enable_audio_profile"])
        if "enable_top_contributors" in data:
            config.enable_top_contributors = bool(data["enable_top_contributors"])
        if "top_k_contributors" in data:
            config.top_k_contributors = int(data["top_k_contributors"])

        return config

    def to_dict(self) -> Dict[str, Any]:
        return {
            "walkability": {
                "protection_weight": self.walkability.protection_weight,
                "comfort_weight": self.walkability.comfort_weight,
                "delight_weight": self.walkability.delight_weight,
                "clear_width_min": self.walkability.clear_width_min,
                "clear_width_ideal": self.walkability.clear_width_ideal,
                "amenity_density_ideal": self.walkability.amenity_density_ideal,
                "crossing_spacing_m": self.walkability.crossing_spacing_m,
                "entrance_density_ideal": self.walkability.entrance_density_ideal,
            },
            "safety": {
                "llm_weight": self.safety.llm_weight,
                "bollard_density_ideal": self.safety.bollard_density_ideal,
            },
            "beauty": {
                "llm_weight": self.beauty.llm_weight,
                "active_frontage_ratio_ideal": self.beauty.active_frontage_ratio_ideal,
            },
            "aggregation": {
                "walkability_weight": self.aggregation.walkability_weight,
                "safety_weight": self.aggregation.safety_weight,
                "beauty_weight": self.aggregation.beauty_weight,
            },
            "enable_llm_eval": self.enable_llm_eval,
            "enable_audio_profile": self.enable_audio_profile,
        }
