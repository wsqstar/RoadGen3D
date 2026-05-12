"""Generation-method provenance helpers."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


GENERATION_METHOD_LLM_ASSISTED = "llm_assisted"
GENERATION_METHOD_PURE_LLM = "pure_llm"
GENERATION_METHOD_PARAMETRIC = "parametric"
GENERATION_METHOD_UNKNOWN_LEGACY = "unknown_legacy"
GENERATION_METHODS = {
    GENERATION_METHOD_LLM_ASSISTED,
    GENERATION_METHOD_PURE_LLM,
    GENERATION_METHOD_PARAMETRIC,
    GENERATION_METHOD_UNKNOWN_LEGACY,
}


def normalize_generation_method(value: Any) -> str:
    method = str(value or "").strip().lower()
    return method if method in GENERATION_METHODS else ""


def infer_generation_method(
    *,
    candidate_source: str | None = None,
    knowledge_source: str | None = None,
    influence_rows: Sequence[Mapping[str, Any]] | None = None,
    rag_evidence: Sequence[Mapping[str, Any]] | None = None,
    parameter_sources_by_field: Mapping[str, Any] | None = None,
    explicit: str | None = None,
) -> str:
    """Infer the source bucket for generated scene parameters.

    This intentionally classifies *parameter provenance*, not whether the final
    exporter or evaluator used LLMs.
    """
    normalized = normalize_generation_method(explicit)
    if normalized:
        return normalized

    source = str(candidate_source or "").strip().lower()
    if source in {"pareto_search", "parametric", "parameter_search", "preset", "preset_patch", "manual", "manual_patch"}:
        return GENERATION_METHOD_PARAMETRIC

    row_types = {
        str(row.get("source_type") or "").strip().lower()
        for row in (influence_rows or ())
        if isinstance(row, Mapping)
    }
    if "search_patch" in row_types and "llm_patch" not in row_types:
        return GENERATION_METHOD_PARAMETRIC

    parameter_sources = {
        str(value or "").strip().lower()
        for value in (parameter_sources_by_field or {}).values()
    }
    if parameter_sources and parameter_sources <= {"system_default", "manual", "preset", "parametric", "search_patch"}:
        return GENERATION_METHOD_PARAMETRIC

    has_llm_patch = source in {"branch_llm_candidate", "llm_candidate", "llm", "pure_llm"} or "llm_patch" in row_types
    has_assistance = any(item in row_types for item in {"rag", "parameter_triple", "directive", "constraint"})
    has_assistance = has_assistance or bool(rag_evidence)
    knowledge = str(knowledge_source or "").strip().lower()
    if knowledge and knowledge not in {"none", "manual", "parametric"}:
        has_assistance = True
    if any(value in {"rag", "llm_inferred", "scenario_parameters", "graph_rag", "pdf_rag"} for value in parameter_sources):
        has_assistance = True

    if has_llm_patch:
        return GENERATION_METHOD_LLM_ASSISTED if has_assistance else GENERATION_METHOD_PURE_LLM

    return GENERATION_METHOD_UNKNOWN_LEGACY
