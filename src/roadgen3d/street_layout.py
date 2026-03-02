"""Street-level scene composition utilities for M3."""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .embedder import ClipTextEmbedder
from .index_store import FaissIndexStore
from .types import StreetComposeConfig, StreetComposeResult, StreetPlacement

DEFAULT_CATEGORIES: Tuple[str, ...] = (
    "bench",
    "lamp",
    "trash",
    "tree",
    "bus_stop",
    "mailbox",
    "hydrant",
    "bollard",
)

DEFAULT_SPACING_M: Dict[str, float] = {
    "lamp": 18.0,
    "tree": 14.0,
    "bench": 22.0,
    "trash": 18.0,
    "bus_stop": 45.0,
    "mailbox": 40.0,
    "hydrant": 30.0,
    "bollard": 6.0,
}

SIDE_PREF: Dict[str, str] = {
    "bus_stop": "right",
    "mailbox": "right",
    "hydrant": "right",
    "bench": "both",
    "lamp": "both",
    "trash": "both",
    "tree": "both",
    "bollard": "both",
}


@dataclass(frozen=True)
class _MeshCacheEntry:
    mesh: object
    half_x: float
    half_z: float
    min_y: float


def _require_trimesh():
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("`trimesh` is required for M3 scene composition. Install requirements-m2.txt.") from exc
    return trimesh


def _resolve_path(path_text: object, base_dir: Path) -> str:
    path = Path(str(path_text)).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return str(path)


def _validate_config(config: StreetComposeConfig) -> None:
    if not config.query.strip():
        raise ValueError("query cannot be empty")
    if config.length_m <= 1.0:
        raise ValueError("length_m must be > 1.0")
    if config.road_width_m <= 0.5:
        raise ValueError("road_width_m must be > 0.5")
    if config.sidewalk_width_m <= 0.2:
        raise ValueError("sidewalk_width_m must be > 0.2")
    if config.lane_count <= 0:
        raise ValueError("lane_count must be >= 1")
    if config.density <= 0:
        raise ValueError("density must be > 0")
    if config.topk_per_category <= 0:
        raise ValueError("topk_per_category must be >= 1")
    if config.max_trials_per_slot <= 0:
        raise ValueError("max_trials_per_slot must be >= 1")


def _validate_export_format(export_format: str) -> str:
    value = export_format.strip().lower()
    if value not in {"glb", "ply", "both"}:
        raise ValueError("export_format must be one of: glb, ply, both")
    return value


def _load_real_manifest(manifest_path: Path) -> List[Dict[str, str]]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"real manifest not found: {manifest_path}")
    required = ("asset_id", "category", "text_desc", "mesh_path", "latent_path")
    rows: List[Dict[str, str]] = []
    base_dir = manifest_path.parent.resolve()
    for line_no, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        missing = [key for key in required if key not in payload or str(payload[key]).strip() == ""]
        if missing:
            raise ValueError(
                f"missing required fields in line {line_no} ({manifest_path}): {', '.join(missing)}"
            )
        row = {
            "asset_id": str(payload["asset_id"]).strip(),
            "category": str(payload["category"]).strip().lower(),
            "text_desc": str(payload["text_desc"]).strip(),
            "mesh_path": _resolve_path(payload["mesh_path"], base_dir),
            "latent_path": _resolve_path(payload["latent_path"], base_dir),
        }
        rows.append(row)
    if not rows:
        raise ValueError(f"real manifest is empty: {manifest_path}")
    return rows


