"""Street-level scene composition utilities for M3."""

from __future__ import annotations

import json
import logging
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from .design_rules import load_constraint_set
from .embedder import ClipTextEmbedder
from .entrance_analysis import (
    CarriagewayBoundary,
    PlacedAssetRegistry,
    evaluate_all_entrances,
    score_entrance_impact,
)
from .eval_metrics import (
    compute_balance_score,
    compute_cross_section_feasibility,
    compute_dropped_slot_rate,
    compute_editability,
    compute_explainability,
    compute_latency_ms_per_instance,
    compute_overlap_rate,
    compute_rule_satisfaction_rate,
    compute_spacing_uniformity,
    compute_style_consistency,
    compute_topology_validity,
    evaluate_topk_category_hits,
)
from .index_store import FaissIndexStore
from .layout_features import CandidateDescriptor, PolicyFeatureContext, vectorize_slot_candidates
from .layout_policy import LayoutPolicyRuntime
from .layout_solver import LayoutSolverRuntime, solve_layout
from .spatial_features import build_spatial_context, compute_slot_distances
from .osm_segment_graph import build_segment_graph
from .poi_taxonomy import (
    CANONICAL_FIRE_POI,
    canonicalize_poi_type,
    asset_backed_poi_anchor_counts,
    asset_category_for_poi,
    core_poi_count,
    extract_poi_points_by_type,
    nonempty_poi_points,
    normalize_poi_counts,
    poi_plot_config,
    poi_weighted_score,
    qualifies_poi_counts,
)
from .program_generator import ProgramGeneratorRuntime
from .street_priors import DEFAULT_CATEGORIES, DEFAULT_SPACING_M, SIDE_PREF
from .street_program import infer_street_program
from .types import (
    InventorySummary,
    LayoutSolverInput,
    ProgramGenerationInput,
    StreetComposeConfig,
    StreetComposeResult,
    StreetPlacement,
)

SOFTMAX_TEMPERATURE = 0.12
CATEGORY_NO_REPEAT_FIRST = True
FILL_PRIORITY = True


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
    # -- M5 validation --
    if config.layout_mode not in ("template", "osm"):
        raise ValueError("layout_mode must be 'template' or 'osm'")
    if config.constraint_mode not in ("off", "soft"):
        raise ValueError("constraint_mode must be 'off' or 'soft'")
    if config.layout_mode == "osm":
        if config.aoi_bbox is None or len(config.aoi_bbox) != 4:
            raise ValueError("aoi_bbox must be a 4-element tuple (min_lon, min_lat, max_lon, max_lat) when layout_mode='osm'")
    if not 0.0 <= config.constraint_weight <= 1.0:
        raise ValueError("constraint_weight must be in [0.0, 1.0]")
    if not 0.0 <= config.constraint_veto_threshold <= 1.0:
        raise ValueError("constraint_veto_threshold must be in [0.0, 1.0]")
    if str(config.program_generator).strip().lower() not in {"heuristic_v1", "learned_v1"}:
        raise ValueError("program_generator must be 'heuristic_v1' or 'learned_v1'")
    if str(config.layout_solver).strip().lower() not in {"banded", "milp_template_v1"}:
        raise ValueError("layout_solver must be 'banded' or 'milp_template_v1'")
    if float(getattr(config, "segment_length_m", 12.0)) <= 0.0:
        raise ValueError("segment_length_m must be > 0")


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


