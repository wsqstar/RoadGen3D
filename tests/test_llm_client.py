from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.llm.llm_client import LLMClient, LLMSettings, extract_json_payload


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
