"""Voxel-to-mesh export utilities for visualization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import numpy as np


@dataclass(frozen=True)
class VoxelExportConfig:
    voxel_size: float = 0.1
    method: str = "marching_cubes"  # marching_cubes | cubes
    export_format: str = "both"  # glb | ply | both


def _validate_export_format(export_format: str) -> str:
    value = export_format.strip().lower()
    if value not in {"glb", "ply", "both"}:
        raise ValueError("export_format must be one of: glb, ply, both")
    return value


def _validate_method(method: str) -> str:
    value = method.strip().lower()
    if value not in {"marching_cubes", "cubes"}:
        raise ValueError("method must be one of: marching_cubes, cubes")
    return value


def _require_trimesh():
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("`trimesh` is required for mesh export. Install requirements-m2.txt.") from exc
    return trimesh


def _mesh_from_cubes(occupancy: np.ndarray, voxel_size: float):
    trimesh = _require_trimesh()
    points = np.argwhere(occupancy)
    if points.size == 0:
        # Keep a tiny fallback cube so downstream viewers always get a valid mesh.
        points = np.array([[0, 0, 0]], dtype=np.float32)
    else:
        points = points.astype(np.float32, copy=False)

    mesh = trimesh.voxel.ops.multibox(points=points, pitch=float(voxel_size))
    if mesh.is_empty:
        mesh = trimesh.creation.box(extents=(voxel_size, voxel_size, voxel_size))
    return mesh


def _mesh_from_marching_cubes(occupancy: np.ndarray, voxel_size: float):
    if occupancy.sum() == 0:
        raise ValueError("occupancy is empty")
    try:
        from skimage.measure import marching_cubes
    except ImportError as exc:
        raise RuntimeError(
            "`scikit-image` is required for marching_cubes export. Install requirements-m2.txt."
        ) from exc
    trimesh = _require_trimesh()

    verts, faces, normals, _ = marching_cubes(occupancy.astype(np.float32), level=0.5)
    mesh = trimesh.Trimesh(
        vertices=(verts * float(voxel_size)),
        faces=faces,
        vertex_normals=normals,
        process=False,
    )
    if mesh.is_empty:
        raise RuntimeError("marching_cubes produced an empty mesh")
    return mesh


def export_voxel_meshes(
    voxel_bin: np.ndarray,
    out_dir: Path,
    stem: str = "voxel",
    voxel_size: float = 0.1,
    method: str = "marching_cubes",
    export_format: str = "both",
    mesh_override=None,
) -> Dict[str, str]:
    """
    Export voxel occupancy to GLB/PLY files.

    Returns dict containing absolute paths:
    - mesh_glb
    - mesh_ply
    - mesh_method
    """

    if voxel_bin.ndim != 3:
        raise ValueError(f"voxel_bin must be rank-3, got shape {voxel_bin.shape}")
    if voxel_size <= 0:
        raise ValueError("voxel_size must be > 0")

    export_format = _validate_export_format(export_format)
    method = _validate_method(method)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    occupancy = np.asarray(voxel_bin > 0, dtype=np.uint8)
    used_method = method

    if mesh_override is not None:
        mesh = mesh_override.copy()
        used_method = "shapee_mesh"
    else:
        if method == "marching_cubes":
            try:
                mesh = _mesh_from_marching_cubes(occupancy=occupancy, voxel_size=voxel_size)
            except Exception:
                mesh = _mesh_from_cubes(occupancy=occupancy, voxel_size=voxel_size)
                used_method = "cubes_fallback"
        else:
            mesh = _mesh_from_cubes(occupancy=occupancy, voxel_size=voxel_size)

    mesh_glb = ""
    mesh_ply = ""

    if export_format in {"glb", "both"}:
        glb_path = (out_dir / f"{stem}.glb").resolve()
        mesh.export(glb_path)
        mesh_glb = str(glb_path)

    if export_format in {"ply", "both"}:
        ply_path = (out_dir / f"{stem}.ply").resolve()
        mesh.export(ply_path)
        mesh_ply = str(ply_path)

    return {
        "mesh_glb": mesh_glb,
        "mesh_ply": mesh_ply,
        "mesh_method": used_method,
    }