def _sample_pose_for_slot(
    *,
    slot_x_center: float,
    slot_z_center: float,
    slot_side: str,
    slot_spacing_m: float,
    band_width_m: float,
    length_m: float,
    rng: random.Random,
) -> Tuple[float, float, float]:
    jitter_x = min(1.5, max(0.25, 0.2 * float(slot_spacing_m)))
    min_x = -float(length_m) / 2.0 + 0.5
    max_x = float(length_m) / 2.0 - 0.5
    x = float(np.clip(float(slot_x_center) + rng.uniform(-jitter_x, jitter_x), min_x, max_x))

    z_jitter = max(0.1, float(band_width_m) * 0.18)
    z = float(slot_z_center) + rng.uniform(-z_jitter, z_jitter)

    if slot_side == "left":
        yaw_base = 180.0
    elif slot_side == "right":
        yaw_base = 0.0
    else:
        yaw_base = 0.0
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
    used_asset_ids: set[str],
    rng: random.Random,
    placement_policy: str = "rule",
    policy_runtime: Optional[LayoutPolicyRuntime] = None,
    policy_temperature: float = SOFTMAX_TEMPERATURE,
    feature_context: Optional[PolicyFeatureContext] = None,
    return_details: bool = False,
) -> Tuple[Dict[str, str], float, str] | Tuple[Dict[str, str], float, str, Dict[str, object]]:
    def _softmax_weights(scores: List[float], temperature: float) -> List[float]:
        if not scores:
            return []
        temp = max(float(temperature), 1e-6)
        arr = np.asarray(scores, dtype=np.float64)
        shifted = (arr - float(arr.max())) / temp
        weights = np.exp(shifted)
        total = float(weights.sum())
        if not np.isfinite(total) or total <= 0.0:
            return [1.0 / len(scores)] * len(scores)
        return (weights / total).tolist()

    def _pick_weighted(
        candidates: List[Tuple[Dict[str, str], float]],
        temperature: float,
    ) -> Tuple[Dict[str, str], float, int]:
        scores = [float(score) for _, score in candidates]
        weights = _softmax_weights(scores, temperature)
        pick_idx = rng.choices(range(len(candidates)), weights=weights, k=1)[0]
        row, score = candidates[pick_idx]
        return row, float(score), int(pick_idx)

    def _pick_with_policy(candidates: List[Tuple[Dict[str, str], float]]) -> Tuple[Dict[str, str], float, int]:
        if not candidates:
            raise RuntimeError("Policy candidate set cannot be empty.")
        if policy_runtime is None or feature_context is None:
            row, score, idx = _pick_weighted(candidates, policy_temperature)
            return row, score, idx

        candidate_desc = [
            CandidateDescriptor(asset_id=row["asset_id"], category=row["category"], score=float(score))
            for row, score in candidates
        ]
        features = vectorize_slot_candidates(feature_context, candidate_desc)
        logits = policy_runtime.score_candidates(features)
        weights = _softmax_weights(logits.tolist(), policy_temperature)
        pick_idx = int(rng.choices(range(len(candidates)), weights=weights, k=1)[0])
        row, score = candidates[pick_idx]
        return row, float(score), pick_idx

    slot_query = f"{query}, {category} street asset"
    query_embedding = embedder.encode_texts([slot_query])
    hits = index_store.search(query_embedding, topk=max(1, int(topk)))[0]
    matching_hits: List[Tuple[Dict[str, str], float]] = []
    all_hits: List[Dict[str, object]] = []
    for hit in hits:
        row = asset_by_id.get(hit.asset_id)
        if row is not None:
            all_hits.append(
                {
                    "asset_id": row["asset_id"],
                    "category": row["category"],
                    "score": float(hit.score),
                }
            )
        if row is not None and row["category"] == category:
            matching_hits.append((row, float(hit.score)))

    top3_hit = any(str(item.get("category", "")).strip().lower() == category for item in all_hits[:3])

    decision_payload: Dict[str, object] = {
        "candidates": all_hits,
        "chosen_index": -1,
        "top3_hit": bool(top3_hit),
    }

    if matching_hits:
        available_hits = [candidate for candidate in matching_hits if candidate[0]["asset_id"] not in used_asset_ids]
        if CATEGORY_NO_REPEAT_FIRST and available_hits:
            if placement_policy == "learned":
                row, score, local_idx = _pick_with_policy(available_hits)
                source = "policy_softmax"
            else:
                row, score, local_idx = _pick_weighted(available_hits, policy_temperature)
                source = "faiss_softmax"
            decision_payload["chosen_index"] = int(local_idx)
            if return_details:
                return row, score, source, decision_payload
            return row, score, source
        if FILL_PRIORITY:
            if placement_policy == "learned":
                row, score, local_idx = _pick_with_policy(matching_hits)
                source = "policy_relaxed_repeat"
            else:
                row, score, local_idx = _pick_weighted(matching_hits, policy_temperature)
                source = "faiss_relaxed_repeat"
            decision_payload["chosen_index"] = int(local_idx)
            if return_details:
                return row, score, source, decision_payload
            return row, score, source

    if not category_pool:
        raise RuntimeError(f"empty category pool: {category}")

    available_pool = [row for row in category_pool if row["asset_id"] not in used_asset_ids]
    if CATEGORY_NO_REPEAT_FIRST and available_pool:
        row = rng.choice(available_pool)
        if return_details:
            decision_payload["chosen_index"] = 0
            return row, 0.0, "fallback_pool", decision_payload
        return row, 0.0, "fallback_pool"
    if FILL_PRIORITY:
        row = rng.choice(category_pool)
        if return_details:
            decision_payload["chosen_index"] = 0
            return row, 0.0, "fallback_pool", decision_payload
        return row, 0.0, "fallback_pool"

    raise RuntimeError(
        f"Unable to pick candidate for category '{category}' from FAISS or fallback pool."
    )


def _build_base_scene(
    length_m: float,
    road_width_m: float,
    left_side_width_m: float,
    right_side_width_m: float,
):
    trimesh = _require_trimesh()
    scene = trimesh.Scene()
    road = trimesh.creation.box(extents=(length_m, 0.06, road_width_m))
    road.visual.face_colors = [65, 68, 72, 255]
    road.apply_translation([0.0, -0.03, 0.0])
    scene.add_geometry(road, node_name="road_slab")

    sidewalk_color = [165, 168, 172, 255]
    if left_side_width_m > 0.0:
        sidewalk_left = trimesh.creation.box(extents=(length_m, 0.08, left_side_width_m))
        sidewalk_left.visual.face_colors = sidewalk_color
        sidewalk_left.apply_translation([0.0, -0.04, road_width_m / 2.0 + left_side_width_m / 2.0])
        scene.add_geometry(sidewalk_left, node_name="sidewalk_left")

    if right_side_width_m > 0.0:
        sidewalk_right = trimesh.creation.box(extents=(length_m, 0.08, right_side_width_m))
        sidewalk_right.visual.face_colors = sidewalk_color
        sidewalk_right.apply_translation([0.0, -0.04, -road_width_m / 2.0 - right_side_width_m / 2.0])
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


# ---------------------------------------------------------------------------
# M5: OSM pose sampling and scene building
# ---------------------------------------------------------------------------

def _sample_pose_osm(
    category: str,
    placement_ctx: object,
    rng: random.Random,
    anchor_position_xz: Optional[Tuple[float, float]] = None,
) -> Optional[Tuple[float, float, float]]:
    """Sample a (x, z, yaw_deg) pose inside the sidewalk zone of *placement_ctx*."""
    from .placement_zones import compute_facing_angle, sample_slot_on_sidewalk

    if anchor_position_xz is not None:
        point = (float(anchor_position_xz[0]), float(anchor_position_xz[1]))
    else:
        point = sample_slot_on_sidewalk(placement_ctx.sidewalk_zone, rng)  # type: ignore[attr-defined]
    if point is None:
        return None
    x, z = point
    yaw = compute_facing_angle(point, placement_ctx.carriageway)  # type: ignore[attr-defined]
    return x, z, yaw


