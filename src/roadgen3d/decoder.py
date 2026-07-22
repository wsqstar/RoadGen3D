"""Decoder interfaces and placeholder latent-to-voxel implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Protocol, Tuple

import numpy as np


@dataclass(frozen=True)
class DecoderConfig:
    resolution: int = 64
    threshold: float = 0.5


class DecoderProtocol(Protocol):
    """Common decoder contract used by the pipeline."""

    def decode(self, latent) -> Tuple[np.ndarray, np.ndarray, Dict[str, object]]:
        ...


class PlaceholderVoxelDecoder:
    """
    Deterministic lightweight decoder placeholder.

    This module is intentionally simple for milestone-1:
    it maps a latent vector to a `(R, R, R)` occupancy probability volume.
    """

    def __init__(self, resolution: int = 64, threshold: float = 0.5):
        if resolution <= 1:
            raise ValueError("resolution must be > 1")
        if not (0.0 < threshold < 1.0):
            raise ValueError("threshold must be in (0, 1)")
        self.resolution = int(resolution)
        self.threshold = float(threshold)

    def decode(self, latent) -> Tuple[np.ndarray, np.ndarray, Dict[str, object]]:
        latent_t = np.asarray(latent, dtype=np.float32).reshape(-1)
        needed = self.resolution * 3
        if latent_t.size < needed:
            latent_t = np.pad(latent_t, (0, needed - latent_t.size))

        x = latent_t[0 : self.resolution].reshape(self.resolution, 1, 1)
        y = latent_t[self.resolution : self.resolution * 2].reshape(1, self.resolution, 1)
        z = latent_t[self.resolution * 2 : self.resolution * 3].reshape(1, 1, self.resolution)
        bias = float(latent_t[self.resolution * 3 :].mean()) if latent_t.size > self.resolution * 3 else 0.0

        logits = (x + y + z) / 3.0 + bias
        prob = (1.0 / (1.0 + np.exp(-np.clip(logits, -80.0, 80.0)))).astype(np.float32)
        voxel = (prob > self.threshold).astype(np.uint8)
        meta: Dict[str, object] = {
            "decoder": "placeholder",
            "resolution": self.resolution,
            "threshold": self.threshold,
        }
        return prob, voxel, meta
