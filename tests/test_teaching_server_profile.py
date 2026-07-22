from __future__ import annotations

import pytest

from roadgen3d import street_layout
from roadgen3d.street_layout import resolve_asset_retrieval_mode


def test_asset_retrieval_mode_defaults_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROADGEN_ASSET_RETRIEVAL_MODE", raising=False)

    assert resolve_asset_retrieval_mode() == "auto"


def test_asset_retrieval_mode_can_disable_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROADGEN_ASSET_RETRIEVAL_MODE", "curated_rule_pool")

    assert resolve_asset_retrieval_mode() == "curated_rule_pool"


def test_asset_retrieval_mode_rejects_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROADGEN_ASSET_RETRIEVAL_MODE", "model_magic")

    with pytest.raises(ValueError, match="ROADGEN_ASSET_RETRIEVAL_MODE"):
        resolve_asset_retrieval_mode()


def test_manifest_absolute_asset_path_is_relocated_to_current_checkout(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout_root = tmp_path / "new-checkout"
    expected_mesh = checkout_root / "assets/building/mesh.glb"
    expected_mesh.parent.mkdir(parents=True)
    expected_mesh.write_bytes(b"glTF")
    monkeypatch.setattr(street_layout, "ROOT", checkout_root)

    resolved = street_layout._resolve_repo_portable_path(
        "/old/developer/RoadGen3D/assets/building/mesh.glb",
        tmp_path / "manifest-dir",
    )

    assert resolved == str(expected_mesh.resolve())
