"""OpenAI-compatible client wrapper for LLM chat completions.

Supports any OpenAI-compatible API (GLM, Gemini via proxy, etc.).
Reads credentials from ``GRAPHRAG_API_KEY`` / ``GRAPHRAG_API_BASE`` (preferred)
or the legacy ``glm_base_url`` / ``key`` environment variables.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Dict, Mapping, Sequence

import httpx

logger = logging.getLogger(__name__)

_retry_rng = random.Random()


class GLMConfigurationError(RuntimeError):
    """Raised when GLM credentials or base URL are missing."""


class GLMResponseError(RuntimeError):
    """Raised when the GLM response cannot be parsed."""


@dataclass(frozen=True)
class GLMSettings:
    base_url: str
    api_key: str
    model: str = "gemini-3-flash-preview"

    @classmethod
    def from_env(cls) -> "GLMSettings":
        _maybe_load_dotenv()
        # Prefer GRAPHRAG_ vars; fall back to legacy glm_ vars
        base_url = (
            str(os.environ.get("GRAPHRAG_API_BASE", "")).strip().rstrip("/")
            or str(os.environ.get("glm_base_url", "")).strip().rstrip("/")
        )
        api_key = (
            str(os.environ.get("GRAPHRAG_API_KEY", "")).strip()
            or str(os.environ.get("key", "")).strip()
        )
        model = (
            str(os.environ.get("LLM_MODEL", "")).strip()
            or str(os.environ.get("glm_model", "")).strip()
            or "gemini-3-flash-preview"
        )
        if not base_url or not api_key:
            raise GLMConfigurationError(
                "Missing LLM configuration. Set GRAPHRAG_API_BASE + GRAPHRAG_API_KEY "
                "(or legacy glm_base_url + key) in the environment or .env."
            )
        return cls(base_url=base_url.rstrip("/"), api_key=api_key, model=model)


class GLMClient:
    """Minimal JSON-capable chat client for the GLM OpenAI-compatible endpoint."""

    def __init__(
        self,
        settings: GLMSettings | None = None,
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 10,
        base_delay: float = 4.0,
    ) -> None:
        self.settings = settings or GLMSettings.from_env()
        self.timeout = float(timeout)
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._client = httpx.Client(timeout=self.timeout, transport=transport)

    @property
    def endpoint(self) -> str:
        base_url = self.settings.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    def close(self) -> None:
        self._client.close()

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float = 0.2,
    ) -> str:
        payload = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": str(item.get("role", "user")),
                    "content": str(item.get("content", "")),
                }
                for item in messages
            ],
            "temperature": float(temperature),
        }
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            response = self._client.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self.settings.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code == 429:
                retry_after = _parse_retry_after(response)
                delay = retry_after if retry_after else self.base_delay * (2 ** (attempt - 1))
                jitter = _retry_rng.uniform(0.5, 1.5)
                delay = delay * jitter
                logger.warning(
                    "Rate limited (429) on attempt %d/%d, retrying in %.1fs",
                    attempt, self.max_retries, delay,
                )
                time.sleep(delay)
                last_exc = httpx.HTTPStatusError(
                    f"429 Too Many Requests for url '{response.url}'",
                    request=response.request,
                    response=response,
                )
                continue
            response.raise_for_status()
            data = response.json()
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise GLMResponseError("GLM response did not include a valid message payload.") from exc
            return _coerce_message_content(content)
        raise last_exc  # type: ignore[misc]

    def chat_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        content = self.chat(messages, temperature=temperature)
        payload = extract_json_payload(content)
        if not isinstance(payload, dict):
            raise GLMResponseError("Expected the LLM to return a JSON object.")
        return payload


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Extract retry-after delay from response headers or body."""
    # Standard Retry-After header (seconds)
    header_val = response.headers.get("retry-after")
    if header_val:
        try:
            return float(header_val)
        except ValueError:
            pass
    # Some APIs include it in the JSON body
    try:
        body = response.json()
        wait = body.get("error", {}).get("retry_after") or body.get("retry_after")
        if wait is not None:
            return float(wait)
    except Exception:
        pass
    return None


def _coerce_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def extract_json_payload(text: str) -> Any:
    decoder = json.JSONDecoder()
    payload_text = str(text or "").strip()
    if not payload_text:
        raise GLMResponseError("LLM returned an empty response.")
    for index, char in enumerate(payload_text):
        if char not in "{[":
            continue
        try:
            payload, _ = decoder.raw_decode(payload_text[index:])
            return payload
        except JSONDecodeError:
            continue
    raise GLMResponseError("LLM response did not contain a valid JSON payload.")


def _maybe_load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    load_dotenv()
