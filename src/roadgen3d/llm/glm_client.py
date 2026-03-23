"""OpenAI-compatible client wrapper for the GLM provider."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Dict, Mapping, Sequence

import httpx


class GLMConfigurationError(RuntimeError):
    """Raised when GLM credentials or base URL are missing."""


class GLMResponseError(RuntimeError):
    """Raised when the GLM response cannot be parsed."""


@dataclass(frozen=True)
class GLMSettings:
    base_url: str
    api_key: str
    model: str = "glm-4-flash"

    @classmethod
    def from_env(cls) -> "GLMSettings":
        _maybe_load_dotenv()
        base_url = str(os.environ.get("glm_base_url", "")).strip()
        api_key = str(os.environ.get("key", "")).strip()
        model = str(os.environ.get("glm_model", "")).strip() or "glm-4-flash"
        if not base_url or not api_key:
            raise GLMConfigurationError(
                "Missing GLM configuration. Set `glm_base_url` and `key` in the environment or .env."
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
    ) -> None:
        self.settings = settings or GLMSettings.from_env()
        self.timeout = float(timeout)
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
        response = self._client.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GLMResponseError("GLM response did not include a valid message payload.") from exc
        return _coerce_message_content(content)

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