def _build_osm_base_scene(placement_ctx: object):
    """Build a trimesh Scene with carriageway + sidewalk extruded slabs from OSM geometry."""
    trimesh = _require_trimesh()
    import numpy as _np
    scene = trimesh.Scene()

    carriageway = placement_ctx.carriageway  # type: ignore[attr-defined]
    sidewalk_zone = placement_ctx.sidewalk_zone  # type: ignore[attr-defined]

    def _extrude_polygon(geom, height: float, color, name_prefix: str) -> None:
        """Extrude a shapely geometry into a thin 3D slab and add to scene.

        ``extrude_polygon`` maps the 2-D polygon (x_east, y_north) to mesh
        (X, Y) and extrudes along Z (0 … height).  The scene convention is
        **Y-up** (XZ = ground), so we swap Y↔Z after extrusion:
            X_3d = x_east,  Y_3d = z_extrude − height,  Z_3d = y_north
        This puts the top surface at Y = 0 with the road lying flat on XZ.
        """
        from shapely.geometry import MultiPolygon, Polygon as ShapelyPolygon
        polygons = []
        if isinstance(geom, ShapelyPolygon):
            polygons = [geom]
        elif isinstance(geom, MultiPolygon):
            polygons = list(geom.geoms)
        for idx, poly in enumerate(polygons):
            if poly.is_empty:
                continue
            try:
                mesh = trimesh.creation.extrude_polygon(poly, height)
                # Swap Y↔Z so road lies flat on XZ ground plane (Y-up)
                verts = mesh.vertices.copy()
                old_y = verts[:, 1].copy()   # was northing
                old_z = verts[:, 2].copy()   # was extrusion 0..height
                verts[:, 1] = old_z - height  # Y = extrusion shifted → top at Y=0
                verts[:, 2] = old_y           # Z = northing
                mesh.vertices = verts
                mesh.fix_normals()
                mesh.visual.face_colors = color
                scene.add_geometry(mesh, node_name=f"{name_prefix}_{idx}")
            except (ValueError, RuntimeError, IndexError):
                logger.debug("Skipping degenerate %s polygon %d", name_prefix, idx)
                continue

    if not carriageway.is_empty:
        _extrude_polygon(carriageway, 0.06, [65, 68, 72, 255], "carriageway")
    if not sidewalk_zone.is_empty:
        _extrude_polygon(sidewalk_zone, 0.08, [165, 168, 172, 255], "sidewalk")

    return scene


def _add_poi_markers_and_zones(scene, poi_points_by_type_or_exclusion_zones, exclusion_zones=None) -> None:
    """Add POI marker spheres and exclusion-zone rings to a trimesh Scene.

    Coordinate convention (Y-up): X_3d = x_east, Y_3d = height, Z_3d = y_north.
    """
    if exclusion_zones is None:
        poi_points_by_type = {}
        exclusion_zones = poi_points_by_type_or_exclusion_zones
    else:
        poi_points_by_type = poi_points_by_type_or_exclusion_zones
    normalized_points = nonempty_poi_points(poi_points_by_type)
    if not exclusion_zones and not normalized_points:
        return
    trimesh = _require_trimesh()
    from shapely.geometry import Point as ShapelyPoint

    _BASE_COLOR = [25, 25, 30, 255]
    _RING_COLOR = [255, 70, 70, 48]  # lighter translucent red

    seen_positions: dict = {}  # (poi_type, x, y) -> idx to avoid duplicate markers

    def _build_marker_mesh(poi_type: str):
        poi_type = canonicalize_poi_type(poi_type)
        if poi_type == "entrance":
            mesh = trimesh.creation.cone(radius=0.55, height=1.8, sections=24)
            mesh.apply_translation([0.0, 0.9, 0.0])
            return mesh
        if poi_type == CANONICAL_FIRE_POI:
            mesh = trimesh.creation.cylinder(radius=0.42, height=1.6, sections=24)
            mesh.apply_translation([0.0, 0.8, 0.0])
            return mesh
        if poi_type == "bus_stop":
            mesh = trimesh.creation.box(extents=(0.95, 2.2, 0.38))
            mesh.apply_translation([0.0, 1.1, 0.0])
            return mesh
        if poi_type in {"crossing", "traffic_signals"}:
            mesh = trimesh.creation.box(extents=(0.8, 1.6, 0.18))
            mesh.apply_translation([0.0, 0.8, 0.0])
            return mesh
        if poi_type in {"parking_entrance", "subway_entrance"}:
            mesh = trimesh.creation.cone(radius=0.42, height=1.5, sections=18)
            mesh.apply_translation([0.0, 0.75, 0.0])
            return mesh
        if poi_type == "post_box":
            mesh = trimesh.creation.box(extents=(0.52, 1.2, 0.52))
            mesh.apply_translation([0.0, 0.6, 0.0])
            return mesh
        if poi_type == "waste_basket":
            mesh = trimesh.creation.cylinder(radius=0.35, height=0.9, sections=20)
            mesh.apply_translation([0.0, 0.45, 0.0])
            return mesh
        if poi_type == "bollard":
            mesh = trimesh.creation.cylinder(radius=0.18, height=1.0, sections=16)
            mesh.apply_translation([0.0, 0.5, 0.0])
            return mesh
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=0.5)
        mesh.apply_translation([0.0, 0.5, 0.0])
        return mesh

    def _add_marker(poi_type: str, point: Tuple[float, float]) -> None:
        key = (poi_type, point[0], point[1])
        if key in seen_positions:
            return
        idx = len(seen_positions)
        seen_positions[key] = idx
        x_east, y_north = point
        marker = _build_marker_mesh(poi_type)
        color_hex = str(poi_plot_config(poi_type)["color"]).lstrip("#")
        marker.visual.face_colors = [
            int(color_hex[0:2], 16),
            int(color_hex[2:4], 16),
            int(color_hex[4:6], 16),
            255,
        ]
        marker.apply_translation([x_east, 0.0, y_north])
        scene.add_geometry(marker, node_name=f"poi_{poi_type}_{idx}")

        base = trimesh.creation.cylinder(radius=0.72, height=0.08, sections=24)
        base.visual.face_colors = _BASE_COLOR
        base.apply_translation([x_east, 0.04, y_north])
        scene.add_geometry(base, node_name=f"poi_base_{poi_type}_{idx}")

    for poi_type, points in normalized_points.items():
        for point in points:
            _add_marker(poi_type, point)

    for zone in exclusion_zones:
        key = (zone.poi_type, zone.position_xz[0], zone.position_xz[1])
        _add_marker(zone.poi_type, zone.position_xz)
        idx = seen_positions[key]
        # Exclusion zone ring (annulus via Shapely buffer difference)
        r = zone.radius_m
        if r < 0.15:
            continue
        inner_r = max(r - 0.08, 0.0)
        x_east, y_north = zone.position_xz
        ring_poly = ShapelyPoint(x_east, y_north).buffer(r).difference(
            ShapelyPoint(x_east, y_north).buffer(inner_r)
        )
        if ring_poly.is_empty:
            continue
        try:
            ring_mesh = trimesh.creation.extrude_polygon(ring_poly, 0.02)
            # Apply same Y↔Z swap as _extrude_polygon
            verts = ring_mesh.vertices.copy()
            old_y = verts[:, 1].copy()
            old_z = verts[:, 2].copy()
            verts[:, 1] = old_z + 0.01
            verts[:, 2] = old_y
            ring_mesh.vertices = verts
            ring_mesh.fix_normals()
            ring_mesh.visual.face_colors = _RING_COLOR
            scene.add_geometry(ring_mesh, node_name=f"exclusion_{zone.poi_type}_{idx}")
        except (ValueError, RuntimeError, IndexError):
            logger.debug("Skipping degenerate exclusion ring for %s", zone.rule_name)
            continue


