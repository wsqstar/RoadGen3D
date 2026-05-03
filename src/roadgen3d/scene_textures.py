"""Helpers for applying default textured materials to generated scene geometry."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

_ASSET_ROOT = Path(__file__).resolve().parents[2] / "assets" / "topdown"

SCENE_TEXTURE_PACK_NAME = "topdown_tiles_v1"
VALID_SCENE_TEXTURE_MODES = {"topdown_tiles_v1", "solid_color_legacy"}

_TEXTURE_PATHS = {
    "context_ground": _ASSET_ROOT / "tiles" / "plaza_concrete.png",
    "carriageway": _ASSET_ROOT / "tiles" / "asphalt_base.png",
    "sidewalk": _ASSET_ROOT / "tiles" / "sidewalk_pavers.png",
    "clear_path": _ASSET_ROOT / "tiles" / "sidewalk_pavers.png",
    "furnishing": _ASSET_ROOT / "tiles" / "plaza_concrete.png",
    "transit_pad": _ASSET_ROOT / "tiles" / "plaza_concrete.png",
    "curb": _ASSET_ROOT / "tiles" / "plaza_concrete.png",
    "grass": _ASSET_ROOT / "tiles" / "grass_soft.png",
    "grass_belt": _ASSET_ROOT / "tiles" / "grass_soft.png",
    "building_buffer": _ASSET_ROOT / "tiles" / "grass_soft.png",
    "tree_pit": _ASSET_ROOT / "tiles" / "tree_pit.png",
    "crossing": _ASSET_ROOT / "overlays" / "crosswalk_stripes.png",
    "lane_mark": _ASSET_ROOT / "overlays" / "lane_dashes.png",
    "lane_edge": _ASSET_ROOT / "overlays" / "lane_dashes.png",
    "lane_edge_mark": _ASSET_ROOT / "overlays" / "lane_dashes.png",
    "bike_lane": _ASSET_ROOT / "tiles" / "asphalt_base.png",
    "bus_lane": _ASSET_ROOT / "tiles" / "asphalt_base.png",
    "parking_lane": _ASSET_ROOT / "tiles" / "asphalt_base.png",
    "median_green": _ASSET_ROOT / "tiles" / "grass_soft.png",
    "shared_street_surface": _ASSET_ROOT / "tiles" / "plaza_concrete.png",
    "colored_pavement": _ASSET_ROOT / "tiles" / "sidewalk_pavers.png",
}

_TEXTURE_TILE_SCALE_M = {
    "context_ground": 6.0,
    "carriageway": 4.0,
    "sidewalk": 1.8,
    "clear_path": 1.8,
    "furnishing": 2.5,
    "transit_pad": 2.5,
    "curb": 0.8,
    "grass": 3.0,
    "building_buffer": 3.0,
    "tree_pit": 1.0,
    "crossing": 1.4,
    "lane_mark": 1.2,
    "lane_edge": 1.2,
    "lane_edge_mark": 1.2,
    "bike_lane": 4.0,
    "bus_lane": 4.0,
    "parking_lane": 4.0,
    "median_green": 3.0,
    "grass_belt": 3.0,
    "shared_street_surface": 2.5,
    "colored_pavement": 1.8,
}


@dataclass
class SceneTextureTracker:
    """Collect texture/fallback diagnostics while a scene is built."""

    mode: str
    texture_pack: str = SCENE_TEXTURE_PACK_NAME
    missing_assets: set[str] = field(default_factory=set)
    fallback_used: bool = False
    textured_geometry_count: int = 0
    fallback_geometry_count: int = 0

    def note_textured(self) -> None:
        self.textured_geometry_count += 1

    def note_fallback(self, missing_asset: str | None = None) -> None:
        self.fallback_used = True
        self.fallback_geometry_count += 1
        if missing_asset:
            self.missing_assets.add(str(missing_asset))

    def merge(self, other: "SceneTextureTracker" | None) -> None:
        if other is None:
            return
        self.fallback_used = self.fallback_used or other.fallback_used
        self.textured_geometry_count += int(other.textured_geometry_count)
        self.fallback_geometry_count += int(other.fallback_geometry_count)
        self.missing_assets.update(set(other.missing_assets))

    def summary_dict(self) -> dict[str, object]:
        return {
            "scene_texture_mode": str(self.mode),
            "scene_texture_pack": str(self.texture_pack if self.mode == "topdown_tiles_v1" else "solid_color_legacy"),
            "scene_texture_fallback_used": bool(self.fallback_used),
            "scene_texture_missing_assets": sorted(self.missing_assets),
            "textured_geometry_count": int(self.textured_geometry_count),
            "fallback_geometry_count": int(self.fallback_geometry_count),
        }


def create_scene_texture_tracker(mode: str) -> SceneTextureTracker:
    normalized = str(mode or "topdown_tiles_v1").strip().lower() or "topdown_tiles_v1"
    return SceneTextureTracker(mode=normalized)


def scene_texture_pack_name(mode: str) -> str:
    normalized = str(mode or "topdown_tiles_v1").strip().lower() or "topdown_tiles_v1"
    if normalized == "topdown_tiles_v1":
        return SCENE_TEXTURE_PACK_NAME
    return "solid_color_legacy"


def _require_pillow():
    try:
        from PIL import Image
    except Exception:
        return None
    return Image


def _require_trimesh():
    try:
        import trimesh
    except Exception:
        return None
    return trimesh


@lru_cache(maxsize=32)
def _load_texture_rgba(path_text: str):
    image_module = _require_pillow()
    if image_module is None:
        return None
    path = Path(path_text)
    if not path.exists():
        return None
    try:
        with image_module.open(path) as image:
            return image.convert("RGBA")
    except Exception:
        return None


def _normalized_rgba(color: Sequence[int]) -> list[float]:
    values = list(color[:4])
    while len(values) < 4:
        values.append(255)
    return [max(0.0, min(1.0, float(v) / 255.0)) for v in values]


def _solid_pbr_mesh(mesh, *, rgba: Sequence[int], roughness: float):
    trimesh = _require_trimesh()
    if trimesh is None:
        return mesh
    from trimesh.visual.material import PBRMaterial

    material = PBRMaterial(
        baseColorFactor=_normalized_rgba(rgba),
        metallicFactor=0.0,
        roughnessFactor=float(roughness),
    )
    mesh.visual = trimesh.visual.TextureVisuals(material=material)
    return mesh


def _projection_axes(normal: np.ndarray) -> tuple[int, int]:
    abs_normal = np.abs(np.asarray(normal, dtype=float))
    dominant_axis = int(abs_normal.argmax())
    if dominant_axis == 1:
        return 0, 2
    if dominant_axis == 0:
        return 2, 1
    return 0, 1


def _build_facewise_uv_mesh(mesh, *, tile_scale_m: float):
    trimesh = _require_trimesh()
    if trimesh is None:
        return mesh, None
    triangles = np.asarray(mesh.triangles, dtype=np.float64)
    if triangles.size == 0:
        return mesh.copy(), None
    face_normals = np.asarray(mesh.face_normals, dtype=np.float64)
    vertices = triangles.reshape(-1, 3)
    faces = np.arange(len(vertices), dtype=np.int64).reshape(-1, 3)
    uv = np.zeros((len(vertices), 2), dtype=np.float64)
    scale = max(float(tile_scale_m), 1e-6)
    for face_index, normal in enumerate(face_normals):
        axis_u, axis_v = _projection_axes(normal)
        tri = triangles[face_index]
        uv_face = tri[:, [axis_u, axis_v]] / scale
        start = face_index * 3
        uv[start : start + 3] = uv_face
    textured = trimesh.Trimesh(vertices=vertices, faces=faces, process=False, maintain_order=True)
    if getattr(mesh, "metadata", None):
        textured.metadata = dict(mesh.metadata)
    return textured, uv


def _missing_asset_label(surface_role: str, path: Path | None) -> str:
    if path is None:
        return f"{surface_role}:unmapped"
    try:
        rel = path.relative_to(_ASSET_ROOT.parent)
        return f"{surface_role}:{rel.as_posix()}"
    except Exception:
        return f"{surface_role}:{path.name}"


def apply_default_scene_texture(
    mesh,
    *,
    surface_role: str,
    tint_rgba: Sequence[int],
    roughness: float,
    texture_mode: str,
    tracker: SceneTextureTracker | None = None,
    texture_overrides: Mapping[str, str] | None = None,
):
    """Return a textured copy of *mesh* or a legacy solid-material fallback."""

    normalized_mode = str(texture_mode or "topdown_tiles_v1").strip().lower() or "topdown_tiles_v1"
    if normalized_mode not in VALID_SCENE_TEXTURE_MODES:
        normalized_mode = "topdown_tiles_v1"
    if normalized_mode == "solid_color_legacy":
        return _solid_pbr_mesh(mesh, rgba=tint_rgba, roughness=roughness)

    trimesh = _require_trimesh()
    normalized_role = str(surface_role or "").strip().lower()
    override_path = ""
    if texture_overrides:
        override_path = str(texture_overrides.get(normalized_role, "") or "").strip()
    texture_path = Path(override_path).expanduser() if override_path else _TEXTURE_PATHS.get(normalized_role)
    texture_image = _load_texture_rgba(str(texture_path)) if texture_path is not None else None
    if trimesh is None or texture_image is None:
        if tracker is not None:
            tracker.note_fallback(_missing_asset_label(surface_role, texture_path))
        return _solid_pbr_mesh(mesh, rgba=tint_rgba, roughness=roughness)

    textured_mesh, uv = _build_facewise_uv_mesh(
        mesh,
        tile_scale_m=float(_TEXTURE_TILE_SCALE_M.get(str(surface_role or "").strip().lower(), 2.0)),
    )
    if uv is None:
        if tracker is not None:
            tracker.note_fallback(_missing_asset_label(surface_role, texture_path))
        return _solid_pbr_mesh(mesh, rgba=tint_rgba, roughness=roughness)

    from trimesh.visual.material import PBRMaterial

    material = PBRMaterial(
        baseColorFactor=_normalized_rgba(tint_rgba),
        metallicFactor=0.0,
        roughnessFactor=float(roughness),
        baseColorTexture=texture_image.copy(),
    )
    textured_mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    if tracker is not None:
        tracker.note_textured()
    return textured_mesh


def topdown_texture_assets() -> Iterable[Path]:
    return tuple(_TEXTURE_PATHS.values())
