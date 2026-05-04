"""Backward-compatible aliases for the unified RoadGen3D LLM client.

The project no longer maintains a separate GLM-specific client.  Existing
imports from ``roadgen3d.llm.glm_client`` are kept working by aliasing them to
the unified OpenAI-compatible ``LLMClient`` implementation.
"""

from __future__ import annotations

from ..eval_engine_ext.road_metrics.evaluators.llm_client import (
    LLMClient as _LLMClient,
    LLMConfigurationError as _LLMConfigurationError,
    LLMResponseError as _LLMResponseError,
    LLMSettings as _LLMSettings,
    extract_json_payload,
)

GLMClient = _LLMClient
GLMSettings = _LLMSettings
GLMConfigurationError = _LLMConfigurationError
GLMResponseError = _LLMResponseError

__all__ = [
    "GLMClient",
    "GLMSettings",
    "GLMConfigurationError",
    "GLMResponseError",
    "extract_json_payload",
]
