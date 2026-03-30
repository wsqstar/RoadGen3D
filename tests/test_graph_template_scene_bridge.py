from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.graph_template_scene_bridge import build_graph_template_scene_bridge  # noqa: E402
from roadgen3d.reference_annotation import build_reference_annotation_compose_config  # noqa: E402


def test_graph_template_scene_bridge_builds_hkust_gz_gate():
    pytest.importorskip("shapely")

    bridge = build_graph_template_scene_bridge(
        build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
        template_id="hkust_gz_gate",
    )

    assert bridge.graph_template.template_id == "hkust_gz_gate"
    assert bridge.road_segment_graph.nodes
    assert bridge.projected_features.roads
    assert bridge.placement_context is not None
    assert bridge.summary_metadata["layout_mode"] == "graph_template"
    assert bridge.summary_metadata["generator"] == "graph_template_bridge_v1"
    assert bridge.summary_metadata["graph_template_id"] == "hkust_gz_gate"
    assert bridge.summary_metadata["graph_template_source_format"] == "roadgen3d_reference_annotation_v2"
