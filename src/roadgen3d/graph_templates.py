"""Built-in graph templates for Workbench-driven street scene generation."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class GraphTemplate:
    """One built-in graph template backed by a checked-in JSON payload."""

    template_id: str
    label: str
    description: str
    annotation_path: Path
    image_path: Path
    source_format: str
    centerline_count: int
    junction_count: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "template_id": self.template_id,
            "label": self.label,
            "description": self.description,
            "annotation_path": str(self.annotation_path),
            "image_path": str(self.image_path),
            "source_format": self.source_format,
            "centerline_count": int(self.centerline_count),
            "junction_count": int(self.junction_count),
        }


_GRAPH_TEMPLATE_DEFINITIONS: Dict[str, Dict[str, object]] = {
    "hkust_gz_gate": {
        "label": "HKUST-GZ Gate Graph",
        "description": (
            "Checked-in street graph template for the HKUST(GZ) gate frontage. "
            "Used directly by Street Workbench for graph-driven 3D street scene generation."
        ),
        "annotation_path": (ROOT / "assets" / "graph_templates" / "hkust_gz_gate" / "annotation.json").resolve(),
        "image_path": (ROOT / "assets" / "hkust-gz" / "image.png").resolve(),
    },
    "hkust_gz_detailed": {
        "label": "HKUST-GZ Detailed",
        "description": (
            "Detailed HKUST(GZ) street graph with 5 building regions, 10 road centerlines, "
            "and 3 cross junctions. Extended coverage with building footprint annotations."
        ),
        "annotation_path": (ROOT / "assets" / "graph_templates" / "hkust_gz_detailed" / "annotation.json").resolve(),
        "image_path": (ROOT / "assets" / "hkust-gz" / "image.png").resolve(),
    },
}


def _extract_annotation_payload(raw_payload: Dict[str, Any], template_id: str) -> Dict[str, Any]:
    if isinstance(raw_payload.get("annotation"), dict):
        payload = copy.deepcopy(raw_payload["annotation"])
    else:
        payload = copy.deepcopy(raw_payload)
    if not isinstance(payload, dict):
        raise ValueError(f"Graph template annotation payload must be a JSON object: {template_id}")
    payload["plan_id"] = str(payload.get("plan_id") or template_id)
    payload["image_path"] = f"/api/graph-templates/{str(template_id).strip().lower()}/image"
    return payload


@lru_cache(maxsize=None)
def _load_template_payload(template_id: str) -> Dict[str, Any]:
    template = _GRAPH_TEMPLATE_DEFINITIONS.get(str(template_id or "").strip().lower())
    if template is None:
        raise KeyError(f"Unknown graph template: {template_id}")
    annotation_path = Path(template["annotation_path"]).expanduser().resolve()
    raw_payload = json.loads(annotation_path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise ValueError(f"Graph template payload must be a JSON object: {annotation_path}")
    return _extract_annotation_payload(raw_payload, str(template_id).strip().lower())


def _build_graph_template(template_id: str) -> GraphTemplate:
    key = str(template_id or "").strip().lower()
    if key not in _GRAPH_TEMPLATE_DEFINITIONS:
        raise KeyError(f"Unknown graph template: {template_id}")
    definition = _GRAPH_TEMPLATE_DEFINITIONS[key]
    payload = _load_template_payload(key)
    return GraphTemplate(
        template_id=key,
        label=str(definition["label"]),
        description=str(definition["description"]),
        annotation_path=Path(definition["annotation_path"]).expanduser().resolve(),
        image_path=Path(definition["image_path"]).expanduser().resolve(),
        source_format=str(payload.get("version") or "roadgen3d_reference_annotation_v2"),
        centerline_count=len(tuple(payload.get("centerlines", ()) or ())),
        junction_count=len(tuple(payload.get("junctions", ()) or ())),
    )


def list_graph_templates() -> Tuple[GraphTemplate, ...]:
    """Return all built-in graph templates."""

    return tuple(_build_graph_template(key) for key in _GRAPH_TEMPLATE_DEFINITIONS.keys())


def get_graph_template(template_id: str) -> GraphTemplate:
    """Return a built-in graph template by id."""

    return _build_graph_template(template_id)


def load_graph_template_annotation_payload(template_id: str) -> Dict[str, Any]:
    """Load a graph template payload as a mutable dict."""

    return copy.deepcopy(_load_template_payload(template_id))


__all__ = [
    "GraphTemplate",
    "get_graph_template",
    "list_graph_templates",
    "load_graph_template_annotation_payload",
]
