"""Bridge built-in graph templates into the corridor scene/export pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

from .graph_templates import GraphTemplate, get_graph_template, load_graph_template_annotation_payload
from .osm_ingest import ProjectedFeatures
from .placement_zones import PlacementContext
from .reference_annotation import ReferenceAnnotation, build_reference_annotation_compose_config
from .reference_annotation_scene_bridge import build_reference_annotation_scene_bridge
from .template_patch import apply_template_patch
from .types import RoadSegmentGraph, StreetComposeConfig


@dataclass(frozen=True)
class GraphTemplateSceneBridgeResult:
    """Synthetic corridor context derived from a built-in graph template."""

    graph_template: GraphTemplate
    annotation: ReferenceAnnotation
    road_segment_graph: RoadSegmentGraph
    projected_features: ProjectedFeatures
    placement_context: PlacementContext
    summary_metadata: Dict[str, Any]


def build_graph_template_scene_bridge(
    compose_config: StreetComposeConfig | Mapping[str, Any] | None = None,
    *,
    template_id: str,
    template_patch: Mapping[str, Any] | None = None,
) -> GraphTemplateSceneBridgeResult:
    """Build synthetic corridor geometry/context for a built-in graph template."""

    graph_template = get_graph_template(template_id)
    resolved_config = (
        compose_config
        if isinstance(compose_config, StreetComposeConfig)
        else build_reference_annotation_compose_config(compose_config or {})
    )
    annotation_payload = load_graph_template_annotation_payload(graph_template.template_id)
    patch_summary: Dict[str, Any] = {}
    if template_patch:
        patch_application = apply_template_patch(annotation_payload, template_patch)
        annotation_payload = patch_application.annotation
        patch_summary = dict(patch_application.summary)
    bridge = build_reference_annotation_scene_bridge(
        annotation_payload,
        compose_config=resolved_config,
    )
    summary_metadata = {
        **dict(bridge.summary_metadata),
        "layout_mode": "graph_template",
        "generator": "graph_template_bridge_v1",
        "graph_template_id": graph_template.template_id,
        "graph_template_label": graph_template.label,
        "graph_template_source_format": graph_template.source_format,
    }
    if patch_summary:
        summary_metadata["template_patch"] = patch_summary
    return GraphTemplateSceneBridgeResult(
        graph_template=graph_template,
        annotation=bridge.annotation,
        road_segment_graph=bridge.road_segment_graph,
        projected_features=bridge.projected_features,
        placement_context=bridge.placement_context,
        summary_metadata=summary_metadata,
    )


__all__ = [
    "GraphTemplateSceneBridgeResult",
    "build_graph_template_scene_bridge",
]
