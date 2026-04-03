"""Load and parse Viewer-exported graph JSON into scene-generation context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from ..osm_ingest import ProjectedFeatures
from ..placement_zones import PlacementContext
from ..reference_annotation import (
    ReferenceAnnotation,
    parse_reference_annotation,
)
from ..reference_annotation_scene_bridge import build_reference_annotation_scene_bridge
from ..types import RoadSegmentGraph


@dataclass(frozen=True)
class GraphSceneContext:
    """Parsed graph context ready for scene generation."""

    road_segment_graph: RoadSegmentGraph
    projected_features: ProjectedFeatures
    placement_context: PlacementContext
    annotation: ReferenceAnnotation
    graph_summary: Dict[str, Any]


def load_graph_from_exported_json(graph_json_path: str | Path) -> GraphSceneContext:
    """Load scene context from a Viewer-exported ``ConvertedGraphPayload`` JSON.

    The JSON may be either:
    * A bare ``ReferenceAnnotation`` dict, or
    * A ``ConvertedGraphPayload`` that wraps ``annotation`` + graph metadata.
    """
    path = Path(graph_json_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Graph JSON not found: {path}")

    raw: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))

    # If the top-level has an "annotation" key it is a ConvertedGraphPayload;
    # otherwise treat the whole dict as a ReferenceAnnotation.
    annotation_payload = raw.get("annotation") or raw

    bridge_result = build_reference_annotation_scene_bridge(annotation_payload)

    annotation = bridge_result.annotation
    graph_summary = _extract_graph_summary(annotation, bridge_result.summary_metadata)

    return GraphSceneContext(
        road_segment_graph=bridge_result.road_segment_graph,
        projected_features=bridge_result.projected_features,
        placement_context=bridge_result.placement_context,
        annotation=annotation,
        graph_summary=graph_summary,
    )


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def _extract_graph_summary(
    annotation: ReferenceAnnotation,
    bridge_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Derive a compact summary dict for LLM prompts."""
    centerline_count = len(annotation.centerlines)
    road_widths: List[float] = []
    for cl in annotation.centerlines:
        road_widths.append(float(cl.carriageway_width_m()))

    junction_count = len(annotation.junctions) if hasattr(annotation, "junctions") else 0
    building_count = len(annotation.building_regions) if hasattr(annotation, "building_regions") else 0
    cross_section_strips: List[Any] = []
    if hasattr(annotation, "cross_section_strips"):
        cross_section_strips = list(annotation.cross_section_strips)

    return {
        "centerline_count": centerline_count,
        "road_widths": road_widths,
        "junction_count": junction_count,
        "building_regions_count": building_count,
        "cross_section_strip_count": len(cross_section_strips),
        "image_width_px": float(annotation.image_width_px),
        "image_height_px": float(annotation.image_height_px),
        "pixels_per_meter": float(annotation.pixels_per_meter),
        **{k: v for k, v in bridge_metadata.items() if isinstance(v, (int, float, str, bool))},
    }


def build_graph_context_description(graph_summary: Dict[str, Any]) -> str:
    """Convert *graph_summary* into a natural-language paragraph for LLM prompts."""
    parts: List[str] = []
    n_center = graph_summary.get("centerline_count", 0)
    widths = graph_summary.get("road_widths", [])
    n_junctions = graph_summary.get("junction_count", 0)
    n_buildings = graph_summary.get("building_regions_count", 0)
    n_strips = graph_summary.get("cross_section_strip_count", 0)

    parts.append(f"The road network has {n_center} centerline(s).")
    if widths:
        width_str = ", ".join(f"{w:.1f}m" for w in widths)
        parts.append(f"Road widths: [{width_str}].")
    if n_junctions:
        parts.append(f"There are {n_junctions} junction(s).")
    if n_buildings:
        parts.append(f"There are {n_buildings} building region(s).")
    if n_strips:
        parts.append(f"Cross-section has {n_strips} strip(s) defined.")

    return " ".join(parts)


def build_initial_design_messages(
    graph_summary: Dict[str, Any],
    *,
    base_map_data_url: str | None = None,
    user_prompt: str = "",
) -> List[Dict[str, Any]]:
    """Build LLM messages that ask the model to propose initial design parameters.

    Returns a list of message dicts suitable for ``GLMClient.chat_json()``.
    """
    from ..services.design_types import ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS

    description = build_graph_context_description(graph_summary)
    allowed_fields = ", ".join(ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS)

    system_prompt = (
        "你是 RoadGen3D 的街道设计专家。"
        "你需要根据道路网络结构和参考底图设计街道家具布局参数。"
        "你只能输出 JSON。"
        "字段必须包含："
        "`compose_config_patch`(object) 和 `design_summary`(string)。"
        f"compose_config_patch 只能使用这些字段：{allowed_fields}。"
        "请尽量为所有允许字段都给出非空值。"
        "不要输出 None/null。不要编造具体资产 ID。"
    )

    user_payload: Dict[str, Any] = {
        "road_network_description": description,
        "graph_summary": graph_summary,
        "user_prompt": str(user_prompt).strip() or "Generate a suitable street design",
        "instruction": (
            "基于道路网络结构和参考底图（如有），"
            "输出适合该道路场景的街道家具布局参数。"
        ),
    }

    user_content: List[Dict[str, Any]] = [
        {"type": "text", "text": json.dumps(user_payload, ensure_ascii=False)},
    ]
    if base_map_data_url:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": base_map_data_url},
        })

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},  # type: ignore[list-item]
    ]
