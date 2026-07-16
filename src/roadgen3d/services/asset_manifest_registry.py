"""Trusted asset-manifest catalog and immutable generation snapshots."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence
from uuid import uuid4

from ..street_priors import DEFAULT_CATEGORIES

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SNAPSHOT_ROOT = ROOT / "artifacts" / "web_viewer_asset_snapshots"


class AssetManifestConflictError(ValueError):
    """Raised when a client submits a stale manifest fingerprint."""


def _registered_manifests() -> Dict[str, Path]:
    roots = (
        ("", ROOT / "data" / "real"),
        ("street_furniture", ROOT / "data" / "street_furniture"),
        ("building", ROOT / "assets" / "building"),
    )
    result: Dict[str, Path] = {}
    for prefix, directory in roots:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.jsonl")):
            name = f"{prefix}/{path.name}" if prefix else path.name
            result[name] = path.resolve()
    return result


def resolve_registered_manifest(name: str) -> Path:
    clean = str(name or "").strip().replace("\\", "/")
    candidate = Path(clean)
    if not clean or candidate.is_absolute() or ".." in candidate.parts or candidate.suffix.lower() != ".jsonl":
        raise ValueError("Invalid asset manifest name.")
    path = _registered_manifests().get(clean)
    if path is None:
        raise ValueError(f"Unknown registered asset manifest: {clean}")
    return path


def _read_rows(path: Path) -> tuple[List[Dict[str, Any]], int]:
    rows: List[Dict[str, Any]] = []
    malformed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            malformed += 1
    return rows, malformed


def _is_enabled(row: Mapping[str, Any]) -> bool:
    value = row.get("scene_eligible")
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _mesh_exists(path: Path, mesh_value: Any) -> bool:
    raw = str(mesh_value or "").strip()
    if not raw:
        return False
    mesh = Path(raw).expanduser()
    if mesh.is_absolute():
        return mesh.is_file()
    return any(candidate.is_file() for candidate in ((path.parent / mesh).resolve(), (ROOT / mesh).resolve()))


def _runtime_eligible(row: Mapping[str, Any]) -> bool:
    if not _is_enabled(row):
        return False
    source = str(row.get("source") or "").strip().lower()
    generator_type = str(row.get("generator_type") or "").strip().lower()
    try:
        quality_tier = int(row.get("quality_tier", 1) or 1)
    except (TypeError, ValueError):
        quality_tier = 1
    return (
        "real_asset" not in source
        and "urbanverse_import" not in source
        and "_v2" not in generator_type
        and "-v2" not in generator_type
        and quality_tier >= 1
    )


def _globally_disabled_asset_ids() -> set[str]:
    registered = _registered_manifests()
    disabled: set[str] = set()
    for name in (
        "real_assets_manifest.jsonl",
        "real_assets_manifest_v2.jsonl",
        "street_furniture/street_furniture_manifest.jsonl",
    ):
        path = registered.get(name)
        if path is None:
            continue
        rows, _ = _read_rows(path)
        disabled.update(
            str(row.get("asset_id") or "").strip()
            for row in rows
            if str(row.get("asset_id") or "").strip() and not _is_enabled(row)
        )
    return disabled


def _ready_asset_ids(path: Path, rows: Sequence[Mapping[str, Any]] | None = None) -> set[str]:
    source_rows = list(rows) if rows is not None else _read_rows(path)[0]
    globally_disabled = _globally_disabled_asset_ids()
    return {
        str(row.get("asset_id") or "").strip()
        for row in source_rows
        if str(row.get("asset_id") or "").strip()
        and str(row.get("category") or "").strip().lower() in DEFAULT_CATEGORIES
        and _runtime_eligible(row)
        and str(row.get("asset_id") or "").strip() not in globally_disabled
        and _mesh_exists(path, row.get("mesh_path"))
    }


def _resolved_resource_value(manifest_path: Path, value: Any) -> Any:
    raw = str(value or "").strip()
    if not raw:
        return value
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path.resolve())
    candidates = ((manifest_path.parent / path).resolve(), (ROOT / path).resolve())
    resolved = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    return str(resolved)


def _write_snapshot_manifest(source: Path, destination: Path) -> None:
    rows, malformed = _read_rows(source)
    if malformed:
        raise ValueError(f"Registered asset manifest contains malformed rows: {source.name}")
    path_fields = ("mesh_path", "latent_path", "parent_mesh_path", "split_output_dir", "preview_path")
    with destination.open("w", encoding="utf-8") as handle:
        for source_row in rows:
            row = dict(source_row)
            for field in path_fields:
                if row.get(field) not in (None, ""):
                    row[field] = _resolved_resource_value(source, row[field])
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def summarize_manifest(name: str, path: Path | None = None) -> Dict[str, Any]:
    resolved = path or resolve_registered_manifest(name)
    rows, malformed = _read_rows(resolved)
    eligible_rows = [row for row in rows if _is_enabled(row)]
    category_counts = Counter(str(row.get("category") or "unknown").strip().lower() for row in eligible_rows)
    ready_asset_ids = _ready_asset_ids(resolved, rows)
    missing_mesh = sum(1 for row in eligible_rows if not _mesh_exists(resolved, row.get("mesh_path")))
    unsupported = sum(
        1 for row in eligible_rows
        if str(row.get("category") or "").strip().lower() not in DEFAULT_CATEGORIES
    )
    ids = [str(row.get("asset_id") or "").strip() for row in rows]
    duplicate_ids = len([item for item, count in Counter(item for item in ids if item).items() if count > 1])
    warnings: List[str] = []
    if malformed:
        warnings.append(f"{malformed} malformed row(s)")
    if missing_mesh:
        warnings.append(f"{missing_mesh} eligible asset(s) have no readable mesh")
    if unsupported:
        warnings.append(f"{unsupported} eligible asset(s) use unsupported categories")
    if duplicate_ids:
        warnings.append(f"{duplicate_ids} duplicate asset id(s)")
    stat = resolved.stat()
    return {
        "name": name,
        "label": name.replace("_", " ").replace(".jsonl", "").title(),
        "count": len(rows),
        "eligibleCount": len(eligible_rows),
        "readyCount": len(ready_asset_ids),
        "categoryCounts": dict(sorted(category_counts.items())),
        "fingerprint": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "updatedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "warnings": warnings,
    }


def list_manifest_summaries() -> List[Dict[str, Any]]:
    return [summarize_manifest(name, path) for name, path in _registered_manifests().items()]


def read_manifest_page(
    name: str,
    *,
    offset: int = 0,
    limit: int = 100,
    eligibility: str = "all",
) -> Dict[str, Any]:
    path = resolve_registered_manifest(name)
    rows, _ = _read_rows(path)
    if eligibility == "eligible":
        rows = [row for row in rows if _is_enabled(row)]
    elif eligibility == "disabled":
        rows = [row for row in rows if not _is_enabled(row)]
    elif eligibility != "all":
        raise ValueError("eligibility must be eligible, disabled, or all")
    start = max(0, int(offset))
    page_size = max(1, min(500, int(limit)))
    selected = rows[start:start + page_size]
    return {
        "assets": selected,
        "total": len(rows),
        "offset": start,
        "limit": page_size,
        "hasMore": start + page_size < len(rows),
        "manifest": summarize_manifest(name, path),
    }


def freeze_candidate_manifests(
    candidates: Sequence[Mapping[str, Any]],
    *,
    snapshot_root: Path | None = None,
) -> Dict[str, Any]:
    """Validate ordered candidate names and copy their exact bytes for one job."""

    if not candidates:
        return {}
    snapshot_id = uuid4().hex
    target_root = (snapshot_root or DEFAULT_SNAPSHOT_ROOT).resolve() / snapshot_id
    target_root.mkdir(parents=True, exist_ok=False)
    manifest_paths: List[str] = []
    manifest_names: List[str] = []
    provenance: List[Dict[str, Any]] = []
    candidate_asset_ids: set[str] = set()
    try:
        for priority, item in enumerate(candidates):
            name = str(item.get("name") or "").strip()
            source = resolve_registered_manifest(name)
            summary = summarize_manifest(name, source)
            expected = str(item.get("expected_fingerprint") or "").strip()
            if not expected:
                raise ValueError(f"expected_fingerprint is required for candidate asset manifest: {name}")
            if expected != summary["fingerprint"]:
                raise AssetManifestConflictError(
                    f"Asset manifest changed since it was reviewed: {name}. Refresh 01B and confirm again."
                )
            snapshot_path = target_root / f"{priority:03d}_{source.name}"
            _write_snapshot_manifest(source, snapshot_path)
            manifest_paths.append(str(snapshot_path))
            manifest_names.append(name)
            provenance.append({
                "name": name,
                "fingerprint": summary["fingerprint"],
                "eligible_count": summary["eligibleCount"],
                "ready_count": summary["readyCount"],
                "priority": priority,
            })
            candidate_asset_ids.update(_ready_asset_ids(source))
    except Exception:
        shutil.rmtree(target_root, ignore_errors=True)
        raise
    return {
        "manifest_paths": manifest_paths,
        "manifest_names": manifest_names,
        "candidate_asset_manifests": provenance,
        "candidate_asset_count": len(candidate_asset_ids),
        "candidate_asset_manifest_snapshot_id": snapshot_id,
    }