def _load_mesh_cache(rows: List[Dict[str, str]]) -> Dict[str, _MeshCacheEntry]:
    trimesh = _require_trimesh()
    cache: Dict[str, _MeshCacheEntry] = {}
    for row in rows:
        asset_id = row["asset_id"]
        mesh_path = Path(row["mesh_path"]).resolve()
        if not mesh_path.exists():
            raise FileNotFoundError(f"mesh missing for asset '{asset_id}': {mesh_path}")
        mesh_or_scene = trimesh.load(mesh_path, force="scene")
        if isinstance(mesh_or_scene, trimesh.Scene):
            if not mesh_or_scene.geometry:
                raise ValueError(f"empty mesh scene for asset '{asset_id}': {mesh_path}")
            mesh = trimesh.util.concatenate(tuple(mesh_or_scene.geometry.values()))
        else:
            mesh = mesh_or_scene
        if mesh.is_empty:
            raise ValueError(f"empty mesh for asset '{asset_id}': {mesh_path}")
        bounds = mesh.bounds
        span = bounds[1] - bounds[0]
        cache[asset_id] = _MeshCacheEntry(
            mesh=mesh,
            half_x=float(max(span[0] / 2.0, 1e-3)),
            half_z=float(max(span[2] / 2.0, 1e-3)),
            min_y=float(bounds[0][1]),
        )
    return cache


def _bbox_intersects(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2])


def _compute_bbox(
    x: float,
    z: float,
    yaw_deg: float,
    half_x: float,
    half_z: float,
    scale: float,
    clearance: float,
) -> Tuple[float, float, float, float]:
    yaw_rad = math.radians(yaw_deg)
    cos_y = abs(math.cos(yaw_rad))
    sin_y = abs(math.sin(yaw_rad))
    aabb_half_x = (cos_y * half_x + sin_y * half_z) * scale + clearance
    aabb_half_z = (sin_y * half_x + cos_y * half_z) * scale + clearance
    return (x - aabb_half_x, x + aabb_half_x, z - aabb_half_z, z + aabb_half_z)


def _sample_pose(
    category: str,
    slot_idx: int,
    trial_idx: int,
    x_center: float,
    length_m: float,
    road_width_m: float,
    sidewalk_width_m: float,
    spacing_m: float,
    rng: random.Random,
) -> Tuple[float, float, float]:
    jitter_x = min(1.5, max(0.25, 0.2 * spacing_m))
    min_x = -length_m / 2.0 + 0.5
    max_x = length_m / 2.0 - 0.5
    x = float(np.clip(x_center + rng.uniform(-jitter_x, jitter_x), min_x, max_x))

    side_pref = SIDE_PREF.get(category, "both")
    if side_pref == "right":
        side = -1.0
    elif side_pref == "left":
        side = 1.0
    else:
        side = 1.0 if ((slot_idx + trial_idx) % 2 == 0) else -1.0

    z_center = side * (road_width_m / 2.0 + sidewalk_width_m * 0.5)
    z_jitter = sidewalk_width_m * 0.2
    z = z_center + rng.uniform(-z_jitter, z_jitter)

    yaw_base = 180.0 if side > 0 else 0.0
    yaw_deg = yaw_base + rng.uniform(-8.0, 8.0)
    return x, z, yaw_deg


def _pick_category_candidate(
    query: str,
    category: str,
    topk: int,
    embedder: ClipTextEmbedder,
    index_store: FaissIndexStore,
    asset_by_id: Dict[str, Dict[str, str]],
    category_pool: List[Dict[str, str]],
    rng: random.Random,
) -> Tuple[Dict[str, str], float, str]:
    slot_query = f"{query}, {category} street asset"
    query_embedding = embedder.encode_texts([slot_query])
    hits = index_store.search(query_embedding, topk=max(1, int(topk)))[0]
    # Collect all matching candidates from top-k, then randomly sample one
    matching_hits = []
    for hit in hits:
        row = asset_by_id.get(hit.asset_id)
        if row is not None and row["category"] == category:
            matching_hits.append((row, float(hit.score)))
    if matching_hits:
        # Weighted random sample favoring higher scores (but allowing variety)
        row, score = rng.choice(matching_hits)
        return row, score, "faiss"
    if not category_pool:
        raise RuntimeError(f"empty category pool: {category}")
    row = rng.choice(category_pool)
    return row, 0.0, "fallback_pool"


