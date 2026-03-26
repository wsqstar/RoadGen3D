from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.metaurban_procedural import (
    MetaUrbanProceduralConfig,
    SUPPORTED_METAURBAN_BLOCKS,
    build_metaurban_layout_payload,
    build_metaurban_segment_graph,
    sample_metaurban_block_sequence,
    write_metaurban_layout_payload,
)


def test_sample_metaurban_block_sequence_uses_supported_tokens():
    sequence = sample_metaurban_block_sequence(12, rng=random.Random(7))
    assert len(sequence) == 12
    assert set(sequence).issubset(set(SUPPORTED_METAURBAN_BLOCKS))


def test_build_metaurban_segment_graph_from_sequence_contains_junctions_and_turns():
    config = MetaUrbanProceduralConfig(
        seed=11,
        block_sequence="SCXT",
        segment_length_m=8.0,
        straight_length_m=20.0,
        branch_length_m=16.0,
        intersection_span_m=12.0,
    )

    graph = build_metaurban_segment_graph(config)

    assert graph.mode == "metaurban_procedural"
    assert len(graph.nodes) >= 8
    assert len(graph.edges) >= len(graph.nodes) - 1
    assert any(node.is_junction for node in graph.nodes)
    assert graph.summary()["junction_segment_count"] >= 1
    assert max(abs(float(node.center_xy[1])) for node in graph.nodes) > 0.1


def test_metaurban_layout_payload_writes_json(tmp_path: Path):
    config = MetaUrbanProceduralConfig(seed=5, block_sequence="SXT", block_count=3)
    output_path = tmp_path / "metaurban_layout.json"

    payload = write_metaurban_layout_payload(output_path, config)

    assert output_path.exists()
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["generator"] == "metaurban_procedural_v1"
    assert saved["summary"]["block_sequence"] == "SXT"
    assert saved["graph"]["mode"] == "metaurban_procedural"
    assert payload["summary"] == saved["summary"]


def test_build_metaurban_layout_payload_uses_explicit_sequence_without_sampling():
    config = MetaUrbanProceduralConfig(seed=99, block_sequence="TCS", block_count=99)

    payload = build_metaurban_layout_payload(config)

    assert payload["summary"]["block_sequence"] == "TCS"
    assert payload["config"]["block_count"] == 99