def _serialize_osm_geometry(placement_ctx: object) -> dict:
    """Extract simplified polygon exterior rings for 2D visualization in layout JSON."""
    from shapely.geometry import MultiPolygon, Polygon as ShapelyPolygon

    def _extract_rings(geom, tolerance: float = 0.5, max_points: int = 200):
        polys: list = []
        if isinstance(geom, ShapelyPolygon):
            polys = [geom]
        elif isinstance(geom, MultiPolygon):
            polys = list(geom.geoms)
        rings: list = []
        for poly in polys:
            if poly.is_empty:
                continue
            simplified = poly.simplify(tolerance)
            coords = list(simplified.exterior.coords)
            if len(coords) > max_points:
                simplified = poly.simplify(tolerance * 2)
                coords = list(simplified.exterior.coords)
            rings.append([[round(c[0], 2), round(c[1], 2)] for c in coords])
        return rings

    result: dict = {}
    carriageway = placement_ctx.carriageway  # type: ignore[attr-defined]
    sidewalk = placement_ctx.sidewalk_zone  # type: ignore[attr-defined]
    if not carriageway.is_empty:
        result["carriageway_rings"] = _extract_rings(carriageway)
    if not sidewalk.is_empty:
        result["sidewalk_rings"] = _extract_rings(sidewalk)
    aoi = getattr(placement_ctx, "aoi_polygon", None)
    if aoi is not None and not aoi.is_empty:
        b = aoi.bounds  # (minx, miny, maxx, maxy)
        result["aoi_bbox_m"] = [round(v, 2) for v in b]
    return result