def _build_base_scene(
    config: StreetComposeConfig,
):
    trimesh = _require_trimesh()
    scene = trimesh.Scene()
    road = trimesh.creation.box(extents=(config.length_m, 0.06, config.road_width_m))
    road.visual.face_colors = [65, 68, 72, 255]
    road.apply_translation([0.0, -0.03, 0.0])
    scene.add_geometry(road, node_name="road_slab")

    sidewalk_color = [165, 168, 172, 255]
    sidewalk_left = trimesh.creation.box(extents=(config.length_m, 0.08, config.sidewalk_width_m))
    sidewalk_left.visual.face_colors = sidewalk_color
    sidewalk_left.apply_translation([0.0, -0.04, config.road_width_m / 2.0 + config.sidewalk_width_m / 2.0])
    scene.add_geometry(sidewalk_left, node_name="sidewalk_left")

    sidewalk_right = trimesh.creation.box(extents=(config.length_m, 0.08, config.sidewalk_width_m))
    sidewalk_right.visual.face_colors = sidewalk_color
    sidewalk_right.apply_translation([0.0, -0.04, -config.road_width_m / 2.0 - config.sidewalk_width_m / 2.0])
    scene.add_geometry(sidewalk_right, node_name="sidewalk_right")
    return scene


def _add_instance_meshes(
    scene,
    placements: List[StreetPlacement],
    mesh_cache: Dict[str, _MeshCacheEntry],
) -> None:
    trimesh = _require_trimesh()
    for placement in placements:
        mesh = mesh_cache[placement.asset_id].mesh.copy()
        mesh.apply_scale(float(placement.scale))
        rotation = trimesh.transformations.rotation_matrix(
            math.radians(float(placement.yaw_deg)),
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0],
        )
        mesh.apply_transform(rotation)
        mesh.apply_translation(
            [
                float(placement.position_xyz[0]),
                float(placement.position_xyz[1]),
                float(placement.position_xyz[2]),
            ]
        )
        scene.add_geometry(mesh, node_name=placement.instance_id)


def _export_scene(scene, out_dir: Path, export_format: str) -> Dict[str, str]:
    export_format = _validate_export_format(export_format)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {"scene_glb": "", "scene_ply": ""}
    if export_format in {"glb", "both"}:
        glb_path = (out_dir / "scene.glb").resolve()
        scene.export(glb_path)
        outputs["scene_glb"] = str(glb_path)
    if export_format in {"ply", "both"}:
        ply_path = (out_dir / "scene.ply").resolve()
        scene_mesh = scene.to_geometry()
        scene_mesh.export(ply_path)
        outputs["scene_ply"] = str(ply_path)
    return outputs


