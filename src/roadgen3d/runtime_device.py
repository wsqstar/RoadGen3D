"""Helpers for selecting a torch runtime device with fallback support."""

from __future__ import annotations

import warnings


def _load_torch():
    try:
        import torch
    except ImportError:
        return None
    return torch


def _mps_available(torch_module) -> bool:
    backends = getattr(torch_module, "backends", None)
    mps = getattr(backends, "mps", None)
    return bool(mps is not None and mps.is_available())


def _cuda_available(torch_module) -> bool:
    cuda = getattr(torch_module, "cuda", None)
    return bool(cuda is not None and cuda.is_available())


def _auto_backend(torch_module) -> str:
    if torch_module is None:
        return "cpu"
    if _mps_available(torch_module):
        return "mps"
    if _cuda_available(torch_module):
        return "cuda"
    return "cpu"


def resolve_device_backend(preferred: str = "auto", allow_fallback: bool = True) -> str:
    """Resolve a preferred backend into one of cpu/mps/cuda."""
    backend = str(preferred).strip().lower() or "auto"
    if backend not in {"auto", "cpu", "mps", "cuda"}:
        raise ValueError(f"Unsupported device backend: {preferred}")

    torch_module = _load_torch()
    if backend == "auto":
        return _auto_backend(torch_module)
    if backend == "cpu":
        return "cpu"

    available = backend == "mps" and torch_module is not None and _mps_available(torch_module)
    available = available or (backend == "cuda" and torch_module is not None and _cuda_available(torch_module))
    if available:
        return backend
    if not allow_fallback:
        raise RuntimeError(f"Requested backend '{backend}' is not available")

    resolved = _auto_backend(torch_module)
    if resolved != backend:
        warnings.warn(
            f"Requested backend '{backend}' is not available; falling back to '{resolved}'",
            stacklevel=2,
        )
    return resolved


def resolve_torch_device(preferred: str = "auto", allow_fallback: bool = True):
    """Resolve a preferred backend into `torch.device`."""
    torch_module = _load_torch()
    if torch_module is None:
        raise RuntimeError("torch is required to resolve a torch.device")
    backend = resolve_device_backend(preferred=preferred, allow_fallback=allow_fallback)
    return torch_module.device(backend)
