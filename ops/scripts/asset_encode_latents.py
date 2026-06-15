#!/usr/bin/env python3
"""Encode real mesh assets into Shape-E latents (with optional fallback)."""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _guess_mesh_path(asset_id: str, mesh_root: Optional[Path]) -> Optional[Path]:
    if mesh_root is None:
        return None
    root = mesh_root.expanduser().resolve()
    candidates = [
        root / f"{asset_id}.glb",
        root / f"{asset_id}.obj",
        root / f"{asset_id}.ply",
        root / f"{asset_id}.stl",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def load_manifest(path: Path, mesh_root: Optional[Path] = None) -> List[Dict[str, object]]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    rows: List[Dict[str, object]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if "asset_id" not in payload:
            raise ValueError(f"missing 'asset_id' in line {line_no} ({path})")
        asset_id = str(payload["asset_id"])
        if "mesh_path" not in payload:
            guessed = _guess_mesh_path(asset_id=asset_id, mesh_root=mesh_root)
            if guessed is None:
                hint = f" under mesh root {mesh_root}" if mesh_root is not None else ""
                raise ValueError(
                    f"missing 'mesh_path' in line {line_no} ({path}) and could not auto-resolve asset '{asset_id}'{hint}. "
                    "Add mesh_path explicitly or place mesh file as <asset_id>.glb/.obj/.ply/.stl."
                )
            payload["mesh_path"] = str(guessed)
        rows.append(payload)
    return rows


def _ensure_local_shapee_cache(model_dir: Optional[Path]) -> None:
    if model_dir is None:
        raise FileNotFoundError("shapee local-only mode requires --shapee-model-dir.")
    required = ("transmitter.pt", "transmitter_config.yaml")
    missing = [name for name in required if not (model_dir / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"Shape-E local cache incomplete at {model_dir}. Missing: {', '.join(missing)}"
        )


def _load_shapee_transmitter(
    device: str,
    shapee_model_dir: Optional[Path],
    shapee_local_only: bool,
):
    try:
        import torch
        from shap_e.models.download import load_model
    except Exception as exc:
        raise RuntimeError("Shape-E runtime unavailable in current environment.") from exc

    if shapee_local_only:
        _ensure_local_shapee_cache(shapee_model_dir)

    load_kwargs = {}
    if shapee_model_dir is not None:
        shapee_model_dir = shapee_model_dir.expanduser().resolve()
        shapee_model_dir.mkdir(parents=True, exist_ok=True)
        load_kwargs["cache_dir"] = str(shapee_model_dir)

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"`torch\.cuda\.amp\.custom_(fwd|bwd)\(args\.\.\.\)` is deprecated\.",
            category=FutureWarning,
        )
        xm = load_model("transmitter", device=torch.device(device), **load_kwargs)
    return torch, xm


def _encode_one_with_shapee(
    mesh_path: Path,
    latent_path: Path,
    xm,
    torch,
    device: str,
    render_cache_dir: Optional[Path],
    verbose: bool,
) -> List[int]:
    from shap_e.util import data_util as shapee_data_util

    @contextmanager
    def _force_blender_backend(backend_name: str):
        original_render_model = shapee_data_util.render_model

        def _render_model_compat(*args, **kwargs):
            kwargs["backend"] = backend_name
            # Blender 4.5+ changed Principled BSDF sockets and breaks Shape-E's
            # legacy material extraction path ("Emission" socket assertion).
            # Use lighting-based rendering instead of material extraction.
            kwargs["extract_material"] = False
            kwargs.setdefault("light_mode", "uniform")
            return original_render_model(*args, **kwargs)

        shapee_data_util.render_model = _render_model_compat
        try:
            yield
        finally:
            shapee_data_util.render_model = original_render_model

    cache_dir = None
    if render_cache_dir is not None:
        render_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = str(render_cache_dir.resolve())

    def _create_batch(force_backend: Optional[str] = None):
        if force_backend:
            with _force_blender_backend(force_backend):
                return shapee_data_util.load_or_create_multimodal_batch(
                    torch.device(device),
                    model_path=str(mesh_path),
                    cache_dir=cache_dir,
                    verbose=bool(verbose),
                )
        with _force_blender_backend("BLENDER_EEVEE"):
            return shapee_data_util.load_or_create_multimodal_batch(
                torch.device(device),
                model_path=str(mesh_path),
                cache_dir=cache_dir,
                verbose=bool(verbose),
            )

    def _raise_mapped_blender_error(exc: Exception) -> None:
        text = str(exc).lower()
        if "set the environment variable `blender_path`" in text or "command not found: blender" in text:
            raise RuntimeError(
                "Shape-E mesh encoding requires Blender 3.3+ executable. "
                "Install Blender and set BLENDER_PATH to /Applications/Blender.app/Contents/MacOS/Blender "
                "(or ensure blender is callable)."
            ) from exc
        if "sigsegv" in text or "died with <signals.sigsegv" in text or "blender.crash.txt" in text:
            raise RuntimeError(
                "Blender crashed during Shape-E rendering (SIGSEGV). "
                "Current Blender build appears incompatible with Shape-E. "
                "Use Blender 3.6 LTS/4.x stable and set BLENDER_PATH accordingly."
            ) from exc
        if "emission not in" in text:
            raise RuntimeError(
                "Blender material socket compatibility error detected. "
                "Retry with material extraction disabled (handled automatically in latest m2_11 script)."
            ) from exc
        raise exc

    try:
        batch = _create_batch()
    except Exception as exc:
        text = str(exc)
        # Blender 4.5+ renamed EEVEE enum to BLENDER_EEVEE_NEXT.
        if 'enum "BLENDER_EEVEE" not found' in text:
            try:
                batch = _create_batch(force_backend="BLENDER_EEVEE_NEXT")
            except Exception as retry_exc:
                _raise_mapped_blender_error(retry_exc)
        else:
            _raise_mapped_blender_error(exc)

    with torch.no_grad():
        latent = xm.encoder.encode_to_bottleneck(batch)

    latent_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(latent.detach().cpu(), latent_path)
    return [int(x) for x in latent.shape]


def _write_placeholder_latent(latent_path: Path, seed: int = 42, dim: int = 256) -> None:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("`torch` is required for placeholder latent fallback.") from exc

    generator = torch.Generator().manual_seed(seed)
    latent = torch.randn(1, dim, generator=generator)
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(latent, latent_path)


def _write_mesh_reference_latent(latent_path: Path, mesh_path: Path) -> None:
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mesh_path": str(mesh_path.resolve())}
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("`torch` is required for mesh-reference latent fallback.") from exc
    torch.save(payload, latent_path)


def _is_blender_compat_error(exc: Exception) -> bool:
    text = str(exc).lower()
    keys = (
        'enum "blender_eevee" not found',
        "emission not in",
        "blender crashed during shape-e rendering",
        "sigsegv",
        "blender.crash.txt",
        "render failed: output file missing",
    )
    return any(key in text for key in keys)


def encode_latents(
    manifest_path: Path,
    output_manifest: Path,
    latents_dir: Path,
    allow_placeholder_fallback: bool,
    dry_run: bool,
    skip_existing: bool,
    device: str,
    shapee_model_dir: Optional[Path],
    shapee_local_only: bool,
    render_cache_dir: Optional[Path],
    verbose: bool,
    mesh_root: Optional[Path],
    allow_mesh_reference_fallback: bool = True,
    encode_mode: str = "mesh_ref",
) -> Dict[str, int]:
    rows = load_manifest(manifest_path, mesh_root=mesh_root)
    latents_dir.mkdir(parents=True, exist_ok=True)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)

    mode = str(encode_mode).strip().lower()
    if mode not in {"auto", "shapee", "mesh_ref"}:
        raise ValueError(f"invalid encode_mode: {encode_mode}")

    shapee_encoded = 0
    mesh_reference_written = 0
    placeholder_written = 0
    skipped_existing = 0
    output_rows: List[Dict[str, object]] = []
    torch = None
    xm = None

    if not dry_run and mode in {"auto", "shapee"}:
        try:
            torch, xm = _load_shapee_transmitter(
                device=device,
                shapee_model_dir=shapee_model_dir,
                shapee_local_only=shapee_local_only,
            )
        except Exception as exc:
            if mode == "shapee" and not (allow_placeholder_fallback or allow_mesh_reference_fallback):
                raise RuntimeError(f"Shape-E load failed: {exc}") from exc
            xm = None

    for idx, row in enumerate(rows, start=1):
        asset_id = str(row["asset_id"])
        mesh_path = Path(str(row["mesh_path"])).expanduser().resolve()
        if not mesh_path.exists():
            raise FileNotFoundError(f"mesh missing for asset '{asset_id}': {mesh_path}")

        latent_path = Path(str(row.get("latent_path", ""))).expanduser()
        if not latent_path.is_absolute() or str(latent_path).strip() == "":
            latent_path = (latents_dir / f"{asset_id}.pt").resolve()
        else:
            latent_path = latent_path.resolve()

        if skip_existing and latent_path.exists():
            out = dict(row)
            out["latent_path"] = str(latent_path)
            out["latent_source"] = out.get("latent_source", "existing")
            output_rows.append(out)
            skipped_existing += 1
            continue

        out = dict(row)
        out["latent_path"] = str(latent_path)

        if dry_run:
            out["latent_source"] = "dry_run"
            output_rows.append(out)
            continue

        if mode == "mesh_ref":
            _write_mesh_reference_latent(latent_path=latent_path, mesh_path=mesh_path)
            out["latent_source"] = "mesh_reference"
            mesh_reference_written += 1
        elif xm is None or torch is None:
            if allow_mesh_reference_fallback:
                _write_mesh_reference_latent(latent_path=latent_path, mesh_path=mesh_path)
                out["latent_source"] = "mesh_reference_fallback"
                out["shapee_error"] = "Shape-E runtime/model unavailable."
                mesh_reference_written += 1
            elif allow_placeholder_fallback:
                _write_placeholder_latent(latent_path=latent_path, seed=42 + idx)
                out["latent_source"] = "placeholder_fallback"
                out["shapee_error"] = "Shape-E runtime/model unavailable."
                placeholder_written += 1
            else:
                raise RuntimeError(f"Shape-E encoder unavailable for asset '{asset_id}'.")
        else:
            try:
                latent_shape = _encode_one_with_shapee(
                    mesh_path=mesh_path,
                    latent_path=latent_path,
                    xm=xm,
                    torch=torch,
                    device=device,
                    render_cache_dir=render_cache_dir,
                    verbose=verbose,
                )
                out["latent_source"] = "shapee"
                out["latent_shape"] = latent_shape
                shapee_encoded += 1
            except Exception as exc:
                if allow_mesh_reference_fallback and _is_blender_compat_error(exc):
                    _write_mesh_reference_latent(latent_path=latent_path, mesh_path=mesh_path)
                    out["latent_source"] = "mesh_reference_fallback"
                    out["shapee_error"] = str(exc)
                    mesh_reference_written += 1
                elif allow_placeholder_fallback:
                    _write_placeholder_latent(latent_path=latent_path, seed=42 + idx)
                    out["latent_source"] = "placeholder_fallback"
                    out["shapee_error"] = str(exc)
                    placeholder_written += 1
                else:
                    raise RuntimeError(f"Shape-E encode failed for '{asset_id}': {exc}") from exc

        output_rows.append(out)

    with output_manifest.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    return {
        "shapee_encoded": shapee_encoded,
        "mesh_reference_written": mesh_reference_written,
        "placeholder_written": placeholder_written,
        "skipped_existing": skipped_existing,
        "total_assets": len(rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Encode real mesh assets into Shape-E latents.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/real/real_assets_manifest.jsonl"),
        help="Input real manifest with mesh paths.",
    )
    parser.add_argument(
        "--output-manifest",
        type=Path,
        default=Path("data/real/real_assets_manifest.jsonl"),
        help="Output manifest with updated latent paths.",
    )
    parser.add_argument("--latents-dir", type=Path, default=Path("data/real/latents"))
    parser.add_argument("--python-exec", default=sys.executable, help="Deprecated compatibility argument.")
    parser.add_argument("--device", default="cpu", help="Torch device for Shape-E encoding.")
    parser.add_argument(
        "--shapee-model-dir",
        type=Path,
        default=Path("models/shapee"),
        help="Shape-E checkpoint cache dir (transmitter.pt/config).",
    )
    parser.add_argument(
        "--shapee-local-only",
        action="store_true",
        help="Do not download Shape-E weights; require files under --shapee-model-dir.",
    )
    parser.add_argument(
        "--render-cache-dir",
        type=Path,
        default=Path("artifacts/real/shapee_render_cache"),
        help="Cache directory for temporary multiview/point-cloud render artifacts.",
    )
    parser.add_argument(
        "--mesh-root",
        type=Path,
        default=Path("data/real/meshes"),
        help="Fallback mesh directory when manifest rows omit mesh_path (<asset_id>.<ext>).",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose Shape-E data preparation logs.")
    parser.add_argument("--dry-run", action="store_true", help="Do not encode; only materialize output manifest.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip assets with existing latent files.")
    parser.add_argument(
        "--no-placeholder-fallback",
        action="store_true",
        help="Disable placeholder latent fallback when Shape-E encode fails.",
    )
    parser.add_argument(
        "--no-mesh-reference-fallback",
        action="store_true",
        help="Disable mesh-reference fallback for Blender compatibility failures.",
    )
    parser.add_argument(
        "--encode-mode",
        choices=["auto", "shapee", "mesh_ref"],
        default="mesh_ref",
        help="Encoding strategy: mesh_ref (no Blender), auto (try Shape-E, fallback), shapee (force Shape-E first).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        stats = encode_latents(
            manifest_path=args.manifest,
            output_manifest=args.output_manifest,
            latents_dir=args.latents_dir,
            allow_placeholder_fallback=not args.no_placeholder_fallback,
            dry_run=args.dry_run,
            skip_existing=args.skip_existing,
            device=args.device,
            shapee_model_dir=args.shapee_model_dir,
            shapee_local_only=args.shapee_local_only,
            render_cache_dir=args.render_cache_dir,
            verbose=args.verbose,
            mesh_root=args.mesh_root,
            allow_mesh_reference_fallback=not args.no_mesh_reference_fallback,
            encode_mode=args.encode_mode,
        )
    except Exception as exc:
        print(f"Encode failed: {exc}", file=sys.stderr)
        return 1

    print(f"Encode mode: {args.encode_mode}")
    print(f"Shape-E encoded: {stats['shapee_encoded']}")
    print(f"Mesh-reference written: {stats['mesh_reference_written']}")
    print(f"Placeholder written: {stats['placeholder_written']}")
    print(f"Skipped existing: {stats['skipped_existing']}")
    print(f"Total assets: {stats['total_assets']}")
    print(f"Output manifest: {args.output_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
