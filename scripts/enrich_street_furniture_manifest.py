#!/usr/bin/env python3
"""Enrich street_furniture_manifest.jsonl for pipeline use.

Adds: text_desc, latent_path, normalized category, split, mesh_face_count,
quality_notes, latent_source.  Creates placeholder latent .pt files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import trimesh
except ImportError:
    trimesh = None  # type: ignore[assignment]

try:
    import torch
except ImportError:
    torch = None  # type: ignore[assignment]


# ── category normalisation ──────────────────────────────────────────

CATEGORY_MAP: dict[str, str] = {
    "bench": "bench",
    "lamp": "lamp",
    "lamppost": "lamp",
    "lantern": "lamp",
    "trash bin": "trash",
    "tree": "tree",
    "bus shelter": "bus_stop",
    "mailbox": "mailbox",
    "fire hydrant": "hydrant",
    "bollard": "bollard",
    "traffic sign": "traffic_sign",
    "potted plant": "tree",
    "picnic table": "bench",
    "stone block": "bollard",
    "guard stone": "bollard",
    "obelisk": "bollard",
}


# ── helpers ─────────────────────────────────────────────────────────

def _build_asset_annotation_map(annotation_data: dict) -> dict[str, dict]:
    """Return {asset_id: {l1, l2, l3}} from the annotation section."""
    out: dict[str, dict] = {}
    for l3_name, info in annotation_data.items():
        l1 = info.get("class_name_l1", "")
        l2 = info.get("class_name_l2", "")
        for uid in info.get("asset_uids", []):
            out[uid] = {"l1": l1, "l2": l2, "l3": l3_name}
    return out


def _mesh_face_count(mesh_path: Path) -> int | None:
    if trimesh is None:
        return None
    try:
        scene = trimesh.load(str(mesh_path), force="scene")
        total = 0
        for geom in scene.geometry.values():
            total += len(geom.faces)
        return total
    except Exception:
        return None


def _write_placeholder_latent(latent_path: Path, mesh_path: Path) -> None:
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mesh_path": str(mesh_path)}
    if torch is not None:
        torch.save(payload, latent_path)
    else:
        latent_path.write_text(
            json.dumps(payload, ensure_ascii=True), encoding="utf-8"
        )


# ── main ────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--input",
        default="data/street_furniture/street_furniture_manifest.jsonl",
        help="Input JSONL manifest",
    )
    ap.add_argument(
        "--output",
        default=None,
        help="Output JSONL (default: overwrite input)",
    )
    ap.add_argument(
        "--latents-dir",
        default="data/street_furniture/latents",
        help="Directory for placeholder latent .pt files",
    )
    ap.add_argument(
        "--annotation",
        default="data/street_furniture/urbanverse_master_annotation.json",
        help="Master annotation JSON file",
    )
    args = ap.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else input_path
    latents_dir = Path(args.latents_dir)
    annotation_path = Path(args.annotation)

    # Load annotation index
    asset_ann: dict[str, dict] = {}
    if annotation_path.exists():
        with open(annotation_path, encoding="utf-8") as f:
            ann_data = json.load(f)
        asset_ann = _build_asset_annotation_map(ann_data.get("annotation", {}))
        print(f"Loaded {len(asset_ann)} asset annotations")

    # Process manifest
    latents_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with open(input_path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)

            asset_id = row["asset_id"]
            raw_cat = row["category"]
            mesh_path = Path(row["mesh_path"])
            tags = row.get("tags", [])

            # 1. normalise category
            normalized = CATEGORY_MAP.get(raw_cat)
            if normalized:
                row["category"] = normalized
            else:
                row["category"] = raw_cat.lower()

            # 2. text_desc
            ann = asset_ann.get(asset_id)
            if ann:
                row["text_desc"] = (
                    f"{ann['l3']} ({ann['l2']}, {ann['l1']}). "
                    f"tags: {', '.join(tags)}"
                )
            else:
                row["text_desc"] = (
                    f"a {raw_cat} street furniture item. "
                    f"tags: {', '.join(tags)}"
                )

            # 3. placeholder latent
            latent_path = latents_dir / f"{asset_id}.pt"
            row["latent_path"] = str(latent_path)
            if not latent_path.exists():
                _write_placeholder_latent(latent_path, mesh_path)

            # 4. extra fields
            row["split"] = "train"
            row["latent_source"] = "mesh_reference"

            face_count = _mesh_face_count(mesh_path)
            if face_count is not None:
                row["mesh_face_count"] = face_count
            else:
                row["mesh_face_count"] = -1

            quality_notes = [f"mesh_face_count={row['mesh_face_count']}"]
            quality_notes.append(f"quality_tier={row.get('quality_tier', 'unknown')}")
            if row.get("scene_eligible"):
                quality_notes.append("scene_ready")
            quality_notes.append("source=urbanverse")
            row["quality_notes"] = quality_notes

            rows.append(row)

    # Write output
    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Wrote {len(rows)} enriched entries to {output_path}")
    print(f"Latent files dir: {latents_dir}")


if __name__ == "__main__":
    main()
