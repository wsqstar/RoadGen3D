#!/usr/bin/env python3
"""Generate one parametric bench or lamp asset from a JSON request."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.parametric_assets import GenerationRequest, generate_parametric_asset


def _load_request_payload(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request JSON must be an object")
    return payload


def _infer_asset_id(payload: Dict[str, Any], request: GenerationRequest, result_meta: Dict[str, Any]) -> str:
    explicit = str(payload.get("asset_id", "")).strip()
    if explicit:
        return explicit
    style_tags = result_meta.get("style_tags", [])
    style_tag = str(style_tags[0]) if style_tags else "default"
    return f"{request.asset_kind}_{style_tag}_{request.runtime_profile}"


def _infer_text_desc(payload: Dict[str, Any], request: GenerationRequest, result_meta: Dict[str, Any]) -> str:
    explicit = str(payload.get("text_desc", "")).strip()
    if explicit:
        return explicit
    style_tags = result_meta.get("style_tags", [])
    style_tag = str(style_tags[0]) if style_tags else "default"
    material_family = str(result_meta.get("material_family", "")).strip()
    if material_family:
        return f"parametric {style_tag} {material_family} {request.asset_kind}"
    return f"parametric {style_tag} {request.asset_kind}"


def _write_placeholder_latent(latent_path: Path, mesh_path: Path) -> str:
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import torch
    except ImportError:
        latent_path.write_text(json.dumps({"mesh_path": str(mesh_path)}, ensure_ascii=True), encoding="utf-8")
        return "torch unavailable; wrote JSON placeholder instead of torch latent"
    torch.save({"mesh_path": str(mesh_path)}, latent_path)
    return ""


def _manifest_row(
    *,
    asset_id: str,
    text_desc: str,
    mesh_path: Path,
    latent_path: Path,
    result_meta: Dict[str, Any],
) -> Dict[str, Any]:
    bbox = result_meta["bbox"]["size_xyz"]
    return {
        "asset_id": asset_id,
        "category": result_meta["asset_kind"],
        "text_desc": text_desc,
        "mesh_path": str(mesh_path),
        "latent_path": str(latent_path),
        "source": "parametric_generated",
        "generator_type": result_meta["generator_type"],
        "runtime_profile": result_meta["runtime_profile"],
        "style_tags": list(result_meta["style_tags"]),
        "material_family": result_meta["material_family"],
        "parameter_snapshot": dict(result_meta["parameter_snapshot"]),
        "quality_metrics": dict(result_meta["quality_metrics"]),
        "frontage_width_m": float(bbox[0]),
        "depth_m": float(bbox[2]),
        "asset_role": "street_furniture",
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate one parametric bench or lamp asset.")
    parser.add_argument("--request-json", type=Path, required=True, help="GenerationRequest JSON payload")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output directory for mesh/result files")
    parser.add_argument("--manifest-out", type=Path, default=None, help="Optional JSONL manifest to append a row into")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    payload = _load_request_payload(args.request_json)
    request = GenerationRequest(
        asset_kind=str(payload.get("asset_kind", "")).strip().lower(),  # type: ignore[arg-type]
        runtime_profile=str(payload.get("runtime_profile", "preview")).strip().lower(),  # type: ignore[arg-type]
        device_backend=str(payload.get("device_backend", "auto")).strip().lower(),  # type: ignore[arg-type]
        seed=int(payload.get("seed", 42)),
        quality_profile=str(payload.get("quality_profile", "default_v1")),
        physics_profile=str(payload.get("physics_profile", "default_v1")),
        design_profile=str(payload.get("design_profile", "default_v1")),
        precision=str(payload.get("precision", "fp32")).strip().lower(),  # type: ignore[arg-type]
        allow_fallback=bool(payload.get("allow_fallback", True)),
        params=dict(payload.get("params", {})),
    )
    result = generate_parametric_asset(request)
    result_meta = result.to_metadata()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    asset_id = _infer_asset_id(payload, request, result_meta)
    text_desc = _infer_text_desc(payload, request, result_meta)
    mesh_path = out_dir / f"{asset_id}.glb"
    result_path = out_dir / f"{asset_id}.result.json"
    latent_path = out_dir / f"{asset_id}.pt"
    result.mesh.export(str(mesh_path))
    latent_warning = _write_placeholder_latent(latent_path, mesh_path)
    if latent_warning:
        result_meta.setdefault("warnings", []).append(latent_warning)
    result_meta["asset_id"] = asset_id
    result_meta["text_desc"] = text_desc
    result_meta["mesh_path"] = str(mesh_path)
    result_meta["latent_path"] = str(latent_path)
    result_path.write_text(json.dumps(result_meta, indent=2, ensure_ascii=True), encoding="utf-8")

    if args.manifest_out is not None:
        manifest_out = Path(args.manifest_out).resolve()
        manifest_out.parent.mkdir(parents=True, exist_ok=True)
        row = _manifest_row(
            asset_id=asset_id,
            text_desc=text_desc,
            mesh_path=mesh_path,
            latent_path=latent_path,
            result_meta=result_meta,
        )
        with manifest_out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    print(json.dumps(result_meta, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
