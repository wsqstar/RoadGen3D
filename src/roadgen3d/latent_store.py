"""Asset metadata + latent loading utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .types import AssetRecord


def load_asset_records(assets_jsonl_path: Path) -> List[AssetRecord]:
    """Read line-delimited JSON asset records."""
    if not assets_jsonl_path.exists():
        raise FileNotFoundError(f"Asset metadata file not found: {assets_jsonl_path}")

    records: List[AssetRecord] = []
    for line_no, line in enumerate(assets_jsonl_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        try:
            records.append(
                AssetRecord(
                    asset_id=str(payload["asset_id"]),
                    description=str(payload["description"]),
                    latent_path=str(payload["latent_path"]),
                )
            )
        except KeyError as exc:
            raise ValueError(f"Invalid asset record at line {line_no} in {assets_jsonl_path}") from exc
    return records


class LatentStore:
    """Resolves and loads latent tensors by `asset_id`."""

    def __init__(self, assets_jsonl_path: Path):
        self.assets_jsonl_path = assets_jsonl_path
        self.assets_root = assets_jsonl_path.parent
        self.records = load_asset_records(assets_jsonl_path)
        self._by_id: Dict[str, AssetRecord] = {}
        for record in self.records:
            if record.asset_id in self._by_id:
                raise ValueError(f"Duplicate asset_id found: {record.asset_id}")
            self._by_id[record.asset_id] = record

    def get_record(self, asset_id: str) -> AssetRecord:
        if asset_id not in self._by_id:
            raise KeyError(f"Asset ID not found: {asset_id}")
        return self._by_id[asset_id]

    def latent_file_path(self, asset_id: str) -> Path:
        record = self.get_record(asset_id)
        return (self.assets_root / record.latent_path).resolve()

    def load(self, asset_id: str):
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("`torch` is not installed. Install requirements-m1.txt first.") from exc

        latent_path = self.latent_file_path(asset_id)
        if not latent_path.exists():
            raise FileNotFoundError(f"Latent file for asset '{asset_id}' not found at: {latent_path}")
        # Prefer safe tensor-only deserialization on newer PyTorch.
        try:
            latent = torch.load(latent_path, map_location="cpu", weights_only=True)
        except TypeError:
            latent = torch.load(latent_path, map_location="cpu")
        if isinstance(latent, dict) and "mesh_path" in latent:
            mesh_path = Path(str(latent["mesh_path"])).expanduser()
            if not mesh_path.is_absolute():
                mesh_path = (self.assets_root / mesh_path).resolve()
            if not mesh_path.exists():
                raise FileNotFoundError(f"Mesh reference for asset '{asset_id}' not found at: {mesh_path}")
            return {"mesh_path": str(mesh_path)}
        if not hasattr(latent, "shape"):
            raise TypeError(f"Latent for asset '{asset_id}' is not a tensor-like object: {latent_path}")
        return latent
