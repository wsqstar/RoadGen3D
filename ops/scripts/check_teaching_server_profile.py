#!/usr/bin/env python3
"""Fail fast when a server is not using the lightweight teaching profile."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BANNED_MODULES = ("torch", "transformers")
REQUIRED_MANIFESTS = (
    ROOT / "data/street_furniture/street_furniture_manifest.jsonl",
    ROOT / "data/materials/ground_material_manifest.jsonl",
    ROOT / "data/materials/sky_manifest.jsonl",
    ROOT / "assets/building/buildings_manifest.jsonl",
)


def _nonempty_jsonl_rows(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            count += 1
    return count


def _resolve_checkout_path(raw_path: object, manifest_path: Path) -> Path | None:
    path = Path(str(raw_path or "")).expanduser()
    candidate = path if path.is_absolute() else manifest_path.parent / path
    if candidate.is_file():
        return candidate.resolve()
    for anchor in ("assets", "data"):
        if anchor in path.parts:
            checkout_candidate = ROOT / Path(*path.parts[path.parts.index(anchor) :])
            if checkout_candidate.is_file():
                return checkout_candidate.resolve()
    return None


def _resolvable_mesh_rows(path: Path) -> int:
    count = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        row = json.loads(raw_line)
        if _resolve_checkout_path(row.get("mesh_path"), path) is not None:
            count += 1
    return count


def main() -> int:
    errors: list[str] = []
    for module_name in BANNED_MODULES:
        if importlib.util.find_spec(module_name) is not None:
            errors.append(f"banned module is installed: {module_name}")

    mode = os.getenv("ROADGEN_ASSET_RETRIEVAL_MODE", "").strip().lower()
    if mode != "curated_rule_pool":
        errors.append("ROADGEN_ASSET_RETRIEVAL_MODE must equal curated_rule_pool")

    manifest_counts: dict[str, int] = {}
    mesh_counts: dict[str, int] = {}
    for manifest_path in REQUIRED_MANIFESTS:
        if not manifest_path.is_file():
            errors.append(f"required curated manifest is missing: {manifest_path}")
            continue
        row_count = _nonempty_jsonl_rows(manifest_path)
        manifest_counts[str(manifest_path.relative_to(ROOT))] = row_count
        if row_count == 0:
            errors.append(f"required curated manifest is empty: {manifest_path}")
        if manifest_path.name in {"street_furniture_manifest.jsonl", "buildings_manifest.jsonl"}:
            mesh_count = _resolvable_mesh_rows(manifest_path)
            mesh_counts[str(manifest_path.relative_to(ROOT))] = mesh_count
            if mesh_count == 0:
                errors.append(f"manifest has no resolvable mesh files: {manifest_path}")

    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "program_generator": "heuristic_v1",
                "placement_policy": "rule",
                "asset_retrieval_mode": mode,
                "curated_manifest_rows": manifest_counts,
                "resolvable_mesh_rows": mesh_counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
