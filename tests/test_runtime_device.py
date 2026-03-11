from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d import runtime_device


def _fake_torch(*, mps_available: bool, cuda_available: bool):
    return SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps_available)),
        cuda=SimpleNamespace(is_available=lambda: cuda_available),
        device=lambda backend: f"device:{backend}",
    )


def test_resolve_device_backend_auto_prefers_mps(monkeypatch):
    monkeypatch.setattr(runtime_device, "_load_torch", lambda: _fake_torch(mps_available=True, cuda_available=True))
    assert runtime_device.resolve_device_backend("auto") == "mps"


def test_resolve_device_backend_auto_falls_back_to_cuda_then_cpu(monkeypatch):
    monkeypatch.setattr(runtime_device, "_load_torch", lambda: _fake_torch(mps_available=False, cuda_available=True))
    assert runtime_device.resolve_device_backend("auto") == "cuda"

    monkeypatch.setattr(runtime_device, "_load_torch", lambda: _fake_torch(mps_available=False, cuda_available=False))
    assert runtime_device.resolve_device_backend("auto") == "cpu"


def test_resolve_device_backend_explicit_backend_can_fallback(monkeypatch):
    monkeypatch.setattr(runtime_device, "_load_torch", lambda: _fake_torch(mps_available=False, cuda_available=True))
    with pytest.warns(UserWarning, match="falling back to 'cuda'"):
        assert runtime_device.resolve_device_backend("mps", allow_fallback=True) == "cuda"


def test_resolve_device_backend_strict_mode_raises(monkeypatch):
    monkeypatch.setattr(runtime_device, "_load_torch", lambda: _fake_torch(mps_available=False, cuda_available=False))
    with pytest.raises(RuntimeError, match="Requested backend 'cuda' is not available"):
        runtime_device.resolve_device_backend("cuda", allow_fallback=False)


def test_resolve_torch_device_uses_resolved_backend(monkeypatch):
    monkeypatch.setattr(runtime_device, "_load_torch", lambda: _fake_torch(mps_available=False, cuda_available=True))
    assert runtime_device.resolve_torch_device("auto") == "device:cuda"
