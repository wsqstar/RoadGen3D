from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.graph_templates import get_graph_template, list_graph_templates, load_graph_template_annotation_payload  # noqa: E402


def test_graph_template_registry_exposes_hkust_gz_gate():
    templates = list_graph_templates()

    assert any(template.template_id == "hkust_gz_gate" for template in templates)
    template = get_graph_template("hkust_gz_gate")
    assert template.annotation_path.exists()
    assert template.image_path.exists()
    assert template.centerline_count == 10
    assert template.junction_count == 3


def test_graph_template_payload_normalizes_image_path():
    payload = load_graph_template_annotation_payload("hkust_gz_gate")

    assert payload["plan_id"] == "hkust_gz_gate"
    assert payload["version"] == "roadgen3d_reference_annotation_v2"
    assert payload["image_path"] == "/api/graph-templates/hkust_gz_gate/image"
    assert len(payload["centerlines"]) == 10
    assert len(payload["junctions"]) == 3
