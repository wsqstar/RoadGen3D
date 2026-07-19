"""Asset manifest editing API routes."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from roadgen3d.json_safe import make_json_safe
from roadgen3d.services.asset_manifest_registry import (
    AssetManifestConflictError,
    AssetReferenceError,
    list_manifest_summaries,
    read_manifest_page,
    resolve_registered_asset,
    search_registered_assets,
)
from web.api.schemas import AssetManifestSplitRequestModel

ROOT = Path(__file__).resolve().parents[3]

router = APIRouter(prefix="/api/asset-manifest", tags=["assets"])
catalog_router = APIRouter(tags=["assets"])


@catalog_router.get("/api/asset-manifests")
def list_asset_manifests() -> Dict[str, Any]:
    return make_json_safe({"manifests": list_manifest_summaries()})


@catalog_router.get("/api/asset-catalog/search")
def search_asset_catalog(
    q: str = Query(default="", max_length=160),
    manifest: List[str] | None = Query(default=None),
    category: str = Query(default="", max_length=80),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, Any]:
    try:
        return make_json_safe(search_registered_assets(
            query=q,
            manifest_names=manifest,
            category=category,
            offset=offset,
            limit=limit,
        ))
    except ValueError as exc:
        message = str(exc)
        status = 404 if message.startswith("Unknown registered") else 400
        raise HTTPException(status_code=status, detail=message) from exc


@catalog_router.get("/api/asset-catalog/model")
def read_asset_catalog_model(
    manifest_name: str = Query(min_length=1),
    asset_id: str = Query(min_length=1),
    fingerprint: str = Query(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
) -> FileResponse:
    try:
        resolved = resolve_registered_asset(
            manifest_name,
            asset_id,
            expected_fingerprint=fingerprint,
            require_ready=True,
        )
    except AssetManifestConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (AssetReferenceError, ValueError) as exc:
        message = str(exc)
        status = 404 if message.startswith(("Unknown asset", "Unknown registered")) else 422
        raise HTTPException(status_code=status, detail=message) from exc
    mesh_path = Path(str(resolved["row"].get("mesh_path") or "")).resolve()
    if not mesh_path.is_file():
        raise HTTPException(status_code=404, detail="Registered asset model is unavailable.")
    return FileResponse(
        mesh_path,
        media_type="model/gltf-binary",
        filename=f"{asset_id}.glb",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.get("")
def get_asset_manifest(
    name: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    eligibility: str = Query(default="all", pattern="^(eligible|disabled|all)$"),
) -> Dict[str, Any]:
    try:
        return make_json_safe(read_manifest_page(name, offset=offset, limit=limit, eligibility=eligibility))
    except ValueError as exc:
        message = str(exc)
        status = 404 if message.startswith("Unknown registered") else 400
        raise HTTPException(status_code=status, detail=message) from exc


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_slug(value: str, fallback: str = "asset") -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value.strip())
    return safe.strip("._") or fallback


def _resolve_manifest_path(manifest_name: str) -> Path:
    manifest_ref = Path(manifest_name)
    if manifest_ref.is_absolute() or ".." in manifest_ref.parts:
        raise HTTPException(status_code=400, detail="Invalid manifest name")

    data_root = (ROOT / "data").resolve()
    candidates: List[Path] = []
    if len(manifest_ref.parts) > 1:
        candidates.append((data_root / manifest_ref).resolve())
    else:
        candidates.append((data_root / "real" / manifest_ref).resolve())
        candidates.append((data_root / manifest_ref).resolve())

    for candidate in candidates:
        if _is_relative_to(candidate, data_root) and candidate.exists():
            return candidate

    candidate = candidates[0]
    if not _is_relative_to(candidate, data_root):
        raise HTTPException(status_code=400, detail="Manifest path escapes data directory")
    return candidate


def _read_manifest_rows(manifest_path: Path) -> List[Dict[str, Any]]:
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"Manifest not found: {manifest_path}")

    rows: List[Dict[str, Any]] = []
    for line_number, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Invalid JSON in {manifest_path.name} at line {line_number}: {exc}",
            ) from exc
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _resolve_mesh_path(mesh_path: Any) -> Path:
    if not isinstance(mesh_path, str) or not mesh_path.strip():
        raise HTTPException(status_code=400, detail="Selected asset has no mesh_path")

    path = Path(mesh_path).expanduser()
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"Mesh file not found: {resolved}")
    return resolved


def _unique_asset_id(base_id: str, existing_ids: set[str]) -> str:
    candidate = base_id
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base_id}-{suffix}"
        suffix += 1
    existing_ids.add(candidate)
    return candidate


def _list_field(row: Dict[str, Any], key: str) -> List[str]:
    value = row.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _append_unique(values: List[str], *extra_values: str) -> List[str]:
    seen = set(values)
    result = list(values)
    for value in extra_values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _split_failure(status_code: int, message: str, output: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"message": message, "output": output[-12000:]})


def _write_placeholder_latent(latent_path: Path, asset_id: str, parent_asset_id: str, method: str) -> None:
    payload = {
        "placeholder": True,
        "asset_id": asset_id,
        "parent_asset_id": parent_asset_id,
        "created_by": f"asset_splitter_{method}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    latent_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _build_child_record(
    parent: Dict[str, Any],
    parent_mesh_path: Path,
    output_dir: Path,
    glb_path: Path,
    cluster: Dict[str, Any],
    index: int,
    existing_ids: set[str],
    method: str,
) -> Dict[str, Any]:
    parent_asset_id = str(parent.get("asset_id") or "asset")
    asset_id = _unique_asset_id(f"{parent_asset_id}-split-{index:03d}", existing_ids)
    latent_path = output_dir / f"{asset_id}.pt"
    face_count = int(cluster.get("face_count") or 0)

    tags = _append_unique(_list_field(parent, "tags"), "split_asset", f"split_method:{method}", f"split_parent:{parent_asset_id}")
    notes = _append_unique(
        _list_field(parent, "quality_notes"), f"split_from={parent_asset_id}",
        f"split_method={method}", f"split_index={index:03d}", f"mesh_face_count={face_count}",
    )
    text_desc = str(parent.get("text_desc") or parent.get("description") or parent_asset_id)

    _write_placeholder_latent(latent_path, asset_id, parent_asset_id, method)

    record: Dict[str, Any] = {
        "asset_id": asset_id,
        "category": parent.get("category") or "traffic_sign",
        "mesh_path": str(glb_path.resolve()),
        "source": f"asset_splitter_{method}",
        "license": parent.get("license") or "derived_from_parent_asset",
        "quality_tier": parent.get("quality_tier", 3),
        "scene_eligible": parent.get("scene_eligible", True),
        "tags": tags,
        "text_desc": f"{text_desc} split component {index:03d}",
        "latent_path": str(latent_path.resolve()),
        "latent_source": "mesh_reference",
        "split": parent.get("split") or "train",
        "mesh_face_count": face_count,
        "parent_asset_id": parent_asset_id,
        "parent_mesh_path": str(parent_mesh_path),
        "asset_composition_type": "split_component",
        "split_method": method,
        "split_index": index,
        "split_output_dir": str(output_dir.resolve()),
        "quality_notes": notes,
    }

    for key in ("subcategory", "size_class", "scale_hint", "source_dataset"):
        if key in parent:
            record[key] = parent[key]
    return record


@router.post("/split-selected")
def split_selected_asset(request_body: AssetManifestSplitRequestModel) -> Dict[str, Any]:
    allowed_methods = {"auto", "primitive", "projection", "loose-3d"}
    if request_body.method not in allowed_methods:
        raise HTTPException(status_code=400, detail=f"Unsupported split method: {request_body.method}")

    manifest_path = _resolve_manifest_path(request_body.manifest_name)
    rows = _read_manifest_rows(manifest_path)
    parent = next((row for row in rows if row.get("asset_id") == request_body.asset_id), None)
    if parent is None:
        raise HTTPException(status_code=404, detail=f"Asset not found in manifest: {request_body.asset_id}")

    parent_mesh_path = _resolve_mesh_path(parent.get("mesh_path"))
    splitter_script = ROOT / "scripts" / "split_glb_signs.py"
    if not splitter_script.exists():
        raise HTTPException(status_code=500, detail=f"Splitter script not found: {splitter_script}")

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parent_slug = _safe_slug(request_body.asset_id)
    output_base = manifest_path.parent / "assets_split" / parent_slug
    output_method = "auto_or_primitive" if request_body.method == "auto" else request_body.method
    output_dir = output_base / f"{output_method}_{run_stamp}"
    suffix = 2
    while output_dir.exists():
        output_dir = output_base / f"{output_method}_{run_stamp}_{suffix}"
        suffix += 1

    cmd = [
        sys.executable, str(splitter_script),
        "--method", request_body.method,
        "--input", str(parent_mesh_path),
        "--output-dir", str(output_dir),
        "--projection-margin", str(request_body.projection_margin),
        "--write-preview",
    ]
    try:
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30 * 60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        raise _split_failure(504, "Asset split timed out", str(output)) from exc

    script_output = completed.stdout or ""
    if completed.returncode != 0:
        raise _split_failure(500, f"Asset split failed with exit code {completed.returncode}", script_output)

    report_path = output_dir / "clusters_split.json"
    if not report_path.exists():
        report_path = output_dir / "clusters_projection.json"
    if not report_path.exists():
        raise _split_failure(500, "Split finished without clusters_split.json or clusters_projection.json", script_output)

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid split report: {exc}") from exc

    clusters = report.get("clusters")
    if not isinstance(clusters, list):
        raise HTTPException(status_code=500, detail="Split report does not contain a clusters list")
    actual_method = str(report.get("actual_method") or report.get("method") or request_body.method)
    fallback_reason = report.get("fallback_reason")

    glb_files = sorted(output_dir.glob("sign_*.glb"))
    if len(glb_files) != len(clusters):
        raise _split_failure(500, f"Split output mismatch: {len(glb_files)} GLB files for {len(clusters)} clusters", script_output)

    existing_ids = {str(row.get("asset_id")) for row in rows if row.get("asset_id")}
    created_assets: List[Dict[str, Any]] = []
    for index, (glb_path, cluster) in enumerate(zip(glb_files, clusters), start=1):
        if not isinstance(cluster, dict):
            cluster = {}
        child = _build_child_record(
            parent=parent, parent_mesh_path=parent_mesh_path, output_dir=output_dir, glb_path=glb_path,
            cluster=cluster, index=index, existing_ids=existing_ids, method=actual_method,
        )
        created_assets.append(child)

    with manifest_path.open("a", encoding="utf-8") as handle:
        for child in created_assets:
            handle.write(json.dumps(make_json_safe(child), ensure_ascii=True) + "\n")

    return make_json_safe({
        "ok": True,
        "manifest_name": request_body.manifest_name,
        "asset_id": request_body.asset_id,
        "requested_method": request_body.method,
        "method": actual_method,
        "actual_method": actual_method,
        "fallback_reason": fallback_reason,
        "output_dir": str(output_dir.resolve()),
        "cluster_count": len(clusters),
        "created_count": len(created_assets),
        "total_face_count": sum(int(cluster.get("face_count") or 0) for cluster in clusters if isinstance(cluster, dict)),
        "assets": created_assets,
        "report_path": str(report_path.resolve()),
        "preview_paths": {
            "top": str((output_dir / "projection_top.svg").resolve()),
            "front": str((output_dir / "projection_front.svg").resolve()),
        },
        "script_output_tail": script_output[-12000:],
    })
