#!/usr/bin/env python3
"""Publish or download RoadGen3D street-furniture assets on Hugging Face."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, snapshot_download


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "street_furniture"
MANIFEST_PATH = DATA_DIR / "street_furniture_manifest.jsonl"
UPLOAD_DIRS = ("assets_std_glb_flat", "assets_split")

DATASET_CARD = """---
license: odc-by
pretty_name: RoadGen3D Street Furniture Assets
---

# RoadGen3D Street Furniture Assets

Street-furniture meshes used by RoadGen3D. The manifest preserves asset IDs,
categories, eligibility flags, source metadata, and relative mesh paths.

The assets are derived from UrbanVerse-100K and split-mesh projections recorded
in the manifest. They are distributed under ODC-BY 1.0. Users must preserve
the manifest attribution and comply with the upstream UrbanVerse terms.
"""


def _repo_id(value: str | None) -> str:
    resolved = str(value or os.getenv("ROADGEN_STREET_FURNITURE_HF_REPO") or "").strip()
    if not resolved:
        raise SystemExit(
            "Set --repo-id or ROADGEN_STREET_FURNITURE_HF_REPO "
            "(for example: username/roadgen3d-street-furniture)."
        )
    return resolved


def _rows() -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise SystemExit(f"Manifest is empty: {MANIFEST_PATH}")
    absolute = [str(row.get("mesh_path") or "") for row in rows if Path(str(row.get("mesh_path") or "")).is_absolute()]
    if absolute:
        raise SystemExit(f"Manifest still contains {len(absolute)} absolute mesh paths.")
    unsupported = sorted({str(row.get("license") or "") for row in rows} - {"ODC-BY 1.0"})
    if unsupported:
        raise SystemExit(f"Unexpected asset licenses: {unsupported}")
    return rows


def _link_or_copy(source: str, target: str) -> str:
    try:
        os.link(source, target)
        return target
    except OSError:
        return shutil.copy2(source, target)


def upload(repo_id: str, *, private: bool) -> None:
    rows = _rows()
    api = HfApi()
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="roadgen3d-hf-assets-") as temporary:
        staging = Path(temporary)
        shutil.copy2(MANIFEST_PATH, staging / MANIFEST_PATH.name)
        (staging / "README.md").write_text(DATASET_CARD, encoding="utf-8")
        for directory_name in UPLOAD_DIRS:
            source = DATA_DIR / directory_name
            if not source.is_dir():
                raise SystemExit(f"Required asset directory is missing: {source}")
            shutil.copytree(source, staging / directory_name, copy_function=_link_or_copy)
        api.upload_large_folder(repo_id=repo_id, repo_type="dataset", folder_path=staging)
    print(f"Uploaded {len(rows)} manifest rows to https://huggingface.co/datasets/{repo_id}")


def download(repo_id: str) -> None:
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        local_dir=DATA_DIR,
        allow_patterns=[
            "street_furniture_manifest.jsonl",
            "assets_std_glb_flat/**",
            "assets_split/**",
            "README.md",
        ],
    )
    _rows()
    print(f"Street-furniture assets are ready in {DATA_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("upload", "download"))
    parser.add_argument("--repo-id")
    parser.add_argument("--private", action="store_true", help="Create a private dataset when uploading.")
    args = parser.parse_args()
    repo_id = _repo_id(args.repo_id)
    if args.action == "upload":
        upload(repo_id, private=bool(args.private))
    else:
        download(repo_id)


if __name__ == "__main__":
    main()
