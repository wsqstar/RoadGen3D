"""LLM clients and prompting helpers for the design assistant."""

from .llm_client import LLMClient, LLMConfigurationError, LLMResponseError
from .prompts import (
    build_parameter_followup_query_messages,
    build_design_draft_messages,
    build_design_intent_messages,
    build_graph_aware_design_messages,
    build_layout_edit_messages,
    build_layout_evaluation_messages,
    build_rag_query_translation_messages,
)

# Optional: LLM-based design workflow (requires knowledge base)
# from .design_workflow import DesignAssistantService

__all__ = [
    "LLMClient",
    "LLMConfigurationError",
    "LLMResponseError",
    "build_parameter_followup_query_messages",
    "build_design_draft_messages",
    "build_design_intent_messages",
    "build_graph_aware_design_messages",
    "build_layout_edit_messages",
    "build_layout_evaluation_messages",
    "build_rag_query_translation_messages",
]
