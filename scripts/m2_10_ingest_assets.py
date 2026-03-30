#!/usr/bin/env python3
"""Ingest and normalize real mesh assets into RoadGen3D manifest format."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

REQUIRED_FIELDS = (
    "asset_id",
    "category",
    "text_desc",
    "mesh_path",
    "latent_path",
    "license",
    "source",
    "split",
)


def validate_manifest_row(row: Dict[str, object]) -> List[str]:
    errors: List[str] = []
    for field in REQUIRED_FIELDS:
        value = row.get(field)
        if value is None or str(value).strip() == "":
            errors.append(f"missing field: {field}")
    split = str(row.get("split", "")).strip().lower()
    if split not in {"train", "val", "test"}:
        errors.append("split must be one of train|val|test")
    return errors


def load_manifest(path: Path) -> List[Dict[str, object]]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    rows: List[Dict[str, object]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        errors = validate_manifest_row(payload)
        if errors:
            raise ValueError(f"invalid manifest row at line {line_no}: {', '.join(errors)}")
        rows.append(payload)
    return rows


def check_mesh_latent_pairs(rows: List[Dict[str, object]]) -> List[str]:
    errors: List[str] = []
    for row in rows:
        asset_id = str(row.get("asset_id", ""))
        mesh_path = Path(str(row.get("mesh_path", ""))).expanduser()
        latent_path = Path(str(row.get("latent_path", ""))).expanduser()
        if not mesh_path.is_absolute():
            mesh_path = mesh_path.resolve()
        if not latent_path.is_absolute():
            latent_path = latent_path.resolve()
        if not mesh_path.exists():
            errors.append(f"{asset_id}: mesh missing -> {mesh_path}")
        if not latent_path.exists():
            errors.append(f"{asset_id}: latent missing -> {latent_path}")
    return errors


def _load_mesh_as_single_mesh(mesh_path: Path):
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("`trimesh` is required for ingestion. Install requirements-m2.txt.") from exc

    mesh_or_scene = trimesh.load(mesh_path, force="scene")
    if isinstance(mesh_or_scene, trimesh.Scene):
        if not mesh_or_scene.geometry:
            raise ValueError(f"empty scene mesh: {mesh_path}")
        merged = trimesh.util.concatenate(tuple(mesh_or_scene.geometry.values()))
        return merged
    return mesh_or_scene


def _apply_rotation_deg_xyz(mesh, rotation_deg_xyz):
    if rotation_deg_xyz is None:
        return mesh
    angles = tuple(float(value) for value in rotation_deg_xyz)
    if len(angles) != 3:
        raise ValueError("rotation_deg_xyz must contain exactly 3 values")
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("`trimesh` is required for ingestion. Install requirements-m2.txt.") from exc

    rotated = mesh.copy()
    for axis, angle_deg in zip(([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]), angles):
        if abs(float(angle_deg)) <= 1e-9:
            continue
        transform = trimesh.transformations.rotation_matrix(
            np.deg2rad(float(angle_deg)),
            axis,
            [0.0, 0.0, 0.0],
        )
        rotated.apply_transform(transform)
    return rotated


def _normalize_mesh(mesh):
    bbox = mesh.bounds
    center = bbox.mean(axis=0)
    span = bbox[1] - bbox[0]
    max_span = float(max(span.max(), 1e-6))
    mesh = mesh.copy()
    mesh.apply_translation(-center)
    mesh.apply_scale(1.0 / max_span)
    return mesh


def _ground_mesh_to_y_zero(mesh):
    grounded = mesh.copy()
    min_y = float(grounded.bounds[0][1])
    grounded.apply_translation([0.0, -min_y, 0.0])
    return grounded


def normalize_grounded_mesh(mesh, rotation_deg_xyz=None):
    normalized = _apply_rotation_deg_xyz(mesh, rotation_deg_xyz)
    normalized = _normalize_mesh(normalized)
    normalized = _ground_mesh_to_y_zero(normalized)
    return normalized


# ---------------------------------------------------------------------------
# Scene-preserving import helpers (preserve PBR materials & textures)
# ---------------------------------------------------------------------------


def _filter_scene_geometry(scene):
    """Remove non-model junk (backdrop spheres, ground planes) from a Scene.

    Returns a new ``trimesh.Scene`` containing only the retained geometries.
    Raises ``ValueError`` if *all* geometries would be removed.
    """
    import trimesh

    geom_items = list(scene.geometry.items())
    if len(geom_items) <= 1:
        return scene  # nothing to filter

    # Compute per-geometry bounding sphere radii.
    radii: Dict[str, float] = {}
    face_counts: Dict[str, int] = {}
    for name, geom in geom_items:
        bounds = np.asarray(geom.bounds, dtype=np.float64)
        span = bounds[1] - bounds[0]
        radii[name] = float(np.linalg.norm(span) / 2.0)
        face_counts[name] = int(len(getattr(geom, "faces", ())))

    sorted_radii = sorted(radii.values())
    median_radius = float(sorted_radii[len(sorted_radii) // 2]) if sorted_radii else 0.0

    keep_names: List[str] = []
    for name, geom in geom_items:
        # Skip geometries with very few faces (ground planes, helper quads).
        if face_counts[name] < 20:
            continue
        # Skip geometries whose bounding sphere is disproportionately large
        # (backdrop / environment spheres).
        if median_radius > 1e-6 and radii[name] > 5.0 * median_radius:
            continue
        keep_names.append(name)

    if not keep_names:
        raise ValueError("all geometries filtered out; cannot produce a valid scene")

    if len(keep_names) == len(geom_items):
        return scene  # nothing removed

    filtered = trimesh.Scene()
    for name in keep_names:
        filtered.add_geometry(scene.geometry[name], node_name=name)
    return filtered


def _load_as_filtered_scene(mesh_path: Path):
    """Load a GLB as a ``trimesh.Scene`` with materials intact, filtering junk."""
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("`trimesh` is required for ingestion. Install requirements-m2.txt.") from exc

    scene = trimesh.load(mesh_path, force="scene")
    if isinstance(scene, trimesh.Scene):
        if not scene.geometry:
            raise ValueError(f"empty scene mesh: {mesh_path}")
        return _filter_scene_geometry(scene)
    # Single mesh -- wrap in a Scene to keep a uniform return type.
    scene_wrap = trimesh.Scene()
    scene_wrap.add_geometry(scene, node_name="geometry_0")
    return scene_wrap


def normalize_grounded_scene(scene, rotation_deg_xyz=None):
    """Normalize a ``trimesh.Scene`` to unit cube grounded at Y=0.

    Same mathematical operations as ``normalize_grounded_mesh`` but applied at
    the Scene level so that sub-geometry visuals (PBR materials, textures, UVs)
    are preserved.
    """
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("`trimesh` is required for ingestion. Install requirements-m2.txt.") from exc

    scene = scene.copy()

    # --- optional rotation ---------------------------------------------------
    if rotation_deg_xyz is not None:
        angles = tuple(float(value) for value in rotation_deg_xyz)
        if len(angles) != 3:
            raise ValueError("rotation_deg_xyz must contain exactly 3 values")
        for axis, angle_deg in zip(
            ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]),
            angles,
        ):
            if abs(float(angle_deg)) <= 1e-9:
                continue
            transform = trimesh.transformations.rotation_matrix(
                np.deg2rad(float(angle_deg)),
                axis,
                [0.0, 0.0, 0.0],
            )
            scene.apply_transform(transform)

    # --- normalize to unit cube centred at origin ----------------------------
    bounds = np.asarray(scene.bounds, dtype=np.float64)
    center = bounds.mean(axis=0)
    span = bounds[1] - bounds[0]
    max_span = float(max(span.max(), 1e-6))
    scene.apply_translation(-center)
    scene.apply_scale(1.0 / max_span)

    # --- ground at Y=0 -------------------------------------------------------
    bounds = np.asarray(scene.bounds, dtype=np.float64)
    min_y = float(bounds[0][1])
    scene.apply_translation([0.0, -min_y, 0.0])

    return scene


def scene_to_merged_mesh(scene):
    """Concatenate all geometries of a Scene into a single Trimesh.

    **This is intentionally lossy** -- PBR materials are discarded.  Use the
    returned mesh only for validation (e.g. PCA trunk-axis checks).
    """
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("`trimesh` is required for ingestion. Install requirements-m2.txt.") from exc

    if isinstance(scene, trimesh.Scene):
        if not scene.geometry:
            raise ValueError("cannot merge empty scene")
        return trimesh.util.concatenate(tuple(scene.geometry.values()))
    return scene


def validate_tree_upright(
    mesh,
    *,
    ground_tolerance: float = 1e-3,
    trunk_slice_ratio: float = 0.35,
    max_axis_angle_deg: float = 15.0,
):
    bounds = np.asarray(mesh.bounds, dtype=np.float64)
    span = bounds[1] - bounds[0]
    width = float(span[0])
    height = float(span[1])
    depth = float(span[2])
    min_y = float(bounds[0][1])
    diagnostics = {
        "min_y": min_y,
        "width": width,
        "height": height,
        "depth": depth,
        "ground_tolerance": float(ground_tolerance),
        "trunk_slice_ratio": float(trunk_slice_ratio),
        "max_axis_angle_deg": float(max_axis_angle_deg),
    }

    if abs(min_y) > float(ground_tolerance):
        diagnostics["failure_reason"] = "tree_not_grounded"
        return False, diagnostics
    if height <= max(width, depth):
        diagnostics["failure_reason"] = "tree_not_taller_than_wide"
        return False, diagnostics

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    if vertices.shape[0] < 3:
        diagnostics["failure_reason"] = "tree_insufficient_vertices"
        return False, diagnostics

    lower_max_y = min_y + height * float(trunk_slice_ratio)
    try:
        sampled_points = np.asarray(mesh.sample(4096), dtype=np.float64)
    except Exception:
        sampled_points = vertices
    trunk_points = sampled_points[sampled_points[:, 1] <= lower_max_y]
    diagnostics["trunk_vertex_count"] = int(trunk_points.shape[0])
    if trunk_points.shape[0] < 8:
        trunk_points = vertices[vertices[:, 1] <= lower_max_y]
        diagnostics["trunk_vertex_count"] = int(trunk_points.shape[0])
    if trunk_points.shape[0] < 3:
        diagnostics["failure_reason"] = "tree_insufficient_trunk_slice"
        return False, diagnostics

    centered = trunk_points - trunk_points.mean(axis=0, keepdims=True)
    covariance = np.cov(centered, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(covariance)
    principal_axis = eigvecs[:, int(np.argmax(eigvals))]
    principal_axis = principal_axis / max(np.linalg.norm(principal_axis), 1e-9)
    world_up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    dot = float(np.clip(abs(np.dot(principal_axis, world_up)), -1.0, 1.0))
    angle_deg = float(np.degrees(np.arccos(dot)))
    diagnostics["trunk_axis_angle_deg"] = angle_deg
    if angle_deg > float(max_axis_angle_deg):
        diagnostics["failure_reason"] = "tree_trunk_not_upright"
        return False, diagnostics

    diagnostics["failure_reason"] = ""
    return True, diagnostics


def ingest_assets(
    input_manifest: Path,
    output_manifest: Path,
    mesh_out_dir: Path,
    normalize_mesh: bool = True,
) -> Tuple[int, Path]:
    rows = load_manifest(input_manifest)
    mesh_out_dir.mkdir(parents=True, exist_ok=True)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)

    output_rows: List[Dict[str, object]] = []
    for row in rows:
        asset_id = str(row["asset_id"])
        mesh_path = Path(str(row["mesh_path"])).expanduser().resolve()
        if not mesh_path.exists():
            raise FileNotFoundError(f"mesh_path for asset '{asset_id}' not found: {mesh_path}")

        target_mesh_path = (mesh_out_dir / f"{asset_id}.glb").resolve()
        if normalize_mesh:
            mesh = _load_mesh_as_single_mesh(mesh_path)
            mesh = normalize_grounded_mesh(mesh)
            mesh.export(target_mesh_path)
        else:
            shutil.copy2(mesh_path, target_mesh_path)

        latent_path = Path(str(row["latent_path"])).expanduser()
        if not latent_path.is_absolute():
            latent_path = (output_manifest.parent / latent_path).resolve()

        out = dict(row)
        out["mesh_path"] = str(target_mesh_path)
        out["latent_path"] = str(latent_path)
        out["split"] = str(out["split"]).lower()
        output_rows.append(out)

    with output_manifest.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    return len(output_rows), output_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest and normalize real assets manifest.")
    parser.add_argument("--input-manifest", type=Path, required=True, help="Raw input manifest (.jsonl).")
    parser.add_argument(
        "--output-manifest",
        type=Path,
        default=Path("data/real/real_assets_manifest.jsonl"),
        help="Normalized output manifest (.jsonl).",
    )
    parser.add_argument(
        "--mesh-out-dir",
        type=Path,
        default=Path("data/real/meshes"),
        help="Directory for normalized meshes.",
    )
    parser.add_argument(
        "--no-normalize-mesh",
        action="store_true",
        help="Disable centering/scaling normalization and copy mesh directly.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        count, path = ingest_assets(
            input_manifest=args.input_manifest,
            output_manifest=args.output_manifest,
            mesh_out_dir=args.mesh_out_dir,
            normalize_mesh=not args.no_normalize_mesh,
        )
    except Exception as exc:
        print(f"Ingest failed: {exc}", file=sys.stderr)
        return 1

    print(f"Ingested assets: {count}")
    print(f"Output manifest: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
