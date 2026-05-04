from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.knowledge.scenario_parameters import (
    ScenarioParameterTripleStore,
    build_preset_triples,
    parse_suitability_matrix_triples,
    write_triples_jsonl,
    write_triples_metadata,
)
from roadgen3d.presets import SCENE_PRESETS


MATRIX_PATH = (
    ROOT
    / "knowledge"
    / "graphRAG"
    / "graphrag_quickstart"
    / "input"
    / "003_1.1.2_DESIGN_TREATMENT_SUITABILITY_MATRIX.txt"
)


def test_parse_suitability_matrix_width_rows_and_column_order():
    triples = parse_suitability_matrix_triples(
        MATRIX_PATH.read_text(encoding="utf-8"),
        source_path=MATRIX_PATH,
    )

    assert len(triples) == 44
    by_key = {(item.scenario_id, item.parameter_name): item for item in triples}
    sidewalk = by_key[("street_type.high_volume_pedestrian", "sidewalk_width_m")]
    assert sidewalk.scenario_label == "High-Volume Pedestrian"
    assert sidewalk.raw_value == "≥16'"
    assert sidewalk.normalized_value == 4.877
    assert sidewalk.unit == "m"

    no_minimum = by_key[("street_type.shared_narrow", "furnishing_zone_width_m")]
    assert no_minimum.raw_value == "No. Min."
    assert no_minimum.normalized_value is None
    assert no_minimum.unit == ""
    assert "No minimum" in no_minimum.notes


def test_build_preset_triples_extracts_expanded_config_patch():
    triples = build_preset_triples(SCENE_PRESETS)
    by_key = {(item.scenario_id, item.parameter_name): item for item in triples}

    density = by_key[("preset.pedestrian_friendly", "density")]
    assert density.raw_value == 0.5
    assert density.normalized_value == 0.5
    assert density.unit == "ratio"
    assert density.confidence == 1.0
    assert len(triples) == 102


def test_jsonl_output_is_stable_and_metadata_counts_sources(tmp_path: Path):
    matrix_triples = parse_suitability_matrix_triples(
        MATRIX_PATH.read_text(encoding="utf-8"),
        source_path=MATRIX_PATH,
    )
    preset_triples = build_preset_triples(SCENE_PRESETS)
    triples = [*matrix_triples, *preset_triples]
    jsonl_path = tmp_path / "scenario_parameter_triples.jsonl"
    metadata_path = tmp_path / "scenario_parameter_triples.metadata.json"

    summary = write_triples_jsonl(jsonl_path, triples)
    second_summary = write_triples_jsonl(jsonl_path, triples)
    metadata = write_triples_metadata(
        metadata_path,
        triples_path=jsonl_path,
        triples=triples,
        extra={
            "matrix_triple_count": len(matrix_triples),
            "preset_triple_count": len(preset_triples),
        },
    )
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]

    assert summary["fingerprint"] == second_summary["fingerprint"]
    assert summary["triple_count"] == 146
    assert metadata["fingerprint"] == summary["fingerprint"]
    assert metadata["matrix_triple_count"] == 44
    assert metadata["preset_triple_count"] == 102
    assert metadata["sources"]["Complete-Streets-Design-Handbook-2024"] == 44
    assert metadata["sources"]["roadgen3d.presets.SCENE_PRESETS"] == 102
    assert len({row["chunk_id"] for row in rows}) == len(rows)
    assert rows == sorted(
        rows,
        key=lambda item: (
            item["source_doc"],
            item["scenario_id"],
            item["parameter_name"],
            str(item["raw_value"]),
            item["chunk_id"],
        ),
    )


def test_lexical_search_prioritizes_walkable_commercial_sidewalk_width(tmp_path: Path):
    triples = parse_suitability_matrix_triples(
        MATRIX_PATH.read_text(encoding="utf-8"),
        source_path=MATRIX_PATH,
    )
    jsonl_path = tmp_path / "scenario_parameter_triples.jsonl"
    write_triples_jsonl(jsonl_path, triples)

    store = ScenarioParameterTripleStore(jsonl_path)
    hits = store.search("walkable commercial sidewalk width", topk=5)

    assert hits
    assert (
        hits[0].chunk.chunk_id
        == "scenario_parameters::matrix::street_type_walkable_commercial_corridor::sidewalk_width_m"
    )
    assert json.loads(hits[0].chunk.text)["normalized_value"] == 3.658