def compose_street_scene(
    config: StreetComposeConfig,
    manifest_path: Path,
    artifacts_dir: Path,
    model_name: str = "openai/clip-vit-base-patch32",
    model_dir: Optional[Path] = None,
    local_files_only: bool = False,
    device: str = "cpu",
    export_format: str = "both",
    out_dir: Path = Path("artifacts/real"),
) -> StreetComposeResult:
    """
    Compose a street scene by category-aware retrieval and collision-aware placement.

    Outputs:
    - scene.glb/scene.ply under `out_dir` (per `export_format`)
    - scene_layout.json under `out_dir`
    """
    _validate_config(config)
    export_format = _validate_export_format(export_format)
    manifest_path = Path(manifest_path).resolve()
    artifacts_dir = Path(artifacts_dir).resolve()
    out_dir = Path(out_dir).resolve()

    rows = _load_real_manifest(manifest_path)
    asset_by_id = {row["asset_id"]: row for row in rows}

    category_to_rows: Dict[str, List[Dict[str, str]]] = {category: [] for category in DEFAULT_CATEGORIES}
    for row in rows:
        category = row["category"]
        if category in category_to_rows:
            category_to_rows[category].append(row)

    available_categories = [category for category, pool in category_to_rows.items() if pool]
    if not available_categories:
        raise RuntimeError(
            f"No supported categories found in manifest: {manifest_path}. "
            f"Expected at least one of {DEFAULT_CATEGORIES}."
        )

    mesh_cache = _load_mesh_cache([row for row in rows if row["category"] in category_to_rows])

    embedder = ClipTextEmbedder(
        model_name=model_name,
        model_dir=model_dir,
        local_files_only=bool(local_files_only),
        device=device,
    )
    index_store = FaissIndexStore.load(
        index_path=artifacts_dir / "index_ip.faiss",
        id_map_path=artifacts_dir / "id_map.json",
    )

    rng = random.Random(int(config.seed))
    placements: List[StreetPlacement] = []
    existing_bboxes: List[Tuple[float, float, float, float]] = []
    dropped_slots = 0
    instance_counter = 1
    clearance = 0.2
    effective_density = max(float(config.density), 0.1)

    for category in DEFAULT_CATEGORIES:
        pool = category_to_rows.get(category, [])
        if not pool:
            continue
        base_spacing = float(DEFAULT_SPACING_M[category])
        spacing = base_spacing / effective_density
        slot_count = max(1, int(math.floor(float(config.length_m) / spacing)))
        segment = float(config.length_m) / float(slot_count)
        for slot_idx in range(slot_count):
            x_center = -float(config.length_m) / 2.0 + (slot_idx + 0.5) * segment
            row, score, source = _pick_category_candidate(
                query=config.query,
                category=category,
                topk=config.topk_per_category,
                embedder=embedder,
                index_store=index_store,
                asset_by_id=asset_by_id,
                category_pool=pool,
                rng=rng,
            )
            entry = mesh_cache[row["asset_id"]]
            placed = False
            for trial_idx in range(int(config.max_trials_per_slot)):
                x, z, yaw_deg = _sample_pose(
                    category=category,
                    slot_idx=slot_idx,
                    trial_idx=trial_idx,
                    x_center=x_center,
                    length_m=float(config.length_m),
                    road_width_m=float(config.road_width_m),
                    sidewalk_width_m=float(config.sidewalk_width_m),
                    spacing_m=spacing,
                    rng=rng,
                )
                scale = 1.0
                bbox = _compute_bbox(
                    x=x,
                    z=z,
                    yaw_deg=yaw_deg,
                    half_x=entry.half_x,
                    half_z=entry.half_z,
                    scale=scale,
                    clearance=clearance,
                )
                if any(_bbox_intersects(bbox, existing) for existing in existing_bboxes):
                    continue
                existing_bboxes.append(bbox)
                y = -entry.min_y * scale
                placement = StreetPlacement(
                    instance_id=f"inst_{instance_counter:04d}",
                    asset_id=row["asset_id"],
                    category=category,
                    score=float(score),
                    position_xyz=[float(x), float(y), float(z)],
                    yaw_deg=float(yaw_deg),
                    scale=float(scale),
                    bbox_xz=[float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
                    selection_source=source,
                )
                placements.append(placement)
                instance_counter += 1
                placed = True
                break
            if not placed:
                dropped_slots += 1

    if not placements:
        raise RuntimeError(
            "Street composition produced zero placements. "
            "Try larger length/density or check category coverage in manifest."
        )

    scene = _build_base_scene(config=config)
    _add_instance_meshes(scene=scene, placements=placements, mesh_cache=mesh_cache)
    outputs = _export_scene(scene=scene, out_dir=out_dir, export_format=export_format)

    layout_path = (out_dir / "scene_layout.json").resolve()
    layout_payload = {
        "query": config.query,
        "config": config.to_dict(),
        "summary": {
            "instance_count": len(placements),
            "dropped_slots": int(dropped_slots),
        },
        "placements": [placement.to_dict() for placement in placements],
        "outputs": outputs,
    }
    layout_path.write_text(json.dumps(layout_payload, indent=2, ensure_ascii=True), encoding="utf-8")

    outputs["scene_layout"] = str(layout_path)
    return StreetComposeResult(
        query=config.query,
        instance_count=len(placements),
        dropped_slots=int(dropped_slots),
        placements=placements,
        outputs=outputs,
    )
