"""LLM-based safety evaluation for street scenes."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .llm_client import LLMClient, LLMConfigurationError

_CACHE_DIR = Path(__file__).resolve().parents[3] / "artifacts" / "eval_cache"
_CACHE_VERSION = "safety_eval_v1"


def _cache_key(payload: Mapping[str, Any], image_path: str | None) -> str:
    key_source = {
        "version": _CACHE_VERSION,
        "features": dict(payload),
        "image_path": str(image_path or ""),
    }
    digest = hashlib.sha256(
        json.dumps(key_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return str(digest)


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"safety_{key}.json"


def _load_cached(key: str) -> Dict[str, Any] | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if str(payload.get("version", "")) != _CACHE_VERSION:
            return None
        return dict(payload.get("result", {}))
    except Exception:
        return None


def _save_cached(key: str, result: Mapping[str, Any]) -> None:
    path = _cache_path(key)
    try:
        path.write_text(
            json.dumps({"version": _CACHE_VERSION, "result": dict(result)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _build_safety_eval_messages(
    features: Mapping[str, Any],
    image_data_url: str | None,
) -> Sequence[Dict[str, Any]]:
    system_prompt = (
        "You are an urban design auditor scoring perceived pedestrian safety.\n"
        "Rate the scene on four sub-dimensions and an overall score, each on a 0–5 scale.\n"
        "0 = very unsafe / absent, 5 = excellent / fully provided.\n"
        "You must respond with ONLY a JSON object containing these keys:\n"
        "  lighting (number 0–5): continuity and adequacy of street lighting\n"
        "  visibility (number 0–5): eyes-on-street, openness, and sightlines\n"
        "  protection (number 0–5): physical separation from traffic (buffers, bollards, crossings)\n"
        "  activation (number 0–5): activity and surveillance provided by entrances/POIs\n"
        "  overall (number 0–5): holistic safety impression\n"
        "  reasoning (string): brief justification citing the provided metrics\n"
        "Do not output markdown or any text outside the JSON object."
    )
    user_text = {
        "structured_features": dict(features),
        "instruction": "Evaluate pedestrian safety based on the metrics and the scene image (if provided).",
    }
    user_content: list[dict] = [{"type": "text", "text": json.dumps(user_text, ensure_ascii=False)}]
    if image_data_url:
        user_content.append({"type": "image_url", "image_url": {"url": image_data_url}})
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def evaluate_safety(
    features: Mapping[str, Any],
    image_path: str | None = None,
    llm_client: LLMClient | Any | None = None,
) -> Dict[str, Any]:
    """Run LLM safety evaluation with file-based caching.

    Args:
        features: Dict with keys like LIGHT_UNI, CROSS_PROV, BUFFER_RATIO,
                  BOLLARD_DENSITY, VISIBILITY_PENALTY.
        image_path: Optional path to a top-down preview image.
        llm_client: Optional LLMClient instance.

    Returns:
        Dict with keys: lighting, visibility, protection, activation, overall
        (all numbers 0–1 after normalization), reasoning, and cached (bool).
    """
    cache_key = _cache_key(features, image_path)
    cached = _load_cached(cache_key)
    if cached is not None:
        cached["cached"] = True
        return cached

    image_data_url = None
    if image_path:
        img = Path(image_path).expanduser().resolve()
        if img.exists():
            image_data_url = f"data:image/png;base64,{base64.b64encode(img.read_bytes()).decode('ascii')}"

    messages = _build_safety_eval_messages(features, image_data_url)
    client = llm_client or LLMClient()
    try:
        payload = client.chat_json(messages, temperature=0.2)
    except LLMConfigurationError:
        payload = {}

    def _norm(value: object) -> float:
        try:
            return max(0.0, min(1.0, float(value) / 5.0))
        except Exception:
            return 0.0

    result = {
        "lighting": _norm(payload.get("lighting", 0)),
        "visibility": _norm(payload.get("visibility", 0)),
        "protection": _norm(payload.get("protection", 0)),
        "activation": _norm(payload.get("activation", 0)),
        "overall": _norm(payload.get("overall", 0)),
        "reasoning": str(payload.get("reasoning", "") or "").strip(),
        "cached": False,
    }
    _save_cached(cache_key, result)
    return result
