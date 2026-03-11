"""Text embedding utilities for CLIP-based retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np

from .runtime_device import resolve_device_backend, resolve_torch_device


class ModelLoadError(RuntimeError):
    """Raised when the CLIP model cannot be loaded."""


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """L2-normalize rows; zero rows stay zero."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return (matrix / norms).astype(np.float32, copy=False)


@dataclass(frozen=True)
class EmbedderConfig:
    model_name: str = "openai/clip-vit-base-patch32"
    model_dir: Optional[Path] = None
    local_files_only: bool = False
    device: str = "auto"


class ClipTextEmbedder:
    """Wrapper around CLIP text projection via `get_text_features`."""

    def __init__(
        self,
        model_name: str = "openai/clip-vit-base-patch32",
        model_dir: Optional[Union[str, Path]] = None,
        local_files_only: bool = False,
        device: str = "auto",
    ) -> None:
        try:
            import torch
        except ImportError as exc:
            raise ModelLoadError("`torch` is not installed. Install requirements-m1.txt first.") from exc

        try:
            from transformers import CLIPModel, CLIPTokenizer
        except ImportError as exc:
            raise ModelLoadError("`transformers` is not installed. Install requirements-m1.txt first.") from exc

        source = str(model_dir) if model_dir else model_name
        load_kwargs = {"local_files_only": local_files_only}
        try:
            # Text-only retrieval only needs tokenizer + model.
            self.tokenizer = CLIPTokenizer.from_pretrained(source, **load_kwargs)
            self.model = CLIPModel.from_pretrained(source, **load_kwargs)
        except Exception as exc:
            extra_hint = ""
            text = str(exc)
            if "CVE-2025-32434" in text or "upgrade torch to at least v2.6" in text:
                extra_hint = (
                    " Detected torch<2.6 with unsafe .bin loading restriction. "
                    "Fix option A (recommended): upgrade torch in your venv, e.g. "
                    "`uv pip install --python .venv/bin/python 'torch>=2.6,<2.8'`. "
                    "Fix option B: use local safetensors weights (model.safetensors)."
                )
            message = (
                f"Failed to load CLIP model from '{source}'. "
                "If you are offline, use --local-files-only with a prepared --model-dir. "
                f"Root cause: {type(exc).__name__}: {exc}.{extra_hint}"
            )
            raise ModelLoadError(message) from exc

        self._torch = torch
        self.device_backend = resolve_device_backend(device)
        self.device = resolve_torch_device(device)
        self.model.to(self.device)
        self.model.eval()
        self.projection_dim = int(self.model.config.text_config.projection_dim)
        self.model_source = source

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        """Encode text strings into normalized CLIP projection features."""
        if not texts:
            return np.zeros((0, self.projection_dim), dtype=np.float32)

        inputs = self.tokenizer(
            list(texts),
            return_tensors="pt",
            padding=True,
            truncation=True,
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self._torch.no_grad():
            features = self.model.get_text_features(**inputs)
            features = self._torch.nn.functional.normalize(features, p=2, dim=-1)
        return features.detach().cpu().numpy().astype(np.float32)