def _slot_spatial_kwargs(slot, spatial_ctx) -> dict:
    """Compute spatial distance fields for a PolicyFeatureContext."""
    if spatial_ctx is None:
        return {}
    sd = compute_slot_distances((float(slot.x_center_m), float(slot.z_center_m)), spatial_ctx)
    return {
        "dist_to_road_edge_m": sd.dist_to_road_edge_m,
        "dist_to_nearest_junction_m": sd.dist_to_nearest_junction_m,
        "dist_to_nearest_entrance_m": sd.dist_to_nearest_entrance_m,
    }


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
    placement_policy: str = "rule",
    policy_ckpt: Optional[Path] = None,
    program_ckpt: Optional[Path] = None,
    policy_temperature: float = SOFTMAX_TEMPERATURE,
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
    policy_mode = str(placement_policy).strip().lower()
    if policy_mode not in {"rule", "learned"}:
        raise ValueError("placement_policy must be 'rule' or 'learned'")

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

    policy_runtime: Optional[LayoutPolicyRuntime] = None
    policy_used = "rule"
    policy_fallback_reason = ""
    if policy_mode == "learned":
        ckpt_path = Path(policy_ckpt).expanduser().resolve() if policy_ckpt else None
        if ckpt_path is None or not ckpt_path.exists():
            policy_fallback_reason = (
                "Policy checkpoint missing; fallback to rule policy."
                if ckpt_path is None
                else f"Policy checkpoint not found: {ckpt_path}. Fallback to rule policy."
            )
        else:
            try:
                policy_runtime = LayoutPolicyRuntime.from_checkpoint(ckpt_path, device=device)
                policy_used = "learned"
            except Exception as exc:
                policy_fallback_reason = f"Policy runtime load failed ({exc}); fallback to rule policy."

    program_runtime = ProgramGeneratorRuntime(backend="heuristic_v1")
    program_used = "heuristic_v1"
    program_fallback_reason = ""
    if str(config.program_generator).strip().lower() == "learned_v1":
        ckpt_path = Path(program_ckpt).expanduser().resolve() if program_ckpt else None
        if ckpt_path is None or not ckpt_path.exists():
            program_fallback_reason = (
                "Program generator checkpoint missing; fallback to heuristic_v1."
                if ckpt_path is None
                else f"Program generator checkpoint not found: {ckpt_path}. Fallback to heuristic_v1."
            )
        else:
            try:
                program_runtime = ProgramGeneratorRuntime.from_checkpoint(ckpt_path, device=device)
                program_used = "learned_v1"
            except Exception as exc:
                program_fallback_reason = f"Program generator load failed ({exc}); fallback to heuristic_v1."

    rng = random.Random(int(config.seed))
    placements: List[StreetPlacement] = []
    existing_bboxes: List[Tuple[float, float, float, float]] = []
    used_asset_ids_by_category: Dict[str, set[str]] = {category: set() for category in DEFAULT_CATEGORIES}
    retrieval_predictions: List[Dict[str, object]] = []
    dropped_slots = 0
    instance_counter = 1
    clearance = 0.2
    start_perf = time.perf_counter()

    placement_ctx = None
    projected = None
    effective_poi_counts: Dict[str, int] = normalize_poi_counts({})
    if config.layout_mode == "osm":
        from .osm_ingest import fetch_osm_data, parse_osm_features, project_to_local
        from .placement_zones import evaluate_projected_road_context

        raw = fetch_osm_data(bbox=config.aoi_bbox, cache_dir=Path(config.osm_cache_dir))
        features = parse_osm_features(raw)
        projected = project_to_local(features, config.aoi_bbox)
        projected, placement_ctx, effective_poi_counts = evaluate_projected_road_context(projected, config)
        if not qualifies_poi_counts(effective_poi_counts):
            raise RuntimeError(
                "Selected road does not retain enough effective POIs after compose filtering "
                "(requires weighted POI score >= 2.0 and at least 1 core POI)."
            )

    poi_ctx = None
    rule_set = None
    from .poi_rules import PoiContext, build_poi_context
    if placement_ctx is not None:
        poi_ctx = build_poi_context(placement_ctx)
    else:
        poi_ctx = PoiContext((), (), ())

    if config.constraint_mode == "soft":
        from .poi_rules import load_rule_set
        from .poi_rules import score_placement as _score_placement

        rule_set = load_rule_set(config.poi_rule_set)

    # --- Entrance analysis registry ---
    entrance_registry = PlacedAssetRegistry()
    entrance_points_xz: Tuple[Tuple[float, float], ...] = ()
    carriageway_boundary: Optional[CarriagewayBoundary] = None
    if poi_ctx is not None and poi_ctx.entrance_points_xz:
        entrance_points_xz = poi_ctx.entrance_points_xz
    if placement_ctx is not None and hasattr(placement_ctx, "carriageway_polygon") and placement_ctx.carriageway_polygon is not None:
        carriageway_boundary = CarriagewayBoundary.from_polygon(placement_ctx.carriageway_polygon)
    else:
        carriageway_boundary = CarriagewayBoundary.from_template(
            road_width_m=float(config.road_width_m),
            length_m=float(config.length_m),
        )

    inventory_summary = InventorySummary(
        category_counts={category: len(pool) for category, pool in category_to_rows.items() if pool},
        asset_ids_by_category={
            category: tuple(row["asset_id"] for row in pool)
            for category, pool in category_to_rows.items()
            if pool
        },
    )
    if config.layout_mode == "osm":
        for poi_type, required_count in asset_backed_poi_anchor_counts(
            extract_poi_points_by_type(placement_ctx) if placement_ctx is not None else {}
        ).items():
            if int(required_count) <= 0:
                continue
            category = asset_category_for_poi(poi_type)
            if category and category not in inventory_summary.category_counts:
                raise RuntimeError(
                    f"Selected road has {poi_type} POIs but the asset inventory has no {category} category."
                )
    road_segment_graph = build_segment_graph(projected, config) if projected is not None else None

    # --- Spatial context for distance features (M8) ---
    spatial_ctx = build_spatial_context(config, road_segment_graph, poi_ctx)

    program_result = program_runtime.generate(
        ProgramGenerationInput(
            query=config.query,
            compose_config=config,
            available_categories=tuple(available_categories),
            constraint_profile=str(config.design_rule_profile),
            placement_context=placement_ctx,
            inventory_summary=inventory_summary,
            road_segment_graph=road_segment_graph,
            poi_context=poi_ctx,
        )
    )
    if program_result.backend_used == "learned_v1":
        program_used = "learned_v1"
    if program_result.fallback_reason and not program_fallback_reason:
        program_fallback_reason = program_result.fallback_reason
    street_program = program_result.program
    constraint_set = load_constraint_set(config.design_rule_profile)
    solver_runtime = LayoutSolverRuntime(backend=str(config.layout_solver))
    solver_result = solver_runtime.solve(
        LayoutSolverInput(
            program=street_program,
            config=config,
            available_categories=tuple(available_categories),
            constraint_set=constraint_set,
            placement_context=placement_ctx,
            inventory_summary=inventory_summary,
            road_segment_graph=road_segment_graph,
        )
    )
    resolved_program = solver_result.resolved_program
    slot_plans = list(solver_result.slot_plans)
    for poi_type, required_count in asset_backed_poi_anchor_counts(
        extract_poi_points_by_type(placement_ctx) if placement_ctx is not None else {}
    ).items():
        category = asset_category_for_poi(poi_type)
        actual_count = sum(
            1
            for slot in slot_plans
            if slot.category == category and slot.anchor_poi_type == poi_type
        )
        if int(required_count) > int(actual_count):
            raise RuntimeError(
                f"Layout solver did not preserve all required POI-backed {category} slots."
            )
    if not slot_plans:
        raise RuntimeError(
            "Layout solver produced zero slots. "
            "Check the design rule profile, asset coverage, or scene length."
        )

    band_by_name = {band.name: band for band in resolved_program.bands}
    category_slot_counts: Dict[str, int] = {}
    for slot in slot_plans:
        category_slot_counts[slot.category] = category_slot_counts.get(slot.category, 0) + 1
    total_scene_slots = max(len(slot_plans), 1)
    placed_score_sums: Dict[str, float] = {category: 0.0 for category in DEFAULT_CATEGORIES}
    placed_counts: Dict[str, int] = {category: 0 for category in DEFAULT_CATEGORIES}
    slot_index_by_category: Dict[str, int] = {category: 0 for category in DEFAULT_CATEGORIES}

    for slot in slot_plans:
        category = slot.category
        pool = category_to_rows.get(category, [])
        if not pool:
            dropped_slots += 1
            continue

        feature_ctx = PolicyFeatureContext(
            query=config.query,
            category=category,
            slot_idx=int(slot_index_by_category.get(category, 0)),
            slot_x=float(slot.x_center_m),
            slot_z=float(slot.z_center_m),
            length_m=float(config.length_m),
            road_width_m=float(resolved_program.road_width_m),
            sidewalk_width_m=float(resolved_program.sidewalk_width_m),
            lane_count=int(resolved_program.lane_count),
            density=float(config.density),
            topk=int(config.topk_per_category),
            used_asset_ids=set(used_asset_ids_by_category.setdefault(category, set())),
            placed_count_in_category=placed_counts.get(category, 0),
            total_slots_in_category=category_slot_counts.get(category, 1),
            category_pool_size=len(pool),
            mean_score_placed=(
                placed_score_sums[category] / placed_counts[category]
                if placed_counts.get(category, 0) > 0
                else 0.0
            ),
            total_slots_in_scene=total_scene_slots,
            **_slot_spatial_kwargs(slot, spatial_ctx),
        )
        row, score, source, decision_details = _pick_category_candidate(
            query=config.query,
            category=category,
            topk=config.topk_per_category,
            embedder=embedder,
            index_store=index_store,
            asset_by_id=asset_by_id,
            category_pool=pool,
            used_asset_ids=used_asset_ids_by_category.setdefault(category, set()),
            rng=rng,
            placement_policy=policy_used,
            policy_runtime=policy_runtime,
            policy_temperature=policy_temperature,
            feature_context=feature_ctx,
            return_details=True,
        )
        retrieval_predictions.append(
            {
                "target_category": category,
                "hits": decision_details.get("candidates", []),
            }
        )

        band = band_by_name.get(slot.band_name)
        if band is None:
            dropped_slots += 1
            slot_index_by_category[category] = slot_index_by_category.get(category, 0) + 1
            continue

        entry = mesh_cache[row["asset_id"]]
        placed = False
        trial_candidates: List[Tuple[float, float, float, Tuple[float, float, float, float], float, float, Tuple[str, ...]]] = []
        anchor_position = getattr(slot, "anchor_position_xz", None)
        for _trial_idx in range(int(config.max_trials_per_slot)):
            if config.layout_mode == "osm" and placement_ctx is not None:
                pose = _sample_pose_osm(category, placement_ctx, rng, anchor_position_xz=anchor_position)
                if pose is None:
                    continue
                x, z, yaw_deg = pose
            else:
                x, z, yaw_deg = _sample_pose_for_slot(
                    slot_x_center=float(slot.x_center_m),
                    slot_z_center=float(slot.z_center_m),
                    slot_side=str(slot.side),
                    slot_spacing_m=float(slot.spacing_m),
                    band_width_m=float(band.width_m),
                    length_m=float(config.length_m),
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

            c_penalty, c_feasibility, c_violated = 0.0, 1.0, ()
            if config.constraint_mode == "soft" and rule_set is not None and poi_ctx is not None:
                cr = _score_placement((x, z), category, rule_set, poi_ctx)
                if cr.penalty > config.constraint_veto_threshold:
                    continue
                c_penalty = cr.penalty
                c_feasibility = cr.feasibility_score
                c_violated = cr.violated_rules

            # Entrance openness / noise-shielding impact
            if entrance_points_xz and carriageway_boundary is not None:
                e_penalty, e_bonus, e_violated = score_entrance_impact(
                    candidate_xz=(x, z),
                    candidate_category=category,
                    candidate_bbox_xz=(bbox[0], bbox[1], bbox[2], bbox[3]),
                    entrance_points_xz=entrance_points_xz,
                    registry=entrance_registry,
                    carriageway_boundary=carriageway_boundary,
                )
                c_penalty += e_penalty - e_bonus
                c_feasibility *= math.exp(-e_penalty)
                c_violated = tuple(list(c_violated) + list(e_violated))

            trial_candidates.append((x, z, yaw_deg, bbox, c_penalty, c_feasibility, c_violated))
            if config.constraint_mode != "soft" or anchor_position is not None:
                break

        if trial_candidates:
            if config.constraint_mode == "soft" and len(trial_candidates) > 1:
                score_norm = min(1.0, max(0.0, float(score)))
                best = max(
                    trial_candidates,
                    key=lambda candidate: (1.0 - config.constraint_weight) * score_norm + config.constraint_weight * candidate[5],
                )
            else:
                best = trial_candidates[0]

            bx, bz, byaw, bbbox, bpenalty, bfeas, bviolated = best
            existing_bboxes.append(bbbox)
            y = -entry.min_y * scale
            placements.append(
                StreetPlacement(
                    instance_id=f"inst_{instance_counter:04d}",
                    asset_id=row["asset_id"],
                    category=category,
                    score=float(score),
                    position_xyz=[float(bx), float(y), float(bz)],
                    yaw_deg=float(byaw),
                    scale=float(scale),
                    bbox_xz=[float(bbbox[0]), float(bbbox[1]), float(bbbox[2]), float(bbbox[3])],
                    selection_source=source,
                    constraint_penalty=float(bpenalty),
                    feasibility_score=float(bfeas),
                    violated_rules=bviolated,
                    **_slot_spatial_kwargs(slot, spatial_ctx),
                )
            )
            used_asset_ids_by_category.setdefault(category, set()).add(row["asset_id"])
            placed_score_sums[category] = placed_score_sums.get(category, 0.0) + float(score)
            placed_counts[category] = placed_counts.get(category, 0) + 1
            instance_counter += 1
            placed = True
            entrance_registry.add(
                position_xz=(float(bx), float(bz)),
                category=category,
                bbox_xz=(float(bbbox[0]), float(bbbox[1]), float(bbbox[2]), float(bbbox[3])),
            )
        elif anchor_position is not None:
            raise RuntimeError(f"Unable to place required POI-backed asset for category '{category}'.")

        if not placed:
            dropped_slots += 1
        slot_index_by_category[category] = slot_index_by_category.get(category, 0) + 1

    if not placements:
        raise RuntimeError(
            "Street composition produced zero placements. "
            "Try a different design-rule profile, larger length/density, or check category coverage in manifest."
        )

    if config.layout_mode == "osm" and placement_ctx is not None:
        scene = _build_osm_base_scene(placement_ctx)
    else:
        left_side_width = sum(float(band.width_m) for band in resolved_program.bands if band.side == "left")
        right_side_width = sum(float(band.width_m) for band in resolved_program.bands if band.side == "right")
        scene = _build_base_scene(
            length_m=float(config.length_m),
            road_width_m=float(resolved_program.road_width_m),
            left_side_width_m=float(left_side_width),
            right_side_width_m=float(right_side_width),
        )
    _add_instance_meshes(scene=scene, placements=placements, mesh_cache=mesh_cache)

    # Compute exclusion zones early so we can add 3D markers before export
    exclusion_zones: tuple = ()
    if rule_set is not None and poi_ctx is not None:
        from .poi_rules import build_exclusion_zones as _build_exclusion_zones
        exclusion_zones = _build_exclusion_zones(poi_ctx, rule_set)
        _add_poi_markers_and_zones(scene, extract_poi_points_by_type(poi_ctx, suffix="xz"), exclusion_zones)
    elif poi_ctx is not None:
        _add_poi_markers_and_zones(scene, extract_poi_points_by_type(poi_ctx, suffix="xz"), ())

    outputs = _export_scene(scene=scene, out_dir=out_dir, export_format=export_format)

    elapsed_ms_total = (time.perf_counter() - start_perf) * 1000.0
    unique_asset_count = len({placement.asset_id for placement in placements})
    diversity_ratio = float(unique_asset_count / len(placements)) if placements else 0.0
    dropped_slot_rate = compute_dropped_slot_rate(instance_count=len(placements), dropped_slots=int(dropped_slots))
    overlap_rate = compute_overlap_rate([placement.bbox_xz for placement in placements])
    retrieval_top3_category_hit = evaluate_topk_category_hits(retrieval_predictions, topk=3)
    latency_ms_per_instance = compute_latency_ms_per_instance(
        latency_ms_total=elapsed_ms_total,
        instance_count=len(placements),
    )

    placement_dicts = [placement.to_dict() for placement in placements]
    spacing_uniformity = compute_spacing_uniformity(placement_dicts)
    style_consistency = compute_style_consistency(placement_dicts)
    balance_score = compute_balance_score(placement_dicts)
    per_category_unique = {
        category: len({placement.asset_id for placement in placements if placement.category == category})
        for category in DEFAULT_CATEGORIES
        if any(placement.category == category for placement in placements)
    }
    selection_source_counts: Dict[str, int] = {}
    for placement in placements:
        selection_source_counts[placement.selection_source] = (
            selection_source_counts.get(placement.selection_source, 0) + 1
        )

    violations_total = sum(1 for placement in placements if placement.violated_rules)
    compliance_rate_total = 1.0 - (violations_total / len(placements)) if placements else 0.0
    avg_constraint_penalty = (
        sum(placement.constraint_penalty for placement in placements) / len(placements) if placements else 0.0
    )
    avg_feasibility_score = (
        sum(placement.feasibility_score for placement in placements) / len(placements) if placements else 1.0
    )
    rule_violation_counts: Dict[str, int] = {}
    for placement in placements:
        for rule_name in placement.violated_rules:
            rule_violation_counts[rule_name] = rule_violation_counts.get(rule_name, 0) + 1

    rule_satisfaction_rate = compute_rule_satisfaction_rate(solver_result.rule_evaluations)

    # --- Entrance analysis (post-placement) ---
    entrance_report = evaluate_all_entrances(
        entrance_points_xz=entrance_points_xz,
        registry=entrance_registry,
        carriageway_boundary=carriageway_boundary,
    )
    mean_entrance_openness = float(entrance_report.mean_openness)
    mean_noise_shielding = float(entrance_report.mean_shielding)
    topology_validity = compute_topology_validity(solver_result.topology_validity)
    cross_section_feasibility = compute_cross_section_feasibility(solver_result.cross_section_feasibility)
    editability = compute_editability(solver_result.edits)
    conflict_explainability = compute_explainability(solver_result.conflicts)
    rule_evaluation_counts: Dict[str, int] = {}
    for evaluation in solver_result.rule_evaluations:
        rule_evaluation_counts[evaluation.status] = rule_evaluation_counts.get(evaluation.status, 0) + 1

    layout_path = (out_dir / "scene_layout.json").resolve()
    layout_payload = {
        "query": config.query,
        "config": config.to_dict(),
        "program_generation": program_result.to_dict(),
        "street_program": resolved_program.to_dict(),
        "constraint_set": constraint_set.to_dict(),
        "solver": solver_result.to_dict(),
        "summary": {
            "instance_count": len(placements),
            "dropped_slots": int(dropped_slots),
            "dropped_slot_rate": float(dropped_slot_rate),
            "unique_asset_count": int(unique_asset_count),
            "diversity_ratio": float(diversity_ratio),
            "overlap_rate": float(overlap_rate),
            "retrieval_top3_category_hit": float(retrieval_top3_category_hit),
            "policy_used": policy_used,
            "latency_ms_total": float(elapsed_ms_total),
            "latency_ms_per_instance": float(latency_ms_per_instance),
            "per_category_unique": per_category_unique,
            "selection_source_counts": selection_source_counts,
            "layout_mode": config.layout_mode,
            "constraint_mode": config.constraint_mode,
            "aoi_bbox": list(config.aoi_bbox) if config.aoi_bbox else None,
            "compliance_rate_total": float(compliance_rate_total),
            "violations_total": int(violations_total),
            "rule_violation_counts": rule_violation_counts,
            "avg_constraint_penalty": float(avg_constraint_penalty),
            "avg_feasibility_score": float(avg_feasibility_score),
            "spacing_uniformity": float(spacing_uniformity),
            "style_consistency": float(style_consistency),
            "balance_score": float(balance_score),
            "design_rule_profile": str(config.design_rule_profile),
            "program_generator_requested": str(config.program_generator),
            "program_generator_used": str(program_result.backend_used),
            "layout_solver_requested": str(config.layout_solver),
            "layout_solver_used": str(solver_result.backend_used),
            "cross_section_type": str(resolved_program.cross_section_type),
            "rule_satisfaction_rate": float(rule_satisfaction_rate),
            "topology_validity": float(topology_validity),
            "cross_section_feasibility": float(cross_section_feasibility),
            "editability": float(editability),
            "conflict_explainability": float(conflict_explainability),
            "solver_edit_count": int(len(solver_result.edits)),
            "solver_conflict_count": int(len(solver_result.conflicts)),
            "rule_evaluation_counts": rule_evaluation_counts,
            "program_fallback_reason": program_fallback_reason,
            "solver_fallback_reason": str(solver_result.fallback_reason),
            "road_segment_graph_summary": solver_result.road_segment_graph_summary,
            "mean_entrance_openness": float(mean_entrance_openness),
            "mean_noise_shielding": float(mean_noise_shielding),
            "entrances_below_openness_threshold": int(entrance_report.entrances_below_openness_threshold),
            "min_entrance_openness": float(entrance_report.min_openness),
            "entrance_count": len(entrance_points_xz),
            "selected_road_osm_id": int(config.selected_road_osm_id) if config.selected_road_osm_id is not None else None,
            "selected_road_discovered_poi_count": (
                int(config.selected_road_discovered_poi_count)
                if config.selected_road_discovered_poi_count is not None
                else None
            ),
            "selected_road_discovered_poi_score": (
                float(config.selected_road_discovered_poi_score)
                if config.selected_road_discovered_poi_score is not None
                else None
            ),
            "selected_road_discovered_core_poi_count": (
                int(config.selected_road_discovered_core_poi_count)
                if config.selected_road_discovered_core_poi_count is not None
                else None
            ),
            "selected_road_effective_poi_count": int(sum(int(value) for value in effective_poi_counts.values())),
            "selected_road_effective_poi_score": float(poi_weighted_score(effective_poi_counts)),
            "selected_road_core_poi_count": int(core_poi_count(effective_poi_counts)),
            "observed_poi_counts": dict(resolved_program.observed_poi_counts),
            "spatial_context": {
                "junction_points_xz": [list(p) for p in spatial_ctx.junction_points_xz],
                "entrance_points_xz": [list(p) for p in spatial_ctx.entrance_points_xz],
                "bus_stop_points_xz": [list(p) for p in spatial_ctx.bus_stop_points_xz],
                "fire_points_xz": [list(p) for p in spatial_ctx.fire_points_xz],
                "poi_points_by_type_xz": {
                    poi_type: [list(point) for point in points]
                    for poi_type, points in nonempty_poi_points(spatial_ctx.poi_points_by_type_xz).items()
                },
                "road_half_width_m": float(spatial_ctx.road_half_width_m),
                "length_m": float(spatial_ctx.length_m),
            },
        },
        "placements": [placement.to_dict() for placement in placements],
        "outputs": outputs,
    }
    # Attach OSM polygon geometry for 2D visualization
    if config.layout_mode == "osm" and placement_ctx is not None:
        layout_payload["summary"]["osm_geometry"] = _serialize_osm_geometry(placement_ctx)

    # Attach POI exclusion zone data for visualization
    if exclusion_zones:
        layout_payload["summary"]["poi_exclusion_zones"] = [
            {
                "poi_type": z.poi_type,
                "position_xz": [round(z.position_xz[0], 3), round(z.position_xz[1], 3)],
                "radius_m": round(z.radius_m, 3),
                "rule_name": z.rule_name,
            }
            for z in exclusion_zones
        ]
        layout_payload["summary"]["poi_conflict_assets"] = [
            {
                "instance_id": p.instance_id,
                "category": p.category,
                "position_xz": [round(float(p.position_xyz[0]), 3), round(float(p.position_xyz[2]), 3)],
                "violated_rules": list(p.violated_rules),
                "constraint_penalty": round(float(p.constraint_penalty), 4),
            }
            for p in placements
            if p.violated_rules
        ]

    layout_path.write_text(json.dumps(layout_payload, indent=2, ensure_ascii=True), encoding="utf-8")

    outputs["scene_layout"] = str(layout_path)
    outputs["policy_used"] = policy_used
    outputs["design_rule_profile"] = str(config.design_rule_profile)
    outputs["program_cross_section_type"] = str(resolved_program.cross_section_type)
    outputs["program_generator_requested"] = str(config.program_generator)
    outputs["program_generator_used"] = str(program_result.backend_used)
    outputs["layout_solver_requested"] = str(config.layout_solver)
    outputs["layout_solver_used"] = str(solver_result.backend_used)
    if solver_result.fallback_reason:
        outputs["solver_fallback_reason"] = str(solver_result.fallback_reason)
    if policy_fallback_reason:
        outputs["policy_fallback_reason"] = policy_fallback_reason
    if program_fallback_reason:
        outputs["program_fallback_reason"] = program_fallback_reason
    return StreetComposeResult(
        query=config.query,
        instance_count=len(placements),
        dropped_slots=int(dropped_slots),
        placements=placements,
        outputs=outputs,
        street_program=resolved_program,
        solver_result=solver_result,
    )
