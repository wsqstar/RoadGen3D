"""Main evaluation engine - orchestrator for all evaluation modules.

This is the primary entry point for the decoupled evaluation engine.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from ..core.config import EvalConfig
from ..core.types import (
    AudioProfile,
    BeautyReport,
    EvaluationResult,
    SafetyReport,
    SceneLayout,
    WalkabilityIndicators,
)
from ..metrics.walkability import compute_walkability
from ..metrics.safety import compute_structural_safety, compute_llm_enhanced_safety, diagnose_safety
from ..metrics.beauty import (
    compute_structural_beauty,
    compute_llm_enhanced_beauty,
    diagnose_beauty,
    compute_active_front_ratio,
    compute_anchor_poi_score,
)
from ..metrics.audio import analyze_scene_audio


class EvalEngine:
    """Main evaluation engine.

    Decoupled from RoadGen3D internals, uses only SceneLayout and EvalConfig.
    """

    def __init__(self, config: EvalConfig | None = None):
        self.config = config or EvalConfig.default()

    def evaluate(self, payload: Mapping[str, Any]) -> EvaluationResult:
        """Evaluate a scene from scene_layout.json payload.

        Args:
            payload: Full scene_layout.json content

        Returns:
            EvaluationResult with all scores and reports
        """
        # 解析场景
        scene = SceneLayout.from_layout_payload(payload)

        # 1. 步行性评估
        walkability = self._evaluate_walkability(scene)

        # 2. 安全性评估
        safety = self._evaluate_safety(scene, walkability)

        # 3. 美观性评估
        beauty = self._evaluate_beauty(scene)

        # 4. 音频配置 (可选)
        audio = None
        if self.config.enable_audio_profile:
            audio = self._evaluate_audio(scene)

        # 5. 综合评分
        evaluation_score = self._aggregate_scores(
            walkability.walkability_index,
            safety.final_score,
            beauty.final_score,
        )

        return EvaluationResult(
            walkability=walkability,
            safety=safety,
            beauty=beauty,
            audio=audio,
            evaluation_score=evaluation_score,
        )

    def _evaluate_walkability(self, scene: SceneLayout) -> WalkabilityIndicators:
        """Compute walkability indicators."""
        return compute_walkability(
            placements=scene.placements,
            length_m=scene.length_m,
            road_width_m=scene.road_width_m,
            sidewalk_width_m=scene.sidewalk_width_m,
            left_clear_path_width_m=scene.left_clear_path_width_m,
            right_clear_path_width_m=scene.right_clear_path_width_m,
            left_furnishing_width_m=scene.left_furnishing_width_m,
            right_furnishing_width_m=scene.right_furnishing_width_m,
            entrance_count=scene.entrance_count,
            mean_entrance_openness=scene.mean_entrance_openness,
            mean_noise_shielding=scene.mean_noise_shielding,
            bus_stop_points_xz=scene.bus_stop_points_xz,
            poi_points_by_type_xz=scene.poi_points_by_type_xz,
            land_use_summary=scene.land_use_summary,
            config=self.config.walkability,
        )

    def _evaluate_safety(self, scene: SceneLayout, walkability: WalkabilityIndicators) -> SafetyReport:
        """Compute safety report."""
        # 提取特征
        features, structural_score = compute_structural_safety(
            light_uni=walkability.light_uni,
            cross_prov=walkability.cross_prov,
            buffer_ratio=walkability.buffer_ratio,
            bollard_count=sum(
                1 for p in scene.placements
                if str(p.get("category", "")).strip().lower() == "bollard"
            ),
            length_m=scene.length_m,
            mean_openness=scene.mean_entrance_openness,
            dropped_slot_rate=float(scene.placements and 0.0 or 0.0),  # TODO: 从summary提取
            config=self.config.safety,
        )

        # LLM增强 (可选)
        llm_scores = None
        final_score = structural_score
        needs_review = False

        if self.config.enable_llm_eval:
            # TODO: 调用LLM评估
            # llm_scores = call_llm_safety_eval(features, scene)
            pass

        # 如果有LLM评分,重新计算
        if llm_scores:
            final_score, needs_review = compute_llm_enhanced_safety(
                features, llm_scores, self.config.safety
            )

        # 诊断
        diagnosis = diagnose_safety(features, llm_scores)

        return SafetyReport(
            features=features,
            structural_score=structural_score,
            llm_scores=llm_scores,
            final_score=final_score,
            llm_required=self.config.enable_llm_eval,
            needs_review=needs_review,
            diagnosis=diagnosis,
        )

    def _evaluate_beauty(self, scene: SceneLayout) -> BeautyReport:
        """Compute beauty report."""
        # 提取特征
        presentation_score = float(
            scene.placements[0].get("score", 0.0) if scene.placements else 0.0
        )  # TODO: 从composition_report提取

        active_front_ratio = compute_active_front_ratio(
            entrance_count=scene.entrance_count,
            length_m=scene.length_m,
            active_frontage_span_m=self.config.beauty.active_frontage_span_m,
            active_frontage_ratio_ideal=self.config.beauty.active_frontage_ratio_ideal,
        )

        anchor_poi_score = compute_anchor_poi_score(
            poi_points=scene.poi_points_by_type_xz,
            length_m=scene.length_m,
            anchor_poi_density_ideal=self.config.beauty.anchor_poi_density_ideal,
        )

        features, structural_score = compute_structural_beauty(
            presentation_score=presentation_score,
            active_front_ratio=active_front_ratio,
            anchor_poi_score=anchor_poi_score,
            visual_clutter=0.0,  # TODO: 从composition_report提取
            config=self.config.beauty,
        )

        # LLM增强 (可选)
        llm_scores = None
        final_score = structural_score
        needs_review = False

        if self.config.enable_llm_eval:
            # TODO: 调用LLM评估
            # llm_scores = call_llm_beauty_eval(features, scene)
            pass

        # 如果有LLM评分,重新计算
        if llm_scores:
            final_score, needs_review = compute_llm_enhanced_beauty(
                features, llm_scores, self.config.beauty
            )

        # 诊断
        diagnosis = diagnose_beauty(features, llm_scores)

        return BeautyReport(
            features=features,
            structural_score=structural_score,
            llm_scores=llm_scores,
            final_score=final_score,
            llm_required=self.config.enable_llm_eval,
            needs_review=needs_review,
            diagnosis=diagnosis,
        )

    def _evaluate_audio(self, scene: SceneLayout) -> AudioProfile:
        """Compute audio profile."""
        return analyze_scene_audio(
            placements=scene.placements,
            length_m=scene.length_m,
            road_width_m=scene.road_width_m,
            lane_count=scene.lane_count,
            density=scene.density,
            vehicle_demand=float(
                scene.placements[0].get("vehicle_demand_level", 0.5)
                if scene.placements else 0.5
            ),  # TODO: 从config提取
            config=self.config.audio,
        )

    def _aggregate_scores(
        self,
        walkability_index: float,
        safety_score: float,
        beauty_score: float,
    ) -> float:
        """Compute combined evaluation score."""
        agg = self.config.aggregation
        return round(
            agg.walkability_weight * walkability_index
            + agg.safety_weight * safety_score
            + agg.beauty_weight * beauty_score,
            4,
        )
