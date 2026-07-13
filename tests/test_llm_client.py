from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.eval_engine_ext.road_metrics.evaluators.llm_client import (
    LLMClient,
    LLMSettings,
    extract_json_payload,
    public_llm_capabilities_from_env,
)


def test_extract_json_payload_accepts_wrapped_text():
    payload = extract_json_payload("Sure, here it is:\n{\"ok\": true, \"count\": 2}")
    assert payload == {"ok": True, "count": 2}


def test_llm_client_chat_json_uses_openai_compatible_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "```json\n{\"rag_queries\": [\"complete street safety\"]}\n```"
                        }
                    }
                ]
            },
        )

    client = LLMClient(
        LLMSettings(base_url="https://example.com/v4", api_key="test-key", model="llm-test"),
        transport=httpx.MockTransport(handler),
    )
    payload = client.chat_json([{"role": "user", "content": "hello"}])

    assert payload["rag_queries"] == ["complete street safety"]
    client.close()


def test_llm_settings_keep_openai_and_gpt4o_mini_as_development_defaults(monkeypatch):
    for key in (
        "ROADGEN_LLM_PROVIDER",
        "ROADGEN_LLM_BASE_URL",
        "ROADGEN_LLM_API_KEY",
        "ROADGEN_LLM_MODEL",
        "ROADGEN_LLM_VISION_MODEL",
        "GRAPHRAG_API_BASE",
        "GRAPHRAG_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "OPENAI_API_KEY",
        "LLM_MODEL",
        "key",
        "llm_base_url",
    ):
        monkeypatch.setenv(key, "")

    monkeypatch.setenv("ROADGEN_LLM_API_KEY", "test-key")

    settings = LLMSettings.from_env()

    assert settings.provider == "openai"

    assert settings.base_url == "https://api.openai.com/v1"
    assert settings.model == "gpt-4o-mini"
    assert settings.vision_model == "gpt-4o-mini"


def test_public_vision_identity_can_resolve_without_api_key(monkeypatch):
    monkeypatch.setenv("ROADGEN_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("ROADGEN_LLM_BASE_URL", "https://dashscope.example/v1")
    monkeypatch.setenv("ROADGEN_LLM_API_KEY", "")
    monkeypatch.setenv("ROADGEN_LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("ROADGEN_LLM_VISION_MODEL", "qwen-vl-max")

    identity = LLMSettings.public_identity_from_env("vision")

    assert identity["provider"] == "openai_compatible"
    assert identity["model"] == "qwen-vl-max"
    assert identity["endpoint_fingerprint"].startswith("sha256:")
    assert "dashscope.example" not in json.dumps(identity)


def test_qwen_uses_openai_compatible_protocol_and_separate_vision_model(monkeypatch):
    monkeypatch.setenv("ROADGEN_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("ROADGEN_LLM_BASE_URL", "https://dashscope.example/v1")
    monkeypatch.setenv("ROADGEN_LLM_API_KEY", "qwen-secret")
    monkeypatch.setenv("ROADGEN_LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("ROADGEN_LLM_VISION_MODEL", "qwen-vl-max")
    requested_models = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        requested_models.append(payload["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"ok\": true}"}}]})

    settings = LLMSettings.from_env()
    client = LLMClient(settings, transport=httpx.MockTransport(handler))
    client.chat_json([{"role": "user", "content": "text"}], capability="text")
    client.chat_json(
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}],
        capability="vision",
    )
    client.close()

    assert requested_models == ["qwen-plus", "qwen-vl-max"]
    capabilities = public_llm_capabilities_from_env()
    assert capabilities["protocol"] == "openai_chat_completions"
    assert capabilities["provider"] == "openai_compatible"
    assert capabilities["endpoint_fingerprint"].startswith("sha256:")
    assert "dashscope.example" not in json.dumps(capabilities)
    assert "qwen-secret" not in json.dumps(capabilities)
