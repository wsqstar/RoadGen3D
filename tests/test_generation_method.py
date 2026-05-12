from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.generation_method import infer_generation_method


def test_generation_method_classifies_pareto_as_parametric():
    assert infer_generation_method(candidate_source="pareto_search", knowledge_source="graph_rag") == "parametric"


def test_generation_method_classifies_llm_with_evidence_as_assisted():
    assert infer_generation_method(
        candidate_source="branch_llm_candidate",
        knowledge_source="graph_rag",
        influence_rows=[
            {"source_type": "rag"},
            {"source_type": "parameter_triple"},
            {"source_type": "llm_patch"},
        ],
    ) == "llm_assisted"


def test_generation_method_classifies_llm_without_evidence_as_pure_llm():
    assert infer_generation_method(
        candidate_source="branch_llm_candidate",
        knowledge_source="none",
        influence_rows=[{"source_type": "llm_patch"}],
        rag_evidence=[],
    ) == "pure_llm"
