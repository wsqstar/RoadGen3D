#!/usr/bin/env python3
"""Seed a small mock asset + latent dataset for milestone-1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple


BASE_ASSETS: List[Tuple[str, str]] = [
    ("bench", "a wooden park bench"),
    ("lamp", "a tall metal street lamp"),
    ("trash", "a concrete trash can"),
    ("tree", "a broadleaf roadside tree"),
    ("bus_stop", "a simple urban bus stop shelter"),
    ("mailbox", "a red street mailbox"),
    ("hydrant", "a cast iron fire hydrant"),
    ("bollard", "a short safety bollard"),
    ("kiosk", "a small roadside information kiosk"),
    ("bike_rack", "a steel bicycle parking rack"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed mock assets and latent tensors.")
    parser.add_argument("--out-dir", type=Path, default=Path("data/m1"), help="Output directory.")
    parser.add_argument("--num-assets", type=int, default=8, help="Number of assets to seed.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for latent generation.")
    parser.add_argument("--latent-dim", type=int, default=256, help="Latent vector dimension.")
    return parser.parse_args()


def build_asset_rows(num_assets: int) -> List[Dict[str, str]]:
    if num_assets <= 0:
        raise ValueError("--num-assets must be >= 1")

    rows: List[Dict[str, str]] = []
    for i in range(num_assets):
        prefix, description = BASE_ASSETS[i % len(BASE_ASSETS)]
        asset_id = f"{prefix}_{i + 1:02d}"
        variant = i // len(BASE_ASSETS)
        if variant > 0:
            description = f"{description}, variant {variant}"
        rows.append(
            {
                "asset_id": asset_id,
                "description": description,
                "latent_path": f"latents/{asset_id}.pt",
            }
        )
    return rows


def seed_assets(out_dir: Path, num_assets: int, seed: int, latent_dim: int) -> List[Dict[str, str]]:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("`torch` is not installed. Install requirements-m1.txt first.") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    latents_dir = out_dir / "latents"
    latents_dir.mkdir(parents=True, exist_ok=True)

    assets = build_asset_rows(num_assets)
    generator = torch.Generator().manual_seed(seed)
    for row in assets:
        latent_path = out_dir / row["latent_path"]
        latent_path.parent.mkdir(parents=True, exist_ok=True)
        latent = torch.randn(1, latent_dim, generator=generator)
        torch.save(latent, latent_path)

    assets_path = out_dir / "assets.jsonl"
    with assets_path.open("w", encoding="utf-8") as handle:
        for row in assets:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    return assets


def main() -> int:
    args = parse_args()
    rows = seed_assets(
        out_dir=args.out_dir,
        num_assets=args.num_assets,
        seed=args.seed,
        latent_dim=args.latent_dim,
    )
    print(f"Seeded {len(rows)} assets under: {args.out_dir}")
    print(f"Asset metadata: {args.out_dir / 'assets.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

