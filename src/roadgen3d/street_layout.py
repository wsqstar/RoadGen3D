"""Street-level scene composition utilities for M3."""

from __future__ import annotations

import gc
import hashlib
import json
import logging
import math
import random
import time
from collections import Counter
import dataclasses
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)

from .beauty import (
    apply_composition_pass,
    asset_generator_type,
    compute_presentation_report,
    curate_candidates,
    render_presentation_views,
    shape_program_for_style,
    style_palette,
    surface_roughness,
)
from .asset_scale import VALID_ASSET_SCALE_MODES, asset_scale_prior, compute_asset_scale, summarize_asset_scales
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
from .placement_field import (
    UniformSpatialHash,
    compose_candidate_energy,
    load_placement_field_config,
    pair_cutoff_radius_m,
    pair_interaction_scores,
    placement_field_path,
    placement_priority_rank,
    poi_attraction_score,
)
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
from .reference_annotation import VALID_FUNCTIONAL_ZONE_KINDS
from .poi_rules import load_rule_set
from .scene_graph_viz import build_scene_graph
from .scene_textures import (
    VALID_SCENE_TEXTURE_MODES,
    apply_default_scene_texture,
    create_scene_texture_tracker,
    scene_texture_pack_name,
)
from .services.scene_backends import (
    GroundMaterialBackend,
    ObjectAssetBackend,
    SkyBackend,
    collect_environment_source_datasets,
)
from .spatial_viz import (
    plot_poi_exclusion_overview,
    plot_zoning_grid_preview as plot_zoning_grid_preview_2d,
)
from .street_priors import CATEGORY_PLACEMENT_RANK, DEFAULT_CATEGORIES, DEFAULT_SPACING_M, SIDE_PREF
from .street_program import infer_street_program
from .street_band_semantics import (
    band_name_aliases,
    coerce_band_rule_kinds,
    detailed_strip_band_kind,
    detailed_strip_band_name,
    detailed_strip_kind_from_band_name,
    resolve_band_by_alias,
)
from .theme_buildings import (
    assign_theme_id_for_point,
    build_zoning_grid_preview,
    building_query,
    collect_building_footprints,
    generate_frontage_infill_footprints,
    generate_grid_growth_lots,
    infer_theme_segments,
    rerank_building_candidates,
    summarize_land_use_grid,
    theme_profile_style,
)
from .building_placement import (
    building_forbidden_geometry,
    resolve_building_pose,
)
from .types import (
    DEFAULT_BUILDING_FRONT_SETBACK_MAX_M,
    DEFAULT_BUILDING_FRONT_SETBACK_MIN_M,
    BuildingFootprint,
    BuildingPlacementPlan,
    GeneratedLot,
    InventorySummary,
    LayoutSlotPlan,
    LayoutSolverInput,
    LayoutSolverResult,
    ProductionStepRecord,
    ProgramGenerationInput,
    StreetBand,
    ThemeSegment,
    StreetComposeConfig,
    StreetComposeResult,
    StreetPlacement,
)

SOFTMAX_TEMPERATURE = 0.12
CATEGORY_NO_REPEAT_FIRST = True
FILL_PRIORITY = True

# Default maximum number of full meshes to keep in memory at once
# This limits memory usage while still allowing mesh reuse
DEFAULT_MAX_MESH_CACHE_SIZE = 20
DEFAULT_SKY_DOME_ASSET_ID = "objaverse_tree_a90b8cca57b44f5492e796cf94d64e80-sky-dome"
DEFAULT_SKY_DOME_MESH_PATH = (
    ROOT / "data" / "real" / "split_meshes" / f"{DEFAULT_SKY_DOME_ASSET_ID}.glb"
).resolve()
DEFAULT_SKY_DOME_DIMENSIONS_M = {
    "width": 96.1440,
    "height": 96.1379,
    "depth": 96.0366,
}
DEFAULT_SKY_DOME_MIN_DIAMETER_M = 1200.0
DEFAULT_SKY_DOME_SCENE_SPAN_MULTIPLIER = 6.0
DEFAULT_SKY_DOME_TEXTURE_SIZE = (1024, 512)
DEFAULT_SKY_DOME_MATERIAL_NAME = "roadgen3d_default_sky_gradient"


@dataclass(frozen=True)
class _MeshMetadata:
    """Lightweight mesh metadata that doesn't require loading the full mesh.

    This contains only bounding-box derived values needed for layout computation.
    The full mesh is loaded lazily only when needed for GLB export.
    """
    asset_id: str
    half_x: float
    half_z: float
    min_y: float
    center_x: float = 0.0
    center_z: float = 0.0
    is_scene: bool = False
    native_height_y: float = 0.0
    mesh_path: str = ""  # Path for lazy loading
    source_scale: float = 1.0
    source_scale_source: str = ""
    source_scale_confidence: str = ""
    source_scale_rejected_reason: str = ""
    raw_size_m: Dict[str, float] = dataclasses.field(default_factory=dict)
    metric_size_m: Dict[str, float] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True)
class _MeshCacheEntry:
    """Full mesh cache entry with both metadata and loaded mesh object."""
    mesh: object
    half_x: float
    half_z: float
    min_y: float
    center_x: float = 0.0
    center_z: float = 0.0
    is_scene: bool = False
    native_height_y: float = 0.0
    source_scale: float = 1.0
    source_scale_source: str = ""
    source_scale_confidence: str = ""
    source_scale_rejected_reason: str = ""
    raw_size_m: Dict[str, float] = dataclasses.field(default_factory=dict)
    metric_size_m: Dict[str, float] = dataclasses.field(default_factory=dict)


class _LazyMeshCache:
    """Memory-efficient mesh cache with lazy loading.

    Stores lightweight metadata for all assets, but only loads full mesh
    objects when needed (for GLB export). Uses LRU eviction to limit memory.

    Memory optimization:
    - Layout computation phase: only uses metadata (half_x, half_z, etc.)
    - Export phase: lazily loads full meshes for placed assets only

    Supports two types of entries:
    - _MeshMetadata: lightweight bbox info loaded from disk
    - _MeshCacheEntry: full mesh object (either pre-loaded or lazy-loaded)
    """

    def __init__(
        self,
        metadata: Dict[str, _MeshMetadata],
        max_mesh_cache_size: int = DEFAULT_MAX_MESH_CACHE_SIZE,
    ) -> None:
        # Store metadata entries
        self._metadata: Dict[str, _MeshMetadata] = metadata
        # Store full mesh entries (lazy-loaded)
        self._mesh_cache: Dict[str, _MeshCacheEntry] = {}
        self._max_size = max_mesh_cache_size
        self._access_order: List[str] = []  # For LRU tracking

    def get_metadata(self, asset_id: str) -> _MeshCacheEntry | _MeshMetadata:
        """Get metadata without loading the full mesh.

        For layout computation where only bbox info is needed.
        """
        return self._metadata[asset_id]

    def get_entry(self, asset_id: str) -> _MeshCacheEntry:
        """Get full mesh entry, loading mesh lazily if needed.

        Uses LRU eviction to keep memory usage bounded.
        """
        # Fast path: already loaded
        if asset_id in self._mesh_cache:
            return self._mesh_cache[asset_id]

        # Load the mesh from metadata
        metadata = self._metadata[asset_id]
        try:
            entry = _load_single_mesh(metadata)
        except Exception as exc:
            raise RuntimeError(
                f"failed to load mesh for asset '{asset_id}' from '{metadata.mesh_path}'"
            ) from exc

        # LRU eviction: remove oldest reloadable entries if cache is full.
        # Procedural fallback entries have no mesh_path and must stay resident.
        while len(self._mesh_cache) >= self._max_size:
            if not self._access_order:
                break
            oldest = self._access_order.pop(0)
            oldest_meta = self._metadata.get(oldest)
            if oldest in self._mesh_cache and str(getattr(oldest_meta, "mesh_path", "") or ""):
                del self._mesh_cache[oldest]
                break
            if oldest in self._mesh_cache:
                self._access_order.append(oldest)
                if all(
                    not str(getattr(self._metadata.get(candidate), "mesh_path", "") or "")
                    for candidate in self._access_order
                    if candidate in self._mesh_cache
                ):
                    break

        # Store in cache
        self._mesh_cache[asset_id] = entry
        self._access_order.append(asset_id)

        return entry

    def preload(self, asset_ids: Sequence[str]) -> None:
        """Preload specific assets into the cache.

        Useful when you know which assets will be needed for export.
        """
        for asset_id in asset_ids:
            if asset_id not in self._mesh_cache and asset_id in self._metadata:
                entry = _load_single_mesh(self._metadata[asset_id])

                # Evict if needed before preload
                while len(self._mesh_cache) >= self._max_size:
                    if self._access_order:
                        oldest = self._access_order.pop(0)
                        if oldest in self._mesh_cache:
                            del self._mesh_cache[oldest]

                self._mesh_cache[asset_id] = entry
                self._access_order.append(asset_id)

    def set_full_entry(self, asset_id: str, entry: _MeshCacheEntry) -> None:
        """Set a complete mesh entry directly (for placeholder/fallback buildings).

        This bypasses lazy loading and stores the entry directly.
        """
        # Add to metadata if not present (needed for fallback buildings)
        if asset_id not in self._metadata:
            self._metadata[asset_id] = _MeshMetadata(
                asset_id=asset_id,
                half_x=entry.half_x,
                half_z=entry.half_z,
                min_y=entry.min_y,
                center_x=entry.center_x,
                center_z=entry.center_z,
                is_scene=entry.is_scene,
                native_height_y=entry.native_height_y,
                mesh_path="",  # Placeholder has no path
                source_scale=entry.source_scale,
                source_scale_source=entry.source_scale_source,
                source_scale_confidence=entry.source_scale_confidence,
                source_scale_rejected_reason=entry.source_scale_rejected_reason,
                raw_size_m=dict(entry.raw_size_m or _native_size_for_entry(entry)),
                metric_size_m=dict(entry.metric_size_m or {}),
            )

        # LRU eviction if needed. Procedural fallback entries have no mesh_path
        # and cannot be lazy-reloaded, so keep them resident for final export.
        while len(self._mesh_cache) >= self._max_size:
            if not self._access_order:
                break
            oldest = self._access_order.pop(0)
            oldest_meta = self._metadata.get(oldest)
            if oldest in self._mesh_cache and str(getattr(oldest_meta, "mesh_path", "") or ""):
                del self._mesh_cache[oldest]
                break
            if oldest in self._mesh_cache:
                self._access_order.append(oldest)
                if all(
                    not str(getattr(self._metadata.get(candidate), "mesh_path", "") or "")
                    for candidate in self._access_order
                    if candidate in self._mesh_cache
                ):
                    break

        self._mesh_cache[asset_id] = entry
        self._access_order.append(asset_id)

    def __contains__(self, asset_id: str) -> bool:
        return asset_id in self._metadata

    def __getitem__(self, asset_id: str) -> _MeshCacheEntry:
        """Direct dict-like access returns the full mesh entry."""
        return self.get_entry(asset_id)

    def __iter__(self):
        """Iterate over metadata keys for backward compatibility."""
        return iter(self._metadata)

    def keys(self):
        return self._metadata.keys()

    def values(self):
        """Iterate over cached entries (loaded ones only)."""
        return self._mesh_cache.values()

    def items(self):
        """Iterate over cached (asset_id, entry) pairs."""
        return self._mesh_cache.items()

    def get_trimmed_cache(self, used_asset_ids: set[str]) -> Dict[str, _MeshCacheEntry]:
        """Get a dict of only the entries for used assets.

        This is used to trim memory after scene export, keeping only
        the meshes that were actually placed in the scene.
        """
        result = {}
        for asset_id in used_asset_ids:
            if asset_id in self._mesh_cache:
                result[asset_id] = self._mesh_cache[asset_id]
            elif asset_id in self._metadata:
                # Asset was used but not loaded - load it now for the final export
                result[asset_id] = self.get_entry(asset_id)
        return result

    def get(self, asset_id: str, default=None):
        """Dict-like get - returns metadata by default."""
        return self._metadata.get(asset_id, default)


@dataclass(frozen=True)
class _SurroundingBuildingResult:
    building_footprints: Tuple[BuildingFootprint, ...]
    generated_lots: Tuple[GeneratedLot, ...]
    placements: Tuple[StreetPlacement, ...]
    plans: Tuple[BuildingPlacementPlan, ...]
    retrieval_predictions: Tuple[Dict[str, object], ...]
    building_summary: Dict[str, object]
    land_use_summary: Dict[str, object]
    lot_generation_summary: Dict[str, object]
    zoning_grid: Tuple[Dict[str, object], ...]
    zoning_preview_summary: Dict[str, object]
    instance_index: int


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


_BLOCKED_ASSET_IDS = {
    "objaverse_tree_7c97aea203b34df6bb615d0d3567d984",
    "objaverse_tree_352c29c013434d6585e74332699310e2",
    "objaverse_tree_7a689370f9ec46cea2cbc94641c225e6",
    "objaverse_tree_a90b8cca57b44f5492e796cf94d64e80",
    "objaverse_tree_209a0ca9d736401da034fe1d29df010e",
}


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return int(default)


def _positive_float(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(parsed) or parsed <= 0.0:
        return float(default)
    return float(parsed)


def _metric_dimensions_from_row(row: Mapping[str, object]) -> Tuple[float, float, float, str]:
    width = _positive_float(row.get("metric_width_m"))
    depth = _positive_float(row.get("metric_depth_m"))
    height = _positive_float(row.get("metric_height_m"))
    if width > 0.0 or depth > 0.0 or height > 0.0:
        return width, depth, height, "metric_fields"

    dimensions = row.get("dimensions_m")
    if isinstance(dimensions, Mapping):
        width = _positive_float(dimensions.get("width") or dimensions.get("width_m"))
        depth = _positive_float(dimensions.get("depth") or dimensions.get("depth_m"))
        height = _positive_float(dimensions.get("height") or dimensions.get("height_m"))
        if width > 0.0 or depth > 0.0 or height > 0.0:
            return width, depth, height, "metric_dimensions_m"

    return 0.0, 0.0, 0.0, ""


def _size_payload_from_dimensions(width: float, depth: float, height: float) -> Dict[str, float]:
    payload: Dict[str, float] = {}
    if float(width) > 0.0:
        payload["width_m"] = float(width)
    if float(depth) > 0.0:
        payload["depth_m"] = float(depth)
    if float(height) > 0.0:
        payload["height_m"] = float(height)
    return payload


def _source_scale_for_row(row: Mapping[str, object], span: np.ndarray) -> Tuple[float, str, str, str, Dict[str, float]]:
    explicit = _positive_float(row.get("scale"))
    width, depth, height, metric_source = _metric_dimensions_from_row(row)
    metric_size = _size_payload_from_dimensions(width, depth, height)
    if explicit > 0.0:
        return explicit, "manifest_scale", "explicit", "", metric_size

    if not metric_source:
        return 1.0, "native_bbox", "native", "", metric_size

    span_x = float(span[0])
    span_y = float(span[1])
    span_z = float(span[2])

    def _candidate_ratios(*, swapped_horizontal_axes: bool) -> List[float]:
        ratios: List[float] = []
        x_metric = depth if swapped_horizontal_axes else width
        z_metric = width if swapped_horizontal_axes else depth
        if x_metric > 0.0 and span_x > 1e-6:
            ratios.append(float(x_metric) / span_x)
        if z_metric > 0.0 and span_z > 1e-6:
            ratios.append(float(z_metric) / span_z)
        if height > 0.0 and span_y > 1e-6:
            ratios.append(float(height) / span_y)
        return ratios

    candidates: List[Tuple[float, float, bool, List[float]]] = []
    for swapped in (False, True):
        ratios = _candidate_ratios(swapped_horizontal_axes=swapped)
        if len(ratios) < 2:
            continue
        ordered = sorted(float(value) for value in ratios if float(value) > 0.0 and math.isfinite(float(value)))
        if len(ordered) < 2:
            continue
        spread = float(ordered[-1] / max(ordered[0], 1e-9))
        median = float(ordered[len(ordered) // 2]) if len(ordered) % 2 else float((ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2.0)
        candidates.append((spread, median, swapped, ordered))

    if not candidates:
        return 1.0, "native_bbox", "rejected", "insufficient_metric_axes", metric_size

    spread, median, swapped, ratios = min(candidates, key=lambda item: (item[0], abs(math.log(max(item[1], 1e-9)))))
    if spread <= 1.35:
        confidence = "metric_high_swapped_axes" if swapped else "metric_high"
        return float(median), metric_source, confidence, "", metric_size
    if spread <= 1.75:
        confidence = "metric_medium_swapped_axes" if swapped else "metric_medium"
        return float(median), metric_source, confidence, "", metric_size
    return (
        1.0,
        "native_bbox",
        "rejected",
        f"metric_ratio_conflict_{spread:.2f}",
        metric_size,
    )


def _row_scene_eligible(row: Mapping[str, object]) -> bool:
    asset_id = str(row.get("asset_id", "") or "").strip()
    if asset_id in _BLOCKED_ASSET_IDS:
        return False
    # Filter out real_asset / urbanverse_import sources (L0 quality)
    source = str(row.get("source", "") or "").strip().lower()
    if "real_asset" in source or "urbanverse_import" in source:
        return False
    # Filter out v2 generator types (L0 quality)
    generator_type = str(row.get("generator_type", "") or "").strip().lower()
    if "_v2" in generator_type or "-v2" in generator_type:
        return False
    # Filter out L0 quality tier assets
    if _safe_int(row.get("quality_tier"), 1) < 1:
        return False
    value = row.get("scene_eligible")
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    if text in {"1", "true", "yes", "on"}:
        return True
    return True


_PARALLEL_TO_CARRIAGEWAY_CATEGORIES = {"bench", "bus_stop", "bollard"}
# Ground-level categories whose full bounding box must stay out of the carriageway
# and must not overlap other placed objects.  Tall / overhanging categories
# (tree, lamp) only need their center point to land in the correct slot.
_GROUND_LEVEL_CATEGORIES = frozenset({"bench", "trash", "bollard", "bus_stop", "kiosk", "sculpture"})
# Ground-level categories whose full bounding box must stay out of the carriageway
# and must not overlap other placed objects.  Tall / overhanging categories
# (tree, lamp) only need their center point to land in the correct slot.
_GROUND_LEVEL_CATEGORIES = frozenset({"bench", "trash", "bollard", "bus_stop"})
_CURATED_STREET_ASSET_PROFILES = {"fixed_hq_v1", "disabled"}
_CURATED_STREET_ASSET_IDS_FIXED_HQ = {
    "lamp": "lamp_modern_production",
    "trash": "objaverse_trash_f16b7d84113d4cba869412ee95769910",
    "bollard": "curated_railing_module_v1",
    "tree": "objaverse_tree_909de376b61d4a2fb073e195fb719619",
}
_CURATED_ALLOWLIST_SELECTION_SOURCE = "curated_allowlist_stable"


def _normalize_curated_street_assets_profile(value: object) -> str:
    key = str(value or "fixed_hq_v1").strip().lower()
    return key if key in _CURATED_STREET_ASSET_PROFILES else "fixed_hq_v1"


def _curated_locked_asset_ids_for_profile(profile: str) -> Dict[str, str]:
    if _normalize_curated_street_assets_profile(profile) != "fixed_hq_v1":
        return {}
    return dict(_CURATED_STREET_ASSET_IDS_FIXED_HQ)


def _curated_locked_asset_ids(config: StreetComposeConfig | None) -> Dict[str, str]:
    if config is None:
        return {}
    profile = _normalize_curated_street_assets_profile(
        getattr(config, "curated_street_assets_profile", "fixed_hq_v1")
    )
    return _curated_locked_asset_ids_for_profile(profile)


def _stable_index_for_key(key: str, count: int) -> int:
    if count <= 0:
        return 0
    digest = hashlib.sha256(str(key).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % int(count)


def _curated_allowlist_rows_for_category(
    category_pool: Sequence[Mapping[str, object]],
    *,
    category: str,
    config: StreetComposeConfig | None,
) -> List[Mapping[str, object]]:
    if config is None:
        return []
    profile = _normalize_curated_street_assets_profile(
        getattr(config, "curated_street_assets_profile", "fixed_hq_v1")
    )
    if profile == "disabled":
        return []
    category_key = str(category).strip().lower()
    if category_key not in _curated_locked_asset_ids_for_profile(profile):
        return []

    rows: List[Mapping[str, object]] = []
    for row in category_pool:
        if str(row.get("category", "")).strip().lower() != category_key:
            continue
        if not _row_scene_eligible(row):
            continue
        if row.get("scene_eligible") is None and _safe_int(row.get("quality_tier"), 0) < 2:
            continue
        if category_key == "tree" and not _is_external_tree_asset(row):
            continue
        rows.append(row)

    return sorted(rows, key=lambda row: str(row.get("asset_id", "")))


def _stable_curated_allowlist_row(
    category_pool: Sequence[Mapping[str, object]],
    *,
    category: str,
    config: StreetComposeConfig | None,
    stable_selection_key: str,
    asset_id_whitelist: Optional[set[str]] = None,
) -> Tuple[Mapping[str, object], List[Mapping[str, object]]] | Tuple[None, List[Mapping[str, object]]]:
    allowlist_rows = _curated_allowlist_rows_for_category(
        category_pool,
        category=category,
        config=config,
    )
    if asset_id_whitelist:
        allowlist_rows = [
            row
            for row in allowlist_rows
            if str(row.get("asset_id", "")) in asset_id_whitelist
        ]
    if not allowlist_rows:
        return None, []
    key = str(stable_selection_key or f"{category}:default")
    row = allowlist_rows[_stable_index_for_key(key, len(allowlist_rows))]
    return row, allowlist_rows


def _curated_allowlist_ids_by_category(
    category_to_rows: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    config: StreetComposeConfig,
) -> Dict[str, List[str]]:
    profile = _normalize_curated_street_assets_profile(
        getattr(config, "curated_street_assets_profile", "fixed_hq_v1")
    )
    categories = sorted(_curated_locked_asset_ids_for_profile(profile))
    allowlists: Dict[str, List[str]] = {}
    for category in categories:
        rows = _curated_allowlist_rows_for_category(
            category_to_rows.get(category, ()),
            category=category,
            config=config,
        )
        if rows:
            allowlists[category] = [str(row.get("asset_id", "")) for row in rows]
    return allowlists


def _create_curated_railing_entry(*, asset_id: str = "curated_railing_module_v1") -> Tuple[Dict[str, object], _MeshCacheEntry]:
    trimesh = _require_trimesh()
    scene = trimesh.Scene()

    def _add_box(
        extents: Tuple[float, float, float],
        translation: Tuple[float, float, float],
        color: Tuple[int, int, int, int],
    ) -> None:
        mesh = trimesh.creation.box(extents=extents)
        mesh.visual.face_colors = list(color)
        mesh.apply_translation(translation)
        scene.add_geometry(mesh)

    metal = (102, 110, 118, 255)
    trim = (188, 194, 198, 255)
    base = (86, 92, 98, 255)
    module_length_m = 2.4
    module_depth_m = 0.12
    post_height_m = 1.08
    rail_depth_m = 0.08
    rail_width_m = 0.12
    half_length = module_length_m / 2.0
    post_offsets = (-half_length + 0.08, 0.0, half_length - 0.08)
    for x_offset in post_offsets:
        _add_box((0.09, post_height_m, 0.09), (x_offset, post_height_m / 2.0, 0.0), metal)
        _add_box((0.16, 0.03, 0.16), (x_offset, 0.015, 0.0), base)
    for rail_height in (0.34, 0.62, 0.9):
        _add_box((module_length_m - 0.12, rail_width_m, rail_depth_m), (0.0, rail_height, 0.0), trim)
    _add_box((module_length_m, 0.05, module_depth_m), (0.0, 1.02, 0.0), metal)

    bounds = np.asarray(scene.bounds, dtype=np.float64)
    span = bounds[1] - bounds[0]
    row: Dict[str, object] = {
        "asset_id": str(asset_id),
        "category": "bollard",
        "text_desc": "high-quality pedestrian safety railing module used in place of isolated bollards",
        "asset_role": "street_furniture",
        "source": "curated_virtual",
        "generator_type": "virtual_curated_v1",
        "theme_tags": ["transit", "civic", "walkable", "safety"],
        "scene_eligible": True,
        "quality_tier": 3,
        "mesh_face_count": int(
            sum(len(np.asarray(getattr(geom, "faces", []), dtype=np.int64)) for geom in scene.geometry.values())
        ),
        "quality_notes": [
            "curated_asset_lock",
            "railing_visual_replace",
            "scene_ready",
            "generator=virtual_curated_v1",
        ],
    }
    entry = _MeshCacheEntry(
        mesh=scene,
        half_x=float(max(span[0] / 2.0, 1e-3)),
        half_z=float(max(span[2] / 2.0, 1e-3)),
        min_y=float(bounds[0][1]),
        center_x=float((bounds[0][0] + bounds[1][0]) / 2.0),
        center_z=float((bounds[0][2] + bounds[1][2]) / 2.0),
        is_scene=True,
        native_height_y=float(max(span[1], 1e-3)),
    )
    return row, entry


def _inject_curated_virtual_assets(
    rows: List[Dict[str, object]],
    mesh_cache: _LazyMeshCache,
    *,
    profile: str,
) -> List[Dict[str, object]]:
    normalized = _normalize_curated_street_assets_profile(profile)
    if normalized == "disabled":
        return rows
    locked_asset_ids = _curated_locked_asset_ids_for_profile(normalized)
    asset_ids_present = {str(row.get("asset_id", "")) for row in rows}
    injected_rows = list(rows)
    railing_asset_id = str(locked_asset_ids.get("bollard", "") or "")
    if railing_asset_id and railing_asset_id not in asset_ids_present:
        railing_row, railing_entry = _create_curated_railing_entry(asset_id=railing_asset_id)
        injected_rows.append(railing_row)
        mesh_cache.set_full_entry(railing_asset_id, railing_entry)
    return injected_rows


def _validate_curated_locked_assets(
    *,
    asset_by_id: Mapping[str, Mapping[str, object]],
    profile: str,
) -> Dict[str, str]:
    usable_locked_asset_ids: Dict[str, str] = {}
    for category, asset_id in _curated_locked_asset_ids_for_profile(profile).items():
        row = asset_by_id.get(asset_id)
        if row is None:
            logger.warning(
                "Curated asset lock '%s' is missing %s asset '%s'; falling back to category pool.",
                profile,
                category,
                asset_id,
            )
            continue
        if not _row_scene_eligible(row):
            logger.warning(
                "Curated asset lock '%s' asset '%s' for %s is not scene eligible; falling back to category pool.",
                profile,
                asset_id,
                category,
            )
            continue
        usable_locked_asset_ids[str(category)] = str(asset_id)
    return usable_locked_asset_ids


def _curated_locked_row_for_category(
    *,
    category: str,
    asset_by_id: Mapping[str, Mapping[str, object]],
    config: StreetComposeConfig | None,
) -> Mapping[str, object] | None:
    locked_asset_ids = _curated_locked_asset_ids(config)
    asset_id = str(locked_asset_ids.get(str(category).strip().lower(), "") or "")
    if not asset_id:
        return None
    row = asset_by_id.get(asset_id)
    if row is None or not _row_scene_eligible(row):
        return None
    return row


def _row_quality_notes(row: Mapping[str, object]) -> Tuple[str, ...]:
    notes = row.get("quality_notes")
    if notes is None:
        return ()
    if isinstance(notes, str):
        text = notes.strip()
        return (text,) if text else ()
    return tuple(str(item).strip() for item in notes if str(item).strip())


def _tree_upright_validated(row: Mapping[str, object]) -> bool:
    if "tree_upright_validated" in _row_quality_notes(row):
        return True
    metrics = row.get("quality_metrics")
    if isinstance(metrics, Mapping):
        validation = metrics.get("tree_upright_validation")
        if isinstance(validation, Mapping):
            return not bool(str(validation.get("failure_reason", "")).strip())
    return False


def _is_external_tree_asset(row: Mapping[str, object]) -> bool:
    if str(row.get("category", "")).strip().lower() != "tree":
        return False
    provenance = asset_generator_type(row)
    if provenance in {"parametric", "legacy", "procedural_fallback"}:
        return False
    source = str(row.get("source", "") or "").strip().lower()
    return source not in {"procedural_generated", "parametric_generated", "procedural_fallback"} and _tree_upright_validated(row)


def _yaw_for_asset_category(category: str, facing_yaw_deg: float) -> float:
    yaw_deg = float(facing_yaw_deg)
    if str(category).strip().lower() in _PARALLEL_TO_CARRIAGEWAY_CATEGORIES:
        yaw_deg -= 90.0
    yaw_deg = math.fmod(yaw_deg, 360.0)
    if yaw_deg < 0.0:
        yaw_deg += 360.0
    return float(yaw_deg)


def _placement_asset_source_key(
    row: Mapping[str, object] | None,
    *,
    selection_source: str = "",
) -> str:
    """Return a stable provenance/source key for one placed asset."""

    if row is not None:
        source = str(row.get("source", "") or "").strip().lower()
        if source:
            return source
        generator = asset_generator_type(row)
        if generator:
            return str(generator).strip().lower()
    if str(selection_source).strip().lower() == "procedural_fallback":
        return "procedural_fallback"
    return "unknown"


def _native_size_for_entry(entry: _MeshCacheEntry | _MeshMetadata) -> Dict[str, float]:
    return {
        "width_m": float(max(entry.half_x * 2.0, 0.0)),
        "depth_m": float(max(entry.half_z * 2.0, 0.0)),
        "height_m": float(max(entry.native_height_y, 0.0)),
        "canopy_width_m": float(max(entry.half_x * 2.0, entry.half_z * 2.0, 0.0)),
    }


def _raw_size_for_entry(entry: _MeshCacheEntry | _MeshMetadata) -> Dict[str, float]:
    raw_size = dict(getattr(entry, "raw_size_m", {}) or {})
    return raw_size or _native_size_for_entry(entry)


def _metric_size_for_entry(entry: _MeshCacheEntry | _MeshMetadata) -> Dict[str, float]:
    return dict(getattr(entry, "metric_size_m", {}) or {})


def _street_furniture_scale_info(
    *,
    category: str,
    entry: _MeshCacheEntry | _MeshMetadata,
    config: StreetComposeConfig,
) -> Dict[str, Any]:
    native_size = _native_size_for_entry(entry)
    scale_native_size = dict(native_size)
    semantic_axis_remap = ""
    if str(category).strip().lower() in {"tree", "lamp", "hydrant", "bollard"}:
        horizontal_max = max(float(native_size["width_m"]), float(native_size["depth_m"]))
        native_height = float(native_size["height_m"])
        if horizontal_max > 0.0 and native_height > 0.0 and native_height < horizontal_max * 0.5:
            scale_native_size["height_m"] = float(horizontal_max)
            semantic_axis_remap = "max_horizontal_axis_as_height"
    scale_info = compute_asset_scale(
        category=category,
        width_m=float(scale_native_size["width_m"]),
        depth_m=float(scale_native_size["depth_m"]),
        height_m=float(scale_native_size["height_m"]),
        mode=str(getattr(config, "asset_scale_mode", "canonical_v1")),
    )
    applied_scale = float(scale_info.get("applied_scale", 1.0) or 1.0)
    sanity_bounds = dict(asset_scale_prior(category).get("sanity_bounds", {}) or {})
    footprint_caps: List[float] = []
    for dim_key, native_key in (("width_m", "width_m"), ("depth_m", "depth_m"), ("canopy_width_m", "canopy_width_m")):
        bounds = sanity_bounds.get(dim_key)
        native_value = float(native_size.get(native_key, 0.0) or 0.0)
        if bounds and native_value > 1e-6:
            footprint_caps.append(float(bounds[1]) / native_value)
    if footprint_caps:
        footprint_cap = max(0.02, min(float(value) for value in footprint_caps if float(value) > 0.0))
        if applied_scale > footprint_cap:
            applied_scale = float(footprint_cap)
            scale_info["applied_scale"] = applied_scale
            existing_reason = str(scale_info.get("scale_gate_reason", "") or "")
            scale_info["scale_gate_failed"] = True
            scale_info["scale_gate_blocking"] = False
            scale_info["scale_gate_reason"] = ",".join(
                reason for reason in (existing_reason, "footprint_capped_for_placement") if reason
            )
    scale_info["native_size_m"] = dict(native_size)
    scale_info["final_size_m"] = {
        key: float(value) * applied_scale
        for key, value in native_size.items()
    }
    if semantic_axis_remap:
        scale_info["semantic_axis_remap"] = semantic_axis_remap
    scale_info["source_scale"] = float(getattr(entry, "source_scale", 1.0) or 1.0)
    scale_info["source_scale_source"] = str(getattr(entry, "source_scale_source", "") or "")
    scale_info["source_scale_confidence"] = str(getattr(entry, "source_scale_confidence", "") or "")
    scale_info["source_scale_rejected_reason"] = str(getattr(entry, "source_scale_rejected_reason", "") or "")
    scale_info["raw_size_m"] = _raw_size_for_entry(entry)
    scale_info["metric_size_m"] = _metric_size_for_entry(entry)
    return scale_info


def _is_corridor_layout_mode(layout_mode: object) -> bool:
    return str(layout_mode or "").strip().lower() in {"osm", "metaurban", "graph_template"}


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
    if str(getattr(config, "curated_street_assets_profile", "fixed_hq_v1") or "").strip().lower() not in _CURATED_STREET_ASSET_PROFILES:
        raise ValueError("curated_street_assets_profile is invalid")
    if config.topk_per_category <= 0:
        raise ValueError("topk_per_category must be >= 1")
    if config.max_trials_per_slot <= 0:
        raise ValueError("max_trials_per_slot must be >= 1")
    # -- M5 validation --
    if config.layout_mode not in ("template", "osm", "metaurban", "graph_template"):
        raise ValueError("layout_mode must be 'template', 'osm', 'metaurban', or 'graph_template'")
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
    if str(config.layout_solver).strip().lower() not in {"banded", "milp_template_v1", "hybrid_milp_v1"}:
        raise ValueError("layout_solver must be 'banded', 'milp_template_v1', or 'hybrid_milp_v1'")
    if str(getattr(config, "objective_profile", "balanced")).strip().lower() not in {"balanced", "greening", "commerce", "transit"}:
        raise ValueError("objective_profile must be 'balanced', 'greening', 'commerce', or 'transit'")
    demand_levels = {"low", "medium", "high"}
    for field_name in ("ped_demand_level", "bike_demand_level", "transit_demand_level", "vehicle_demand_level"):
        if str(getattr(config, field_name, "medium")).strip().lower() not in demand_levels:
            raise ValueError(f"{field_name} must be 'low', 'medium', or 'high'")
    if float(getattr(config, "segment_length_m", 12.0)) <= 0.0:
        raise ValueError("segment_length_m must be > 0")
    if str(getattr(config, "width_budget_mode", "expand_total_width")).strip().lower() != "expand_total_width":
        raise ValueError("width_budget_mode must be 'expand_total_width'")
    if str(getattr(config, "sidewalk_distribution", "per_side")).strip().lower() != "per_side":
        raise ValueError("sidewalk_distribution must be 'per_side'")
    if str(getattr(config, "poi_fit_mode", "hard_containment")).strip().lower() != "hard_containment":
        raise ValueError("poi_fit_mode must be 'hard_containment'")
    base_lane_width_m = getattr(config, "base_lane_width_m", None)
    if base_lane_width_m is not None and float(base_lane_width_m) <= 0.0:
        raise ValueError("base_lane_width_m must be > 0 when provided")
    if str(getattr(config, "beauty_mode", "presentation_v1")).strip().lower() not in {"presentation_v1"}:
        raise ValueError("beauty_mode must be 'presentation_v1'")
    if str(getattr(config, "render_preset", "axonometric_board_v1")).strip().lower() not in {
        "axonometric_board_v1",
        "jury_default_v1",
    }:
        raise ValueError("render_preset must be 'axonometric_board_v1' or 'jury_default_v1'")
    if str(getattr(config, "topdown_render_mode", "design_tiles_v1")).strip().lower() not in {
        "legacy_vector",
        "design_tiles_v1",
    }:
        raise ValueError("topdown_render_mode must be 'legacy_vector' or 'design_tiles_v1'")
    if str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")).strip().lower() not in VALID_SCENE_TEXTURE_MODES:
        raise ValueError("scene_texture_mode must be 'topdown_tiles_v1' or 'solid_color_legacy'")
    if int(getattr(config, "topdown_canvas_px", 2048)) <= 0:
        raise ValueError("topdown_canvas_px must be > 0")
    if str(getattr(config, "asset_curation_mode", "scene_ready_first")).strip().lower() not in {
        "scene_ready_first",
        "parametric_first",
        "curated_first",
        "legacy",
    }:
        raise ValueError("asset_curation_mode must be 'scene_ready_first', 'parametric_first', 'curated_first' or 'legacy'")
    if str(getattr(config, "asset_scale_mode", "canonical_v1")).strip().lower() not in VALID_ASSET_SCALE_MODES:
        raise ValueError("asset_scale_mode must be 'canonical_v1' or 'native_raw'")
    if str(getattr(config, "road_selection", "walkable_neighborhood")).strip().lower() not in {
        "all",
        "primary_road",
        "longest",
        "walkable_neighborhood",
    }:
        raise ValueError("road_selection must be 'all', 'primary_road', 'longest' or 'walkable_neighborhood'")
    if int(getattr(config, "building_search_topk", 1)) <= 0:
        raise ValueError("building_search_topk must be >= 1")
    if str(getattr(config, "surrounding_building_mode", "grid_growth")).strip().lower() not in {"footprint_based", "grid_growth"}:
        raise ValueError("surrounding_building_mode must be 'footprint_based' or 'grid_growth'")
    if str(getattr(config, "auto_land_use_mode", "road_buffer")).strip().lower() not in {"road_buffer", "off"}:
        raise ValueError("auto_land_use_mode must be 'road_buffer' or 'off'")
    if float(getattr(config, "land_use_buffer_m", 35.0)) <= 0.0:
        raise ValueError("land_use_buffer_m must be > 0")
    if float(getattr(config, "min_land_use_polygon_area_m2", 12.0)) < 0.0:
        raise ValueError("min_land_use_polygon_area_m2 must be >= 0")
    if float(getattr(config, "max_frontage_lot_length_m", 18.0)) <= 0.0:
        raise ValueError("max_frontage_lot_length_m must be > 0")
    if str(getattr(config, "zoning_granularity", "fine")).strip().lower() not in {"coarse", "balanced", "fine"}:
        raise ValueError("zoning_granularity must be 'coarse', 'balanced' or 'fine'")
    if not 0.0 <= float(getattr(config, "streetwall_continuity", 0.95)) <= 1.0:
        raise ValueError("streetwall_continuity must be in [0.0, 1.0]")
    if not 0.0 <= float(getattr(config, "building_density", 0.55)) <= 1.0:
        raise ValueError("building_density must be in [0.0, 1.0]")
    if float(getattr(config, "building_max_per_100m", 10.0)) <= 0.0:
        raise ValueError("building_max_per_100m must be > 0")
    if str(getattr(config, "infill_policy", "aggressive")).strip().lower() not in {
        "off",
        "large_gap_only",
        "balanced",
        "aggressive",
    }:
        raise ValueError("infill_policy must be 'off', 'large_gap_only', 'balanced' or 'aggressive'")
    if str(getattr(config, "tree_species_policy", "per_theme_single_species")).strip().lower() not in {
        "per_theme_single_species",
        "free_mixed",
    }:
        raise ValueError("tree_species_policy must be 'per_theme_single_species' or 'free_mixed'")
    if str(getattr(config, "furniture_balance_policy", "overall_balanced")).strip().lower() not in {
        "overall_balanced",
        "side_biased_legacy",
    }:
        raise ValueError("furniture_balance_policy must be 'overall_balanced' or 'side_biased_legacy'")
    if str(getattr(config, "placement_logging_mode", "full_with_ui_summary")).strip().lower() not in {
        "off",
        "summary_only",
        "full_with_ui_summary",
    }:
        raise ValueError("placement_logging_mode must be 'off', 'summary_only' or 'full_with_ui_summary'")
    if str(getattr(config, "theme_inference_mode", "deterministic_auto")).strip().lower() not in {"deterministic_auto"}:
        raise ValueError("theme_inference_mode must be 'deterministic_auto'")
    if str(getattr(config, "theme_vocab_name", "fixed_v1")).strip().lower() not in {"fixed_v1"}:
        raise ValueError("theme_vocab_name must be 'fixed_v1'")


def _validate_export_format(export_format: str) -> str:
    value = export_format.strip().lower()
    if value not in {"glb", "ply", "both", "none"}:
        raise ValueError("export_format must be one of: glb, ply, both, none")
    return value


def _load_real_manifest(manifest_path: Path) -> List[Dict[str, object]]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"real manifest not found: {manifest_path}")
    required = ("asset_id", "category", "text_desc", "mesh_path", "latent_path")
    rows: List[Dict[str, object]] = []
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
        for optional_key in (
            "style_tags",
            "quality_tier",
            "material_family",
            "hero_asset",
            "avoid_with_presets",
            "asset_role",
            "theme_tags",
            "frontage_width_m",
            "depth_m",
            "height_class",
            "source",
            "generator_type",
            "runtime_profile",
            "parameter_snapshot",
            "quality_metrics",
            "scene_eligible",
            "mesh_face_count",
            "quality_notes",
            "scale",
            "metric_width_m",
            "metric_depth_m",
            "metric_height_m",
            "dimensions_m",
        ):
            if optional_key in payload:
                row[optional_key] = payload[optional_key]
        if "asset_role" not in row:
            row["asset_role"] = "building" if row["category"] == "building" else "street_furniture"
        rows.append(row)
    if not rows:
        raise ValueError(f"real manifest is empty: {manifest_path}")
    return rows


def _default_sky_dome_texture_image():
    from PIL import Image

    width, height = DEFAULT_SKY_DOME_TEXTURE_SIZE
    stops = (
        (0.0, np.array([99, 158, 223], dtype=np.float64)),
        (0.45, np.array([158, 203, 245], dtype=np.float64)),
        (0.70, np.array([222, 239, 255], dtype=np.float64)),
        (1.0, np.array([248, 230, 198], dtype=np.float64)),
    )
    rows: list[np.ndarray] = []
    for y in range(height):
        t = y / max(1, height - 1)
        lower = stops[0]
        upper = stops[-1]
        for idx in range(len(stops) - 1):
            if stops[idx][0] <= t <= stops[idx + 1][0]:
                lower = stops[idx]
                upper = stops[idx + 1]
                break
        span = max(1e-6, upper[0] - lower[0])
        local_t = (t - lower[0]) / span
        color = lower[1] * (1.0 - local_t) + upper[1] * local_t
        rows.append(np.tile(np.clip(color, 0, 255).astype(np.uint8), (width, 1)))
    return Image.fromarray(np.stack(rows, axis=0))


def _spherical_uv_for_vertices(vertices: np.ndarray) -> np.ndarray:
    vertices = np.asarray(vertices, dtype=np.float64)
    if vertices.size == 0:
        return np.zeros((0, 2), dtype=np.float64)
    centered = vertices - vertices.mean(axis=0)
    x = centered[:, 0]
    y = centered[:, 1]
    z = centered[:, 2]
    radius_xz = np.hypot(x, z)
    u = (np.arctan2(z, x) / (2.0 * math.pi) + 0.5) % 1.0
    v = 1.0 - (np.arctan2(y, radius_xz) / math.pi + 0.5)
    return np.column_stack([u, np.clip(v, 0.0, 1.0)])


def _default_sky_dome_uv(geom) -> np.ndarray:
    vertices = np.asarray(getattr(geom, "vertices", []), dtype=np.float64)
    existing = getattr(getattr(geom, "visual", None), "uv", None)
    existing_uv = np.asarray(existing, dtype=np.float64) if existing is not None else np.empty((0, 2))
    if existing_uv.ndim == 2 and existing_uv.shape[0] == vertices.shape[0] and existing_uv.shape[1] >= 2:
        return existing_uv[:, :2]
    return _spherical_uv_for_vertices(vertices)


def _apply_default_sky_dome_material(scene_or_mesh):
    trimesh = _require_trimesh()
    from trimesh.visual.material import PBRMaterial

    texture_image = _default_sky_dome_texture_image()
    if isinstance(scene_or_mesh, trimesh.Scene):
        geometries = tuple(scene_or_mesh.geometry.values())
    else:
        geometries = (scene_or_mesh,)
    for geom in geometries:
        uv = _default_sky_dome_uv(geom)
        material = PBRMaterial(
            name=DEFAULT_SKY_DOME_MATERIAL_NAME,
            baseColorTexture=texture_image.copy(),
            emissiveTexture=texture_image.copy(),
            emissiveFactor=[1.0, 1.0, 1.0],
            metallicFactor=0.0,
            roughnessFactor=1.0,
            doubleSided=True,
        )
        geom.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    return scene_or_mesh


def _default_sky_dome_row() -> Dict[str, object] | None:
    if not DEFAULT_SKY_DOME_MESH_PATH.exists():
        return None
    return {
        "asset_id": DEFAULT_SKY_DOME_ASSET_ID,
        "category": "sky_dome",
        "text_desc": "Default extracted sky dome used as the generated scene environment dome.",
        "mesh_path": str(DEFAULT_SKY_DOME_MESH_PATH),
        "latent_path": "",
        "style_tags": ["sky", "sky_dome", "environment"],
        "quality_tier": 3,
        "asset_role": "environment",
        "source": "asset_editor_sky_dome_extract",
        "scene_eligible": True,
        "mesh_face_count": 960,
        "scale": 1.0,
        "dimensions_m": dict(DEFAULT_SKY_DOME_DIMENSIONS_M),
        "origin_alignment": "center",
    }


def _ensure_default_sky_dome_row(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    if any(str(row.get("asset_id", "")) == DEFAULT_SKY_DOME_ASSET_ID for row in rows):
        return rows
    sky_row = _default_sky_dome_row()
    if sky_row is None:
        return rows
    return [*rows, sky_row]


def _default_sky_dome_placement(
    config: StreetComposeConfig,
    row: Mapping[str, object] | None,
    mesh_metadata: _MeshCacheEntry | _MeshMetadata | None = None,
) -> StreetPlacement | None:
    if row is None:
        return None
    native_diameter_m = 0.0
    if mesh_metadata is not None:
        native_diameter_m = max(
            float(getattr(mesh_metadata, "half_x", 0.0) or 0.0) * 2.0,
            float(getattr(mesh_metadata, "native_height_y", 0.0) or 0.0),
            float(getattr(mesh_metadata, "half_z", 0.0) or 0.0) * 2.0,
        )
    if native_diameter_m <= 0.0 or not math.isfinite(native_diameter_m):
        dims = row.get("dimensions_m") if isinstance(row.get("dimensions_m"), Mapping) else DEFAULT_SKY_DOME_DIMENSIONS_M
        native_diameter_m = max(
            float(dims.get("width", DEFAULT_SKY_DOME_DIMENSIONS_M["width"])),
            float(dims.get("height", DEFAULT_SKY_DOME_DIMENSIONS_M["height"])),
            float(dims.get("depth", DEFAULT_SKY_DOME_DIMENSIONS_M["depth"])),
            1.0,
        )
    scene_span_m = max(
        float(getattr(config, "length_m", 80.0) or 80.0),
        float(getattr(config, "road_width_m", 12.0) or 12.0)
        + 2.0 * float(getattr(config, "sidewalk_width_m", 4.0) or 4.0)
        + 40.0,
        80.0,
    )
    target_diameter_m = max(
        native_diameter_m,
        DEFAULT_SKY_DOME_MIN_DIAMETER_M,
        scene_span_m * DEFAULT_SKY_DOME_SCENE_SPAN_MULTIPLIER,
    )
    scale = max(1.0, target_diameter_m / native_diameter_m)
    half_extent = 0.5 * native_diameter_m * scale
    return StreetPlacement(
        instance_id="environment_default_sky_dome",
        asset_id=DEFAULT_SKY_DOME_ASSET_ID,
        category="sky_dome",
        score=1.0,
        position_xyz=[0.0, 0.0, 0.0],
        yaw_deg=0.0,
        scale=float(scale),
        bbox_xz=[-half_extent, half_extent, -half_extent, half_extent],
        selection_source="default_sky_dome",
        placement_group="environment",
        constraint_penalty=0.0,
        feasibility_score=1.0,
        violated_rules=(),
    )


def _load_building_manifest(manifest_path: Path) -> List[Dict[str, object]]:
    """Load building assets from UrbanVerse manifest.

    Unlike street furniture assets, buildings don't require latent_path.
    We generate text descriptions for CLIP embedding from available metadata.
    """
    if not manifest_path.exists():
        return []  # No building manifest, return empty list

    rows: List[Dict[str, object]] = []
    base_dir = manifest_path.parent.resolve()
    for line_no, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        asset_id = str(payload.get("asset_id", "")).strip()
        if not asset_id:
            continue

        # Resolve mesh path
        mesh_path = str(payload.get("mesh_path", "")).strip()
        if mesh_path:
            mesh_path = _resolve_path(mesh_path, base_dir)
        else:
            continue  # Skip if no mesh path
        if not Path(mesh_path).is_file():
            logger.debug("Skipping building asset '%s' with missing mesh path: %s", asset_id, mesh_path)
            continue

        # Generate text description from bucket and tags if not provided
        text_desc = str(payload.get("text_desc", "")).strip()
        if not text_desc:
            bucket = str(payload.get("bucket", "")).strip()
            tags = payload.get("tags", [])
            tag_str = ", ".join(str(t) for t in tags) if tags else "urban building"
            text_desc = f"UrbanVerse building from bucket {bucket}, {tag_str}"

        row: Dict[str, object] = {
            "asset_id": asset_id,
            "category": "building",
            "text_desc": text_desc,
            "mesh_path": mesh_path,
            "source": str(payload.get("source", "UrbanVerse")).strip(),
            "bucket": str(payload.get("bucket", "")).strip(),
            "tags": list(payload.get("tags", [])) if isinstance(payload.get("tags"), list) else [],
            "asset_role": "building",
            "scene_eligible": bool(payload.get("scene_eligible", True)),
            "quality_tier": int(payload.get("quality_tier", 2)),
        }

        # Include dimensions if available
        if "dimensions_m" in payload and isinstance(payload["dimensions_m"], dict):
            dims = payload["dimensions_m"]
            row["frontage_width_m"] = float(dims.get("width", 0))
            row["depth_m"] = float(dims.get("depth", 0))
            row["height_m"] = float(dims.get("height", 0))
            row["dimensions_m"] = dict(dims)
        for optional_key in ("scale", "metric_width_m", "metric_depth_m", "metric_height_m"):
            if optional_key in payload:
                row[optional_key] = payload[optional_key]

        rows.append(row)

    return rows


def _generate_building_text_embeddings(
    rows: List[Dict[str, object]],
    embedder: ClipTextEmbedder,
) -> Dict[str, np.ndarray]:
    """Generate CLIP text embeddings for building assets.

    Returns a dict mapping asset_id to normalized embedding vector.
    """
    if not rows:
        return {}

    # Generate text descriptions for each building
    texts = []
    asset_ids = []
    for row in rows:
        # Create a descriptive text for CLIP embedding
        bucket = str(row.get("bucket", "")).strip()
        tags = row.get("tags", [])
        theme_tags = row.get("theme_tags", [])
        tag_str = ", ".join(str(t) for t in (tags + theme_tags)) if (tags or theme_tags) else "urban commercial building"

        frontage = float(row.get("frontage_width_m", 0))
        depth = float(row.get("depth_m", 0))
        height = float(row.get("height_m", 0))

        size_desc = ""
        if height >= 30:
            size_desc = "highrise commercial building"
        elif height >= 15:
            size_desc = "midrise office or residential building"
        else:
            size_desc = "lowrise storefront or residential building"

        text = f"{size_desc}, urbanverse bucket {bucket}, {tag_str}, {frontage:.1f}m wide, {depth:.1f}m deep"
        texts.append(text)
        asset_ids.append(row["asset_id"])

    # Encode all texts at once
    embeddings = embedder.encode_texts(texts)

    return {asset_id: embedding for asset_id, embedding in zip(asset_ids, embeddings)}


def _load_single_mesh(metadata: _MeshMetadata) -> _MeshCacheEntry:
    """Load a single mesh from disk and create a cache entry.

    This function does the actual mesh loading and Y-axis normalization.
    It's called lazily when the mesh is actually needed.
    """
    trimesh = _require_trimesh()
    mesh_path = Path(metadata.mesh_path).resolve()

    mesh_or_scene = trimesh.load(mesh_path, force="scene")
    if isinstance(mesh_or_scene, trimesh.Scene):
        if not mesh_or_scene.geometry:
            raise ValueError(f"empty mesh scene for asset '{metadata.asset_id}': {mesh_path}")
        display_geom = mesh_or_scene

        # Get initial bounds
        bounds = np.asarray(display_geom.bounds, dtype=np.float64)
        min_y_val = float(bounds[0][1])
        max_y_val = float(bounds[1][1])
        height = max_y_val - min_y_val
        span_x = float(bounds[1][0] - bounds[0][0])
        span_z = float(bounds[1][2] - bounds[0][2])
        span_xz = max(span_x, span_z)

        # Detect abnormal geometry: height >> width/depth suggests disjoint clusters
        has_disjoint_geometry = (
            height > 3.0 and height > span_xz * 2.5 and min_y_val < -0.1
        )

        if has_disjoint_geometry:
            all_vertices = []
            for geom in display_geom.geometry.values():
                if hasattr(geom, "vertices"):
                    all_vertices.append(np.asarray(geom.vertices))

            if all_vertices:
                all_verts = np.vstack(all_vertices)
                y_coords = all_verts[:, 1]
                ground_level = float(np.percentile(y_coords, 5))

                if abs(ground_level) > 1e-6:
                    display_geom.apply_translation([0.0, -ground_level, 0.0])
        elif abs(min_y_val) > 1e-6:
            display_geom.apply_translation([0.0, -min_y_val, 0.0])

        bounds = np.asarray(display_geom.bounds, dtype=np.float64)
        is_scene = True
    else:
        if mesh_or_scene.is_empty:
            raise ValueError(f"empty mesh for asset '{metadata.asset_id}': {mesh_path}")
        display_geom = mesh_or_scene
        bounds = np.asarray(display_geom.bounds, dtype=np.float64)
        min_y_val = float(bounds[0][1])
        if abs(min_y_val) > 1e-6:
            display_geom.apply_translation([0.0, -min_y_val, 0.0])
        bounds = np.asarray(display_geom.bounds, dtype=np.float64)
        is_scene = False

    source_scale = float(getattr(metadata, "source_scale", 1.0) or 1.0)
    if not math.isfinite(source_scale) or source_scale <= 0.0:
        source_scale = 1.0
    if abs(source_scale - 1.0) > 1e-9:
        display_geom.apply_scale(source_scale)
        bounds = np.asarray(display_geom.bounds, dtype=np.float64)

    if metadata.asset_id == DEFAULT_SKY_DOME_ASSET_ID:
        display_geom = _apply_default_sky_dome_material(display_geom)

    span = bounds[1] - bounds[0]
    return _MeshCacheEntry(
        mesh=display_geom,
        half_x=float(max(span[0] / 2.0, 1e-3)),
        half_z=float(max(span[2] / 2.0, 1e-3)),
        min_y=float(bounds[0][1]),
        center_x=float((bounds[0][0] + bounds[1][0]) / 2.0),
        center_z=float((bounds[0][2] + bounds[1][2]) / 2.0),
        is_scene=bool(is_scene),
        native_height_y=float(max(span[1], 1e-3)),
        source_scale=source_scale,
        source_scale_source=str(getattr(metadata, "source_scale_source", "") or ""),
        source_scale_confidence=str(getattr(metadata, "source_scale_confidence", "") or ""),
        source_scale_rejected_reason=str(getattr(metadata, "source_scale_rejected_reason", "") or ""),
        raw_size_m=dict(getattr(metadata, "raw_size_m", {}) or {}),
        metric_size_m=dict(getattr(metadata, "metric_size_m", {}) or {}),
    )


def _load_mesh_metadata(rows: List[Dict[str, str]]) -> Dict[str, _MeshMetadata]:
    """Load lightweight mesh metadata without loading actual mesh files.

    This is a memory-efficient alternative to _load_mesh_cache for layout
    computation phases where only bounding-box information is needed.
    """
    trimesh = _require_trimesh()
    metadata: Dict[str, _MeshMetadata] = {}

    for row in rows:
        asset_id = row["asset_id"]
        mesh_path = Path(row["mesh_path"]).resolve()

        if not mesh_path.exists():
            raise FileNotFoundError(f"mesh missing for asset '{asset_id}': {mesh_path}")

        # Load mesh to extract bounding box info (lightweight operation)
        try:
            mesh_or_scene = trimesh.load(mesh_path, force="scene", verbose=False)

            if isinstance(mesh_or_scene, trimesh.Scene):
                if not mesh_or_scene.geometry:
                    raise ValueError(f"empty mesh scene for asset '{asset_id}': {mesh_path}")
                display_geom = mesh_or_scene

                bounds = np.asarray(display_geom.bounds, dtype=np.float64)
                min_y_val = float(bounds[0][1])
                max_y_val = float(bounds[1][1])
                height = max_y_val - min_y_val
                span_x = float(bounds[1][0] - bounds[0][0])
                span_z = float(bounds[1][2] - bounds[0][2])
                span_xz = max(span_x, span_z)

                has_disjoint_geometry = (
                    height > 3.0 and height > span_xz * 2.5 and min_y_val < -0.1
                )

                if has_disjoint_geometry:
                    all_vertices = []
                    for geom in display_geom.geometry.values():
                        if hasattr(geom, "vertices"):
                            all_vertices.append(np.asarray(geom.vertices))

                    if all_vertices:
                        all_verts = np.vstack(all_vertices)
                        y_coords = all_verts[:, 1]
                        ground_level = float(np.percentile(y_coords, 5))
                        if abs(ground_level) > 1e-6:
                            min_y_val = min_y_val - ground_level

                span = bounds[1] - bounds[0]
                is_scene = True
            else:
                if mesh_or_scene.is_empty:
                    raise ValueError(f"empty mesh for asset '{asset_id}': {mesh_path}")
                bounds = np.asarray(mesh_or_scene.bounds, dtype=np.float64)
                min_y_val = float(bounds[0][1])
                span = bounds[1] - bounds[0]
                is_scene = False

            source_scale, source_scale_source, source_scale_confidence, source_scale_rejected_reason, metric_size_m = _source_scale_for_row(row, span)
            effective_span = span * float(source_scale)
            effective_bounds = bounds * float(source_scale)
            effective_min_y = float(min_y_val) * float(source_scale)
            raw_size_m = {
                "width_m": float(max(span[0], 0.0)),
                "depth_m": float(max(span[2], 0.0)),
                "height_m": float(max(span[1], 0.0)),
                "canopy_width_m": float(max(span[0], span[2], 0.0)),
            }

            metadata[asset_id] = _MeshMetadata(
                asset_id=asset_id,
                half_x=float(max(effective_span[0] / 2.0, 1e-3)),
                half_z=float(max(effective_span[2] / 2.0, 1e-3)),
                min_y=float(max(effective_min_y, 0.0)),  # Normalized to >= 0
                center_x=float((effective_bounds[0][0] + effective_bounds[1][0]) / 2.0),
                center_z=float((effective_bounds[0][2] + effective_bounds[1][2]) / 2.0),
                is_scene=bool(is_scene),
                native_height_y=float(max(effective_span[1], 1e-3)),
                mesh_path=str(mesh_path),
                source_scale=float(source_scale),
                source_scale_source=str(source_scale_source),
                source_scale_confidence=str(source_scale_confidence),
                source_scale_rejected_reason=str(source_scale_rejected_reason),
                raw_size_m=raw_size_m,
                metric_size_m=dict(metric_size_m),
            )
        finally:
            # Explicitly delete to help GC reclaim memory faster
            del mesh_or_scene
            if "display_geom" in locals():
                del display_geom

    return metadata


def _load_mesh_cache(rows: List[Dict[str, str]], max_mesh_cache_size: int = DEFAULT_MAX_MESH_CACHE_SIZE) -> _LazyMeshCache:
    """Load mesh cache with lazy loading for memory efficiency.

    Returns a _LazyMeshCache that stores metadata for all assets but only
    loads full mesh objects when needed. This significantly reduces memory
    usage during layout computation phases.

    Memory optimization:
    - For layout: use metadata only (half_x, half_z, etc.)
    - For export: call cache.preload() for needed assets before export
    """
    metadata = _load_mesh_metadata(rows)
    return _LazyMeshCache(metadata, max_mesh_cache_size=max_mesh_cache_size)


def _bbox_intersects(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2])


def _multi_box_intersects(
    bbox: Tuple[float, float, float, float],
    existing_bboxes: Sequence[Tuple[float, float, float, float]],
    neighbor_indices: Sequence[int],
    clearance: float = 0.1,
) -> bool:
    """Two-stage collision detection for multi-box assets.

    Stage 1 (Coarse): Check outer AABB against neighbors
    Stage 2 (Fine): If coarse overlaps, check all sub-box pairs

    Args:
        bbox: Outer bounding box of candidate asset (x_min, x_max, z_min, z_max)
        existing_bboxes: List of all existing asset bounding boxes
        neighbor_indices: Indices of potential colliding neighbors
        clearance: Minimum clearance distance between assets

    Returns:
        True if collision detected, False otherwise
    """
    for idx in neighbor_indices:
        other_bbox = existing_bboxes[int(idx)]

        # Stage 1: Coarse detection (outer bounds with clearance)
        coarse_overlap = not (
            bbox[1] + clearance < other_bbox[0] or
            bbox[0] - clearance > other_bbox[1] or
            bbox[3] + clearance < other_bbox[2] or
            bbox[2] - clearance > other_bbox[3]
        )

        if coarse_overlap:
            # Stage 2: Fine detection
            # If candidate has sub_boxes, use multi-box check
            # For now, fall back to single-box check
            # (Multi-box support requires passing sub_boxes separately)
            if _bbox_intersects(bbox, other_bbox):
                return True

    return False


def _bbox_intrudes_carriageway(
    bbox: Tuple[float, float, float, float],
    *,
    placement_ctx: object | None,
    config: StreetComposeConfig,
) -> bool:
    carriageway_geom = None
    if placement_ctx is not None:
        carriageway_geom = getattr(placement_ctx, "carriageway_polygon", None)
        if carriageway_geom is None:
            carriageway_geom = getattr(placement_ctx, "carriageway", None)
    if carriageway_geom is not None and not getattr(carriageway_geom, "is_empty", False):
        try:
            from shapely.geometry import box as shapely_box
        except Exception:
            carriageway_geom = None
        else:
            intersection = carriageway_geom.buffer(-0.1).intersection(
                shapely_box(float(bbox[0]), float(bbox[2]), float(bbox[1]), float(bbox[3]))
            )
            return bool(float(getattr(intersection, "area", 0.0) or 0.0) > 1e-6)
    carriageway_bbox = (
        -float(config.length_m) / 2.0,
        float(config.length_m) / 2.0,
        -float(config.road_width_m) / 2.0,
        float(config.road_width_m) / 2.0,
    )
    return _bbox_intersects(bbox, carriageway_bbox)


def _compute_bbox(
    x: float,
    z: float,
    yaw_deg: float,
    half_x: float,
    half_z: float,
    scale: float | Sequence[float],
    clearance: float,
) -> Tuple[float, float, float, float]:
    if isinstance(scale, (list, tuple)):
        scale_x = float(scale[0]) if len(scale) >= 1 else 1.0
        scale_z = float(scale[2]) if len(scale) >= 3 else float(scale[-1]) if len(scale) >= 1 else 1.0
    else:
        scale_x = float(scale)
        scale_z = float(scale)
    yaw_rad = math.radians(yaw_deg)
    cos_y = abs(math.cos(yaw_rad))
    sin_y = abs(math.sin(yaw_rad))
    aabb_half_x = cos_y * half_x * scale_x + sin_y * half_z * scale_z + clearance
    aabb_half_z = sin_y * half_x * scale_x + cos_y * half_z * scale_z + clearance
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


def _softmax_weights(scores: Sequence[float], temperature: float) -> List[float]:
    if not scores:
        return []
    temp = max(float(temperature), 1e-6)
    arr = np.asarray([float(score) for score in scores], dtype=np.float64)
    shifted = (arr - float(arr.max())) / temp
    weights = np.exp(shifted)
    total = float(weights.sum())
    if not np.isfinite(total) or total <= 0.0:
        return [1.0 / len(scores)] * len(scores)
    return (weights / total).tolist()


def _segment_node_lookup(road_segment_graph: object | None) -> Dict[str, object]:
    return {
        str(getattr(node, "segment_id", "")): node
        for node in getattr(road_segment_graph, "nodes", ()) or ()
    }


def _aggregate_solver_results(
    *,
    resolved_program,
    solver_results: Sequence[LayoutSolverResult],
    slot_plans: Sequence[object],
    road_segment_graph_summary: Dict[str, object] | None = None,
) -> LayoutSolverResult:
    if not solver_results:
        raise RuntimeError("solver_results cannot be empty")
    backend_requested = str(solver_results[0].backend_requested)
    backend_used_values = tuple(dict.fromkeys(str(result.backend_used) for result in solver_results))
    fallback_values = [str(result.fallback_reason).strip() for result in solver_results if str(result.fallback_reason).strip()]
    return LayoutSolverResult(
        resolved_program=resolved_program,
        band_solutions=tuple(
            band_solution
            for result in solver_results
            for band_solution in result.band_solutions
        ),
        slot_plans=tuple(slot_plans),
        rule_evaluations=tuple(
            evaluation
            for result in solver_results
            for evaluation in result.rule_evaluations
        ),
        edits=tuple(edit for result in solver_results for edit in result.edits),
        conflicts=tuple(conflict for result in solver_results for conflict in result.conflicts),
        topology_validity=float(sum(float(result.topology_validity) for result in solver_results) / len(solver_results)),
        cross_section_feasibility=float(sum(float(result.cross_section_feasibility) for result in solver_results) / len(solver_results)),
        rule_satisfaction_rate=float(sum(float(result.rule_satisfaction_rate) for result in solver_results) / len(solver_results)),
        editability=float(sum(float(result.editability) for result in solver_results) / len(solver_results)),
        conflict_explainability=float(sum(float(result.conflict_explainability) for result in solver_results) / len(solver_results)),
        active_constraints=tuple(
            dict.fromkeys(
                constraint_name
                for result in solver_results
                for constraint_name in result.active_constraints
            )
        ),
        throughput_feasibility={
            "overall_satisfied": all(bool(result.throughput_feasibility.get("overall_satisfied", True)) for result in solver_results),
            "by_mode": {
                mode: data
                for result in solver_results
                for mode, data in dict(result.throughput_feasibility.get("by_mode", {})).items()
            },
        },
        objective_profile=str(getattr(resolved_program, "objective_profile", "balanced")),
        objective_score_breakdown={
            key: float(sum(float(result.objective_score_breakdown.get(key, 0.0)) for result in solver_results))
            for key in {"total_width_score", "unused_row_budget_m", "slot_mix_bias"}
        },
        backend_requested=backend_requested,
        backend_used=backend_used_values[0] if len(backend_used_values) == 1 else "mixed",
        fallback_reason=" | ".join(dict.fromkeys(fallback_values)),
        road_segment_graph_summary=road_segment_graph_summary,
    )


def _globalize_theme_slot_plans(
    slot_plans: Sequence[object],
    *,
    theme_segment: ThemeSegment,
    road_segment_graph: object | None,
) -> Tuple[Tuple[object, ...], Dict[str, object]]:
    nodes_by_id = _segment_node_lookup(road_segment_graph)
    theme_nodes = [
        nodes_by_id[segment_id]
        for segment_id in theme_segment.segment_ids
        if segment_id in nodes_by_id
    ]
    theme_nodes = sorted(theme_nodes, key=lambda node: float(getattr(node, "station_center_m", 0.0)))
    ordered_slots = sorted(slot_plans, key=lambda slot: float(getattr(slot, "x_center_m", 0.0)))
    slot_to_segment: Dict[str, object] = {}
    updated_slots: List[object] = []
    for idx, slot in enumerate(ordered_slots):
        slot_id = f"{theme_segment.theme_id}_{getattr(slot, 'slot_id', f'slot_{idx:03d}')}"
        node = None
        if getattr(slot, "anchor_position_xz", None) is not None and theme_nodes:
            anchor_x, anchor_z = getattr(slot, "anchor_position_xz")
            node = min(
                theme_nodes,
                key=lambda item: math.hypot(
                    float(getattr(item, "center_xy", (0.0, 0.0))[0]) - float(anchor_x),
                    float(getattr(item, "center_xy", (0.0, 0.0))[1]) - float(anchor_z),
                ),
            )
            slot_x = float(anchor_x)
            slot_z = float(anchor_z)
        elif theme_nodes:
            node_idx = min(int(math.floor(idx * len(theme_nodes) / max(len(ordered_slots), 1))), len(theme_nodes) - 1)
            node = theme_nodes[node_idx]
            center_x = float(getattr(node, "center_xy", (0.0, 0.0))[0])
            center_y = float(getattr(node, "center_xy", (0.0, 0.0))[1])
            original_z_center = float(getattr(slot, "z_center_m", 0.0))
            if abs(original_z_center) > 1e-6:
                start_xy = tuple(float(v) for v in getattr(node, "start_xy", (0.0, 0.0)))
                end_xy = tuple(float(v) for v in getattr(node, "end_xy", (0.0, 0.0)))
                dx = end_xy[0] - start_xy[0]
                dy = end_xy[1] - start_xy[1]
                seg_len = math.hypot(dx, dy)
                if seg_len > 1e-6:
                    left_normal = (-dy / seg_len, dx / seg_len)
                    slot_x = center_x + left_normal[0] * original_z_center
                    slot_z = center_y + left_normal[1] * original_z_center
                else:
                    slot_x = center_x
                    slot_z = center_y
            else:
                slot_x = center_x
                slot_z = center_y
        else:
            slot_x = float(getattr(slot, "x_center_m", 0.0)) + float(theme_segment.center_x_m)
            slot_z = float(getattr(slot, "z_center_m", 0.0))
        updated = replace(
            slot,
            slot_id=slot_id,
            x_center_m=float(slot_x),
            z_center_m=float(slot_z),
            theme_id=theme_segment.theme_id,
        )
        updated_slots.append(updated)
        if node is not None:
            slot_to_segment[slot_id] = node
    return tuple(updated_slots), slot_to_segment


def _annotation_furniture_to_slot_plans(
    road_segment_graph: object,
    theme_segments: Sequence[object],
) -> Tuple[List[object], Dict[str, object]]:
    """Convert explicit annotation furniture instances to LayoutSlotPlan objects.

    Reads ``street_furniture_instances`` from each :class:`RoadSegmentNode` in
    *road_segment_graph* and returns equivalent ``LayoutSlotPlan`` objects that
    can be injected directly into the slot-to-asset binding pipeline.
    """

    slots: List[object] = []
    segment_lookup: Dict[str, object] = {}

    if road_segment_graph is None:
        return slots, segment_lookup

    nodes = getattr(road_segment_graph, "nodes", ()) or ()

    # Build theme_id lookup: segment_id -> theme_id
    seg_to_theme: Dict[str, str] = {}
    for ts in theme_segments:
        for sid in getattr(ts, "segment_ids", ()):
            seg_to_theme[str(sid)] = str(ts.theme_id)
    default_theme = str(theme_segments[0].theme_id) if theme_segments else ""

    for node in nodes:
        instances = getattr(node, "street_furniture_instances", ()) or ()
        if not instances:
            continue

        start_xy = tuple(float(v) for v in node.start_xy)
        end_xy = tuple(float(v) for v in node.end_xy)
        dx = end_xy[0] - start_xy[0]
        dy = end_xy[1] - start_xy[1]
        seg_len = math.hypot(dx, dy)
        if seg_len < 1e-6:
            continue

        nx, ny = -dy / seg_len, dx / seg_len  # left normal
        station_start = float(node.station_start_m)
        node_len = float(node.length_m)
        theme_id = seg_to_theme.get(node.segment_id, default_theme)

        for inst in instances:
            t = (float(inst.station_m) - station_start) / max(node_len, 1e-6)
            t = max(0.0, min(1.0, t))
            base_x = start_xy[0] + t * dx
            base_y = start_xy[1] + t * dy
            lat = float(inst.lateral_offset_m)
            world_x = base_x + nx * lat
            world_z = base_y + ny * lat

            # Determine side from the strip zone when available
            side = "left" if lat < 0 else "right"
            for strip in getattr(node, "cross_section_strips", ()):
                if getattr(strip, "strip_id", None) == inst.strip_id:
                    side = "left" if getattr(strip, "zone", "right") == "left" else "right"
                    break

            slot_id = f"annot_{inst.instance_id}"
            slot = LayoutSlotPlan(
                slot_id=slot_id,
                category=str(inst.kind),
                band_name=f"{side}_furnishing",
                x_center_m=world_x,
                z_center_m=world_z,
                spacing_m=DEFAULT_SPACING_M.get(str(inst.kind), 10.0),
                side=side,
                priority=1.0,
                required=True,
                anchor_position_xz=(world_x, world_z),
                theme_id=theme_id,
            )
            slots.append(slot)
            segment_lookup[slot_id] = node

    return slots, segment_lookup


def _center_planting_tree_slot_plans(
    *,
    road_segment_graph: object | None,
    theme_segments: Sequence[object],
    placement_ctx: object | None,
    spacing_m: float = 28.0,
    max_slots: int = 48,
) -> Tuple[List[LayoutSlotPlan], Dict[str, object]]:
    """Inject tree slots along center planting strips such as grass medians."""

    slots: List[LayoutSlotPlan] = []
    segment_lookup: Dict[str, object] = {}
    if road_segment_graph is None or placement_ctx is None:
        return slots, segment_lookup

    center_band_names = ("center_grass_belt", "center_median_green")
    strip_zones = getattr(placement_ctx, "strip_zones", {}) or {}
    if not any(name in strip_zones for name in center_band_names):
        return slots, segment_lookup

    seg_to_theme: Dict[str, str] = {}
    for ts in theme_segments:
        for sid in getattr(ts, "segment_ids", ()):
            seg_to_theme[str(sid)] = str(ts.theme_id)
    default_theme = str(theme_segments[0].theme_id) if theme_segments else ""

    last_point: Tuple[float, float] | None = None
    nodes = sorted(
        tuple(getattr(road_segment_graph, "nodes", ()) or ()),
        key=lambda node: (
            int(getattr(node, "road_id", 0) or 0),
            float(getattr(node, "station_center_m", 0.0) or 0.0),
            str(getattr(node, "segment_id", "") or ""),
        ),
    )
    for node in nodes:
        segment_id = str(getattr(node, "segment_id", "") or "")
        segment_zones = (getattr(placement_ctx, "segment_strip_zones", {}) or {}).get(segment_id, {})
        band_name = next(
            (
                candidate
                for candidate in center_band_names
                if segment_zones.get(candidate) is not None and not getattr(segment_zones.get(candidate), "is_empty", False)
            ),
            "",
        )
        if not band_name:
            continue
        zone = segment_zones[band_name]
        center_xy = tuple(float(value) for value in getattr(node, "center_xy", (0.0, 0.0)))
        point_xz = center_xy
        if not _point_in_zone(zone, point_xz, tolerance_m=0.01):
            try:
                representative = zone.representative_point()
                point_xz = (float(representative.x), float(representative.y))
            except Exception:
                continue
        if last_point is not None and math.hypot(point_xz[0] - last_point[0], point_xz[1] - last_point[1]) < float(spacing_m):
            continue
        slot_id = f"center_planting_tree_{len(slots):03d}"
        slot = LayoutSlotPlan(
            slot_id=slot_id,
            category="tree",
            band_name=band_name,
            x_center_m=float(point_xz[0]),
            z_center_m=float(point_xz[1]),
            spacing_m=float(spacing_m),
            side="center",
            priority=1.15,
            required=False,
            anchor_position_xz=(float(point_xz[0]), float(point_xz[1])),
            theme_id=seg_to_theme.get(segment_id, default_theme),
        )
        slots.append(slot)
        segment_lookup[slot_id] = node
        last_point = point_xz
        if len(slots) >= int(max_slots):
            break

    return slots, segment_lookup


def _sample_pose_osm_for_segment(
    category: str,
    placement_ctx: object,
    rng: random.Random,
    *,
    segment_node: object | None = None,
    slot_side: str = "",
    slot_band_name: str = "",
    band_width_m: float = 1.0,
    anchor_position_xz: Optional[Tuple[float, float]] = None,
) -> Optional[Tuple[float, float, float]]:
    from .placement_zones import compute_facing_angle, sample_slot_on_sidewalk

    strip_zone = _target_strip_zone(
        placement_ctx=placement_ctx,
        segment_node=segment_node,
        slot_side=slot_side,
        band_name=slot_band_name,
    )
    if anchor_position_xz is not None:
        point = (float(anchor_position_xz[0]), float(anchor_position_xz[1]))
        if strip_zone is not None and not _point_in_zone(strip_zone, point):
            return None
        yaw = _yaw_for_asset_category(
            category,
            compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
        )
        return point[0], point[1], yaw

    if strip_zone is not None and not getattr(strip_zone, "is_empty", False):
        point = sample_slot_on_sidewalk(strip_zone, rng)
        if point is not None:
            yaw = _yaw_for_asset_category(
                category,
                compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
            )
            return point[0], point[1], yaw

    if segment_node is not None:
        try:
            from shapely.geometry import Point as ShapelyPoint
        except Exception:
            segment_node = None
        else:
            start_xy = tuple(float(v) for v in getattr(segment_node, "start_xy", (0.0, 0.0)))
            end_xy = tuple(float(v) for v in getattr(segment_node, "end_xy", (0.0, 0.0)))
            center_xy = tuple(float(v) for v in getattr(segment_node, "center_xy", (0.0, 0.0)))
            dx = end_xy[0] - start_xy[0]
            dz = end_xy[1] - start_xy[1]
            length = math.hypot(dx, dz)
            if length > 1e-6:
                tangent = (dx / length, dz / length)
                left_normal = (-tangent[1], tangent[0])
                side_pref = slot_side or SIDE_PREF.get(category, "both")
                sign = 1.0 if side_pref == "left" else -1.0 if side_pref == "right" else (1.0 if rng.random() >= 0.5 else -1.0)
                normal = left_normal if sign > 0 else (-left_normal[0], -left_normal[1])
                carriageway_half = float(getattr(placement_ctx, "carriageway_width_m", 8.0) or 8.0) / 2.0
                lateral = carriageway_half + max(float(band_width_m) * 0.45, 0.8)
                along = rng.uniform(-max(length * 0.25, 0.5), max(length * 0.25, 0.5))
                point = (
                    center_xy[0] + tangent[0] * along + normal[0] * lateral,
                    center_xy[1] + tangent[1] * along + normal[1] * lateral,
                )
                preferred_zone = getattr(placement_ctx, "left_sidewalk_zone", None) if sign > 0 else getattr(placement_ctx, "right_sidewalk_zone", None)
                candidate_zone = preferred_zone if preferred_zone is not None and not getattr(preferred_zone, "is_empty", False) else placement_ctx.sidewalk_zone
                if strip_zone is not None and not getattr(strip_zone, "is_empty", False):
                    candidate_zone = strip_zone
                if candidate_zone is not None and not getattr(candidate_zone, "is_empty", False) and candidate_zone.buffer(0.05).contains(ShapelyPoint(point)):
                    yaw = _yaw_for_asset_category(
                        category,
                        compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
                    )
                    return point[0], point[1], yaw

    side_pref = SIDE_PREF.get(category, "both")
    overall_zone = placement_ctx.sidewalk_zone  # type: ignore[attr-defined]
    if side_pref == "left":
        preferred_zone = getattr(placement_ctx, "left_sidewalk_zone", None)
    elif side_pref == "right":
        preferred_zone = getattr(placement_ctx, "right_sidewalk_zone", None)
    else:
        preferred_zone = overall_zone
    zone = preferred_zone
    if strip_zone is not None and not getattr(strip_zone, "is_empty", False):
        zone = strip_zone
    if zone is None or getattr(zone, "is_empty", False):
        zone = overall_zone
    point = sample_slot_on_sidewalk(zone, rng)
    if point is None and zone is not overall_zone:
        point = sample_slot_on_sidewalk(overall_zone, rng)
    if point is None:
        return None
    yaw = _yaw_for_asset_category(
        category,
        compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
    )
    return point[0], point[1], yaw


def _placeholder_building_entry(
    *,
    asset_id: str,
    frontage_width_m: float,
    depth_m: float,
    height_class: str,
    theme_name: str,
    target_height_m: float = 0.0,
) -> _MeshCacheEntry:
    try:
        from .parametric_assets import generate_parametric_asset

        params: Dict[str, object] = {
            "frontage_width_m": float(frontage_width_m),
            "depth_m": float(depth_m),
            "height_class": str(height_class),
            "theme_name": str(theme_name),
        }
        if target_height_m > 0.0:
            params["height_m"] = float(target_height_m)
        result = generate_parametric_asset(
            {
                "asset_kind": "building",
                "runtime_profile": "preview",
                "params": params,
            }
        )
        mesh = result.mesh
    except Exception:
        trimesh = _require_trimesh()
        if target_height_m > 0.0:
            height_m = float(target_height_m)
        else:
            height_m = {
                "lowrise": max(float(frontage_width_m) * 0.8, 8.0),
                "midrise": max(float(frontage_width_m) * 1.4, 14.0),
                "highrise": max(float(frontage_width_m) * 2.0, 22.0),
            }.get(str(height_class), max(float(frontage_width_m) * 1.2, 12.0))
        mesh = trimesh.creation.box(extents=(float(frontage_width_m), float(height_m), float(depth_m)))
        face_color = {
            "residential": (188, 174, 153, 255),
            "commercial": (176, 184, 192, 255),
            "transit": (151, 165, 182, 255),
            "green": (166, 171, 148, 255),
        }.get(str(theme_name), (178, 180, 178, 255))
        mesh.visual.face_colors = list(face_color)
    bounds = mesh.bounds
    span = bounds[1] - bounds[0]
    return _MeshCacheEntry(
        mesh=mesh,
        half_x=float(max(span[0] / 2.0, 1e-3)),
        half_z=float(max(span[2] / 2.0, 1e-3)),
        min_y=float(bounds[0][1]),
        center_x=float((bounds[0][0] + bounds[1][0]) / 2.0),
        center_z=float((bounds[0][2] + bounds[1][2]) / 2.0),
        native_height_y=float(max(span[1], 1e-3)),
        raw_size_m={
            "width_m": float(max(span[0], 0.0)),
            "depth_m": float(max(span[2], 0.0)),
            "height_m": float(max(span[1], 0.0)),
            "canopy_width_m": float(max(span[0], span[2], 0.0)),
        },
    )


def _rotate_world_xz_to_local(dx: float, dz: float, yaw_deg: float) -> Tuple[float, float]:
    yaw_rad = math.radians(float(yaw_deg))
    cos_y = math.cos(yaw_rad)
    sin_y = math.sin(yaw_rad)
    return (
        float(dx * cos_y - dz * sin_y),
        float(dx * sin_y + dz * cos_y),
    )


def _rotate_local_xz_to_world(local_x: float, local_z: float, yaw_deg: float) -> Tuple[float, float]:
    yaw_rad = math.radians(float(yaw_deg))
    cos_y = math.cos(yaw_rad)
    sin_y = math.sin(yaw_rad)
    return (
        float(local_x * cos_y + local_z * sin_y),
        float(-local_x * sin_y + local_z * cos_y),
    )


def _building_door_local_pose(
    *,
    street_edge_xz: Optional[Tuple[float, float]],
    placement_xz: Tuple[float, float],
    yaw_deg: float,
    side: str,
    entry: _MeshCacheEntry,
    scale_xyz: Sequence[float],
    facade_offset_m: float,
) -> Tuple[Tuple[float, float], str] | None:
    scale_x = float(scale_xyz[0]) if len(scale_xyz) >= 1 else 1.0
    scale_z = float(scale_xyz[2]) if len(scale_xyz) >= 3 else scale_x
    center_x = float(entry.center_x) * scale_x
    center_z = float(entry.center_z) * scale_z
    half_x = max(float(entry.half_x) * scale_x, 1e-3)
    half_z = max(float(entry.half_z) * scale_z, 1e-3)
    candidate_faces = (
        ("front", (float(center_x), float(center_z - half_z - facade_offset_m))),
        ("back", (float(center_x), float(center_z + half_z + facade_offset_m))),
        ("left", (float(center_x - half_x - facade_offset_m), float(center_z))),
        ("right", (float(center_x + half_x + facade_offset_m), float(center_z))),
    )

    if street_edge_xz is not None:
        sx = float(street_edge_xz[0])
        sz = float(street_edge_xz[1])
        if abs(sx - float(placement_xz[0])) + abs(sz - float(placement_xz[1])) > 1e-6:
            best: Tuple[float, str, Tuple[float, float]] | None = None
            for facing, (local_x, local_z) in candidate_faces:
                world_dx, world_dz = _rotate_local_xz_to_world(local_x, local_z, yaw_deg)
                wx = float(placement_xz[0]) + float(world_dx)
                wz = float(placement_xz[1]) + float(world_dz)
                distance_sq = (wx - sx) ** 2 + (wz - sz) ** 2
                if best is None or distance_sq < best[0]:
                    best = (float(distance_sq), str(facing), (float(local_x), float(local_z)))
            if best is not None:
                return best[2], best[1]

    side_lc = str(side).strip().lower()
    if side_lc == "left":
        return candidate_faces[0][1], candidate_faces[0][0]
    if side_lc == "right":
        return candidate_faces[1][1], candidate_faces[1][0]
    return None


def _resolve_building_door_spec(
    *,
    target: Mapping[str, object],
    entry: _MeshCacheEntry,
    scale_xyz: Sequence[float],
) -> Dict[str, object]:
    frontage_width_m = float(target.get("frontage_width_m", 0.0) or 0.0)
    depth_m = float(target.get("depth_m", 0.0) or 0.0)
    yaw_deg = float(target.get("yaw_deg", 0.0) or 0.0)
    placement_xz_raw = target.get("placement_xz", target.get("center_xz", (0.0, 0.0))) or (0.0, 0.0)
    street_edge_xz_raw = target.get("street_edge_xz", ()) or ()
    side = str(target.get("side", "") or "").strip().lower()
    if frontage_width_m < 1.2 or depth_m < 1.0:
        return {
            "door_added": False,
            "door_facing": "",
            "door_center_local_x": 0.0,
            "door_width_m": 0.0,
            "door_height_m": 0.0,
            "door_dims_m": {},
            "door_center_world_xyz": [],
            "door_missing_reason": "mesh_too_small_for_door",
        }

    placement_xz = (
        float(placement_xz_raw[0]) if len(placement_xz_raw) >= 2 else 0.0,
        float(placement_xz_raw[1]) if len(placement_xz_raw) >= 2 else 0.0,
    )
    street_edge_xz: Optional[Tuple[float, float]] = None
    if len(street_edge_xz_raw) >= 2:
        street_edge_xz = (float(street_edge_xz_raw[0]), float(street_edge_xz_raw[1]))

    if street_edge_xz is not None:
        dx = float(street_edge_xz[0] - placement_xz[0])
        dz = float(street_edge_xz[1] - placement_xz[1])
        if abs(dx) + abs(dz) <= 1e-6:
            street_edge_xz = None
        else:
            local_dir_x, local_dir_z = _rotate_world_xz_to_local(dx, dz, yaw_deg)
    if street_edge_xz is None:
        if side == "left":
            local_dir_x, local_dir_z = 0.0, -1.0
        elif side == "right":
            local_dir_x, local_dir_z = 0.0, 1.0
        else:
            return {
                "door_added": False,
                "door_facing": "",
                "door_center_local_x": 0.0,
                "door_width_m": 0.0,
                "door_height_m": 0.0,
                "door_dims_m": {},
                "door_center_world_xyz": [],
                "door_missing_reason": "no_street_facing_side_resolved",
            }

    if abs(local_dir_x) >= abs(local_dir_z):
        door_facing = "right" if local_dir_x > 0.0 else "left"
    else:
        door_facing = "back" if local_dir_z > 0.0 else "front"

    building_height_m = float(target.get("target_height_m", 0.0) or 0.0)
    if building_height_m <= 0.0:
        scale_y = float(scale_xyz[1]) if len(scale_xyz) >= 2 else 1.0
        building_height_m = float(max(entry.native_height_y * scale_y, 4.0))
    if building_height_m <= 2.0:
        return {
            "door_added": False,
            "door_facing": "",
            "door_center_local_x": 0.0,
            "door_width_m": 0.0,
            "door_height_m": 0.0,
            "door_dims_m": {},
            "door_center_world_xyz": [],
            "door_missing_reason": "mesh_too_small_for_door",
        }

    door_width_m = float(min(1.6, max(1.0, frontage_width_m * 0.12)))
    door_height_m = float(min(3.2, max(2.2, building_height_m * 0.18)))
    door_thickness_m = 0.08
    facade_offset_m = door_thickness_m / 2.0 + 0.015
    local_pose = _building_door_local_pose(
        street_edge_xz=street_edge_xz,
        placement_xz=placement_xz,
        yaw_deg=yaw_deg,
        side=side,
        entry=entry,
        scale_xyz=scale_xyz,
        facade_offset_m=facade_offset_m,
    )
    if local_pose is None:
        return {
            "door_added": False,
            "door_facing": "",
            "door_center_local_x": 0.0,
            "door_width_m": 0.0,
            "door_height_m": 0.0,
            "door_dims_m": {},
            "door_center_world_xyz": [],
            "door_missing_reason": "no_street_facing_side_resolved",
        }
    (local_door_x, local_door_z), door_facing = local_pose

    world_dx, world_dz = _rotate_local_xz_to_world(local_door_x, local_door_z, yaw_deg)
    return {
        "door_added": True,
        "door_facing": str(door_facing),
        "door_center_local_x": float(local_door_x - (float(entry.center_x) * (float(scale_xyz[0]) if len(scale_xyz) >= 1 else 1.0))),
        "door_width_m": float(door_width_m),
        "door_height_m": float(door_height_m),
        "door_dims_m": {
            "width_m": float(door_width_m),
            "height_m": float(door_height_m),
            "thickness_m": float(door_thickness_m),
        },
        "door_center_world_xyz": [
            float(placement_xz[0] + world_dx),
            float(door_height_m / 2.0),
            float(placement_xz[1] + world_dz),
        ],
        "door_missing_reason": "",
    }


def _door_colors_for_land_use(land_use_type: str) -> Dict[str, Tuple[int, int, int, int]]:
    land_use_key = str(land_use_type or "").strip().lower()
    if land_use_key in {"commercial", "transit"}:
        return {
            "panel": (132, 159, 184, 228),
            "frame": (235, 236, 238, 255),
            "canopy": (233, 216, 200, 238),
        }
    return {
        "panel": (114, 84, 63, 255),
        "frame": (224, 214, 204, 255),
        "canopy": (208, 196, 184, 230),
    }


def _create_attached_building_door_meshes(
    *,
    plan: BuildingPlacementPlan,
    entry: _MeshCacheEntry,
) -> List[object]:
    trimesh = _require_trimesh()
    if not bool(plan.door_added):
        return []
    door_dims = dict(plan.door_dims_m or {})
    door_width_m = float(door_dims.get("width_m", plan.door_width_m) or 0.0)
    door_height_m = float(door_dims.get("height_m", plan.door_height_m) or 0.0)
    door_thickness_m = float(door_dims.get("thickness_m", 0.08) or 0.08)
    if door_width_m <= 0.0 or door_height_m <= 0.0:
        return []

    scale_y = float(plan.scale_xyz[1]) if len(plan.scale_xyz) >= 2 else 1.0
    local_ground_y = float(entry.min_y) * scale_y
    panel_center_y = local_ground_y + door_height_m / 2.0
    facade_offset_m = door_thickness_m / 2.0 + 0.015
    local_pose = _building_door_local_pose(
        street_edge_xz=tuple(plan.street_edge_xz) if tuple(plan.street_edge_xz) else None,
        placement_xz=tuple(plan.placement_xz),
        yaw_deg=float(plan.yaw_deg),
        side=str(plan.side),
        entry=entry,
        scale_xyz=plan.scale_xyz,
        facade_offset_m=facade_offset_m,
    )
    if local_pose is None:
        return []
    (local_center_x, local_center_z), door_facing = local_pose

    if str(door_facing) == "front":
        local_center = (local_center_x, panel_center_y, local_center_z)
        panel_extents = (door_width_m, door_height_m, door_thickness_m)
        frame_span_extents = (0.08, door_height_m + 0.14, door_thickness_m * 1.2)
        lintel_extents = (door_width_m + 0.18, 0.08, door_thickness_m * 1.2)
        frame_offsets = [(-door_width_m / 2.0 - 0.05, 0.0, 0.0), (door_width_m / 2.0 + 0.05, 0.0, 0.0)]
        lintel_offset = (0.0, door_height_m / 2.0 + 0.05, 0.0)
        canopy_extents = (door_width_m + 0.45, 0.05, 0.45)
        canopy_offset = (0.0, door_height_m / 2.0 + 0.3, -0.2)
    elif str(door_facing) == "back":
        local_center = (local_center_x, panel_center_y, local_center_z)
        panel_extents = (door_width_m, door_height_m, door_thickness_m)
        frame_span_extents = (0.08, door_height_m + 0.14, door_thickness_m * 1.2)
        lintel_extents = (door_width_m + 0.18, 0.08, door_thickness_m * 1.2)
        frame_offsets = [(-door_width_m / 2.0 - 0.05, 0.0, 0.0), (door_width_m / 2.0 + 0.05, 0.0, 0.0)]
        lintel_offset = (0.0, door_height_m / 2.0 + 0.05, 0.0)
        canopy_extents = (door_width_m + 0.45, 0.05, 0.45)
        canopy_offset = (0.0, door_height_m / 2.0 + 0.3, 0.2)
    elif str(door_facing) == "left":
        local_center = (local_center_x, panel_center_y, local_center_z)
        panel_extents = (door_thickness_m, door_height_m, door_width_m)
        frame_span_extents = (door_thickness_m * 1.2, door_height_m + 0.14, 0.08)
        lintel_extents = (door_thickness_m * 1.2, 0.08, door_width_m + 0.18)
        frame_offsets = [(0.0, 0.0, -door_width_m / 2.0 - 0.05), (0.0, 0.0, door_width_m / 2.0 + 0.05)]
        lintel_offset = (0.0, door_height_m / 2.0 + 0.05, 0.0)
        canopy_extents = (0.45, 0.05, door_width_m + 0.45)
        canopy_offset = (-0.2, door_height_m / 2.0 + 0.3, 0.0)
    else:
        local_center = (local_center_x, panel_center_y, local_center_z)
        panel_extents = (door_thickness_m, door_height_m, door_width_m)
        frame_span_extents = (door_thickness_m * 1.2, door_height_m + 0.14, 0.08)
        lintel_extents = (door_thickness_m * 1.2, 0.08, door_width_m + 0.18)
        frame_offsets = [(0.0, 0.0, -door_width_m / 2.0 - 0.05), (0.0, 0.0, door_width_m / 2.0 + 0.05)]
        lintel_offset = (0.0, door_height_m / 2.0 + 0.05, 0.0)
        canopy_extents = (0.45, 0.05, door_width_m + 0.45)
        canopy_offset = (0.2, door_height_m / 2.0 + 0.3, 0.0)

    colors = _door_colors_for_land_use(plan.land_use_type)
    meshes: List[object] = []

    panel = trimesh.creation.box(extents=panel_extents)
    panel.visual.face_colors = list(colors["panel"])
    panel.apply_translation(local_center)
    meshes.append(panel)

    for offset in frame_offsets:
        frame = trimesh.creation.box(extents=frame_span_extents)
        frame.visual.face_colors = list(colors["frame"])
        frame.apply_translation(
            [
                float(local_center[0] + offset[0]),
                float(local_center[1] + offset[1]),
                float(local_center[2] + offset[2]),
            ]
        )
        meshes.append(frame)

    lintel = trimesh.creation.box(extents=lintel_extents)
    lintel.visual.face_colors = list(colors["frame"])
    lintel.apply_translation(
        [
            float(local_center[0] + lintel_offset[0]),
            float(local_center[1] + lintel_offset[1]),
            float(local_center[2] + lintel_offset[2]),
        ]
    )
    meshes.append(lintel)

    if str(plan.land_use_type).strip().lower() in {"commercial", "transit"}:
        canopy = trimesh.creation.box(extents=canopy_extents)
        canopy.visual.face_colors = list(colors["canopy"])
        canopy.apply_translation(
            [
                float(local_center[0] + canopy_offset[0]),
                float(local_center[1] + canopy_offset[1]),
                float(local_center[2] + canopy_offset[2]),
            ]
        )
        meshes.append(canopy)

    return meshes


def _pick_category_candidate(
    query: str,
    category: str,
    topk: int,
    embedder: ClipTextEmbedder,
    index_store: FaissIndexStore,
    asset_by_id: Dict[str, Dict[str, object]],
    category_pool: List[Dict[str, object]],
    used_asset_ids: set[str],
    rng: random.Random,
    config: Optional[StreetComposeConfig] = None,
    placement_policy: str = "rule",
    policy_runtime: Optional[LayoutPolicyRuntime] = None,
    policy_temperature: float = SOFTMAX_TEMPERATURE,
    feature_context: Optional[PolicyFeatureContext] = None,
    return_details: bool = False,
    asset_id_whitelist: Optional[set[str]] = None,
    stable_selection_key: str = "",
) -> Tuple[Dict[str, object], float, str] | Tuple[Dict[str, object], float, str, Dict[str, object]]:
    allowlist_row, allowlist_rows = _stable_curated_allowlist_row(
        category_pool,
        category=category,
        config=config,
        stable_selection_key=stable_selection_key or f"{query}:{category}",
        asset_id_whitelist=asset_id_whitelist,
    )
    if allowlist_row is not None:
        decision_payload: Dict[str, object] = {
            "candidates": [
                {
                    "asset_id": str(row.get("asset_id", "")),
                    "category": str(row.get("category", "")),
                    "score": 1.0,
                }
                for row in allowlist_rows
            ],
            "chosen_index": next(
                (
                    idx
                    for idx, row in enumerate(allowlist_rows)
                    if str(row.get("asset_id", "")) == str(allowlist_row.get("asset_id", ""))
                ),
                0,
            ),
            "top3_hit": True,
            "curated_asset_allowlist": True,
            "stable_selection_key": str(stable_selection_key or f"{query}:{category}"),
            "allowlist_candidate_count": int(len(allowlist_rows)),
        }
        if return_details:
            return dict(allowlist_row), 1.0, _CURATED_ALLOWLIST_SELECTION_SOURCE, decision_payload
        return dict(allowlist_row), 1.0, _CURATED_ALLOWLIST_SELECTION_SOURCE

    def _pick_weighted(
        candidates: List[Tuple[Dict[str, object], float]],
        temperature: float,
    ) -> Tuple[Dict[str, object], float, int]:
        scores = [float(score) for _, score in candidates]
        weights = _softmax_weights(scores, temperature)
        pick_idx = rng.choices(range(len(candidates)), weights=weights, k=1)[0]
        row, score = candidates[pick_idx]
        return row, float(score), int(pick_idx)

    def _pick_with_policy(candidates: List[Tuple[Dict[str, object], float]]) -> Tuple[Dict[str, object], float, int]:
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
    matching_hits: List[Tuple[Dict[str, object], float]] = []
    all_hits: List[Dict[str, object]] = []
    for hit in hits:
        if asset_id_whitelist is not None and hit.asset_id not in asset_id_whitelist:
            continue
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
        ranked_hits = list(matching_hits)
        if config is not None:
            ranked_hits, curation_info = curate_candidates(ranked_hits, category=category, config=config)
            decision_payload.update(curation_info)
        available_hits = [candidate for candidate in ranked_hits if candidate[0]["asset_id"] not in used_asset_ids]
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
                row, score, local_idx = _pick_with_policy(ranked_hits)
                source = "policy_relaxed_repeat"
            else:
                row, score, local_idx = _pick_weighted(ranked_hits, policy_temperature)
                source = "faiss_relaxed_repeat"
            decision_payload["chosen_index"] = int(local_idx)
            if return_details:
                return row, score, source, decision_payload
            return row, score, source

    if not category_pool:
        raise RuntimeError(f"empty category pool: {category}")

    pool_for_pick = list(category_pool)
    if config is not None:
        curated_pool, curation_info = curate_candidates(
            [(row, 0.0) for row in category_pool],
            category=category,
            config=config,
        )
        pool_for_pick = [row for row, _score in curated_pool]
        decision_payload["fallback_curated_used"] = bool(curation_info.get("curated_used", False))
        decision_payload["fallback_curated_candidate_count"] = int(curation_info.get("curated_candidate_count", 0))

    available_pool = [row for row in pool_for_pick if row["asset_id"] not in used_asset_ids]
    if CATEGORY_NO_REPEAT_FIRST and available_pool:
        row = rng.choice(available_pool)
        if return_details:
            decision_payload["chosen_index"] = 0
            return row, 0.0, "fallback_pool", decision_payload
        return row, 0.0, "fallback_pool"
    if FILL_PRIORITY:
        row = rng.choice(pool_for_pick)
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
    *,
    street_program: object | None = None,
    palette: Optional[Dict[str, Tuple[int, int, int, int]]] = None,
    roughness: Optional[Dict[str, float]] = None,
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
):
    trimesh = _require_trimesh()
    scene = trimesh.Scene()
    total_width_m = float(road_width_m + left_side_width_m + right_side_width_m)
    context_ground = trimesh.creation.box(
        extents=(float(length_m) + 24.0, 0.04, max(total_width_m + 28.0, 24.0))
    )
    ctx_color = list((palette or {}).get("context_ground", (168, 163, 150, 255)))
    context_ground.apply_translation([0.0, -0.10, 0.0])
    context_ground = _apply_surface_finish(
        context_ground,
        surface_role="context_ground",
        rgba=ctx_color,
        roughness=(roughness or {}).get("context_ground", 0.85),
        texture_mode=texture_mode,
        texture_tracker=texture_tracker,
        texture_overrides=texture_overrides,
    )
    scene.add_geometry(context_ground, node_name="context_ground")

    colors = palette or {}
    sidewalk_color = list(colors.get("sidewalk", (165, 168, 172, 255)))
    furnishing_color = list(colors.get("furnishing", tuple(sidewalk_color)))
    clear_color = list(colors.get("clear_path", tuple(sidewalk_color)))

    # Sidewalk top at Y = SIDEWALK_ELEVATION_M; slab is 0.08 m thick
    sw_y_translation = SIDEWALK_ELEVATION_M - 0.04  # centre of 0.08-thick slab

    def _add_center_flowerbed_strip(*, band_name: str, width_m: float, z_center_m: float) -> None:
        soil_color = list(colors.get("planting_soil", colors.get("tree_pit", (98, 93, 76, 255))))
        curb_color = list(colors.get("curb", (145, 145, 145, 255)))
        curb_width_m = min(CENTER_FLOWERBED_CURB_WIDTH_M, max(float(width_m) * 0.2, 0.0))
        soil_width_m = float(width_m)
        render_curbs = bool(float(width_m) > 2.0 * curb_width_m + 0.10 and curb_width_m > 0.0)
        if render_curbs:
            soil_width_m = max(float(width_m) - 2.0 * curb_width_m, 0.05)
            for side_name, z_sign in (("left", 1.0), ("right", -1.0)):
                curb = trimesh.creation.box(
                    extents=(float(length_m), CENTER_FLOWERBED_CURB_HEIGHT_M, curb_width_m)
                )
                curb.apply_translation(
                    [
                        0.0,
                        CENTER_FLOWERBED_CURB_TOP_Y_M - CENTER_FLOWERBED_CURB_HEIGHT_M / 2.0,
                        float(z_center_m) + z_sign * (float(width_m) / 2.0 - curb_width_m / 2.0),
                    ]
                )
                curb = _apply_surface_finish(
                    curb,
                    surface_role="curb",
                    rgba=curb_color,
                    roughness=(roughness or {}).get("curb", 0.40),
                    texture_mode=texture_mode,
                    texture_tracker=texture_tracker,
                    texture_overrides=texture_overrides,
                )
                scene.add_geometry(curb, node_name=f"{band_name}_curb_{side_name}")

        soil = trimesh.creation.box(
            extents=(float(length_m), CENTER_PLANTING_SOIL_HEIGHT_M, soil_width_m)
        )
        soil.apply_translation(
            [0.0, CENTER_PLANTING_SOIL_TOP_Y_M - CENTER_PLANTING_SOIL_HEIGHT_M / 2.0, float(z_center_m)]
        )
        soil = _apply_surface_finish(
            soil,
            surface_role="planting_soil",
            rgba=soil_color,
            roughness=(roughness or {}).get("planting_soil", (roughness or {}).get("tree_pit", 0.90)),
            texture_mode=texture_mode,
            texture_tracker=texture_tracker,
            texture_overrides=texture_overrides,
        )
        scene.add_geometry(soil, node_name=f"{band_name}_soil")

    center_bands: List[Any] = []
    side_bands: List[Any] = []
    has_center_non_carriageway = False
    if street_program is not None and getattr(street_program, "bands", None):
        for band in getattr(street_program, "bands", ()) or ():
            side = str(getattr(band, "side", "") or "").strip().lower()
            kind = str(getattr(band, "kind", "") or "").strip().lower()
            if side == "center":
                center_bands.append(band)
                if kind != "carriageway":
                    has_center_non_carriageway = True
            elif side in ("left", "right"):
                side_bands.append(band)

    if not has_center_non_carriageway:
        road = trimesh.creation.box(extents=(length_m, 0.06, road_width_m))
        road_color = list(colors.get("carriageway", (65, 68, 72, 255)))
        road.apply_translation([0.0, -0.03, 0.0])
        road = _apply_surface_finish(
            road,
            surface_role="carriageway",
            rgba=road_color,
            roughness=(roughness or {}).get("carriageway", 0.95),
            texture_mode=texture_mode,
            texture_tracker=texture_tracker,
            texture_overrides=texture_overrides,
        )
        scene.add_geometry(road, node_name="road_slab")
    else:
        for band in center_bands:
            kind = str(getattr(band, "kind", "") or "").strip().lower()
            width_m = float(getattr(band, "width_m", 0.0) or 0.0)
            if kind in ("median", "grass_belt", "median_green") and width_m < 0.5:
                width_m = 0.5
            if width_m <= 0.0:
                continue
            z_center_m = float(getattr(band, "z_center_m", 0.0) or 0.0)
            if kind in {"grass_belt", "median_green"}:
                _add_center_flowerbed_strip(
                    band_name=str(getattr(band, "name", kind) or kind),
                    width_m=float(width_m),
                    z_center_m=float(z_center_m),
                )
                continue
            band_color = list(
                colors.get(
                    kind,
                    colors.get(
                        "carriageway",
                        (65, 68, 72, 255),
                    ),
                )
            )
            roughness_key = kind
            if kind == "drive_lane":
                roughness_key = "carriageway"
            elif kind == "median":
                roughness_key = "median_green"
            slab = trimesh.creation.box(extents=(length_m, 0.06, width_m))
            slab.apply_translation([0.0, -0.03, z_center_m])
            slab = _apply_surface_finish(
                slab,
                surface_role=roughness_key,
                rgba=band_color,
                roughness=(roughness or {}).get(roughness_key, 0.95),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
            )
            scene.add_geometry(slab, node_name=f"road_{getattr(band, 'name', kind)}")

    if side_bands:
        left_offset = road_width_m / 2.0
        right_offset = road_width_m / 2.0
        for band in side_bands:
            band_kind = str(getattr(band, "kind", "") or "").strip().lower()
            if band_kind == "carriageway":
                continue
            width_m = float(getattr(band, "width_m", 0.0) or 0.0)
            if width_m <= 0.0:
                continue
            color = clear_color if band_kind == "clear_path" else furnishing_color
            slab = trimesh.creation.box(extents=(length_m, 0.08, width_m))
            side = str(getattr(band, "side", "") or "").strip().lower()
            if side == "left":
                slab.apply_translation([0.0, sw_y_translation, left_offset + width_m / 2.0])
                left_offset += width_m
            elif side == "right":
                slab.apply_translation([0.0, sw_y_translation, -right_offset - width_m / 2.0])
                right_offset += width_m
            else:
                continue
            r_key = "clear_path" if band_kind == "clear_path" else "furnishing"
            slab = _apply_surface_finish(
                slab,
                surface_role=r_key,
                rgba=color,
                roughness=(roughness or {}).get(r_key, 0.70),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
            )
            scene.add_geometry(slab, node_name=f"sidewalk_{getattr(band, 'name', 'band')}")
    else:
        if left_side_width_m > 0.0:
            sidewalk_left = trimesh.creation.box(extents=(length_m, 0.08, left_side_width_m))
            sidewalk_left.apply_translation([0.0, sw_y_translation, road_width_m / 2.0 + left_side_width_m / 2.0])
            sidewalk_left = _apply_surface_finish(
                sidewalk_left,
                surface_role="sidewalk",
                rgba=sidewalk_color,
                roughness=(roughness or {}).get("sidewalk", 0.70),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
            )
            scene.add_geometry(sidewalk_left, node_name="sidewalk_left")

        if right_side_width_m > 0.0:
            sidewalk_right = trimesh.creation.box(extents=(length_m, 0.08, right_side_width_m))
            sidewalk_right.apply_translation([0.0, sw_y_translation, -road_width_m / 2.0 - right_side_width_m / 2.0])
            sidewalk_right = _apply_surface_finish(
                sidewalk_right,
                surface_role="sidewalk",
                rgba=sidewalk_color,
                roughness=(roughness or {}).get("sidewalk", 0.70),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
            )
            scene.add_geometry(sidewalk_right, node_name="sidewalk_right")

    # Curb stones along road edges
    curb_color = list(colors.get("curb", (145, 145, 145, 255)))
    curb_height = SIDEWALK_ELEVATION_M
    curb_width = 0.12
    for side_name, z_sign in (("left", 1.0), ("right", -1.0)):
        curb = trimesh.creation.box(extents=(length_m, curb_height, curb_width))
        curb.apply_translation([0.0, curb_height / 2.0, z_sign * (road_width_m / 2.0 + curb_width / 2.0)])
        curb = _apply_surface_finish(
            curb,
            surface_role="curb",
            rgba=curb_color,
            roughness=(roughness or {}).get("curb", 0.40),
            texture_mode=texture_mode,
            texture_tracker=texture_tracker,
            texture_overrides=texture_overrides,
        )
        scene.add_geometry(curb, node_name=f"curb_{side_name}")

    lane_count = int(max(1, int(getattr(street_program, "lane_count", 2) or 2))) if street_program is not None else 2
    _add_centerline_markings(
        scene,
        road_length_m=float(length_m),
        road_width_m=float(road_width_m),
        road_center_x_m=0.0,
        road_center_z_m=0.0,
        road_yaw_deg=0.0,
        lane_count=lane_count,
        highway_type=str(getattr(street_program, "road_type", "")),
        base_lane_width_m=(float(road_width_m) / float(lane_count)) if lane_count > 0 else None,
        road_coords=_road_reference_coords(street_program) if street_program is not None else (),
        color=colors.get("lane_mark", (245, 245, 245, 255)),
        roughness=(roughness or {}).get("lane_mark", 0.30),
        texture_mode=texture_mode,
        texture_tracker=texture_tracker,
        texture_overrides=texture_overrides,
    )

    return scene


def _apply_ground_pose(mesh, *, x_m: float, z_m: float, yaw_deg: float) -> None:
    trimesh = _require_trimesh()
    rotation = trimesh.transformations.rotation_matrix(
        math.radians(float(yaw_deg)),
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0],
    )
    mesh.apply_transform(rotation)
    mesh.apply_translation([float(x_m), 0.0, float(z_m)])


SIDEWALK_ELEVATION_M = 0.20
CENTER_ISLAND_TOP_Y_M = 0.12
CENTER_ISLAND_HEIGHT_M = 0.12
CENTER_FLOWERBED_CURB_WIDTH_M = 0.12
CENTER_FLOWERBED_CURB_TOP_Y_M = CENTER_ISLAND_TOP_Y_M
CENTER_FLOWERBED_CURB_HEIGHT_M = CENTER_ISLAND_HEIGHT_M
CENTER_PLANTING_SOIL_TOP_Y_M = CENTER_ISLAND_TOP_Y_M - 0.015
CENTER_PLANTING_SOIL_HEIGHT_M = CENTER_PLANTING_SOIL_TOP_Y_M
_CENTER_PLANTING_BAND_TOKENS = frozenset(
    {"center_grass_belt", "center_median_green", "grass_belt", "median_green"}
)


def _is_center_planting_band_name(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    return normalized in _CENTER_PLANTING_BAND_TOKENS or any(
        token in normalized for token in ("center_grass_belt", "center_median_green")
    )


def _band_surface_y_m(band: object | None) -> float:
    if band is not None:
        side = str(getattr(band, "side", "") or "").strip().lower()
        name = str(getattr(band, "name", "") or "").strip().lower()
        kind = str(getattr(band, "kind", "") or "").strip().lower()
        if side == "center" and (_is_center_planting_band_name(name) or _is_center_planting_band_name(kind)):
            return CENTER_PLANTING_SOIL_TOP_Y_M
    return SIDEWALK_ELEVATION_M


def _placement_surface_y_m(placement: StreetPlacement) -> float:
    if _is_center_planting_band_name(getattr(placement, "anchor_geom_id", "")):
        return CENTER_PLANTING_SOIL_TOP_Y_M
    return SIDEWALK_ELEVATION_M


def _build_curb_boundary_zone(carriageway: Any, elevated_side_zone: Any, curb_width_m: float) -> Any:
    """Build curb only where carriageway borders elevated side/facility surfaces.

    A raw ``carriageway.buffer(width) - carriageway`` ring also creates caps at
    road-arm endpoints. Those caps read as curbs across the road mouth at
    junctions. Curbs should instead live on the facility-lane boundary, so keep
    only the part of the ring that overlaps the raised sidewalk/furnishing zone.
    """
    from shapely.geometry import MultiPolygon, Polygon as ShapelyPolygon

    def _clean(geometry: Any) -> Any:
        if geometry is None or getattr(geometry, "is_empty", True):
            return MultiPolygon()
        try:
            if not getattr(geometry, "is_valid", True):
                geometry = geometry.buffer(0)
        except Exception:
            return MultiPolygon()
        if isinstance(geometry, (ShapelyPolygon, MultiPolygon)):
            return geometry
        polygons = [
            item
            for item in getattr(geometry, "geoms", ()) or ()
            if isinstance(item, (ShapelyPolygon, MultiPolygon)) and not getattr(item, "is_empty", True)
        ]
        if not polygons:
            return MultiPolygon()
        from shapely.ops import unary_union

        return _clean(unary_union(polygons))

    carriageway = _clean(carriageway)
    elevated_side_zone = _clean(elevated_side_zone)
    if carriageway.is_empty or elevated_side_zone.is_empty:
        return MultiPolygon()

    curb_width = max(float(curb_width_m), 0.0)
    if curb_width <= 0.0:
        return MultiPolygon()

    try:
        # Road arms and normalized junction patches often meet on exact split
        # lines, or with sub-centimeter numerical gaps. Close those only for
        # curb derivation so split lines do not become tiny curb caps.
        topology_tolerance = min(curb_width * 0.25, 0.03)
        curb_source = carriageway
        if topology_tolerance > 0.0:
            curb_source = _clean(carriageway.buffer(topology_tolerance).buffer(-topology_tolerance))
        raw_ring = curb_source.buffer(curb_width).difference(curb_source)
        # A tiny tolerance makes the operation robust when normalized junction
        # surfaces are numerically adjacent but do not overlap exactly.
        side_contact_zone = (
            elevated_side_zone.buffer(topology_tolerance)
            if topology_tolerance > 0.0
            else elevated_side_zone
        )
        curb_zone = raw_ring.intersection(side_contact_zone)
        return _clean(curb_zone)
    except Exception:
        logger.debug("Failed to build curb boundary zone", exc_info=True)
        return MultiPolygon()


def _apply_pbr_material(mesh, rgba, roughness=0.9):
    """Apply a PBR material to a mesh instead of plain face colors."""
    trimesh = _require_trimesh()
    from trimesh.visual.material import PBRMaterial

    mat = PBRMaterial(
        baseColorFactor=[rgba[0] / 255.0, rgba[1] / 255.0, rgba[2] / 255.0, rgba[3] / 255.0],
        metallicFactor=0.0,
        roughnessFactor=float(roughness),
    )
    mesh.visual = trimesh.visual.TextureVisuals(material=mat)
    return mesh


def _apply_surface_finish(
    mesh,
    *,
    surface_role: str,
    rgba: Sequence[int],
    roughness: float,
    texture_mode: str,
    texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
    horizontal_axes: tuple[tuple[float, float], tuple[float, float]] | None = None,
):
    resolved_surface_role = str(surface_role)
    resolved_texture_mode = str(texture_mode)
    if resolved_surface_role.strip().lower() in {"lane_mark", "lane_edge", "lane_edge_mark", "crossing"}:
        resolved_texture_mode = "solid_color_legacy"
    return apply_default_scene_texture(
        mesh,
        surface_role=resolved_surface_role,
        tint_rgba=list(rgba),
        roughness=float(roughness),
        texture_mode=resolved_texture_mode,
        tracker=texture_tracker,
        texture_overrides=texture_overrides,
        horizontal_axes=horizontal_axes,
    )


def _road_pose_from_context(placement_ctx: object | None, fallback_length_m: float) -> Tuple[float, float, float, float]:
    road_reference = getattr(placement_ctx, "road_reference", None)
    coords = list(getattr(road_reference, "coords", []) or [])
    if len(coords) >= 2:
        start_x, start_z = float(coords[0][0]), float(coords[0][1])
        end_x, end_z = float(coords[-1][0]), float(coords[-1][1])
        dx = end_x - start_x
        dz = end_z - start_z
        seg_length = math.hypot(dx, dz)
        if seg_length > 1e-6:
            return (
                (start_x + end_x) / 2.0,
                (start_z + end_z) / 2.0,
                math.degrees(math.atan2(dz, dx)),
                max(float(fallback_length_m), float(seg_length)),
            )
    return (0.0, 0.0, 0.0, float(fallback_length_m))


def _road_reference_coords(source: object | None) -> Tuple[Tuple[float, float], ...]:
    road_reference = source
    if source is not None and not hasattr(source, "coords"):
        road_reference = getattr(source, "road_reference", None)
    coords = tuple(
        (float(point[0]), float(point[1]))
        for point in (getattr(road_reference, "coords", []) or [])
        if len(point) >= 2
    )
    if len(coords) >= 2:
        return coords
    return tuple()


def _polyline_length_m(coords: Sequence[Tuple[float, float]]) -> float:
    total = 0.0
    for start, end in zip(coords, coords[1:]):
        total += math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))
    return float(total)


def _polyline_pose_at_distance(
    coords: Sequence[Tuple[float, float]],
    distance_m: float,
) -> Tuple[float, float, float]:
    clamped_distance = max(float(distance_m), 0.0)
    traversed = 0.0
    for start, end in zip(coords, coords[1:]):
        dx = float(end[0]) - float(start[0])
        dz = float(end[1]) - float(start[1])
        segment_length = math.hypot(dx, dz)
        if segment_length <= 1e-6:
            continue
        if traversed + segment_length >= clamped_distance:
            local_t = (clamped_distance - traversed) / segment_length
            x_m = float(start[0]) + dx * local_t
            z_m = float(start[1]) + dz * local_t
            yaw_deg = math.degrees(math.atan2(dz, dx))
            return float(x_m), float(z_m), float(yaw_deg)
        traversed += segment_length
    if len(coords) >= 2:
        start = coords[-2]
        end = coords[-1]
        return (
            float(end[0]),
            float(end[1]),
            float(math.degrees(math.atan2(float(end[1]) - float(start[1]), float(end[0]) - float(start[0])))),
        )
    return (0.0, 0.0, 0.0)


def _marking_dash_pattern(
    *,
    road_width_m: float,
    lane_count: int | None = None,
    base_lane_width_m: float | None = None,
    highway_type: str | None = None,
) -> Tuple[float, float]:
    """Return (dash_length_m, dash_gap_m) according to road class.

    - 高速/高等级道路: 6m 线段 + 9m 间隙
    - 城市/普通道路: 4m 线段 + 6m 间隙
    """
    normalized_highway_type = str(highway_type or "").strip().lower()
    if normalized_highway_type in {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "expressway",
        "primary",
        "primary_link",
    }:
        return 6.0, 9.0

    road_width = float(road_width_m)
    lanes = int(lane_count) if lane_count is not None else 0
    if lanes > 0 and road_width > 0.0:
        lane_width = road_width / float(lanes)
    elif base_lane_width_m is not None and float(base_lane_width_m) > 0.0:
        lane_width = float(base_lane_width_m)
    elif road_width > 0.0:
        lane_width = road_width / 2.0
    else:
        lane_width = 0.0

    if lane_width >= 3.65:
        return 6.0, 9.0
    if lane_width > 0.0:
        return 4.0, 6.0
    if road_width >= 16.0:
        return 6.0, 9.0
    return 4.0, 6.0


def _road_axes_from_yaw_deg(yaw_deg: float) -> Tuple[tuple[float, float], tuple[float, float]]:
    yaw_rad = math.radians(float(yaw_deg))
    return (
        (math.cos(yaw_rad), math.sin(yaw_rad)),
        (-math.sin(yaw_rad), math.cos(yaw_rad)),
    )


def _add_road_box(
    scene,
    *,
    length_m: float,
    width_m: float,
    height_m: float,
    local_x_m: float,
    local_z_m: float,
    road_center_x_m: float,
    road_center_z_m: float,
    road_yaw_deg: float,
    y_min_m: float,
    color: Sequence[int],
    surface_role: str,
    node_name: str,
    roughness: float = 0.7,
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
    horizontal_axes: tuple[tuple[float, float], tuple[float, float]] | None = None,
) -> None:
    trimesh = _require_trimesh()
    mesh = trimesh.creation.box(extents=(float(length_m), float(height_m), float(width_m)))
    mesh.apply_translation([float(local_x_m), float(y_min_m) + float(height_m) / 2.0, float(local_z_m)])
    _apply_ground_pose(mesh, x_m=road_center_x_m, z_m=road_center_z_m, yaw_deg=road_yaw_deg)
    mesh = _apply_surface_finish(
        mesh,
        surface_role=surface_role,
        rgba=list(color),
        roughness=float(roughness),
        texture_mode=texture_mode,
        texture_tracker=texture_tracker,
        texture_overrides=texture_overrides,
        horizontal_axes=horizontal_axes,
    )
    scene.add_geometry(mesh, node_name=node_name)


def _should_render_centerline_marking(*, carriageway_width_m: float, lane_count: int | None = None) -> bool:
    if lane_count is not None and int(lane_count) > 1:
        return True
    return float(carriageway_width_m) >= 5.5


def _drive_lane_boundary_offsets(detailed_strip_profiles: Sequence[Mapping[str, Any]]) -> List[float]:
    edge_offsets: List[float] = []
    for profile in detailed_strip_profiles:
        if str(profile.get("side", "")).lower() == "center" and profile.get("kind") == "drive_lane":
            inner = float(profile.get("inner_m", 0))
            outer = float(profile.get("outer_m", 0))
            if inner not in edge_offsets:
                edge_offsets.append(inner)
            if outer not in edge_offsets:
                edge_offsets.append(outer)
    edge_offsets.sort()
    return edge_offsets


def _drive_lane_internal_offsets(detailed_strip_profiles: Sequence[Mapping[str, Any]]) -> List[float]:
    edge_offsets = _drive_lane_boundary_offsets(detailed_strip_profiles)
    if len(edge_offsets) < 3:
        return []
    return [
        offset
        for offset in edge_offsets[1:-1]
        if abs(float(offset)) < max(abs(float(edge_offsets[0])), abs(float(edge_offsets[-1]))) - 0.08
    ]


def _junction_marking_exclusion_geometries(
    junction_geometries: Sequence[Mapping[str, Any]],
    *,
    padding_m: float = 0.35,
) -> List[Any]:
    geometries: List[Any] = []

    def _add_geometry(geometry: Any) -> None:
        if geometry is None or getattr(geometry, "is_empty", True):
            return
        try:
            geometry = geometry.buffer(float(padding_m)) if float(padding_m) > 0.0 else geometry
        except Exception:
            pass
        if geometry is not None and not getattr(geometry, "is_empty", True):
            geometries.append(geometry)

    for junction in junction_geometries or ():
        normalized_surface_patches = list(junction.get("normalized_surface_patches", []) or ())
        if normalized_surface_patches:
            for patch in normalized_surface_patches:
                role = str(patch.get("surface_role", "") or "carriageway").strip().lower()
                if role in {"sidewalk", "furnishing", "context_ground"}:
                    continue
                _add_geometry(patch.get("geometry"))
            continue
        _add_geometry(junction.get("carriageway_core") or junction.get("junction_core_rect"))
        for bucket_name in ("crosswalk_patches", "turn_lane_patches", "lane_surface_patches", "merged_surface_patches"):
            for patch in junction.get(bucket_name, []) or ():
                if isinstance(patch, Mapping):
                    _add_geometry(patch.get("geometry"))

    if not geometries:
        return []
    try:
        from shapely.ops import unary_union

        merged = unary_union(geometries)
        return [merged] if merged is not None and not getattr(merged, "is_empty", True) else []
    except Exception:
        return geometries


def _marking_point_in_exclusion(x_m: float, z_m: float, exclusion_geometries: Sequence[Any] | None) -> bool:
    if not exclusion_geometries:
        return False
    from shapely.geometry import Point

    point = Point(float(x_m), float(z_m))
    for geometry in exclusion_geometries:
        if geometry is None or getattr(geometry, "is_empty", True):
            continue
        try:
            if geometry.covers(point):
                return True
        except Exception:
            continue
    return False


def _add_centerline_markings(
    scene,
    *,
    road_length_m: float,
    road_width_m: float,
    road_center_x_m: float,
    road_center_z_m: float,
    road_yaw_deg: float,
    lane_count: int | None,
    base_lane_width_m: float | None = None,
    highway_type: str | None = None,
    road_coords: Sequence[Tuple[float, float]] | None = None,
    lane_separator_offsets_m: Sequence[float] | None = None,
    marking_exclusion_geometries: Sequence[Any] | None = None,
    color: Sequence[int],
    roughness: float,
    node_name_prefix: str = "centerline_mark",
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
) -> None:
    explicit_offsets = tuple(float(offset) for offset in (lane_separator_offsets_m or ()))
    if not explicit_offsets and not _should_render_centerline_marking(
        carriageway_width_m=float(road_width_m),
        lane_count=lane_count,
    ):
        return
    separator_offsets: List[float] = []
    if explicit_offsets:
        separator_offsets = list(explicit_offsets)
    else:
        lane_count_int = int(lane_count or 0)
        if lane_count_int > 1 and lane_count_int <= 8 and float(road_width_m) > 0.0:
            lane_width_m = float(road_width_m) / float(lane_count_int)
            separator_offsets = [
                -float(road_width_m) / 2.0 + lane_width_m * float(lane_idx)
                for lane_idx in range(1, lane_count_int)
            ]
        else:
            separator_offsets = [0.0]
    if float(road_width_m) > 0.0:
        half_width_m = float(road_width_m) / 2.0
        separator_offsets = [
            offset
            for offset in separator_offsets
            if abs(float(offset)) < half_width_m - 0.08
        ]
    deduped_offsets: List[float] = []
    for offset in sorted(separator_offsets):
        if not any(abs(float(offset) - float(existing)) <= 0.02 for existing in deduped_offsets):
            deduped_offsets.append(float(offset))
    if not deduped_offsets:
        return
    separator_offsets = deduped_offsets
    dash_length_m, dash_gap_m = _marking_dash_pattern(
        road_width_m=float(road_width_m),
        lane_count=lane_count,
        base_lane_width_m=base_lane_width_m,
        highway_type=highway_type,
    )
    highway_key = str(highway_type or "").strip().lower()
    if any(token in highway_key for token in ("motorway", "trunk", "express", "freeway")):
        dash_length_m, dash_gap_m = 6.0, 9.0
    else:
        dash_length_m, dash_gap_m = 4.0, 6.0

    coords = tuple((float(point[0]), float(point[1])) for point in (road_coords or ()))
    if len(coords) >= 2:
        road_length_m = max(float(road_length_m), _polyline_length_m(coords))
        if float(road_length_m) <= dash_length_m:
            return
        dash_step_m = float(dash_length_m) + float(dash_gap_m)
        for separator_idx, lane_z in enumerate(separator_offsets):
            dash_idx = 0
            distance_m = float(dash_length_m) * 0.5
            while distance_m < float(road_length_m) - float(dash_length_m) * 0.5:
                center_x_m, center_z_m, yaw_deg = _polyline_pose_at_distance(coords, distance_m)
                if _marking_point_in_exclusion(center_x_m, center_z_m, marking_exclusion_geometries):
                    dash_idx += 1
                    distance_m += dash_step_m
                    continue
                horizontal_axes = _road_axes_from_yaw_deg(yaw_deg)
                node_name = (
                    f"{node_name_prefix}_{dash_idx}"
                    if len(separator_offsets) == 1
                    else f"{node_name_prefix}_{separator_idx}_{dash_idx}"
                )
                _add_road_box(
                    scene,
                    length_m=float(dash_length_m),
                    width_m=0.14,
                    height_m=0.01,
                    local_x_m=0.0,
                    local_z_m=float(lane_z),
                    road_center_x_m=center_x_m,
                    road_center_z_m=center_z_m,
                    road_yaw_deg=yaw_deg,
                    y_min_m=0.004,
                    color=color,
                    surface_role="lane_mark",
                    node_name=node_name,
                    roughness=roughness,
                    texture_mode=texture_mode,
                    texture_tracker=texture_tracker,
                    texture_overrides=texture_overrides,
                    horizontal_axes=horizontal_axes,
                )
                dash_idx += 1
                distance_m += dash_step_m
        return
    horizontal_axes = _road_axes_from_yaw_deg(road_yaw_deg)
    dash_x = -float(road_length_m) / 2.0 + float(dash_length_m) * 0.5
    dash_idx = 0
    while dash_x < float(road_length_m) / 2.0 - float(dash_length_m) * 0.5:
        world_center_x_m = float(road_center_x_m) + math.cos(math.radians(float(road_yaw_deg))) * float(dash_x)
        world_center_z_m = float(road_center_z_m) + math.sin(math.radians(float(road_yaw_deg))) * float(dash_x)
        if _marking_point_in_exclusion(world_center_x_m, world_center_z_m, marking_exclusion_geometries):
            dash_idx += 1
            dash_x += float(dash_length_m) + float(dash_gap_m)
            continue
        for separator_idx, lane_z in enumerate(separator_offsets):
            node_name = (
                f"{node_name_prefix}_{dash_idx}"
                if len(separator_offsets) == 1
                else f"{node_name_prefix}_{separator_idx}_{dash_idx}"
            )
            _add_road_box(
                scene,
                length_m=float(dash_length_m),
                width_m=0.14,
                height_m=0.01,
                local_x_m=float(dash_x),
                local_z_m=float(lane_z),
                road_center_x_m=road_center_x_m,
                road_center_z_m=road_center_z_m,
                road_yaw_deg=road_yaw_deg,
                y_min_m=0.004,
                color=color,
                surface_role="lane_mark",
                node_name=node_name,
                roughness=roughness,
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
                horizontal_axes=horizontal_axes,
            )
        dash_idx += 1
        dash_x += float(dash_length_m) + float(dash_gap_m)


def _add_lane_edge_markings(
    scene,
    *,
    road_length_m: float,
    road_center_x_m: float,
    road_center_z_m: float,
    road_yaw_deg: float,
    detailed_strip_profiles: list,
    road_width_m: float | None = None,
    highway_type: str | None = None,
    road_coords: Sequence[Tuple[float, float]] | None = None,
    marking_exclusion_geometries: Sequence[Any] | None = None,
    edge_color: Sequence[int] = (230, 200, 50, 255),  # Yellow for lane edges
    roughness: float = 0.30,
    node_name_prefix: str = "lane_edge",
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
) -> None:
    """Add solid lane edge markings along the road.

    Renders continuous yellow solid lines at the edges of each drive_lane strip.
    Uses the inner_m and outer_m values from detailed_strip_profiles to determine positions.
    """
    # Collect the outer drive-lane boundaries. Internal lane separators are
    # rendered by _add_centerline_markings so they can share the lane_mark role.
    edge_offsets = _drive_lane_boundary_offsets(detailed_strip_profiles)
    if not edge_offsets:
        if road_width_m is None or float(road_width_m) <= 0.0:
            return
        edge_inset_m = 0.25
        edge_offsets = [
            -float(road_width_m) / 2.0 + edge_inset_m,
            float(road_width_m) / 2.0 - edge_inset_m,
        ]

    edge_marking_offsets = list(edge_offsets)
    if len(edge_marking_offsets) >= 2:
        edge_marking_offsets = [edge_marking_offsets[0], edge_marking_offsets[-1]]
    edge_marking_offsets = [
        offset
        for offset in edge_marking_offsets
        if abs(float(offset)) > 0.08
    ]

    if not edge_marking_offsets:
        return

    coords = tuple((float(point[0]), float(point[1])) for point in (road_coords or ()))

    # If we have road coordinates, render solid edge lines as short overlapping
    # segments so curved roads still follow the reference polyline.
    if len(coords) >= 2:
        road_len = max(float(road_length_m), _polyline_length_m(coords))
        mark_length_m = max(2.0, min(6.0, road_len / 48.0 if road_len > 0.0 else 4.0))
        mark_step_m = max(0.5, mark_length_m - 0.08)
        if road_len < mark_length_m:
            return
        for edge_idx, edge_offset in enumerate(edge_marking_offsets):
            mark_idx = 0
            distance_m = mark_length_m * 0.5
            while distance_m < road_len - mark_length_m * 0.15:
                center_x_m, center_z_m, yaw_deg = _polyline_pose_at_distance(coords, distance_m)
                if _marking_point_in_exclusion(center_x_m, center_z_m, marking_exclusion_geometries):
                    mark_idx += 1
                    distance_m += mark_step_m
                    continue
                horizontal_axes = _road_axes_from_yaw_deg(yaw_deg)
                _add_road_box(
                    scene,
                    length_m=mark_length_m,
                    width_m=0.10,
                    height_m=0.01,
                    local_x_m=0.0,
                    local_z_m=float(edge_offset),
                    road_center_x_m=center_x_m,
                    road_center_z_m=center_z_m,
                    road_yaw_deg=yaw_deg,
                    y_min_m=0.005,
                    color=edge_color,
                    surface_role="lane_edge_mark",
                    node_name=f"{node_name_prefix}_{edge_idx}_{mark_idx}",
                    roughness=roughness,
                    texture_mode=texture_mode,
                    texture_tracker=texture_tracker,
                    texture_overrides=texture_overrides,
                    horizontal_axes=horizontal_axes,
                )
                mark_idx += 1
                distance_m += mark_step_m
    else:
        # Fallback: render lines without following road shape
        for edge_idx, edge_offset in enumerate(edge_marking_offsets):
            if _marking_point_in_exclusion(road_center_x_m, road_center_z_m, marking_exclusion_geometries):
                continue
            _add_road_box(
                scene,
                length_m=float(road_length_m),
                width_m=0.10,
                height_m=0.01,
                local_x_m=0.0,
                local_z_m=float(edge_offset),
                road_center_x_m=road_center_x_m,
                road_center_z_m=road_center_z_m,
                road_yaw_deg=road_yaw_deg,
                y_min_m=0.005,
                color=edge_color,
                surface_role="lane_edge_mark",
                node_name=f"{node_name_prefix}_{edge_idx}",
                roughness=roughness,
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
                horizontal_axes=_road_axes_from_yaw_deg(road_yaw_deg),
            )


def _add_beauty_scene_proxies(
    scene,
    *,
    config: StreetComposeConfig,
    street_program: object,
    placement_ctx: object | None,
    poi_ctx: object | None,
    placements: List[StreetPlacement],
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
) -> None:
    colors = style_palette(getattr(config, "style_preset", None))
    rough = surface_roughness(getattr(config, "style_preset", None))
    road_center_x_m, road_center_z_m, road_yaw_deg, road_length_m = _road_pose_from_context(
        placement_ctx,
        float(config.length_m),
    )
    road_width_m = float(getattr(street_program, "road_width_m", config.road_width_m))
    lane_count = max(1, int(getattr(street_program, "lane_count", config.lane_count)))
    render_linear_road_overlays = not _is_corridor_layout_mode(getattr(config, "layout_mode", "template"))

    if render_linear_road_overlays:
        if lane_count > 1:
            lane_width_m = road_width_m / float(lane_count)
            dash_length_m, dash_gap_m = _marking_dash_pattern(
                road_width_m=road_width_m,
                lane_count=lane_count,
                base_lane_width_m=lane_width_m,
                highway_type=str(getattr(street_program, "road_type", "")),
            )
            dash_x = -road_length_m / 2.0 + dash_length_m
            horizontal_axes = _road_axes_from_yaw_deg(road_yaw_deg)
            dash_idx = 0
            while dash_x < road_length_m / 2.0 - 1.5:
                for lane_idx in range(1, lane_count):
                    lane_z = -road_width_m / 2.0 + lane_width_m * float(lane_idx)
                    if abs(float(lane_z)) <= 1e-6:
                        continue
                    _add_road_box(
                        scene,
                        length_m=dash_length_m,
                        width_m=0.14,
                        height_m=0.01,
                        local_x_m=dash_x,
                        local_z_m=lane_z,
                        road_center_x_m=road_center_x_m,
                        road_center_z_m=road_center_z_m,
                        road_yaw_deg=road_yaw_deg,
                        y_min_m=0.004,
                        color=colors.get("lane_mark", (238, 232, 208, 255)),
                        surface_role="lane_mark",
                        node_name=f"lane_mark_{lane_idx}_{dash_idx}",
                        roughness=rough.get("lane_mark", 0.30),
                        texture_mode=texture_mode,
                        texture_tracker=texture_tracker,
                        texture_overrides=texture_overrides,
                        horizontal_axes=horizontal_axes,
                    )
                dash_idx += 1
                dash_x += dash_length_m + dash_gap_m

        curb_half_width = road_width_m / 2.0
        # Curb geometry is now part of _build_base_scene; skip duplicate here.

        crossing_points = nonempty_poi_points(getattr(poi_ctx, "poi_points_by_type_xz", {}) or {}).get("crossing", ())
        for idx, point in enumerate(crossing_points):
            _add_road_box(
                scene,
                length_m=1.8,
                width_m=max(road_width_m + 0.35, 4.0),
                height_m=0.012,
                local_x_m=0.0,
                local_z_m=0.0,
                road_center_x_m=float(point[0]),
                road_center_z_m=float(point[1]),
                road_yaw_deg=road_yaw_deg,
                y_min_m=0.004,
                color=colors.get("crossing", (236, 228, 208, 255)),
                surface_role="crossing",
                node_name=f"crossing_patch_{idx}",
                roughness=rough.get("crossing", 0.35),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
                horizontal_axes=_road_axes_from_yaw_deg(road_yaw_deg),
            )

    for idx, placement in enumerate(placements):
        x_m = float(placement.position_xyz[0])
        z_m = float(placement.position_xyz[2])
        if placement.category == "tree":
            _add_road_box(
                scene,
                length_m=1.2,
                width_m=1.2,
                height_m=0.03,
                local_x_m=0.0,
                local_z_m=0.0,
                road_center_x_m=x_m,
                road_center_z_m=z_m,
                road_yaw_deg=0.0,
                y_min_m=_placement_surface_y_m(placement) + 0.001,
                color=colors.get("tree_pit", (98, 93, 76, 255)),
                surface_role="tree_pit",
                node_name=f"tree_pit_{idx}",
                roughness=rough.get("tree_pit", 0.90),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
            )
        elif placement.category == "bus_stop":
            _add_road_box(
                scene,
                length_m=4.5,
                width_m=1.6,
                height_m=0.018,
                local_x_m=0.0,
                local_z_m=0.0,
                road_center_x_m=x_m,
                road_center_z_m=z_m,
                road_yaw_deg=road_yaw_deg,
                y_min_m=SIDEWALK_ELEVATION_M + 0.004,
                color=colors.get("transit_pad", (118, 129, 145, 255)),
                surface_role="transit_pad",
                node_name=f"transit_pad_{idx}",
                roughness=rough.get("transit_pad", 0.50),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
            )


def _add_instance_meshes(
    scene,
    placements: List[StreetPlacement],
    mesh_cache: _LazyMeshCache | Dict[str, _MeshCacheEntry],
    building_plans_by_instance: Optional[Mapping[str, BuildingPlacementPlan]] = None,
) -> None:
    trimesh = _require_trimesh()
    for placement in placements:
        # Support both _LazyMeshCache and plain dict
        if isinstance(mesh_cache, _LazyMeshCache):
            entry = mesh_cache.get_entry(placement.asset_id)
        else:
            entry = mesh_cache[placement.asset_id]
        mesh = entry.mesh
        if placement.scale_xyz:
            scale = [float(value) for value in placement.scale_xyz]
        else:
            scale = float(placement.scale)
        rotation = trimesh.transformations.rotation_matrix(
            math.radians(float(placement.yaw_deg)),
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0],
        )
        # Street furniture sits on the elevated sidewalk; buildings and environment shells stay at scene origin.
        is_environment_shell = (
            placement.placement_group == "environment"
            or str(placement.category).strip().lower() == "sky_dome"
        )
        y_offset = 0.0 if placement.placement_group == "building" or is_environment_shell else _placement_surface_y_m(placement)
        translation = trimesh.transformations.translation_matrix(
            [
                float(placement.position_xyz[0]),
                float(placement.position_xyz[1]) + y_offset,
                float(placement.position_xyz[2]),
            ]
        )
        # Build a combined 4×4 affine transform: T · R · S
        if isinstance(scale, list):
            s = [float(v) for v in scale]
            scale_mat = np.diag([s[0], s[1], s[2], 1.0])
        else:
            sv = float(scale)
            scale_mat = np.diag([sv, sv, sv, 1.0])
        combined = translation @ rotation @ scale_mat

        building_plan = (
            building_plans_by_instance.get(placement.instance_id)
            if building_plans_by_instance is not None and placement.placement_group == "building"
            else None
        )
        # Use scene.add_geometry with an explicit *transform* so that the
        # cached mesh entries are never copied.  trimesh's Scene.copy() /
        # Trimesh.copy() deep-copies every TextureVisuals (including PIL
        # images), which is extremely slow for textured assets.
        if isinstance(mesh, trimesh.Scene):
            for gidx, node_name in enumerate(mesh.graph.nodes_geometry):
                local_tf, geom_name = mesh.graph[node_name]
                geom = mesh.geometry[geom_name]
                scene.add_geometry(
                    geom,
                    node_name=f"{placement.instance_id}_{geom_name}_{gidx}",
                    transform=combined @ local_tf,
                    metadata={
                        **_placement_glb_metadata(placement),
                        "component_node_name": str(node_name),
                        "component_geom_name": str(geom_name),
                        "component_index": int(gidx),
                    },
                )
        else:
            scene.add_geometry(
                mesh,
                node_name=placement.instance_id,
                transform=combined,
                metadata=_placement_glb_metadata(placement),
            )
        if building_plan is not None and bool(building_plan.door_added):
            door_meshes = _create_attached_building_door_meshes(plan=building_plan, entry=entry)
            for didx, door_mesh in enumerate(door_meshes):
                placed_door = door_mesh.copy()
                placed_door.apply_transform(rotation)
                placed_door.apply_transform(translation)
                scene.add_geometry(
                    placed_door,
                    node_name=f"{placement.instance_id}_door_{didx}",
                    metadata={
                        **_placement_glb_metadata(placement),
                        "component_kind": "building_door",
                        "component_index": int(didx),
                    },
                )


def _placement_glb_metadata(placement: StreetPlacement) -> Dict[str, object]:
    """Metadata exported to GLB node ``extras`` for layout/GLB fidelity checks."""

    return {
        "schema": "roadgen3d_instance_metadata_v1",
        "instance_id": str(placement.instance_id),
        "category": str(placement.category),
        "asset_id": str(placement.asset_id),
        "placement_group": str(placement.placement_group),
        "selection_source": str(placement.selection_source),
        "anchor_geom_id": str(placement.anchor_geom_id),
        "position_xyz": [float(value) for value in placement.position_xyz],
        "yaw_deg": float(placement.yaw_deg),
        "scale": float(placement.scale),
        "scale_xyz": [float(value) for value in (placement.scale_xyz or [])],
        "bbox_xz": [float(value) for value in (placement.bbox_xz or [])],
        "source_bbox": [float(value) for value in (placement.bbox_xz or [])],
    }


def _export_scene(scene, out_dir: Path, export_format: str) -> Dict[str, str]:
    export_format = _validate_export_format(export_format)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {"scene_glb": "", "scene_ply": ""}
    if export_format == "none":
        return outputs
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


def rebuild_glb_from_layout(
    layout_path: Path,
    manifest_path: Path,
    out_dir: Path | None = None,
) -> Dict[str, str]:
    """Lightweight GLB re-export from a modified scene_layout.json.

    Reconstructs the minimum state needed to call _build_base_scene,
    _add_instance_meshes, and _export_scene without re-running the
    expensive placement pipeline (CLIP embedding, FAISS retrieval,
    collision detection).

    For asset_ids not found in the manifest (e.g. LLM-invented like
    ``flower_bed_llm_edit``), a coloured box placeholder mesh is created.

    Parameters
    ----------
    layout_path:
        Path to scene_layout.json.
    manifest_path:
        Path to the asset manifest JSONL (used to load real meshes).
    out_dir:
        Directory for the exported GLB.  Defaults to the parent of
        *layout_path*.

    Returns
    -------
    dict  with key ``"scene_glb"`` mapping to the exported file path.
    """
    trimesh = _require_trimesh()

    layout_payload = json.loads(Path(layout_path).read_text(encoding="utf-8"))
    if out_dir is None:
        out_dir = Path(layout_path).parent / "rebuild"
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 1. Reconstruct StreetComposeConfig from serialized config dict ---
    config_dict: Dict[str, Any] = layout_payload.get("config") or {}
    valid_fields = {f.name for f in dataclasses.fields(StreetComposeConfig)}
    filtered_config = {k: v for k, v in config_dict.items() if k in valid_fields}
    config = StreetComposeConfig(**filtered_config)

    # --- 2. Reconstruct a StreetProgram-like namespace from bands ---
    sp_dict: Dict[str, Any] = layout_payload.get("street_program") or {}
    bands_data = sp_dict.get("bands") or []

    class _BandProxy:
        """Minimal namespace matching StreetBand fields used by _build_base_scene."""
        pass

    class _ProgramProxy:
        pass

    proxy_bands = []
    for b in bands_data:
        bp = _BandProxy()
        bp.name = str(b.get("name", ""))
        bp.kind = str(b.get("kind", ""))
        bp.side = str(b.get("side", ""))
        bp.width_m = float(b.get("width_m", 0))
        bp.z_center_m = float(b.get("z_center_m", 0))
        bp.allowed_categories = tuple(b.get("allowed_categories") or ())
        proxy_bands.append(bp)

    program_proxy = _ProgramProxy()
    program_proxy.bands = tuple(proxy_bands)
    # Also expose other fields that _build_base_scene may read
    for attr in ("road_width_m", "sidewalk_width_m", "furnishing_width_m",
                  "left_furnishing_width_m", "right_furnishing_width_m",
                  "left_clear_path_width_m", "right_clear_path_width_m"):
        if attr in sp_dict:
            setattr(program_proxy, attr, float(sp_dict[attr]))

    # --- 3. Load manifest rows, filter to referenced asset_ids ---
    placements_data: List[Dict[str, Any]] = [
        *(layout_payload.get("placements") or []),
        *(layout_payload.get("environment_placements") or []),
    ]
    referenced_asset_ids = {str(p.get("asset_id", "")) for p in placements_data}

    rows = _ensure_default_sky_dome_row(_load_real_manifest(Path(manifest_path)))
    filtered_rows = [r for r in rows if str(r["asset_id"]) in referenced_asset_ids]

    # --- 4. Build lazy mesh cache for referenced assets ---
    # Returns _LazyMeshCache that loads metadata first, full meshes lazily
    if filtered_rows:
        mesh_cache = _load_mesh_cache(filtered_rows)
    else:
        # Create empty cache if no rows
        mesh_cache = _LazyMeshCache({}, max_mesh_cache_size=DEFAULT_MAX_MESH_CACHE_SIZE)

    # --- 5. Create placeholder entries for LLM-invented asset_ids ---
    # These need full mesh entries (box placeholders)
    placeholder_ids = referenced_asset_ids - set(mesh_cache.keys())
    for pid in placeholder_ids:
        box = trimesh.creation.box(extents=(0.8, 0.4, 0.8))
        box.visual = trimesh.visual.ColorVisuals(
            mesh=box,
            vertex_colors=np.tile([100, 180, 100, 220], (len(box.vertices), 1)),
        )
        fallback_entry = _MeshCacheEntry(
            mesh=box,
            half_x=0.4,
            half_z=0.4,
            min_y=0.0,
            center_x=0.0,
            center_z=0.0,
            is_scene=False,
            native_height_y=0.4,
        )
        mesh_cache.set_full_entry(pid, fallback_entry)

    # --- 6. Reconstruct StreetPlacement objects ---
    placements: List[StreetPlacement] = []
    for p in placements_data:
        pl = StreetPlacement(
            instance_id=str(p.get("instance_id", "")),
            asset_id=str(p.get("asset_id", "")),
            category=str(p.get("category", "unknown")),
            score=float(p.get("score", 1.0) or 1.0),
            position_xyz=[float(v) for v in (p.get("position_xyz") or [0, 0, 0])],
            yaw_deg=float(p.get("yaw_deg", 0.0) or 0.0),
            scale=float(p.get("scale", 1.0) or 1.0),
            bbox_xz=[float(v) for v in (p.get("bbox_xz") or [])],
            selection_source=str(p.get("selection_source", "llm_layout_edit")),
            placement_group=str(p.get("placement_group", "street_furniture")),
            slot_id=str(p.get("slot_id", "") or ""),
            required=bool(p.get("required", False)),
            theme_id=str(p.get("theme_id", "") or ""),
            anchor_poi_type=str(p.get("anchor_poi_type", "") or ""),
            anchor_geom_id=str(p.get("anchor_geom_id", "") or ""),
            constraint_penalty=float(p.get("constraint_penalty", 0.0)),
            feasibility_score=float(p.get("feasibility_score", 1.0)),
            violated_rules=tuple(p.get("violated_rules") or ()),
        )
        placements.append(pl)

    # --- 7. Rebuild building plans if present ---
    building_plans_by_instance: Dict[str, Any] = {}
    bp_data = layout_payload.get("building_placements") or []
    for bp in bp_data:
        iid = str(bp.get("instance_id", ""))
        if iid:
            building_plans_by_instance[iid] = bp

    # --- 8. Build base scene ---
    length_m = float(config.length_m)
    road_width_m = float(sp_dict.get("road_width_m", config.road_width_m))
    left_sw = float(sp_dict.get("sidewalk_width_m", config.sidewalk_width_m))
    right_sw = float(sp_dict.get("sidewalk_width_m", config.sidewalk_width_m))

    scene = _build_base_scene(
        length_m=length_m,
        road_width_m=road_width_m,
        left_side_width_m=left_sw,
        right_side_width_m=right_sw,
        street_program=program_proxy,
    )

    # --- 9. Add instance meshes ---
    _add_instance_meshes(
        scene,
        placements=placements,
        mesh_cache=mesh_cache,
        building_plans_by_instance=building_plans_by_instance or None,
    )

    # --- 10. Export ---
    outputs = _export_scene(scene, out_dir, export_format="glb")
    print(f"[rebuild_glb] Exported → {outputs.get('scene_glb', '')}")
    return outputs


def _production_step_definitions(
    layout_mode: str,
    *,
    include_land_use_zoning: bool = True,
) -> Tuple[Tuple[str, str], ...]:
    if _is_corridor_layout_mode(layout_mode):
        steps: List[Tuple[str, str]] = [
            ("road_base", "Road Base"),
        ]
        if include_land_use_zoning:
            steps.append(("land_use_zoning", "Land Use / Zoning"))
        steps.extend(
            [
                ("buildings", "Buildings"),
                ("poi_context", "POI Context"),
                ("furniture_anchor", "Furniture Anchor"),
                ("furniture_required", "Furniture Required"),
                ("furniture_optional", "Furniture Optional"),
                ("scene_preview", "Scene Preview"),
            ]
        )
        return tuple(steps)
    return (
        ("road_base", "Road Base"),
        ("furniture_required", "Furniture Required"),
        ("furniture_optional", "Furniture Optional"),
        ("scene_preview", "Scene Preview"),
    )


def _split_furniture_layers(
    placements: Sequence[StreetPlacement],
) -> Tuple[List[StreetPlacement], List[StreetPlacement], List[StreetPlacement], List[StreetPlacement]]:
    building = [placement for placement in placements if placement.placement_group == "building"]
    anchor = [
        placement
        for placement in placements
        if placement.placement_group == "street_furniture" and str(placement.anchor_poi_type or "").strip()
    ]
    required = [
        placement
        for placement in placements
        if placement.placement_group == "street_furniture"
        and bool(placement.required)
        and not str(placement.anchor_poi_type or "").strip()
    ]
    optional = [
        placement
        for placement in placements
        if placement.placement_group == "street_furniture"
        and not bool(placement.required)
        and not str(placement.anchor_poi_type or "").strip()
    ]
    return building, anchor, required, optional


def _stage_counts(
    *,
    visible_instance_ids: Sequence[str],
    visible_placements: Sequence[StreetPlacement],
    zoning_grid: Sequence[Dict[str, object]],
    building_plans: Sequence[BuildingPlacementPlan],
    poi_points_by_type: Mapping[str, Sequence[Tuple[float, float]]],
) -> Dict[str, int]:
    building_count = sum(1 for placement in visible_placements if placement.placement_group == "building")
    furniture_anchor_count = sum(
        1
        for placement in visible_placements
        if placement.placement_group == "street_furniture" and str(placement.anchor_poi_type or "").strip()
    )
    furniture_required_count = sum(
        1
        for placement in visible_placements
        if placement.placement_group == "street_furniture"
        and bool(placement.required)
        and not str(placement.anchor_poi_type or "").strip()
    )
    furniture_optional_count = sum(
        1
        for placement in visible_placements
        if placement.placement_group == "street_furniture"
        and not bool(placement.required)
        and not str(placement.anchor_poi_type or "").strip()
    )
    poi_count = sum(len(points) for points in nonempty_poi_points(poi_points_by_type).values())
    return {
        "visible_instance_count": int(len(visible_instance_ids)),
        "building_count": int(building_count),
        "building_target_count": int(len(building_plans)),
        "furniture_anchor_count": int(furniture_anchor_count),
        "furniture_required_count": int(furniture_required_count),
        "furniture_optional_count": int(furniture_optional_count),
        "street_furniture_count": int(furniture_anchor_count + furniture_required_count + furniture_optional_count),
        "zoning_cell_count": int(len(zoning_grid)),
        "poi_point_count": int(poi_count),
    }


def _stage_summary_text(record: ProductionStepRecord) -> str:
    counts = record.counts
    return (
        f"{record.index + 1}. {record.title}\n"
        f"- step_id: {record.step_id}\n"
        f"- visible_instances: {int(counts.get('visible_instance_count', 0))}\n"
        f"- buildings: {int(counts.get('building_count', 0))}\n"
        f"- anchor_furniture: {int(counts.get('furniture_anchor_count', 0))}\n"
        f"- required_furniture: {int(counts.get('furniture_required_count', 0))}\n"
        f"- optional_furniture: {int(counts.get('furniture_optional_count', 0))}\n"
        f"- poi_points: {int(counts.get('poi_point_count', 0))}\n"
        f"- zoning_cells: {int(counts.get('zoning_cell_count', 0))}"
    )


def _stage_scene_base(
    *,
    config: StreetComposeConfig,
    resolved_program: object,
    placement_ctx: object | None,
    palette: Mapping[str, Tuple[int, int, int, int]],
    roughness: Optional[Dict[str, float]] = None,
    texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
):
    if _is_corridor_layout_mode(config.layout_mode) and placement_ctx is not None:
        return _build_osm_base_scene(
            placement_ctx,
            palette=palette,
            roughness=roughness,
            texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
            texture_tracker=texture_tracker,
            texture_overrides=texture_overrides,
        )
    left_side_width = sum(float(band.width_m) for band in resolved_program.bands if band.side == "left")
    right_side_width = sum(float(band.width_m) for band in resolved_program.bands if band.side == "right")
    return _build_base_scene(
        length_m=float(config.length_m),
        road_width_m=float(resolved_program.road_width_m),
        left_side_width_m=float(left_side_width),
        right_side_width_m=float(right_side_width),
        street_program=resolved_program,
        palette=palette,
        roughness=roughness,
        texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
        texture_tracker=texture_tracker,
        texture_overrides=texture_overrides,
    )


def _add_polygon_slab(
    scene,
    *,
    polygon_xz: Sequence[Sequence[float]],
    height_m: float,
    y_min_m: float,
    color: Sequence[int],
    surface_role: str,
    roughness: float,
    texture_mode: str,
    node_name: str,
    texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
) -> None:
    if len(polygon_xz) < 3:
        return
    trimesh = _require_trimesh()
    try:
        from shapely.geometry import Polygon as ShapelyPolygon
    except Exception:
        ShapelyPolygon = None  # type: ignore[assignment]

    if ShapelyPolygon is not None:
        try:
            poly = ShapelyPolygon([(float(point[0]), float(point[1])) for point in polygon_xz])
            mesh = trimesh.creation.extrude_polygon(poly, float(height_m))
            verts = mesh.vertices.copy()
            old_y = verts[:, 1].copy()
            old_z = verts[:, 2].copy()
            verts[:, 1] = old_z + float(y_min_m)
            verts[:, 2] = old_y
            mesh.vertices = verts
            mesh.fix_normals()
            mesh = _apply_surface_finish(
                mesh,
                surface_role=surface_role,
                rgba=list(color),
                roughness=float(roughness),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
            )
            scene.add_geometry(mesh, node_name=node_name)
            return
        except Exception:
            logger.debug("Falling back to bbox zoning slab for %s", node_name)

    xs = [float(point[0]) for point in polygon_xz]
    zs = [float(point[1]) for point in polygon_xz]
    if not xs or not zs:
        return
    length_m = max(max(xs) - min(xs), 0.1)
    width_m = max(max(zs) - min(zs), 0.1)
    mesh = trimesh.creation.box(extents=(length_m, float(height_m), width_m))
    mesh.visual.face_colors = list(color)
    mesh.apply_translation(
        [
            float((min(xs) + max(xs)) / 2.0),
            float(y_min_m) + float(height_m) / 2.0,
            float((min(zs) + max(zs)) / 2.0),
        ]
    )
    mesh = _apply_surface_finish(
        mesh,
        surface_role=surface_role,
        rgba=list(color),
        roughness=float(roughness),
        texture_mode=texture_mode,
        texture_tracker=texture_tracker,
        texture_overrides=texture_overrides,
    )
    scene.add_geometry(mesh, node_name=node_name)


def _zoning_proxy_color(cell: Mapping[str, object]) -> Tuple[int, int, int, int]:
    lane_role = str(cell.get("lane_role", "") or "")
    land_use_type = str(cell.get("land_use_type", "") or "")
    if lane_role == "carriageway":
        return (85, 90, 96, 220)
    if "sidewalk" in lane_role:
        return (196, 199, 204, 220)
    if lane_role.startswith("left_building_buffer") or lane_role.startswith("right_building_buffer"):
        return {
            "commercial": (224, 122, 95, 220),
            "transit": (77, 150, 255, 220),
            "residential": (127, 176, 105, 220),
            "green": (42, 157, 143, 220),
        }.get(land_use_type, (168, 164, 158, 220))
    return (170, 170, 170, 220)


def _zoning_proxy_surface_role(cell: Mapping[str, object]) -> str:
    lane_role = str(cell.get("lane_role", "") or "")
    land_use_type = str(cell.get("land_use_type", "") or "")
    if lane_role == "carriageway":
        return "carriageway"
    if "sidewalk" in lane_role:
        return "clear_path"
    if lane_role.startswith("left_building_buffer") or lane_role.startswith("right_building_buffer"):
        if land_use_type == "green":
            return "grass"
        return "building_buffer"
    return "furnishing"


def _add_zoning_proxies(
    scene,
    zoning_grid: Sequence[Dict[str, object]],
    *,
    roughness: Optional[Dict[str, float]] = None,
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
) -> None:
    for idx, cell in enumerate(zoning_grid):
        polygon_xz = cell.get("polygon_xz", []) or []
        if not polygon_xz:
            continue
        surface_role = _zoning_proxy_surface_role(cell)
        _add_polygon_slab(
            scene,
            polygon_xz=polygon_xz,
            height_m=0.04 if str(cell.get("lane_role", "") or "") == "carriageway" else 0.08,
            y_min_m=0.01,
            color=_zoning_proxy_color(cell),
            surface_role=surface_role,
            roughness=(roughness or {}).get(surface_role, 0.70),
            texture_mode=texture_mode,
            node_name=f"zoning_proxy_{idx:03d}",
            texture_tracker=texture_tracker,
            texture_overrides=texture_overrides,
        )


def _add_final_land_use_zoning_proxies(
    scene,
    zoning_grid: Sequence[Dict[str, object]],
    *,
    roughness: Optional[Dict[str, float]] = None,
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
) -> None:
    land_use_cells = [
        cell
        for cell in zoning_grid
        if "building_buffer" in str(cell.get("lane_role", "") or "")
    ]
    if not land_use_cells:
        return
    _add_zoning_proxies(
        scene,
        land_use_cells,
        roughness=roughness,
        texture_mode=texture_mode,
        texture_tracker=texture_tracker,
        texture_overrides=texture_overrides,
    )


def _save_stage_companion_figure(fig: object | None, out_path: Path) -> str:
    if fig is None:
        return ""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor="white")
        try:
            import matplotlib.pyplot as plt

            plt.close(fig)
        except Exception:
            pass
        return str(out_path)
    except Exception:
        try:
            import matplotlib.pyplot as plt

            plt.close(fig)
        except Exception:
            pass
        logger.debug("Failed to save companion figure: %s", out_path)
        return ""


def _build_poi_companion_figure(
    *,
    spatial_ctx: object | None,
    placements: Sequence[StreetPlacement],
    config: StreetComposeConfig,
    osm_geometry: Mapping[str, object] | None,
    exclusion_zones: Sequence[object],
):
    if spatial_ctx is None:
        return None
    if not exclusion_zones and not nonempty_poi_points(getattr(spatial_ctx, "poi_points_by_type_xz", {}) or {}):
        return None
    zones = [
        {
            "poi_type": getattr(zone, "poi_type", ""),
            "position_xz": [float(getattr(zone, "position_xz", (0.0, 0.0))[0]), float(getattr(zone, "position_xz", (0.0, 0.0))[1])],
            "radius_m": float(getattr(zone, "radius_m", 0.0) or 0.0),
            "rule_name": str(getattr(zone, "rule_name", "")),
        }
        for zone in exclusion_zones
    ]
    return plot_poi_exclusion_overview(
        spatial_ctx,
        placements,
        config,
        poi_exclusion_zones=zones,
        poi_conflicts=[],
        osm_geometry=osm_geometry,
    )


def _build_production_steps(
    *,
    out_dir: Path,
    config: StreetComposeConfig,
    resolved_program: object,
    placement_ctx: object | None,
    poi_ctx: object | None,
    spatial_ctx: object | None,
    placements: Sequence[StreetPlacement],
    zoning_grid: Sequence[Dict[str, object]],
    building_footprints: Sequence[BuildingFootprint],
    generated_lots: Sequence[GeneratedLot],
    building_plans: Sequence[BuildingPlacementPlan],
    mesh_cache: Dict[str, _MeshCacheEntry],  # Can be either dict or _LazyMeshCache
    exclusion_zones: Sequence[object],
    palette: Mapping[str, Tuple[int, int, int, int]],
    osm_geometry: Mapping[str, object] | None,
    overall_texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
) -> Tuple[ProductionStepRecord, ...]:
    step_dir = (out_dir / "production_steps").resolve()
    step_dir.mkdir(parents=True, exist_ok=True)
    building_placements, anchor_placements, required_placements, optional_placements = _split_furniture_layers(placements)
    poi_points_by_type = extract_poi_points_by_type(poi_ctx, suffix="xz") if poi_ctx is not None else {}
    rough = surface_roughness(getattr(config, "style_preset", None))
    region_direct_mode = bool(building_footprints) and not generated_lots and not zoning_grid and all(
        str(footprint.source or "") == "building_region"
        for footprint in building_footprints
    )

    stage_visibility: Dict[str, Tuple[bool, bool, Tuple[StreetPlacement, ...], Tuple[str, ...]]] = {}
    if _is_corridor_layout_mode(config.layout_mode):
        stage_visibility = {
            "road_base": (False, False, tuple(), tuple()),
            "land_use_zoning": (True, False, tuple(), tuple()),
            "buildings": (True, False, tuple(building_placements), tuple(placement.instance_id for placement in building_placements)),
            "poi_context": (True, True, tuple(building_placements), tuple()),
            "furniture_anchor": (
                True,
                True,
                tuple(list(building_placements) + list(anchor_placements)),
                tuple(placement.instance_id for placement in anchor_placements),
            ),
            "furniture_required": (
                True,
                True,
                tuple(list(building_placements) + list(anchor_placements) + list(required_placements)),
                tuple(placement.instance_id for placement in required_placements),
            ),
            "furniture_optional": (
                True,
                True,
                tuple(list(building_placements) + list(anchor_placements) + list(required_placements) + list(optional_placements)),
                tuple(placement.instance_id for placement in optional_placements),
            ),
            "scene_preview": (
                False,
                False,
                tuple(list(building_placements) + list(anchor_placements) + list(required_placements) + list(optional_placements)),
                tuple(),
            ),
        }
    else:
        non_optional = list(anchor_placements) + list(required_placements)
        all_placements = non_optional + list(optional_placements)
        stage_visibility = {
            "road_base": (False, False, tuple(), tuple()),
            "furniture_required": (
                False,
                False,
                tuple(non_optional),
                tuple(placement.instance_id for placement in non_optional),
            ),
            "furniture_optional": (
                False,
                False,
                tuple(all_placements),
                tuple(placement.instance_id for placement in optional_placements),
            ),
            "scene_preview": (
                False,
                False,
                tuple(all_placements),
                tuple(),
            ),
        }

    records: List[ProductionStepRecord] = []
    for index, (step_id, title) in enumerate(
        _production_step_definitions(
            config.layout_mode,
            include_land_use_zoning=not region_direct_mode,
        )
    ):
        include_zoning, include_poi_overlays, visible_placements, delta_ids = stage_visibility[step_id]
        step_texture_tracker = create_scene_texture_tracker(str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")))
        scene = _stage_scene_base(
            config=config,
            resolved_program=resolved_program,
            placement_ctx=placement_ctx,
            palette=palette,
            roughness=rough,
            texture_tracker=step_texture_tracker,
            texture_overrides=texture_overrides,
        )
        _add_beauty_scene_proxies(
            scene,
            config=config,
            street_program=resolved_program,
            placement_ctx=placement_ctx,
            poi_ctx=poi_ctx,
            placements=list(visible_placements),
            texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
            texture_tracker=step_texture_tracker,
            texture_overrides=texture_overrides,
        )
        if include_zoning:
            _add_zoning_proxies(
                scene,
                zoning_grid,
                roughness=rough,
                texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
                texture_tracker=step_texture_tracker,
                texture_overrides=texture_overrides,
            )
        if visible_placements:
            # For production steps, preload the meshes needed for export
            # This ensures they're in cache before _add_instance_meshes tries to get them
            if isinstance(mesh_cache, _LazyMeshCache):
                mesh_cache.preload([p.asset_id for p in visible_placements])
            _add_instance_meshes(
                scene=scene,
                placements=list(visible_placements),
                mesh_cache=mesh_cache,
                building_plans_by_instance={
                    str(plan.instance_id): plan
                    for plan in building_plans
                },
            )
        if include_poi_overlays:
            _add_poi_markers_and_zones(scene, extract_poi_points_by_type(poi_ctx, suffix="xz") if poi_ctx is not None else {}, exclusion_zones)

        glb_path = (step_dir / f"{index:02d}_{step_id}.glb").resolve()
        scene.export(glb_path)
        del scene
        gc.collect()

        companion_path = ""
        if step_id == "land_use_zoning":
            try:
                from .topdown_render import render_design_zoning_companion

                companion_path = str(
                    render_design_zoning_companion(
                        out_path=step_dir / f"{index:02d}_{step_id}.png",
                        config=config,
                        palette=palette,
                        zoning_grid=zoning_grid,
                        building_footprints=building_footprints,
                        generated_lots=generated_lots,
                        osm_geometry=osm_geometry,
                    )
                    or ""
                )
            except Exception:
                companion_path = ""
            if not str(companion_path).strip():
                companion = plot_zoning_grid_preview_2d(
                    zoning_grid,
                    building_footprints=[],
                    generated_lots=[],
                    building_placements=[],
                    osm_geometry=osm_geometry,
                )
                companion_path = _save_stage_companion_figure(companion, step_dir / f"{index:02d}_{step_id}.png")
        elif step_id == "poi_context":
            companion = _build_poi_companion_figure(
                spatial_ctx=spatial_ctx,
                placements=visible_placements,
                config=config,
                osm_geometry=osm_geometry,
                exclusion_zones=exclusion_zones,
            )
            companion_path = _save_stage_companion_figure(companion, step_dir / f"{index:02d}_{step_id}.png")

        visible_ids = tuple(placement.instance_id for placement in visible_placements)
        counts = _stage_counts(
            visible_instance_ids=visible_ids,
            visible_placements=visible_placements,
            zoning_grid=zoning_grid if include_zoning else tuple(),
            building_plans=building_plans if step_id in {"buildings", "poi_context", "furniture_anchor", "furniture_required", "furniture_optional", "scene_preview"} else tuple(),
            poi_points_by_type=poi_points_by_type if include_poi_overlays else {},
        )
        if overall_texture_tracker is not None:
            overall_texture_tracker.merge(step_texture_tracker)
        records.append(
            ProductionStepRecord(
                step_id=step_id,
                index=index,
                title=title,
                glb_path=str(glb_path),
                companion_path=str(companion_path),
                scene_texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
                textured_base_enabled=bool(step_texture_tracker.textured_geometry_count > 0),
                visible_instance_ids=visible_ids,
                delta_instance_ids=tuple(delta_ids),
                counts=counts,
            )
        )

    manifest_path = (step_dir / "production_steps.json").resolve()
    manifest_path.write_text(
        json.dumps([record.to_dict() for record in records], indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return tuple(records)


def _preferred_final_render_companion_path(render_views: Sequence[Mapping[str, Any]]) -> str:
    preferred_names = (
        "final_oblique_45_axonometric",
        "final_plan_axonometric",
        "final_oblique_45_watercolor",
        "final_plan_watercolor",
    )
    for name in preferred_names:
        for view in render_views:
            view_name = str(view.get("name", "") or "").strip()
            view_path = str(view.get("path", "") or "").strip()
            if view_name == name and view_path:
                return view_path
    return ""


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
        side_pref = SIDE_PREF.get(category, "both")
        overall_zone = placement_ctx.sidewalk_zone  # type: ignore[attr-defined]
        if side_pref == "left":
            preferred_zone = getattr(placement_ctx, "left_sidewalk_zone", None)
        elif side_pref == "right":
            preferred_zone = getattr(placement_ctx, "right_sidewalk_zone", None)
        else:
            preferred_zone = overall_zone
        zone = preferred_zone
        if zone is None or getattr(zone, "is_empty", False):
            zone = overall_zone
        point = sample_slot_on_sidewalk(zone, rng)
        if point is None and zone is not overall_zone:
            point = sample_slot_on_sidewalk(overall_zone, rng)
    if point is None:
        return None
    x, z = point
    yaw = _yaw_for_asset_category(
        category,
        compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
    )
    return x, z, yaw


def _build_osm_base_scene(
    placement_ctx: object,
    *,
    palette: Optional[Dict[str, Tuple[int, int, int, int]]] = None,
    roughness: Optional[Dict[str, float]] = None,
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
    texture_overrides: Mapping[str, str] | None = None,
):
    """Build a trimesh Scene with carriageway + sidewalk extruded slabs from OSM geometry."""
    trimesh = _require_trimesh()
    scene = trimesh.Scene()

    carriageway = placement_ctx.carriageway  # type: ignore[attr-defined]
    sidewalk_zone = placement_ctx.sidewalk_zone  # type: ignore[attr-defined]
    colors = palette or {}
    junction_geometries = list(getattr(placement_ctx, "junction_geometries", []) or [])
    junction_sidewalk_surface_roles = {"sidewalk", "furnishing", "context_ground"}
    junction_vehicle_surface_roles = {"carriageway", "bike_lane", "bus_lane", "parking_lane"}

    def _clean_scene_polygonal_geometry(geometry: Any) -> Any:
        from shapely.geometry import GeometryCollection, MultiPolygon, Polygon as ShapelyPolygon
        from shapely.ops import unary_union

        if geometry is None or getattr(geometry, "is_empty", True):
            return MultiPolygon()
        if not getattr(geometry, "is_valid", True):
            try:
                geometry = geometry.buffer(0)
            except Exception:
                return MultiPolygon()
        if isinstance(geometry, ShapelyPolygon):
            return geometry
        if isinstance(geometry, MultiPolygon):
            return geometry
        if isinstance(geometry, GeometryCollection):
            polygons = [
                item
                for item in geometry.geoms
                if isinstance(item, (ShapelyPolygon, MultiPolygon)) and not getattr(item, "is_empty", True)
            ]
            return _clean_scene_polygonal_geometry(unary_union(polygons)) if polygons else MultiPolygon()
        return MultiPolygon()

    def _union_scene_polygonal_geometries(geometries: Sequence[Any]) -> Any:
        from shapely.geometry import MultiPolygon
        from shapely.ops import unary_union

        valid = [
            _clean_scene_polygonal_geometry(geometry)
            for geometry in geometries
            if geometry is not None and not getattr(geometry, "is_empty", True)
        ]
        valid = [geometry for geometry in valid if not getattr(geometry, "is_empty", True)]
        if not valid:
            return MultiPolygon()
        return _clean_scene_polygonal_geometry(unary_union(valid))

    junction_sidewalk_surfaces: List[Any] = []
    junction_vehicle_surfaces: List[Any] = []
    for junction in junction_geometries:
        for patch in junction.get("normalized_surface_patches", []) or ():
            role = str(patch.get("surface_role", "") or "").strip().lower()
            geometry = patch.get("geometry") if isinstance(patch, Mapping) else None
            if role in junction_sidewalk_surface_roles and geometry is not None and not getattr(geometry, "is_empty", True):
                junction_sidewalk_surfaces.append(geometry)
            elif role in junction_vehicle_surface_roles and geometry is not None and not getattr(geometry, "is_empty", True):
                junction_vehicle_surfaces.append(geometry)
    sidewalk_render_zone = _union_scene_polygonal_geometries([sidewalk_zone, *junction_sidewalk_surfaces])

    scene_bounds: List[Tuple[float, float, float, float]] = []
    for geom in (carriageway, sidewalk_render_zone):
        if geom is None or getattr(geom, "is_empty", True):
            continue
        bounds = getattr(geom, "bounds", None)
        if bounds is None or len(bounds) != 4:
            continue
        scene_bounds.append(tuple(float(value) for value in bounds))

    if scene_bounds:
        min_x = min(bounds[0] for bounds in scene_bounds)
        min_z = min(bounds[1] for bounds in scene_bounds)
        max_x = max(bounds[2] for bounds in scene_bounds)
        max_z = max(bounds[3] for bounds in scene_bounds)
        pad_m = 12.0
        ground = trimesh.creation.box(
            extents=(max(max_x - min_x + pad_m * 2.0, 20.0), 0.04, max(max_z - min_z + pad_m * 2.0, 20.0))
        )
        ground_color = list(colors.get("context_ground", (168, 163, 150, 255)))
        ground.apply_translation(
            [
                float((min_x + max_x) / 2.0),
                -0.10,
                float((min_z + max_z) / 2.0),
            ]
        )
        ground = _apply_surface_finish(
            ground,
            surface_role="context_ground",
            rgba=ground_color,
            roughness=(roughness or {}).get("context_ground", 0.85),
            texture_mode=texture_mode,
            texture_tracker=texture_tracker,
            texture_overrides=texture_overrides,
        )
        scene.add_geometry(ground, node_name="context_ground")

    def _extrude_polygon(
        geom,
        height: float,
        color,
        name_prefix: str,
        *,
        y_offset: float = 0.0,
        roughness_key: str = "",
        surface_role: str = "",
        horizontal_axes: tuple[tuple[float, float], tuple[float, float]] | None = None,
    ) -> None:
        """Extrude a shapely geometry into a thin 3D slab and add to scene.

        ``extrude_polygon`` maps the 2-D polygon (x_east, y_north) to mesh
        (X, Y) and extrudes along Z (0 ... height).  The scene convention is
        **Y-up** (XZ = ground), so we swap Y<->Z after extrusion:
            X_3d = x_east,  Y_3d = z_extrude - height + y_offset,  Z_3d = y_north
        This puts the top surface at Y = y_offset with the road lying flat on XZ.
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
                # Swap Y<->Z so road lies flat on XZ ground plane (Y-up)
                verts = mesh.vertices.copy()
                old_y = verts[:, 1].copy()   # was northing
                old_z = verts[:, 2].copy()   # was extrusion 0..height
                verts[:, 1] = old_z - height + y_offset  # Y = extrusion shifted + offset
                verts[:, 2] = old_y           # Z = northing
                mesh.vertices = verts
                mesh.fix_normals()
                mesh = _apply_surface_finish(
                    mesh,
                    surface_role=surface_role or roughness_key or "sidewalk",
                    rgba=list(color),
                    roughness=(roughness or {}).get(roughness_key or surface_role or "sidewalk", 0.9),
                    texture_mode=texture_mode,
                    texture_tracker=texture_tracker,
                    texture_overrides=texture_overrides,
                    horizontal_axes=horizontal_axes,
                )
                scene.add_geometry(mesh, node_name=f"{name_prefix}_{idx}")
            except (ValueError, RuntimeError, IndexError):
                logger.debug("Skipping degenerate %s polygon %d", name_prefix, idx)
                continue

    def _center_flowerbed_parts(geom: object) -> tuple[object, object]:
        from shapely.geometry import MultiPolygon

        source = _clean_scene_polygonal_geometry(geom)
        if getattr(source, "is_empty", True):
            return source, MultiPolygon()
        try:
            soil = source.buffer(-CENTER_FLOWERBED_CURB_WIDTH_M)
            if getattr(soil, "is_empty", True):
                return source, MultiPolygon()
            soil = _clean_scene_polygonal_geometry(soil)
            curb = _clean_scene_polygonal_geometry(source.difference(soil))
            return soil, curb
        except Exception:
            logger.debug("Failed to split center flowerbed geometry", exc_info=True)
            return source, MultiPolygon()

    def _render_center_flowerbed_polygon(
        geom: object,
        *,
        name_prefix: str,
    ) -> None:
        soil_geom, curb_geom = _center_flowerbed_parts(geom)
        if not getattr(curb_geom, "is_empty", True):
            _extrude_polygon(
                curb_geom,
                CENTER_FLOWERBED_CURB_HEIGHT_M,
                list(colors.get("curb", (145, 145, 145, 255))),
                f"{name_prefix}_curb",
                y_offset=CENTER_FLOWERBED_CURB_TOP_Y_M,
                roughness_key="curb",
                surface_role="curb",
            )
        if not getattr(soil_geom, "is_empty", True):
            _extrude_polygon(
                soil_geom,
                CENTER_PLANTING_SOIL_HEIGHT_M,
                list(colors.get("planting_soil", colors.get("tree_pit", (98, 93, 76, 255)))),
                f"{name_prefix}_soil",
                y_offset=CENTER_PLANTING_SOIL_TOP_Y_M,
                roughness_key="planting_soil",
                surface_role="planting_soil",
            )


    def _coerce_horizontal_axes(
        value: object,
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return None
        u_axis = value[0]
        v_axis = value[1]
        if not isinstance(u_axis, (list, tuple)) or not isinstance(v_axis, (list, tuple)):
            return None
        if len(u_axis) != 2 or len(v_axis) != 2:
            return None
        try:
            u = (float(u_axis[0]), float(u_axis[1]))
            v = (float(v_axis[0]), float(v_axis[1]))
        except (TypeError, ValueError):
            return None
        return u, v

    def _inject_functional_zone(zone: Dict[str, Any]) -> None:
        from shapely import make_valid
        from shapely.geometry import Polygon as ShapelyPolygon
        from .parametric_assets import generate_parametric_asset

        kind = str(zone.get("kind", "") or "").lower()
        points = zone.get("points", []) or []
        if len(points) < 3 or kind not in VALID_FUNCTIONAL_ZONE_KINDS:
            return

        poly = ShapelyPolygon(points)
        if not poly.is_valid:
            poly = make_valid(poly)
        if getattr(poly, "is_empty", True):
            return

        # Extrude ground slab
        color = list(colors.get(kind, colors.get("context_ground", (195, 185, 165, 255))))
        _extrude_polygon(
            poly,
            0.06,
            color,
            f"functional_zone_{kind}_{zone.get('id', 'unk')}",
            y_offset=0.003,
            roughness_key=kind,
            surface_role=kind,
        )

        # Place parametric structure at centroid for asset-bearing kinds
        if kind not in ("plaza", "garden", "parking"):
            centroid = poly.centroid
            if not getattr(centroid, "is_empty", True):
                cx, cz = float(centroid.x), float(centroid.y)
                try:
                    result = generate_parametric_asset({
                        "asset_kind": kind,
                        "runtime_profile": "production",
                        "params": {"detail_level": 2, "style_tag": "modern"},
                    })
                    mesh = result.mesh
                    if mesh is not None:
                        mesh.apply_translation([cx, 0.0, cz])
                        scene.add_geometry(mesh, node_name=f"parametric_zone_{kind}_{zone.get('id', 'unk')}")
                except Exception:
                    logger.debug("Failed to generate parametric asset for zone %s", zone.get("id"), exc_info=True)

        # Render user-placed furniture instances inside the functional zone
        trimesh = _require_trimesh()
        for fidx, inst in enumerate(zone.get("furniture_instances", []) or []):
            fx = float(inst.get("x", 0.0))
            fy = float(inst.get("y", 0.0))
            fkind = str(inst.get("kind", "bench") or "bench").lower()
            yaw_deg = float(inst.get("yaw_deg") or 0.0)
            yaw_rad = math.radians(yaw_deg)

            # Helper to place a mesh with yaw rotation around Y axis
            def _place_zone_furniture_mesh(mesh: object, node_name: str) -> None:
                if yaw_rad != 0.0:
                    rot = np.array([
                        [math.cos(yaw_rad), 0.0, math.sin(yaw_rad), 0.0],
                        [0.0, 1.0, 0.0, 0.0],
                        [-math.sin(yaw_rad), 0.0, math.cos(yaw_rad), 0.0],
                        [0.0, 0.0, 0.0, 1.0],
                    ])
                    mesh.apply_transform(rot)
                mesh.apply_translation([fx, 0.0, fy])
                scene.add_geometry(mesh, node_name=node_name)

            if fkind in ("bench", "lamp", "tree", "kiosk", "sculpture"):
                try:
                    result = generate_parametric_asset({
                        "asset_kind": fkind,
                        "runtime_profile": "production",
                        "params": {"detail_level": 2, "style_tag": "modern"},
                    })
                    fmesh = result.mesh
                    if fmesh is not None:
                        _place_zone_furniture_mesh(fmesh, f"zone_{zone.get('id', 'unk')}_{fkind}_{fidx}")
                        continue
                except Exception:
                    logger.debug("Failed to generate parametric asset for zone furniture %s", fkind, exc_info=True)

            # Fallback placeholders for unsupported or failed kinds
            if fkind == "trash":
                placeholder = trimesh.creation.cylinder(radius=0.25, height=0.6, sections=16)
                placeholder.visual.face_colors = (120, 120, 120, 255)
            elif fkind == "mailbox":
                placeholder = trimesh.creation.box(extents=(0.3, 0.5, 0.2))
                placeholder.visual.face_colors = (80, 120, 180, 255)
            elif fkind == "bollard":
                placeholder = trimesh.creation.cylinder(radius=0.1, height=0.9, sections=12)
                placeholder.visual.face_colors = (220, 200, 60, 255)
            elif fkind == "sign":
                placeholder = trimesh.creation.box(extents=(0.05, 1.5, 0.4))
                placeholder.visual.face_colors = (80, 160, 100, 255)
            elif fkind == "hydrant":
                placeholder = trimesh.creation.cylinder(radius=0.15, height=0.5, sections=12)
                placeholder.visual.face_colors = (200, 60, 60, 255)
            elif fkind == "bus_stop":
                placeholder = trimesh.creation.box(extents=(0.5, 2.0, 1.2))
                placeholder.visual.face_colors = (100, 140, 200, 255)
            else:
                placeholder = trimesh.creation.box(extents=(0.5, 0.5, 0.5))
                placeholder.visual.face_colors = (160, 160, 160, 255)
            _place_zone_furniture_mesh(placeholder, f"zone_{zone.get('id', 'unk')}_{fkind}_{fidx}")

    road_arm_geometries = list(getattr(placement_ctx, "road_arm_geometries", []) or [])
    if road_arm_geometries:
        for arm_idx, arm_geom in enumerate(road_arm_geometries):
            if getattr(arm_geom, "is_empty", True):
                continue
            _extrude_polygon(
                arm_geom,
                0.06,
                list(colors.get("carriageway", (65, 68, 72, 255))),
                f"carriageway_arm_{arm_idx}",
                roughness_key="carriageway",
                surface_role="carriageway",
            )
    elif not carriageway.is_empty:
        _extrude_polygon(
            carriageway,
            0.06,
            list(colors.get("carriageway", (65, 68, 72, 255))),
            "carriageway",
            roughness_key="carriageway",
            surface_role="carriageway",
        )
    if not sidewalk_render_zone.is_empty:
        _extrude_polygon(
            sidewalk_render_zone, 0.08, list(colors.get("sidewalk", (165, 168, 172, 255))), "sidewalk",
            y_offset=SIDEWALK_ELEVATION_M, roughness_key="sidewalk", surface_role="sidewalk",
        )

    # Overlay center strips (bike lane, median) on top of carriageway
    strip_zones = getattr(placement_ctx, "strip_zones", {}) or {}
    center_bike_lane = strip_zones.get("center_bike_lane")
    if center_bike_lane is not None and not getattr(center_bike_lane, "is_empty", True):
        _extrude_polygon(
            center_bike_lane,
            0.065,
            list(colors.get("bike_lane", (50, 110, 80, 255))),
            "center_bike_lane",
            y_offset=0.002,
            roughness_key="bike_lane",
            surface_role="bike_lane",
        )
    center_median = strip_zones.get("center_median")
    if center_median is not None and not getattr(center_median, "is_empty", True):
        _extrude_polygon(
            center_median,
            CENTER_ISLAND_HEIGHT_M,
            list(colors.get("median_green", (95, 125, 75, 255))),
            "center_median",
            y_offset=CENTER_ISLAND_TOP_Y_M,
            roughness_key="median_green",
            surface_role="median_green",
        )
    center_grass_belt = strip_zones.get("center_grass_belt")
    if center_grass_belt is not None and not getattr(center_grass_belt, "is_empty", True):
        _render_center_flowerbed_polygon(
            center_grass_belt,
            name_prefix="center_grass_belt",
        )
    center_median_green = strip_zones.get("center_median_green")
    if center_median_green is not None and not getattr(center_median_green, "is_empty", True):
        _render_center_flowerbed_polygon(
            center_median_green,
            name_prefix="center_median_green",
        )
    center_shared_street_surface = strip_zones.get("center_shared_street_surface")
    if center_shared_street_surface is not None and not getattr(center_shared_street_surface, "is_empty", True):
        _extrude_polygon(
            center_shared_street_surface,
            0.065,
            list(colors.get("shared_street_surface", (180, 160, 140, 255))),
            "center_shared_street_surface",
            y_offset=0.002,
            roughness_key="shared_street_surface",
            surface_role="shared_street_surface",
        )
    colored_pavement = strip_zones.get("colored_pavement")
    if colored_pavement is not None and not getattr(colored_pavement, "is_empty", True):
        _extrude_polygon(
            colored_pavement,
            0.065,
            list(colors.get("colored_pavement", (200, 175, 150, 255))),
            "colored_pavement",
            y_offset=0.002,
            roughness_key="colored_pavement",
            surface_role="colored_pavement",
        )

    # Curb: only along the raised facility-lane boundary, not around road-arm endpoints.
    # Include normalized junction carriageway surfaces so curved turn/apron edges
    # receive the same curb treatment as straight road arms.
    curb_width = 0.12
    curb_color = list(colors.get("curb", (145, 145, 145, 255)))
    rendered_vehicle_surfaces = (
        list(road_arm_geometries) if road_arm_geometries else [carriageway]
    )
    curb_source_surface = _union_scene_polygonal_geometries([*rendered_vehicle_surfaces, *junction_vehicle_surfaces])
    if not curb_source_surface.is_empty:
        try:
            curb_zone = _build_curb_boundary_zone(curb_source_surface, sidewalk_render_zone, curb_width)
            if not curb_zone.is_empty:
                _extrude_polygon(
                    curb_zone, SIDEWALK_ELEVATION_M, curb_color, "curb",
                    y_offset=SIDEWALK_ELEVATION_M, roughness_key="curb", surface_role="curb",
                )
        except Exception:
            logger.debug("Skipping curb geometry in OSM base scene")

    def _turn_lane_patch_surface_key(patch: Mapping[str, Any]) -> str:
        surface_role = str(patch.get("surface_role", "") or "carriageway").strip().lower()
        strip_kind = str(patch.get("strip_kind", "") or "").strip().lower()
        if surface_role in {"bike_lane", "bus_lane", "parking_lane"}:
            return surface_role
        if surface_role == "furnishing" or "furnishing" in strip_kind or "buffer" in strip_kind:
            return "furnishing"
        if surface_role == "context_ground" or strip_kind == "frontage_reserve":
            return "context_ground"
        if surface_role == "sidewalk" or strip_kind == "clear_sidewalk":
            return "sidewalk"
        return "carriageway"

    def _is_vehicle_turn_lane_patch(patch: Mapping[str, Any]) -> bool:
        surface_role = str(patch.get("surface_role", "") or "").strip().lower()
        strip_kind = str(patch.get("strip_kind", "") or "").strip().lower()
        stack_kind = str(patch.get("stack_kind", "") or "").strip().lower()
        return (
            stack_kind == "center"
            or surface_role in {"carriageway", "bike_lane", "bus_lane", "parking_lane"}
            or strip_kind in {"drive_lane", "bike_lane", "bus_lane", "parking_lane"}
        )

    def _has_corner_surface_patches(junction: Mapping[str, Any]) -> bool:
        for bucket_name in ("sidewalk_corner_patches", "nearroad_corner_patches", "frontage_corner_patches"):
            for patch in junction.get(bucket_name, []) or ():
                geometry = patch.get("geometry") if isinstance(patch, Mapping) else None
                if geometry is not None and not getattr(geometry, "is_empty", True):
                    return True
        return False

    def _render_corner_patch_bucket(
        junction: Mapping[str, Any],
        *,
        bucket_name: str,
        node_name: str,
        color_key: str,
        height_m: float,
        surface_role: str,
    ) -> None:
        for patch_index, patch in enumerate(junction.get(bucket_name, []) or ()):
            geometry = patch.get("geometry")
            if geometry is None or getattr(geometry, "is_empty", True):
                continue
            _extrude_polygon(
                geometry,
                height_m,
                list(colors.get(color_key, colors.get("sidewalk", (165, 168, 172, 255)))),
                f"junction_{node_name}_{junction_index}_{patch_index}",
                y_offset=SIDEWALK_ELEVATION_M,
                roughness_key=color_key,
                surface_role=surface_role,
            )

    def _normalized_surface_render_spec(patch: Mapping[str, Any]) -> Tuple[float, List[int], float, str, str]:
        role = str(patch.get("surface_role", "") or "carriageway").strip().lower()
        if role == "crossing":
            return 0.01, list(colors.get("lane_mark", (245, 245, 245, 255))), 0.008, "crossing", "crossing"
        if role in {"sidewalk", "furnishing", "context_ground"}:
            return 0.08, list(colors.get("sidewalk", (165, 168, 172, 255))), SIDEWALK_ELEVATION_M, "sidewalk", "sidewalk"
        if role in {"bike_lane", "bus_lane", "parking_lane"}:
            return 0.012, list(colors.get("carriageway", (65, 68, 72, 255))), 0.004, "carriageway", "carriageway"
        return 0.012, list(colors.get("carriageway", (65, 68, 72, 255))), 0.004, "carriageway", "carriageway"

    def _material_color(material: Mapping[str, Any], fallback: Sequence[int]) -> List[int]:
        value = str(material.get("color_hex", "") or "").strip()
        if value.startswith("#") and len(value) in {7, 9}:
            try:
                rgba = [
                    int(value[1:3], 16),
                    int(value[3:5], 16),
                    int(value[5:7], 16),
                    int(value[7:9], 16) if len(value) == 9 else 255,
                ]
                return rgba
            except ValueError:
                pass
        return list(fallback)

    def _surface_annotation_render_spec(patch: Mapping[str, Any]) -> Tuple[float, List[int], float, str, str]:
        role = str(patch.get("surface_role", "") or "colored_pavement").strip().lower()
        material = patch.get("material", {}) if isinstance(patch.get("material", {}), Mapping) else {}
        preset = str(material.get("preset", "") or "").strip().lower()
        texture_key = str(material.get("texture_key", "") or "").strip().lower()

        if role == "bus_lane" or preset == "bus_lane_green":
            color = list(colors.get("bus_lane", (74, 142, 96, 255)))
            if preset == "bus_lane_green":
                color = [64, 148, 92, 255]
            return 0.014, _material_color(material, color), 0.016, texture_key or "bus_lane", "bus_lane"
        if role == "bike_lane":
            return 0.014, _material_color(material, colors.get("bike_lane", (50, 110, 80, 255))), 0.016, texture_key or "bike_lane", "bike_lane"
        if role == "parking_lane":
            return 0.014, _material_color(material, colors.get("parking_lane", (156, 126, 84, 255))), 0.016, texture_key or "parking_lane", "parking_lane"
        if role in {"median", "median_green", "safety_island"}:
            fallback = colors.get("sidewalk", (180, 178, 168, 255)) if role == "safety_island" else colors.get("median_green", (95, 125, 75, 255))
            return CENTER_ISLAND_HEIGHT_M, _material_color(material, fallback), CENTER_ISLAND_TOP_Y_M, texture_key or ("sidewalk" if role == "safety_island" else "median_green"), role
        if role == "grass_belt":
            return CENTER_ISLAND_HEIGHT_M, _material_color(material, colors.get("grass_belt", (100, 150, 80, 255))), CENTER_ISLAND_TOP_Y_M, texture_key or "grass_belt", "grass_belt"
        if role == "shared_street_surface":
            return 0.014, _material_color(material, colors.get("shared_street_surface", (180, 160, 140, 255))), 0.016, texture_key or "shared_street_surface", "shared_street_surface"
        if role == "transit_pad":
            return 0.014, _material_color(material, colors.get("transit_pad", (118, 129, 145, 255))), 0.018, texture_key or "transit_pad", "transit_pad"
        return 0.014, _material_color(material, colors.get("colored_pavement", (200, 175, 150, 255))), 0.016, texture_key or "colored_pavement", "colored_pavement"

    for patch_index, patch in enumerate(getattr(placement_ctx, "surface_annotations", []) or []):
        geometry = patch.get("geometry") if isinstance(patch, Mapping) else None
        if geometry is None or getattr(geometry, "is_empty", True):
            continue
        height_m, color, y_offset, roughness_key, surface_role = _surface_annotation_render_spec(patch)
        _extrude_polygon(
            geometry,
            height_m,
            color,
            f"surface_annotation_{patch.get('surface_id', patch_index)}",
            y_offset=y_offset,
            roughness_key=roughness_key,
            surface_role=surface_role,
        )

    def _render_crosswalk_zebra_patch(
        geometry,
        *,
        node_name_prefix: str,
        horizontal_axes: Sequence[Sequence[float]] | None = None,
    ) -> None:
        if geometry is None or getattr(geometry, "is_empty", True):
            return
        from shapely.geometry import Polygon

        def _unit_axis(vector: Sequence[float], fallback: Tuple[float, float]) -> Tuple[float, float]:
            x = float(vector[0]) if len(vector) >= 1 else float(fallback[0])
            z = float(vector[1]) if len(vector) >= 2 else float(fallback[1])
            length = math.hypot(x, z)
            if length <= 1e-9:
                return fallback
            return x / length, z / length

        def _orthogonal_axis(
            vector: Sequence[float],
            *,
            against: Tuple[float, float],
        ) -> Tuple[float, float]:
            raw = _unit_axis(vector, (-against[1], against[0]))
            dot_value = raw[0] * against[0] + raw[1] * against[1]
            orthogonal = (raw[0] - dot_value * against[0], raw[1] - dot_value * against[1])
            return _unit_axis(orthogonal, (-against[1], against[0]))

        def _axes_from_geometry() -> Tuple[Tuple[float, float], Tuple[float, float]]:
            rectangle = geometry.minimum_rotated_rectangle
            coords = list(getattr(rectangle, "exterior", rectangle).coords)
            if len(coords) < 4:
                return (1.0, 0.0), (0.0, 1.0)
            edges: List[Tuple[float, Tuple[float, float]]] = []
            for index in range(4):
                start = coords[index]
                end = coords[(index + 1) % 4]
                vector = (float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))
                edges.append((math.hypot(vector[0], vector[1]), vector))
            edges.sort(key=lambda item: item[0])
            short_axis = _unit_axis(edges[0][1], (1.0, 0.0))
            long_axis = _orthogonal_axis(edges[-1][1], against=short_axis)
            return short_axis, long_axis

        coerced_axes = _coerce_horizontal_axes(horizontal_axes)
        if coerced_axes is not None:
            axis_u = _unit_axis(coerced_axes[0], (1.0, 0.0))
            axis_v = _orthogonal_axis(coerced_axes[1], against=axis_u)
        else:
            axis_u, axis_v = _axes_from_geometry()

        rectangle = geometry.minimum_rotated_rectangle
        rect_coords = list(getattr(rectangle, "exterior", rectangle).coords)
        if len(rect_coords) < 4:
            return
        u_values = [float(point[0]) * axis_u[0] + float(point[1]) * axis_u[1] for point in rect_coords[:4]]
        v_values = [float(point[0]) * axis_v[0] + float(point[1]) * axis_v[1] for point in rect_coords[:4]]
        u_min, u_max = min(u_values), max(u_values)
        v_min, v_max = min(v_values), max(v_values)
        crossing_width_m = max(0.1, float(v_max - v_min))

        stripe_width_m = min(0.55, max(0.28, crossing_width_m / 18.0))
        stripe_gap_m = min(0.50, max(0.24, stripe_width_m * 0.85))
        stripe_period_m = stripe_width_m + stripe_gap_m
        stripe_count = max(1, int(math.floor((crossing_width_m + stripe_gap_m) / stripe_period_m)))
        used_width_m = stripe_count * stripe_width_m + max(0, stripe_count - 1) * stripe_gap_m
        cursor_v = v_min + max(0.0, (crossing_width_m - used_width_m) / 2.0)

        def _point(local_u: float, local_v: float) -> Tuple[float, float]:
            return (
                axis_u[0] * local_u + axis_v[0] * local_v,
                axis_u[1] * local_u + axis_v[1] * local_v,
            )

        for stripe_idx in range(stripe_count):
            stripe_v0 = cursor_v + float(stripe_idx) * stripe_period_m
            stripe_v1 = min(stripe_v0 + stripe_width_m, v_max)
            if stripe_v1 <= stripe_v0:
                continue
            stripe_rect = Polygon([
                _point(u_min, stripe_v0),
                _point(u_max, stripe_v0),
                _point(u_max, stripe_v1),
                _point(u_min, stripe_v1),
            ])
            stripe_geometry = stripe_rect.intersection(geometry)
            if stripe_geometry is None or getattr(stripe_geometry, "is_empty", True):
                continue
            _extrude_polygon(
                stripe_geometry,
                0.012,
                list(colors.get("lane_mark", (245, 245, 245, 255))),
                f"{node_name_prefix}_stripe_{stripe_idx}",
                y_offset=0.010,
                roughness_key="crossing",
                surface_role="crossing",
                horizontal_axes=(axis_u, axis_v),
            )

    if junction_geometries:
        for junction_index, junction in enumerate(junction_geometries):
            normalized_surface_patches = list(junction.get("normalized_surface_patches", []) or ())
            if normalized_surface_patches:
                for patch_index, patch in enumerate(normalized_surface_patches):
                    geometry = patch.get("geometry")
                    if geometry is None or getattr(geometry, "is_empty", True):
                        continue
                    role = str(patch.get("surface_role", "") or "carriageway").strip().lower()
                    if role in junction_sidewalk_surface_roles:
                        continue
                    if role == "crossing":
                        _render_crosswalk_zebra_patch(
                            geometry,
                            node_name_prefix=f"junction_normalized_crossing_{junction_index}_{patch_index}",
                            horizontal_axes=patch.get("horizontal_axes"),
                        )
                        continue
                    height_m, color, y_offset, roughness_key, surface_role = _normalized_surface_render_spec(patch)
                    _extrude_polygon(
                        geometry,
                        height_m,
                        color,
                        f"junction_normalized_surface_{junction_index}_{patch_index}",
                        y_offset=y_offset,
                        roughness_key=roughness_key,
                        surface_role=surface_role,
                    )
                continue
            carriageway_core = junction.get("carriageway_core") or junction.get("junction_core_rect")
            if carriageway_core is not None and not getattr(carriageway_core, "is_empty", True):
                _extrude_polygon(
                    carriageway_core,
                    0.012,
                    list(colors.get("carriageway", (65, 68, 72, 255))),
                    f"junction_carriageway_core_{junction_index}",
                    y_offset=0.004,
                    roughness_key="carriageway",
                    surface_role="carriageway",
                )
            for patch_index, patch in enumerate(junction.get("crosswalk_patches", []) or ()):
                geometry = patch.get("geometry")
                if geometry is None or getattr(geometry, "is_empty", True):
                    continue
                patch_axes = _coerce_horizontal_axes(patch.get("horizontal_axes"))
                _render_crosswalk_zebra_patch(
                    geometry,
                    horizontal_axes=patch_axes,
                    node_name_prefix=f"junction_crosswalk_{junction_index}_{patch_index}",
                )
            turn_lane_patches = list(junction.get("turn_lane_patches", []) or ())
            has_corner_surface_patches = _has_corner_surface_patches(junction)
            visible_turn_lane_patches = [
                patch
                for patch in turn_lane_patches
                if _is_vehicle_turn_lane_patch(patch) or not has_corner_surface_patches
            ]
            for patch_index, patch in enumerate(visible_turn_lane_patches):
                geometry = patch.get("geometry")
                if geometry is None or getattr(geometry, "is_empty", True):
                    continue
                stack_kind = str(patch.get("stack_kind", "") or "").strip().lower()
                surface_role = str(patch.get("surface_role", "") or "carriageway").strip().lower()
                color_key = _turn_lane_patch_surface_key(patch)
                if color_key == "furnishing":
                    color = list(colors.get("furnishing", colors.get("sidewalk", (165, 168, 172, 255))))
                elif color_key == "bike_lane":
                    color = list(colors.get("bike_lane", (50, 110, 80, 255)))
                elif color_key == "bus_lane":
                    color = list(colors.get("bus_lane", colors.get("carriageway", (65, 68, 72, 255))))
                elif color_key == "parking_lane":
                    color = list(colors.get("parking_lane", colors.get("carriageway", (65, 68, 72, 255))))
                else:
                    color = list(colors.get(color_key, colors.get("carriageway", (65, 68, 72, 255))))
                is_center_turn = stack_kind == "center" or color_key in {"carriageway", "bike_lane", "bus_lane", "parking_lane"}
                _extrude_polygon(
                    geometry,
                    0.014 if is_center_turn else 0.055,
                    color,
                    f"junction_turn_lane_{junction_index}_{patch_index}",
                    y_offset=0.010 if is_center_turn else SIDEWALK_ELEVATION_M,
                    roughness_key=color_key,
                    surface_role=surface_role,
                )
            for patch_group, group_name in (
                (junction.get("lane_surface_patches", []) or (), "lane_surface"),
                (junction.get("merged_surface_patches", []) or (), "merged_surface"),
            ):
                for patch_index, patch in enumerate(patch_group):
                    geometry = patch.get("geometry")
                    if geometry is None or getattr(geometry, "is_empty", True):
                        continue
                    _extrude_polygon(
                        geometry,
                        0.014,
                        list(colors.get("carriageway", (65, 68, 72, 255))),
                        f"junction_{group_name}_{junction_index}_{patch_index}",
                        y_offset=0.012,
                        roughness_key="carriageway",
                        surface_role="carriageway",
                    )
            _render_corner_patch_bucket(
                junction,
                bucket_name="frontage_corner_patches",
                node_name="frontage_corner",
                color_key="context_ground",
                height_m=0.05,
                surface_role="context_ground",
            )
            _render_corner_patch_bucket(
                junction,
                bucket_name="nearroad_corner_patches",
                node_name="nearroad_corner",
                color_key="furnishing",
                height_m=0.05,
                surface_role="furnishing",
            )
            _render_corner_patch_bucket(
                junction,
                bucket_name="sidewalk_corner_patches",
                node_name="sidewalk_corner",
                color_key="sidewalk",
                height_m=0.08,
                surface_role="sidewalk",
            )

    fallback_length_m = 20.0
    if scene_bounds:
        fallback_length_m = max(
            max(bounds[2] - bounds[0] for bounds in scene_bounds),
            max(bounds[3] - bounds[1] for bounds in scene_bounds),
            20.0,
        )
    road_center_x_m, road_center_z_m, road_yaw_deg, road_length_m = _road_pose_from_context(
        placement_ctx,
        float(fallback_length_m),
    )
    road_references = list(getattr(placement_ctx, "road_references", []) or [])
    marking_exclusion_geometries = _junction_marking_exclusion_geometries(
        list(getattr(placement_ctx, "junction_geometries", []) or ())
    )
    if not road_references:
        fallback_reference = getattr(placement_ctx, "road_reference", None)
        if fallback_reference is not None:
            road_references = [fallback_reference]
    if road_references:
        for road_index, road_reference in enumerate(road_references):
            coords = _road_reference_coords(road_reference)
            road_reference_width_m = float(getattr(road_reference, "width_m", 0.0) or 0.0)
            road_reference_width_m = road_reference_width_m or float(getattr(placement_ctx, "carriageway_width_m", 0.0) or 0.0)
            lane_count_hint: int | None = None
            lane_separator_offsets = _drive_lane_internal_offsets(
                list(getattr(placement_ctx, "detailed_strip_profiles", []) or ())
            )
            for strip in list(getattr(placement_ctx, "detailed_strip_profiles", []) or ()):
                if (
                    str(strip.get("side", "")).strip().lower() == "center"
                    and str(strip.get("kind", "")).strip().lower() == "drive_lane"
                ):
                    lane_count_hint = int(lane_count_hint or 0) + 1
            lane_count_for_markings = (
                len(lane_separator_offsets) + 1
                if lane_separator_offsets
                else lane_count_hint
            )
            _add_centerline_markings(
                scene,
                road_length_m=float(max(_polyline_length_m(coords), 0.0) or road_length_m),
                road_width_m=road_reference_width_m,
                road_center_x_m=float(road_center_x_m),
                road_center_z_m=float(road_center_z_m),
                road_yaw_deg=float(road_yaw_deg),
                lane_count=lane_count_for_markings,
                highway_type=str(getattr(road_reference, "highway_type", "")),
                base_lane_width_m=(
                    road_reference_width_m / float(lane_count_for_markings)
                    if lane_count_for_markings and road_reference_width_m > 0.0
                    else None
                ),
                road_coords=coords,
                lane_separator_offsets_m=lane_separator_offsets,
                marking_exclusion_geometries=marking_exclusion_geometries,
                color=colors.get("lane_mark", (245, 245, 245, 255)),
                roughness=(roughness or {}).get("lane_mark", 0.30),
                node_name_prefix=f"centerline_mark_{road_index}",
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
            )
            # Add lane edge markings for this road reference
            _add_lane_edge_markings(
                scene,
                road_length_m=float(max(_polyline_length_m(coords), 0.0) or road_length_m),
                road_center_x_m=float(road_center_x_m),
                road_center_z_m=float(road_center_z_m),
                road_yaw_deg=float(road_yaw_deg),
                road_width_m=road_reference_width_m,
                detailed_strip_profiles=list(getattr(placement_ctx, "detailed_strip_profiles", []) or []),
                highway_type=str(getattr(road_reference, "highway_type", "")),
                road_coords=coords,
                marking_exclusion_geometries=marking_exclusion_geometries,
                edge_color=list(colors.get("lane_edge", (230, 200, 50, 255))),
                roughness=(roughness or {}).get("lane_edge", 0.30),
                node_name_prefix=f"lane_edge_{road_index}",
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
                texture_overrides=texture_overrides,
            )
    else:
        _add_centerline_markings(
            scene,
            road_length_m=float(road_length_m),
            road_width_m=float(getattr(placement_ctx, "carriageway_width_m", 0.0) or 0.0),
            road_center_x_m=float(road_center_x_m),
            road_center_z_m=float(road_center_z_m),
            road_yaw_deg=float(road_yaw_deg),
            lane_count=None,
            highway_type="",
            road_coords=_road_reference_coords(placement_ctx),
            lane_separator_offsets_m=_drive_lane_internal_offsets(
                list(getattr(placement_ctx, "detailed_strip_profiles", []) or ())
            ),
            marking_exclusion_geometries=marking_exclusion_geometries,
            color=colors.get("lane_mark", (245, 245, 245, 255)),
            roughness=(roughness or {}).get("lane_mark", 0.30),
            texture_mode=texture_mode,
            texture_tracker=texture_tracker,
            texture_overrides=texture_overrides,
        )
        # Add lane edge markings (fallback path)
        _add_lane_edge_markings(
            scene,
            road_length_m=float(road_length_m),
            road_center_x_m=float(road_center_x_m),
            road_center_z_m=float(road_center_z_m),
            road_yaw_deg=float(road_yaw_deg),
            road_width_m=float(getattr(placement_ctx, "carriageway_width_m", 0.0) or 0.0),
            highway_type="",
            detailed_strip_profiles=list(getattr(placement_ctx, "detailed_strip_profiles", []) or []),
            road_coords=_road_reference_coords(placement_ctx),
            marking_exclusion_geometries=marking_exclusion_geometries,
            edge_color=list(colors.get("lane_edge", (230, 200, 50, 255))),
            roughness=(roughness or {}).get("lane_edge", 0.30),
            texture_mode=texture_mode,
            texture_tracker=texture_tracker,
            texture_overrides=texture_overrides,
        )

    for zone in getattr(placement_ctx, "functional_zones", []) or []:
        _inject_functional_zone(zone)

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


def _should_embed_debug_scene_overlays(config: StreetComposeConfig) -> bool:
    # Keep exported GLB focused on presentation geometry; POI diagnostics remain in JSON and 2D plots.
    return False


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

    def _serialize_polyline(points) -> list:
        return [
            [round(float(point[0]), 3), round(float(point[1]), 3)]
            for point in (points or ())
            if len(point) >= 2
        ]

    def _serialize_point(point) -> list:
        if not point or len(point) < 2:
            return [0.0, 0.0]
        return [round(float(point[0]), 3), round(float(point[1]), 3)]

    def _serialize_horizontal_axes(value) -> list | None:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return None
        axes = []
        for axis in value:
            if not isinstance(axis, (list, tuple)) or len(axis) != 2:
                return None
            try:
                x = float(axis[0])
                y = float(axis[1])
            except (TypeError, ValueError):
                return None
            if not math.isfinite(x) or not math.isfinite(y):
                return None
            length = math.hypot(x, y)
            if length <= 1e-9:
                return None
            axes.append((x / length, y / length))
        return [
            [round(float(axis[0]), 6), round(float(axis[1]), 6)]
            for axis in axes
        ]

    def _serialize_corner_patch(patch) -> dict:
        return {
            "patch_id": str(patch.get("patch_id", "") or ""),
            "quadrant_id": str(patch.get("quadrant_id", "") or ""),
            "strip_kind": str(patch.get("strip_kind", "") or ""),
            "strip_id_a": str(patch.get("strip_id_a", "") or ""),
            "strip_id_b": str(patch.get("strip_id_b", "") or ""),
            "from_centerline_id": str(patch.get("from_centerline_id", "") or ""),
            "from_strip_id": str(patch.get("from_strip_id", "") or ""),
            "from_strip_zone": str(patch.get("from_strip_zone", "") or ""),
            "to_centerline_id": str(patch.get("to_centerline_id", "") or ""),
            "to_strip_id": str(patch.get("to_strip_id", "") or ""),
            "to_strip_zone": str(patch.get("to_strip_zone", "") or ""),
            "generation_mode": str(patch.get("generation_mode", "") or ""),
            "chamfer_depth_m": round(float(patch.get("chamfer_depth_m", 0.0) or 0.0), 3),
            "effective_chamfer_depth_m": round(float(patch.get("effective_chamfer_depth_m", 0.0) or 0.0), 3),
            "fillet_radius_m": round(float(patch.get("fillet_radius_m", 0.0) or 0.0), 3),
            "tangent_setback_m": round(float(patch.get("tangent_setback_m", 0.0) or 0.0), 3),
            "reference_q_m": round(float(patch.get("reference_q_m", 0.0) or 0.0), 3),
            "center_q_m": round(float(patch.get("center_q_m", 0.0) or 0.0), 3),
            "radius_floor_m": round(float(patch.get("radius_floor_m", 0.0) or 0.0), 3),
            "surface_role": str(patch.get("surface_role", "") or ""),
            "rings": _extract_rings(patch.get("geometry")),
        }

    def _serialize_normalized_surface_patch(patch) -> dict:
        record = {
            "surface_id": str(patch.get("surface_id", "") or ""),
            "surface_kind": str(patch.get("surface_kind", "") or "normalized"),
            "surface_role": str(patch.get("surface_role", "") or ""),
            "component_index": int(patch.get("component_index", 0) or 0),
            "source_ids": [str(value) for value in patch.get("source_ids", []) or ()],
            "source_kinds": [str(value) for value in patch.get("source_kinds", []) or ()],
            "is_overlay": bool(patch.get("is_overlay", False)),
            "area_m2": round(float(patch.get("area_m2", 0.0) or 0.0), 3),
            "rings": _extract_rings(patch.get("geometry")),
        }
        horizontal_axes = _serialize_horizontal_axes(patch.get("horizontal_axes"))
        if horizontal_axes is not None:
            record["horizontal_axes"] = horizontal_axes
        return record

    def _serialize_surface_annotation_patch(patch) -> dict:
        return {
            "surface_id": str(patch.get("surface_id", "") or ""),
            "annotation_id": str(patch.get("annotation_id", patch.get("surface_id", "")) or ""),
            "label": str(patch.get("label", "") or ""),
            "kind": str(patch.get("kind", "") or ""),
            "surface_kind": str(patch.get("surface_kind", patch.get("kind", "")) or ""),
            "surface_role": str(patch.get("surface_role", "") or ""),
            "centerline_id": str(patch.get("centerline_id", "") or ""),
            "station_start_m": round(float(patch.get("station_start_m", 0.0) or 0.0), 3),
            "station_end_m": round(float(patch.get("station_end_m", 0.0) or 0.0), 3),
            "lateral_start_m": round(float(patch.get("lateral_start_m", 0.0) or 0.0), 3),
            "lateral_end_m": round(float(patch.get("lateral_end_m", 0.0) or 0.0), 3),
            "material": dict(patch.get("material", {}) or {}),
            "area_m2": round(float(patch.get("area_m2", 0.0) or 0.0), 3),
            "rings": _extract_rings(patch.get("geometry")),
        }

    result: dict = {}
    carriageway = placement_ctx.carriageway  # type: ignore[attr-defined]
    sidewalk = placement_ctx.sidewalk_zone  # type: ignore[attr-defined]
    if not carriageway.is_empty:
        result["carriageway_rings"] = _extract_rings(carriageway)
    if not sidewalk.is_empty:
        result["sidewalk_rings"] = _extract_rings(sidewalk)
    left_sidewalk = getattr(placement_ctx, "left_sidewalk_zone", None)
    right_sidewalk = getattr(placement_ctx, "right_sidewalk_zone", None)
    if left_sidewalk is not None and not left_sidewalk.is_empty:
        result["left_sidewalk_rings"] = _extract_rings(left_sidewalk)
    if right_sidewalk is not None and not right_sidewalk.is_empty:
        result["right_sidewalk_rings"] = _extract_rings(right_sidewalk)
    surface_annotations = list(getattr(placement_ctx, "surface_annotations", []) or [])
    if surface_annotations:
        result["surface_annotations"] = [
            _serialize_surface_annotation_patch(patch)
            for patch in surface_annotations
        ]
    aoi = getattr(placement_ctx, "aoi_polygon", None)
    if aoi is not None and not aoi.is_empty:
        b = aoi.bounds  # (minx, miny, maxx, maxy)
        result["aoi_bbox_m"] = [round(v, 2) for v in b]
    junction_geometries = list(getattr(placement_ctx, "junction_geometries", []) or [])
    if junction_geometries:
        result["junction_geometries"] = []
        for item in junction_geometries:
            junction_item = {
                "junction_id": str(item.get("junction_id", "") or ""),
                "kind": str(item.get("kind", "") or ""),
                "anchor_xy": [round(float(value), 3) for value in item.get("anchor_xy", [0.0, 0.0])[:2]],
                "arm_count": int(item.get("arm_count", 0) or 0),
                "connected_road_ids": [int(value) for value in item.get("connected_road_ids", []) or ()],
                "junction_core_rect_rings": _extract_rings(item.get("junction_core_rect")),
                "carriageway_core_rings": _extract_rings(item.get("carriageway_core") or item.get("junction_core_rect")),
                "approach_boundaries": [
                    {
                        "boundary_id": str(boundary.get("boundary_id", "") or ""),
                        "road_id": int(boundary.get("road_id", 0) or 0),
                        "centerline_id": str(boundary.get("centerline_id", "") or ""),
                        "center_xy": [
                            round(float(value), 3)
                            for value in boundary.get("center_xy", [0.0, 0.0])[:2]
                        ],
                        "start_xy": [
                            round(float(value), 3)
                            for value in boundary.get("start_xy", [0.0, 0.0])[:2]
                        ],
                        "end_xy": [
                            round(float(value), 3)
                            for value in boundary.get("end_xy", [0.0, 0.0])[:2]
                        ],
                        "exit_distance_m": round(float(boundary.get("exit_distance_m", 0.0) or 0.0), 3),
                    }
                    for boundary in item.get("approach_boundaries", []) or ()
                ],
                "approach_split_lines": [
                    {
                        "boundary_id": str(boundary.get("boundary_id", "") or ""),
                        "road_id": int(boundary.get("road_id", 0) or 0),
                        "centerline_id": str(boundary.get("centerline_id", "") or ""),
                        "center_xy": [
                            round(float(value), 3)
                            for value in boundary.get("center_xy", [0.0, 0.0])[:2]
                        ],
                        "start_xy": [
                            round(float(value), 3)
                            for value in boundary.get("start_xy", [0.0, 0.0])[:2]
                        ],
                        "end_xy": [
                            round(float(value), 3)
                            for value in boundary.get("end_xy", [0.0, 0.0])[:2]
                        ],
                        "exit_distance_m": round(float(boundary.get("exit_distance_m", 0.0) or 0.0), 3),
                    }
                    for boundary in item.get("approach_split_lines", []) or ()
                ],
                "skeleton_foot_points": [
                    {
                        "foot_id": str(foot.get("foot_id", "") or ""),
                        "road_id": int(foot.get("road_id", 0) or 0),
                        "centerline_id": str(foot.get("centerline_id", "") or ""),
                        "xy": [
                            round(float(value), 3)
                            for value in foot.get("xy", [0.0, 0.0])[:2]
                        ],
                    }
                    for foot in item.get("skeleton_foot_points", []) or ()
                ],
                "sub_lane_control_points": [
                    {
                        "control_id": str(control.get("control_id", "") or ""),
                        "road_id": int(control.get("road_id", 0) or 0),
                        "centerline_id": str(control.get("centerline_id", "") or ""),
                        "strip_kind": str(control.get("strip_kind", "") or ""),
                        "strip_zone": str(control.get("strip_zone", "") or ""),
                        "point_kind": str(control.get("point_kind", "") or ""),
                        "xy": [
                            round(float(value), 3)
                            for value in control.get("xy", [0.0, 0.0])[:2]
                        ],
                    }
                    for control in item.get("sub_lane_control_points", []) or ()
                ],
                "crosswalk_patches": [
                    {
                        "patch_id": str(patch.get("patch_id", "") or ""),
                        "road_id": int(patch.get("road_id", 0) or 0),
                        **(
                            {"horizontal_axes": horizontal_axes}
                            if (horizontal_axes := _serialize_horizontal_axes(patch.get("horizontal_axes"))) is not None
                            else {}
                        ),
                        "rings": _extract_rings(patch.get("geometry")),
                    }
                    for patch in item.get("crosswalk_patches", []) or ()
                ],
                "turn_lane_patches": [
                    {
                        "patch_id": str(patch.get("patch_id", "") or ""),
                        "quadrant_id": str(patch.get("quadrant_id", "") or ""),
                        "strip_kind": str(patch.get("strip_kind", "") or ""),
                        "strip_id_a": str(patch.get("strip_id_a", "") or ""),
                        "strip_id_b": str(patch.get("strip_id_b", "") or ""),
                        "lane_index": int(patch.get("lane_index", 0) or 0),
                        "flow": str(patch.get("flow", "") or ""),
                        "direction": str(patch.get("direction", "") or ""),
                        "surface_role": str(patch.get("surface_role", "") or ""),
                        "stack_kind": str(patch.get("stack_kind", "") or ""),
                        "rings": _extract_rings(patch.get("geometry")),
                    }
                    for patch in item.get("turn_lane_patches", []) or ()
                ],
                "turn_lane_debug": [
                    {
                        "quadrant_id": str(record.get("quadrant_id", "") or ""),
                        "strip_kind": str(record.get("strip_kind", "") or ""),
                        "reason": str(record.get("reason", "") or ""),
                        **(
                            {"sweep_deg": round(float(record.get("sweep_deg", 0.0) or 0.0), 3)}
                            if "sweep_deg" in record
                            else {}
                        ),
                        **(
                            {"has_arm_a": bool(record.get("has_arm_a")), "has_arm_b": bool(record.get("has_arm_b"))}
                            if "has_arm_a" in record or "has_arm_b" in record
                            else {}
                        ),
                    }
                    for record in item.get("turn_lane_debug", []) or ()
                ],
                "arm_skeletons": [
                    {
                        "arm_skeleton_id": str(arm.get("arm_skeleton_id", "") or ""),
                        "arm_index": int(arm.get("arm_index", 0) or 0),
                        "road_id": int(arm.get("road_id", 0) or 0),
                        "centerline_id": str(arm.get("centerline_id", "") or ""),
                        "angle_deg": round(float(arm.get("angle_deg", 0.0) or 0.0), 3),
                        "tangent_xy": _serialize_point(arm.get("tangent_xy", [0.0, 0.0])),
                        "normal_xy": _serialize_point(arm.get("normal_xy", [0.0, 0.0])),
                        "split_center_xy": _serialize_point(arm.get("split_center_xy", [0.0, 0.0])),
                        "split_start_xy": _serialize_point(arm.get("split_start_xy", [0.0, 0.0])),
                        "split_end_xy": _serialize_point(arm.get("split_end_xy", [0.0, 0.0])),
                        "split_distance_m": round(float(arm.get("split_distance_m", 0.0) or 0.0), 3),
                        "core_exit_distance_m": round(float(arm.get("core_exit_distance_m", 0.0) or 0.0), 3),
                        "corner_facing_sides": [
                            {
                                "quadrant_id": str(side.get("quadrant_id", "") or ""),
                                "role": str(side.get("role", "") or ""),
                                "side": str(side.get("side", "") or ""),
                            }
                            for side in arm.get("corner_facing_sides", []) or ()
                        ],
                    }
                    for arm in item.get("arm_skeletons", []) or ()
                ],
                "lane_surface_patches": [
                    {
                        "surface_id": str(patch.get("surface_id", "") or ""),
                        "surface_kind": str(patch.get("surface_kind", "") or "lane"),
                        "lane_id": str(patch.get("lane_id", "") or ""),
                        "arm_key": str(patch.get("arm_key", "") or ""),
                        "flow": str(patch.get("flow", "") or ""),
                        "lane_index": int(patch.get("lane_index", 0) or 0),
                        "provenance": str(patch.get("provenance", "") or ""),
                        "rings": _extract_rings(patch.get("geometry")),
                    }
                    for patch in item.get("lane_surface_patches", []) or ()
                ],
                "merged_surface_patches": [
                    {
                        "surface_id": str(patch.get("surface_id", "") or ""),
                        "surface_kind": str(patch.get("surface_kind", "") or "merged"),
                        "provenance": str(patch.get("provenance", "") or ""),
                        "merged_from_surface_ids": [str(value) for value in patch.get("merged_from_surface_ids", []) or ()],
                        "merged_from_lane_ids": [str(value) for value in patch.get("merged_from_lane_ids", []) or ()],
                        "rings": _extract_rings(patch.get("geometry")),
                    }
                    for patch in item.get("merged_surface_patches", []) or ()
                ],
                "canonical_surface_patches": [
                    {
                        "surface_id": str(patch.get("surface_id", "") or ""),
                        "surface_kind": str(patch.get("surface_kind", "") or "canonical"),
                        "surface_role": str(patch.get("surface_role", "") or ""),
                        "strip_kind": str(patch.get("strip_kind", "") or ""),
                        "source_kind": str(patch.get("source_kind", "") or ""),
                        "paired_connector_id": str(patch.get("paired_connector_id", "") or ""),
                        "endpoint_role": str(patch.get("endpoint_role", "") or ""),
                        "chamfer_depth_m": round(float(patch.get("chamfer_depth_m", 0.0) or 0.0), 3),
                        "effective_chamfer_depth_m": round(float(patch.get("effective_chamfer_depth_m", 0.0) or 0.0), 3),
                        "fillet_radius_m": round(float(patch.get("fillet_radius_m", 0.0) or 0.0), 3),
                        "tangent_setback_m": round(float(patch.get("tangent_setback_m", 0.0) or 0.0), 3),
                        "reference_q_m": round(float(patch.get("reference_q_m", 0.0) or 0.0), 3),
                        "center_q_m": round(float(patch.get("center_q_m", 0.0) or 0.0), 3),
                        "rings": _extract_rings(patch.get("geometry")),
                    }
                    for patch in item.get("canonical_surface_patches", []) or ()
                ],
                "normalized_surface_patches": [
                    _serialize_normalized_surface_patch(patch)
                    for patch in item.get("normalized_surface_patches", []) or ()
                ],
                "surface_normalization_debug": dict(item.get("surface_normalization_debug", {}) or {}),
            }
            if str(item.get("kind", "") or "") == "cross_junction":
                junction_item["quadrant_corner_kernels"] = [
                    {
                        "kernel_id": str(kernel.get("kernel_id", "") or ""),
                        "quadrant_id": str(kernel.get("quadrant_id", "") or ""),
                        "road_a_id": int(kernel.get("road_a_id", 0) or 0),
                        "road_b_id": int(kernel.get("road_b_id", 0) or 0),
                        "centerline_a_id": str(kernel.get("centerline_a_id", "") or ""),
                        "centerline_b_id": str(kernel.get("centerline_b_id", "") or ""),
                        "kernel_kind": str(kernel.get("kernel_kind", "") or ""),
                        "center_xy": _serialize_point(kernel.get("center_xy", [0.0, 0.0])),
                        "radius_m": round(float(kernel.get("radius_m", 0.0) or 0.0), 3),
                        "start_xy": _serialize_point(kernel.get("start_xy", [0.0, 0.0])),
                        "end_xy": _serialize_point(kernel.get("end_xy", [0.0, 0.0])),
                        "start_heading_deg": round(float(kernel.get("start_heading_deg", 0.0) or 0.0), 3),
                        "end_heading_deg": round(float(kernel.get("end_heading_deg", 0.0) or 0.0), 3),
                        "clockwise": kernel.get("clockwise", None),
                        "sampled_points_xy": _serialize_polyline(kernel.get("sampled_points_xy", [])),
                    }
                    for kernel in item.get("quadrant_corner_kernels", []) or ()
                ]
                junction_item["sidewalk_corner_polylines"] = [
                    {
                        "polyline_id": str(polyline.get("polyline_id", "") or ""),
                        "quadrant_id": str(polyline.get("quadrant_id", "") or ""),
                        "kernel_id": str(polyline.get("kernel_id", "") or ""),
                        "points_xy": _serialize_polyline(polyline.get("points_xy", [])),
                        "width_m": round(float(polyline.get("width_m", 0.0) or 0.0), 3),
                    }
                    for polyline in item.get("sidewalk_corner_polylines", []) or ()
                ]
                junction_item["nearroad_corner_polylines"] = [
                    {
                        "polyline_id": str(polyline.get("polyline_id", "") or ""),
                        "quadrant_id": str(polyline.get("quadrant_id", "") or ""),
                        "kernel_id": str(polyline.get("kernel_id", "") or ""),
                        "points_xy": _serialize_polyline(polyline.get("points_xy", [])),
                        "width_m": round(float(polyline.get("width_m", 0.0) or 0.0), 3),
                    }
                    for polyline in item.get("nearroad_corner_polylines", []) or ()
                ]
                junction_item["frontage_corner_polylines"] = [
                    {
                        "polyline_id": str(polyline.get("polyline_id", "") or ""),
                        "quadrant_id": str(polyline.get("quadrant_id", "") or ""),
                        "kernel_id": str(polyline.get("kernel_id", "") or ""),
                        "points_xy": _serialize_polyline(polyline.get("points_xy", [])),
                        "width_m": round(float(polyline.get("width_m", 0.0) or 0.0), 3),
                    }
                    for polyline in item.get("frontage_corner_polylines", []) or ()
                ]
                junction_item["sidewalk_corner_patches"] = [
                    _serialize_corner_patch(patch)
                    for patch in item.get("sidewalk_corner_patches", []) or ()
                ]
                junction_item["nearroad_corner_patches"] = [
                    _serialize_corner_patch(patch)
                    for patch in item.get("nearroad_corner_patches", []) or ()
                ]
                junction_item["frontage_corner_patches"] = [
                    _serialize_corner_patch(patch)
                    for patch in item.get("frontage_corner_patches", []) or ()
                ]
                junction_item["lane_surface_patches"] = [
                    {
                        "surface_id": str(patch.get("surface_id", "") or ""),
                        "surface_kind": str(patch.get("surface_kind", "") or "lane"),
                        "lane_id": str(patch.get("lane_id", "") or ""),
                        "arm_key": str(patch.get("arm_key", "") or ""),
                        "flow": str(patch.get("flow", "") or ""),
                        "lane_index": int(patch.get("lane_index", 0) or 0),
                        "provenance": str(patch.get("provenance", "") or ""),
                        "rings": _extract_rings(patch.get("geometry")),
                    }
                    for patch in item.get("lane_surface_patches", []) or ()
                ]
                junction_item["merged_surface_patches"] = [
                    {
                        "surface_id": str(patch.get("surface_id", "") or ""),
                        "surface_kind": str(patch.get("surface_kind", "") or "merged"),
                        "provenance": str(patch.get("provenance", "") or ""),
                        "merged_from_surface_ids": [str(value) for value in patch.get("merged_from_surface_ids", []) or ()],
                        "merged_from_lane_ids": [str(value) for value in patch.get("merged_from_lane_ids", []) or ()],
                        "rings": _extract_rings(patch.get("geometry")),
                    }
                    for patch in item.get("merged_surface_patches", []) or ()
                ]
            else:
                junction_item["sidewalk_corner_patches"] = [
                    _serialize_corner_patch(patch)
                    for patch in item.get("sidewalk_corner_patches", []) or ()
                ]
                junction_item["nearroad_corner_patches"] = [
                    _serialize_corner_patch(patch)
                    for patch in item.get("nearroad_corner_patches", []) or ()
                ]
                junction_item["frontage_corner_patches"] = [
                    _serialize_corner_patch(patch)
                    for patch in item.get("frontage_corner_patches", []) or ()
                ]
                junction_item["lane_surface_patches"] = [
                    {
                        "surface_id": str(patch.get("surface_id", "") or ""),
                        "surface_kind": str(patch.get("surface_kind", "") or "lane"),
                        "lane_id": str(patch.get("lane_id", "") or ""),
                        "arm_key": str(patch.get("arm_key", "") or ""),
                        "flow": str(patch.get("flow", "") or ""),
                        "lane_index": int(patch.get("lane_index", 0) or 0),
                        "provenance": str(patch.get("provenance", "") or ""),
                        "rings": _extract_rings(patch.get("geometry")),
                    }
                    for patch in item.get("lane_surface_patches", []) or ()
                ]
                junction_item["merged_surface_patches"] = [
                    {
                        "surface_id": str(patch.get("surface_id", "") or ""),
                        "surface_kind": str(patch.get("surface_kind", "") or "merged"),
                        "provenance": str(patch.get("provenance", "") or ""),
                        "merged_from_surface_ids": [str(value) for value in patch.get("merged_from_surface_ids", []) or ()],
                        "merged_from_lane_ids": [str(value) for value in patch.get("merged_from_lane_ids", []) or ()],
                        "rings": _extract_rings(patch.get("geometry")),
                    }
                    for patch in item.get("merged_surface_patches", []) or ()
                ]
            result["junction_geometries"].append(junction_item)
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


def _slot_category_rank(slot: object) -> int:
    category = str(getattr(slot, "category", "") or "")
    return int(CATEGORY_PLACEMENT_RANK.get(category, len(CATEGORY_PLACEMENT_RANK)))


def _slot_placement_sort_key(slot: object) -> Tuple[int, int, int, int, str, float, float, str]:
    anchor_type = str(getattr(slot, "anchor_poi_type", "") or "").strip()
    if anchor_type:
        bucket = 0
        anchor_rank = placement_priority_rank(anchor_type)
    else:
        bucket = 1
        anchor_rank = 999
    return (
        int(bucket),
        int(anchor_rank),
        _slot_category_rank(slot),
        0 if bool(getattr(slot, "required", False)) else 1,
        str(getattr(slot, "theme_id", "") or ""),
        -float(getattr(slot, "priority", 0.0) or 0.0),
        float(getattr(slot, "x_center_m", 0.0) or 0.0),
        str(getattr(slot, "slot_id", "") or ""),
    )


def _placement_status(anchor_distance_m: Optional[float], *, required: bool, placed: bool) -> str:
    if not placed:
        return "unplaced_required" if required else "unplaced_optional"
    if anchor_distance_m is not None and anchor_distance_m >= 0.0:
        if anchor_distance_m <= 0.75:
            return "anchored_exact"
        return "anchored_relaxed"
    return "placed"


def _slot_side_for_placement(
    placement: StreetPlacement,
    *,
    slot_side_by_id: Mapping[str, str],
) -> str:
    side = str(slot_side_by_id.get(str(placement.slot_id), "") or "")
    if side not in {"left", "right"}:
        side = "left" if float(placement.position_xyz[2]) >= 0.0 else "right"
    return side


def _street_furniture_balance_state(
    placements: Sequence[StreetPlacement],
    *,
    slot_side_by_id: Mapping[str, str],
) -> Dict[str, Any]:
    core_categories = {
        category
        for category, side_pref in SIDE_PREF.items()
        if str(side_pref) == "both"
    }
    street_furniture_side_counts = {"left": 0, "right": 0}
    street_furniture_core_side_counts = {"left": 0, "right": 0}
    street_furniture_core_categories_by_side: Dict[str, set[str]] = {
        "left": set(),
        "right": set(),
    }
    for placement in placements:
        if str(placement.placement_group) != "street_furniture":
            continue
        side = _slot_side_for_placement(placement, slot_side_by_id=slot_side_by_id)
        street_furniture_side_counts[side] = street_furniture_side_counts.get(side, 0) + 1
        if str(placement.category) in core_categories:
            street_furniture_core_side_counts[side] = street_furniture_core_side_counts.get(side, 0) + 1
            street_furniture_core_categories_by_side.setdefault(side, set()).add(str(placement.category))
    return {
        "street_furniture_side_counts": dict(street_furniture_side_counts),
        "street_furniture_core_side_counts": dict(street_furniture_core_side_counts),
        "street_furniture_core_categories_by_side": {
            side: sorted(categories)
            for side, categories in street_furniture_core_categories_by_side.items()
        },
        "street_furniture_core_category_count_by_side": {
            side: int(len(categories))
            for side, categories in street_furniture_core_categories_by_side.items()
        },
        "core_total_count": int(sum(street_furniture_core_side_counts.values())),
    }


def _append_placement_decision_event(
    events: List[Dict[str, Any]],
    *,
    event_type: str,
    slot_id: str = "",
    category: str = "",
    theme_id: str = "",
    side: str = "",
    band_name: str = "",
    reason_code: str = "",
    reason_detail: str = "",
    candidate_asset_id: str = "",
    anchor_poi_type: str = "",
    placement_energy: Optional[float] = None,
    feasibility_score: Optional[float] = None,
    violated_rules: Sequence[str] = (),
    blocked_reason: str = "",
    search_tier: str = "",
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "slot_id": str(slot_id),
        "category": str(category),
        "theme_id": str(theme_id),
        "side": str(side),
        "band_name": str(band_name),
        "event_type": str(event_type),
        "reason_code": str(reason_code),
        "reason_detail": str(reason_detail),
        "candidate_asset_id": str(candidate_asset_id),
        "anchor_poi_type": str(anchor_poi_type),
        "placement_energy": None if placement_energy is None else float(placement_energy),
        "feasibility_score": None if feasibility_score is None else float(feasibility_score),
        "violated_rules": [str(rule) for rule in violated_rules if str(rule)],
        "blocked_reason": str(blocked_reason),
        "search_tier": str(search_tier),
    }
    if extra:
        for key, value in extra.items():
            if isinstance(value, tuple):
                payload[str(key)] = list(value)
            elif isinstance(value, set):
                payload[str(key)] = sorted(str(item) for item in value)
            else:
                payload[str(key)] = value
    events.append(payload)


def _summarize_placement_decision_events(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    event_counts = Counter(str(item.get("event_type", "") or "") for item in events if str(item.get("event_type", "") or ""))
    reason_counts = Counter(
        str(item.get("reason_code", "") or item.get("blocked_reason", "") or "")
        for item in events
        if str(item.get("reason_code", "") or item.get("blocked_reason", "") or "")
    )
    return {
        "event_count": int(len(events)),
        "event_counts": dict(event_counts),
        "reason_counts": dict(reason_counts),
        "selected_count": int(event_counts.get("placement_selected", 0)),
        "skipped_count": int(event_counts.get("placement_skipped", 0)),
        "rejected_count": int(event_counts.get("candidate_rejected", 0)),
        "repair_attempt_count": int(event_counts.get("balance_repair_attempt", 0)),
        "repair_success_count": int(event_counts.get("balance_repair_selected", 0)),
        "repair_failure_count": int(event_counts.get("balance_repair_failed", 0)),
    }


def _write_placement_decision_log(log_path: Path, events: Sequence[Mapping[str, Any]]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(dict(event), ensure_ascii=True) + "\n")


def _point_in_zone(zone: object | None, point_xz: Tuple[float, float], *, tolerance_m: float = 0.05) -> bool:
    if zone is None or getattr(zone, "is_empty", False):
        return False
    try:
        from shapely.geometry import Point as ShapelyPoint
    except Exception:
        return True
    point = ShapelyPoint(float(point_xz[0]), float(point_xz[1]))
    return bool(zone.buffer(float(tolerance_m)).contains(point))


def _point_side_matches_slot(
    point_xz: Tuple[float, float],
    *,
    slot_side: str,
    placement_ctx: object | None,
    segment_node: object | None = None,
    band_name: str = "",
) -> Tuple[bool, bool]:
    if placement_ctx is None:
        return True, True
    side_name = str(slot_side or "").strip().lower()
    if side_name == "center":
        center_zone = _target_strip_zone(
            placement_ctx=placement_ctx,
            segment_node=segment_node,
            slot_side=side_name,
            band_name=band_name,
        )
        if center_zone is None or getattr(center_zone, "is_empty", False):
            return True, True
        in_center = _point_in_zone(center_zone, point_xz)
        return in_center, in_center
    overall_zone = getattr(placement_ctx, "sidewalk_zone", None)
    in_overall = _point_in_zone(overall_zone, point_xz)
    if side_name == "left":
        side_zone = getattr(placement_ctx, "left_sidewalk_zone", None)
    elif side_name == "right":
        side_zone = getattr(placement_ctx, "right_sidewalk_zone", None)
    else:
        return True, in_overall
    if side_zone is None or getattr(side_zone, "is_empty", False):
        return True, in_overall
    return _point_in_zone(side_zone, point_xz), in_overall


def _strip_zone_candidate_keys(slot_side: str, band_name: str) -> Tuple[str, ...]:
    normalized_side = str(slot_side or "").strip().lower()
    keys: List[str] = []
    for alias in band_name_aliases(band_name=band_name, side=normalized_side):
        if alias.startswith("left_") or alias.startswith("right_") or alias.startswith("center_"):
            if alias not in keys:
                keys.append(alias)
        elif normalized_side in {"left", "right", "center"}:
            detailed_key = detailed_strip_band_name(normalized_side, alias)
            if detailed_key not in keys:
                keys.append(detailed_key)
    return tuple(keys)


def _target_strip_zone(
    *,
    placement_ctx: object | None,
    segment_node: object | None,
    slot_side: str,
    band_name: str,
) -> object | None:
    if placement_ctx is None:
        return None
    candidate_keys = _strip_zone_candidate_keys(slot_side, band_name)
    segment_id = str(getattr(segment_node, "segment_id", "") or "")
    if segment_id:
        for key in candidate_keys:
            zone = (getattr(placement_ctx, "segment_strip_zones", {}) or {}).get(segment_id, {}).get(key)
            if zone is not None and not getattr(zone, "is_empty", False):
                return zone
    for key in candidate_keys:
        zone = (getattr(placement_ctx, "strip_zones", {}) or {}).get(key)
        if zone is not None and not getattr(zone, "is_empty", False):
            return zone
    return None


def _point_matches_slot_band(
    point_xz: Tuple[float, float],
    *,
    placement_ctx: object | None,
    segment_node: object | None,
    slot_side: str,
    band_name: str,
) -> bool:
    zone = _target_strip_zone(
        placement_ctx=placement_ctx,
        segment_node=segment_node,
        slot_side=slot_side,
        band_name=band_name,
    )
    if zone is None:
        return True
    return _point_in_zone(zone, point_xz)


def _segment_tangent_normal(segment_node: object | None) -> Optional[Tuple[Tuple[float, float], Tuple[float, float], float]]:
    if segment_node is None:
        return None
    start_xy = tuple(float(v) for v in getattr(segment_node, "start_xy", (0.0, 0.0)))
    end_xy = tuple(float(v) for v in getattr(segment_node, "end_xy", (0.0, 0.0)))
    dx = end_xy[0] - start_xy[0]
    dz = end_xy[1] - start_xy[1]
    length = math.hypot(dx, dz)
    if length <= 1e-6:
        return None
    tangent = (dx / length, dz / length)
    left_normal = (-tangent[1], tangent[0])
    return tangent, left_normal, float(length)


def _theme_nodes_for_segment(theme_segment: ThemeSegment, road_segment_graph: object | None) -> Tuple[object, ...]:
    nodes_by_id = _segment_node_lookup(road_segment_graph)
    nodes = [
        nodes_by_id[segment_id]
        for segment_id in theme_segment.segment_ids
        if segment_id in nodes_by_id
    ]
    return tuple(sorted(nodes, key=lambda node: float(getattr(node, "station_center_m", 0.0) or 0.0)))


def _point_within_theme_segment(
    point_xz: Tuple[float, float],
    *,
    theme_segment: ThemeSegment | None,
    road_segment_graph: object | None,
) -> bool:
    if theme_segment is None:
        return True
    if road_segment_graph is not None and getattr(road_segment_graph, "nodes", None):
        nodes = list(getattr(road_segment_graph, "nodes", ()) or ())
        if not nodes:
            return True
        nearest = min(
            nodes,
            key=lambda node: math.hypot(
                float(getattr(node, "center_xy", (0.0, 0.0))[0]) - float(point_xz[0]),
                float(getattr(node, "center_xy", (0.0, 0.0))[1]) - float(point_xz[1]),
            ),
        )
        return str(getattr(nearest, "segment_id", "")) in set(theme_segment.segment_ids)
    return bool(
        float(theme_segment.x_start_m) - 1e-6
        <= float(point_xz[0])
        <= float(theme_segment.x_end_m) + 1e-6
    )


def _theme_poi_points(
    *,
    theme_segment: ThemeSegment | None,
    theme_segments: Sequence[ThemeSegment],
    poi_ctx: object | None,
    road_segment_graph: object | None,
) -> Dict[str, Tuple[Tuple[float, float], ...]]:
    if poi_ctx is None:
        return {}
    points_by_type = nonempty_poi_points(getattr(poi_ctx, "poi_points_by_type_xz", {}) or {})
    if theme_segment is None:
        return {
            poi_type: tuple((float(point[0]), float(point[1])) for point in points)
            for poi_type, points in points_by_type.items()
        }
    filtered: Dict[str, List[Tuple[float, float]]] = {}
    for poi_type, points in points_by_type.items():
        for point in points:
            point_xz = (float(point[0]), float(point[1]))
            if assign_theme_id_for_point(point_xz, theme_segments, road_segment_graph) != theme_segment.theme_id:
                continue
            filtered.setdefault(str(poi_type), []).append(point_xz)
    return {
        poi_type: tuple(points)
        for poi_type, points in filtered.items()
        if points
    }


def _max_pair_cutoff(category: str, existing_categories: Iterable[str]) -> float:
    cutoffs = [8.0]
    for other_category in existing_categories:
        cutoffs.append(pair_cutoff_radius_m(category, str(other_category)))
    return float(max(cutoffs))


def _pair_scores_for_neighbors(
    *,
    category: str,
    point_xz: Tuple[float, float],
    neighbor_indices: Sequence[int],
    placements: Sequence[StreetPlacement],
) -> Tuple[float, float]:
    pair_attraction = 0.0
    pair_repulsion = 0.0
    for idx in neighbor_indices:
        placement = placements[int(idx)]
        attraction, repulsion = pair_interaction_scores(
            str(category),
            point_xz,
            str(placement.category),
            (float(placement.position_xyz[0]), float(placement.position_xyz[2])),
        )
        pair_attraction += float(attraction)
        pair_repulsion += float(repulsion)
    return float(pair_attraction), float(pair_repulsion)


def _band_deviation_penalty(
    *,
    point_xz: Tuple[float, float],
    slot: object,
    band_width_m: float,
) -> float:
    target_x = float(getattr(slot, "x_center_m", 0.0) or 0.0)
    target_z = float(getattr(slot, "z_center_m", 0.0) or 0.0)
    return float(
        math.hypot(float(point_xz[0]) - target_x, float(point_xz[1]) - target_z)
        / max(float(band_width_m), 1.0)
    )


def _search_tier_exact_candidates(
    *,
    category: str,
    anchor_target_xz: Tuple[float, float],
    placement_ctx: object,
) -> Tuple[Dict[str, object], ...]:
    from .placement_zones import compute_facing_angle

    yaw = _yaw_for_asset_category(
        category,
        compute_facing_angle(anchor_target_xz, placement_ctx.carriageway),  # type: ignore[attr-defined]
    )
    return (
        {
            "tier": "tier_1_exact",
            "point_xz": (float(anchor_target_xz[0]), float(anchor_target_xz[1])),
            "yaw_deg": float(yaw),
            "anchor_distance_m": 0.0,
        },
    )


def _search_tier_ring_candidates(
    *,
    category: str,
    anchor_target_xz: Tuple[float, float],
    placement_ctx: object,
) -> Tuple[Dict[str, object], ...]:
    from .placement_zones import compute_facing_angle

    candidates: List[Dict[str, object]] = []
    anchor_x, anchor_z = float(anchor_target_xz[0]), float(anchor_target_xz[1])
    for radius_m in (0.6, 1.2, 2.0, 3.0):
        for step_idx in range(8):
            angle = (2.0 * math.pi * float(step_idx)) / 8.0
            point = (
                anchor_x + math.cos(angle) * float(radius_m),
                anchor_z + math.sin(angle) * float(radius_m),
            )
            yaw = _yaw_for_asset_category(
                category,
                compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
            )
            candidates.append(
                {
                    "tier": "tier_2_ring",
                    "point_xz": point,
                    "yaw_deg": float(yaw),
                    "anchor_distance_m": float(radius_m),
                }
            )
    return tuple(candidates)


def _search_tier_segment_candidates(
    *,
    category: str,
    anchor_target_xz: Tuple[float, float],
    segment_node: object | None,
    placement_ctx: object,
    config: StreetComposeConfig,
) -> Tuple[Dict[str, object], ...]:
    from .placement_zones import compute_facing_angle

    tangent_payload = _segment_tangent_normal(segment_node)
    if tangent_payload is None:
        return tuple()
    tangent, _left_normal, segment_length_m = tangent_payload
    search_extent = max(float(segment_length_m), 6.0, float(getattr(config, "segment_length_m", 6.0)))
    candidates: List[Dict[str, object]] = []
    for offset_m in np.arange(-search_extent, search_extent + 1e-6, 1.0):
        if abs(float(offset_m)) < 1e-6:
            continue
        point = (
            float(anchor_target_xz[0]) + tangent[0] * float(offset_m),
            float(anchor_target_xz[1]) + tangent[1] * float(offset_m),
        )
        anchor_distance_m = float(math.hypot(point[0] - anchor_target_xz[0], point[1] - anchor_target_xz[1]))
        if anchor_distance_m > 8.0 + 1e-6:
            continue
        yaw = _yaw_for_asset_category(
            category,
            compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
        )
        candidates.append(
            {
                "tier": "tier_3_segment",
                "point_xz": point,
                "yaw_deg": float(yaw),
                "anchor_distance_m": anchor_distance_m,
            }
        )
    return tuple(candidates)


def _search_tier_theme_side_candidates(
    *,
    category: str,
    anchor_target_xz: Tuple[float, float],
    placement_ctx: object,
    theme_segment: ThemeSegment | None,
    road_segment_graph: object | None,
    slot_side: str,
    band_width_m: float,
) -> Tuple[Dict[str, object], ...]:
    from .placement_zones import compute_facing_angle

    if theme_segment is None:
        return tuple()
    candidates: List[Dict[str, object]] = []
    theme_nodes = _theme_nodes_for_segment(theme_segment, road_segment_graph)
    carriageway_half = float(getattr(placement_ctx, "carriageway_width_m", 8.0) or 8.0) / 2.0
    lateral = carriageway_half + max(float(band_width_m) * 0.45, 0.8)
    side_name = str(slot_side or "").strip().lower()
    sign = 1.0 if side_name == "left" else -1.0
    for node in theme_nodes:
        tangent_payload = _segment_tangent_normal(node)
        if tangent_payload is None:
            continue
        tangent, left_normal, _segment_length_m = tangent_payload
        normal = left_normal if sign > 0 else (-left_normal[0], -left_normal[1])
        center_x, center_z = tuple(float(v) for v in getattr(node, "center_xy", (0.0, 0.0)))
        for along_offset_m in (-2.0, 0.0, 2.0):
            point = (
                center_x + tangent[0] * float(along_offset_m) + normal[0] * lateral,
                center_z + tangent[1] * float(along_offset_m) + normal[1] * lateral,
            )
            anchor_distance_m = float(math.hypot(point[0] - anchor_target_xz[0], point[1] - anchor_target_xz[1]))
            if anchor_distance_m > 8.0 + 1e-6:
                continue
            yaw = _yaw_for_asset_category(
                category,
                compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
            )
            candidates.append(
                {
                    "tier": "tier_4_theme_side",
                    "point_xz": point,
                    "yaw_deg": float(yaw),
                    "anchor_distance_m": anchor_distance_m,
                }
            )
    return tuple(candidates)


def _filter_candidates_to_target_strip(
    candidates: Sequence[Dict[str, object]],
    *,
    placement_ctx: object | None,
    segment_node: object | None,
    slot_side: str,
    band_name: str,
) -> Tuple[Dict[str, object], ...]:
    filtered = [
        dict(candidate)
        for candidate in candidates
        if _point_matches_slot_band(
            (
                float(candidate.get("point_xz", (0.0, 0.0))[0]),
                float(candidate.get("point_xz", (0.0, 0.0))[1]),
            ),
            placement_ctx=placement_ctx,
            segment_node=segment_node,
            slot_side=slot_side,
            band_name=band_name,
        )
    ]
    return tuple(filtered)


def _iter_slot_candidate_groups(
    *,
    slot: object,
    category: str,
    config: StreetComposeConfig,
    placement_ctx: object | None,
    segment_node: object | None,
    theme_segment: ThemeSegment | None,
    road_segment_graph: object | None,
    band_width_m: float,
    rng: random.Random,
) -> Tuple[Tuple[Dict[str, object], ...], ...]:
    slot_side = str(getattr(slot, "side", "") or "")
    slot_band_name = str(getattr(slot, "band_name", "") or "")
    anchor_target_xz = getattr(slot, "anchor_position_xz", None)
    if anchor_target_xz is not None and placement_ctx is not None and _is_corridor_layout_mode(config.layout_mode):
        target_point = (float(anchor_target_xz[0]), float(anchor_target_xz[1]))
        return (
            _filter_candidates_to_target_strip(
                _search_tier_exact_candidates(category=category, anchor_target_xz=target_point, placement_ctx=placement_ctx),
                placement_ctx=placement_ctx,
                segment_node=segment_node,
                slot_side=slot_side,
                band_name=slot_band_name,
            ),
            _filter_candidates_to_target_strip(
                _search_tier_ring_candidates(category=category, anchor_target_xz=target_point, placement_ctx=placement_ctx),
                placement_ctx=placement_ctx,
                segment_node=segment_node,
                slot_side=slot_side,
                band_name=slot_band_name,
            ),
            _filter_candidates_to_target_strip(
                _search_tier_segment_candidates(
                    category=category,
                    anchor_target_xz=target_point,
                    segment_node=segment_node,
                    placement_ctx=placement_ctx,
                    config=config,
                ),
                placement_ctx=placement_ctx,
                segment_node=segment_node,
                slot_side=slot_side,
                band_name=slot_band_name,
            ),
            _filter_candidates_to_target_strip(
                _search_tier_theme_side_candidates(
                    category=category,
                    anchor_target_xz=target_point,
                    placement_ctx=placement_ctx,
                    theme_segment=theme_segment,
                    road_segment_graph=road_segment_graph,
                    slot_side=slot_side,
                    band_width_m=float(band_width_m),
                ),
                placement_ctx=placement_ctx,
                segment_node=segment_node,
                slot_side=slot_side,
                band_name=slot_band_name,
            ),
        )
    candidates: List[Dict[str, object]] = []
    for _trial_idx in range(int(config.max_trials_per_slot)):
        if _is_corridor_layout_mode(config.layout_mode) and placement_ctx is not None:
            pose = _sample_pose_osm_for_segment(
                category,
                placement_ctx,
                rng,
                segment_node=segment_node,
                slot_side=slot_side,
                slot_band_name=slot_band_name,
                band_width_m=float(band_width_m),
                anchor_position_xz=None,
            )
        else:
            pose = _sample_pose_for_slot(
                slot_x_center=float(getattr(slot, "x_center_m", 0.0) or 0.0),
                slot_z_center=float(getattr(slot, "z_center_m", 0.0) or 0.0),
                slot_side=str(getattr(slot, "side", "") or ""),
                slot_spacing_m=float(getattr(slot, "spacing_m", 1.0) or 1.0),
                band_width_m=float(band_width_m),
                length_m=float(config.length_m),
                rng=rng,
            )
        if pose is None:
            continue
        x, z, yaw_deg = pose
        candidates.append(
            {
                "tier": "tier_optional_sampling",
                "point_xz": (float(x), float(z)),
                "yaw_deg": float(yaw_deg),
                "anchor_distance_m": None,
            }
        )
    return (
        _filter_candidates_to_target_strip(
            tuple(candidates),
            placement_ctx=placement_ctx,
            segment_node=segment_node,
            slot_side=slot_side,
            band_name=slot_band_name,
        ),
    )


def _evaluate_slot_candidate(
    *,
    candidate: Mapping[str, object],
    slot: object,
    category: str,
    band_width_m: float,
    entry: _MeshCacheEntry | _MeshMetadata,
    scale_info: Mapping[str, object],
    placements: Sequence[StreetPlacement],
    spatial_hash: UniformSpatialHash,
    existing_bboxes: Sequence[Tuple[float, float, float, float]],
    placement_ctx: object | None,
    theme_segment: ThemeSegment | None,
    road_segment_graph: object | None,
    theme_poi_points: Mapping[str, Sequence[Tuple[float, float]]],
    poi_ctx: object | None,
    rule_set: object | None,
    config: StreetComposeConfig,
    entrance_registry: PlacedAssetRegistry,
    carriageway_boundary: Optional[CarriagewayBoundary],
    entrance_points_xz: Sequence[Tuple[float, float]],
    segment_node: object | None = None,
    decomposition_cache: object | None = None,  # Optional DecompositionCache
) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
    point_xz = (
        float(candidate["point_xz"][0]),
        float(candidate["point_xz"][1]),
    )
    side_matches, in_overall = _point_side_matches_slot(
        point_xz,
        slot_side=str(getattr(slot, "side", "") or ""),
        placement_ctx=placement_ctx,
        segment_node=segment_node,
        band_name=str(getattr(slot, "band_name", "") or ""),
    )
    if not in_overall:
        return None, "out_of_sidewalk"
    if not side_matches:
        return None, "side_mismatch"
    if not _point_matches_slot_band(
        point_xz,
        placement_ctx=placement_ctx,
        segment_node=segment_node,
        slot_side=str(getattr(slot, "side", "") or ""),
        band_name=str(getattr(slot, "band_name", "") or ""),
    ):
        return None, "out_of_target_strip"
    if not _point_within_theme_segment(point_xz, theme_segment=theme_segment, road_segment_graph=road_segment_graph):
        return None, "out_of_theme_range"
    if bool(scale_info.get("scale_gate_blocking", False)):
        return None, "scale_gate_failed"

    bbox = _compute_bbox(
        x=float(point_xz[0]),
        z=float(point_xz[1]),
        yaw_deg=float(candidate["yaw_deg"]),
        half_x=entry.half_x,
        half_z=entry.half_z,
        scale=float(scale_info.get("applied_scale", 1.0) or 1.0),
        clearance=0.2,
    )
    _needs_bbox_checks = category in _GROUND_LEVEL_CATEGORIES
    if _needs_bbox_checks and _bbox_intrudes_carriageway(
        bbox,
        placement_ctx=placement_ctx,
        config=config,
    ):
        return None, "intrudes_carriageway"
    neighbor_bbox_indices = spatial_hash.query_bbox(bbox)
    if _needs_bbox_checks and any(_bbox_intersects(bbox, existing_bboxes[int(idx)]) for idx in neighbor_bbox_indices):
        return None, "overlap_blocked"

    # Multi-box collision detection (if decomposition cache is provided)
    if decomposition_cache is not None and _needs_bbox_checks:
        decomp = decomposition_cache.get(entry.asset_id) if hasattr(entry, 'asset_id') else None
        if decomp is not None and len(decomp.boxes) > 1:
            # Asset has multiple sub-boxes - use precise collision detection
            # Build world-space sub-boxes for this candidate
            candidate_sub_boxes = []
            for sub_box in decomp.boxes:
                # Transform local coordinates to world coordinates
                half_w = sub_box.width_m / 2.0
                half_d = sub_box.depth_m / 2.0
                world_x_min = float(point_xz[0]) + sub_box.local_x - half_w
                world_x_max = float(point_xz[0]) + sub_box.local_x + half_w
                world_z_min = float(point_xz[1]) + sub_box.local_z - half_d
                world_z_max = float(point_xz[1]) + sub_box.local_z + half_d
                candidate_sub_boxes.append((world_x_min, world_x_max, world_z_min, world_z_max))

            # Check collision for each sub-box
            has_collision = False
            for sub_box in candidate_sub_boxes:
                for idx in neighbor_bbox_indices:
                    other_bbox = existing_bboxes[int(idx)]
                    if _bbox_intersects(sub_box, other_bbox):
                        has_collision = True
                        break
                if has_collision:
                    break

            if has_collision:
                return None, "overlap_blocked"

    poi_repulsion = 0.0
    constraint_penalty = 0.0
    feasibility_score = 1.0
    violated_rules: Tuple[str, ...] = ()
    if rule_set is not None and poi_ctx is not None:
        from .poi_rules import evaluate_repulsion_field, score_placement as _score_placement

        poi_repulsion = float(evaluate_repulsion_field(point_xz, category, rule_set, poi_ctx, aggregate="nearest"))
        if config.constraint_mode == "soft":
            constraint_result = _score_placement(point_xz, category, rule_set, poi_ctx)
            if float(constraint_result.penalty) > float(config.constraint_veto_threshold):
                return None, "constraint_vetoed"
            constraint_penalty = float(constraint_result.penalty)
            feasibility_score = float(constraint_result.feasibility_score)
            violated_rules = tuple(constraint_result.violated_rules)

    if entrance_points_xz and carriageway_boundary is not None:
        entrance_penalty, entrance_bonus, entrance_violated = score_entrance_impact(
            candidate_xz=point_xz,
            candidate_category=category,
            candidate_bbox_xz=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
            entrance_points_xz=tuple((float(point[0]), float(point[1])) for point in entrance_points_xz),
            registry=entrance_registry,
            carriageway_boundary=carriageway_boundary,
        )
        poi_repulsion += max(0.0, float(entrance_penalty) - float(entrance_bonus))
        if config.constraint_mode == "soft":
            constraint_penalty += max(0.0, float(entrance_penalty) - float(entrance_bonus))
            feasibility_score *= math.exp(-max(0.0, float(entrance_penalty)))
            violated_rules = tuple(list(violated_rules) + list(entrance_violated))

    neighbor_pair_radius = _max_pair_cutoff(category, (placement.category for placement in placements))
    neighbor_indices = spatial_hash.query_radius(point_xz, neighbor_pair_radius)
    pair_attraction, pair_repulsion = _pair_scores_for_neighbors(
        category=category,
        point_xz=point_xz,
        neighbor_indices=neighbor_indices,
        placements=placements,
    )
    poi_cutoff_m = max(7.5, float(band_width_m) + 6.0)
    poi_attraction = float(
        poi_attraction_score(
            category,
            point_xz,
            theme_poi_points,
            cutoff_m=poi_cutoff_m,
        )
    )
    energy = compose_candidate_energy(
        anchor_distance_m=(
            float(candidate["anchor_distance_m"])
            if candidate.get("anchor_distance_m") is not None
            else None
        ),
        poi_attraction=poi_attraction,
        poi_repulsion=poi_repulsion,
        pair_attraction=pair_attraction,
        pair_repulsion=pair_repulsion,
        band_deviation_penalty=_band_deviation_penalty(
            point_xz=point_xz,
            slot=slot,
            band_width_m=float(band_width_m),
        ),
    )
    return (
        {
            "x": float(point_xz[0]),
            "z": float(point_xz[1]),
            "yaw_deg": float(candidate["yaw_deg"]),
            "bbox": bbox,
            "scale": float(scale_info.get("applied_scale", 1.0) or 1.0),
            "native_size_m": dict(scale_info.get("native_size_m", {}) or {}),
            "raw_size_m": dict(scale_info.get("raw_size_m", {}) or {}),
            "metric_size_m": dict(scale_info.get("metric_size_m", {}) or {}),
            "final_size_m": dict(scale_info.get("final_size_m", {}) or {}),
            "canonical_target": dict(scale_info.get("canonical_target", {}) or {}),
            "asset_scale_mode": str(scale_info.get("asset_scale_mode", "")),
            "scale_fallback_used": bool(scale_info.get("scale_fallback_used", False)),
            "source_scale": float(scale_info.get("source_scale", 1.0) or 1.0),
            "source_scale_source": str(scale_info.get("source_scale_source", "") or ""),
            "source_scale_confidence": str(scale_info.get("source_scale_confidence", "") or ""),
            "source_scale_rejected_reason": str(scale_info.get("source_scale_rejected_reason", "") or ""),
            "scale_gate_failed": bool(scale_info.get("scale_gate_failed", False)),
            "scale_gate_reason": str(scale_info.get("scale_gate_reason", "") or ""),
            "constraint_penalty": float(constraint_penalty),
            "feasibility_score": float(feasibility_score),
            "violated_rules": tuple(violated_rules),
            "placement_energy": float(energy.total_energy),
            "anchor_distance_m": (
                float(candidate["anchor_distance_m"])
                if candidate.get("anchor_distance_m") is not None
                else None
            ),
            "candidate_tier": str(candidate["tier"]),
        },
        None,
    )

def _rank_building_candidates_for_target(
    *,
    query: str,
    theme_name: str,
    frontage_width_m: float,
    depth_m: float,
    road_type: str,
    height_class: str,
    embedder: ClipTextEmbedder,
    index_store: FaissIndexStore,
    asset_by_id: Dict[str, Dict[str, object]],
    search_topk: int,
) -> Tuple[List[Tuple[Dict[str, object], float]], Dict[str, object]]:
    query_text = building_query(
        query,
        theme_name=theme_name,
        frontage_width_m=float(frontage_width_m),
        depth_m=float(depth_m),
        road_type=road_type,
        height_class=height_class,
    )
    query_embedding = embedder.encode_texts([query_text])
    hits = index_store.search(query_embedding, topk=max(50, int(search_topk), 1))[0]
    reranked = rerank_building_candidates(
        hits=hits,
        asset_by_id=asset_by_id,
        theme_name=theme_name,
        frontage_width_m=float(frontage_width_m),
        depth_m=float(depth_m),
        height_class=height_class,
        limit=max(int(search_topk), 1),
    )
    reranked = [
        (row, score)
        for row, score in reranked
        if str(row.get("category", "") or "").strip().lower() == "building"
        and str(row.get("asset_role", "") or "").strip().lower() == "building"
    ]
    payload = {
        "query": query_text,
        "hit_count": len(hits),
        "candidate_count": len(reranked),
        "candidates": [
            {
                "asset_id": row["asset_id"],
                "category": row["category"],
                "score": float(score),
            }
            for row, score in reranked
        ],
    }
    return reranked, payload


def _pick_building_candidate(
    *,
    query: str,
    theme_name: str,
    frontage_width_m: float,
    depth_m: float,
    road_type: str,
    height_class: str,
    embedder: ClipTextEmbedder,
    index_store: FaissIndexStore,
    asset_by_id: Dict[str, Dict[str, object]],
    search_topk: int,
    rng: random.Random,
) -> Tuple[Optional[Dict[str, object]], float, str, Dict[str, object]]:
    reranked, payload = _rank_building_candidates_for_target(
        query=query,
        theme_name=theme_name,
        frontage_width_m=frontage_width_m,
        depth_m=depth_m,
        road_type=road_type,
        height_class=height_class,
        embedder=embedder,
        index_store=index_store,
        asset_by_id=asset_by_id,
        search_topk=search_topk,
    )
    if not reranked:
        return None, 0.0, "procedural_fallback", payload
    weights = _softmax_weights([float(score) for _row, score in reranked], SOFTMAX_TEMPERATURE)
    pick_idx = int(rng.choices(range(len(reranked)), weights=weights, k=1)[0])
    row, score = reranked[pick_idx]
    payload["chosen_index"] = pick_idx
    return row, float(score), "building_asset", payload


def _resolve_real_building_scale(
    *,
    entry: _MeshCacheEntry | _MeshMetadata,
    frontage_width_m: float,
    depth_m: float,
    target_height_m: float,
) -> Dict[str, object]:
    native_size = _native_size_for_entry(entry)
    native_width = float(native_size.get("width_m", 0.0) or 0.0)
    native_depth = float(native_size.get("depth_m", 0.0) or 0.0)
    native_height = float(native_size.get("height_m", 0.0) or 0.0)
    if native_width <= 1e-6 or native_depth <= 1e-6 or native_height <= 1e-6:
        return {
            "accepted": False,
            "reason": "invalid_native_building_bbox",
            "native_size_m": native_size,
            "final_size_m": dict(native_size),
            "scale": 1.0,
        }

    min_scale = 0.75
    max_scale = 1.35
    footprint_allowance = 1.10
    max_footprint_scale = min(
        float(frontage_width_m) * footprint_allowance / native_width,
        float(depth_m) * footprint_allowance / native_depth,
    )
    preferred_scale = 1.0
    if float(target_height_m) > 0.0:
        preferred_scale = float(target_height_m) / native_height
        preferred_scale = max(min_scale, min(max_scale, preferred_scale))
    scale = min(float(preferred_scale), float(max_footprint_scale))
    if scale < min_scale:
        return {
            "accepted": False,
            "reason": "building_asset_rejected_size_mismatch",
            "native_size_m": native_size,
            "final_size_m": {
                "width_m": native_width * max(scale, 0.0),
                "depth_m": native_depth * max(scale, 0.0),
                "height_m": native_height * max(scale, 0.0),
            },
            "scale": float(scale),
            "required_scale_to_fit": float(max_footprint_scale),
        }
    scale = min(max_scale, max(min_scale, scale))
    final_size = {
        "width_m": native_width * scale,
        "depth_m": native_depth * scale,
        "height_m": native_height * scale,
        "canopy_width_m": max(native_width, native_depth) * scale,
    }
    if final_size["width_m"] > float(frontage_width_m) * footprint_allowance or final_size["depth_m"] > float(depth_m) * footprint_allowance:
        return {
            "accepted": False,
            "reason": "building_asset_rejected_size_mismatch",
            "native_size_m": native_size,
            "final_size_m": final_size,
            "scale": float(scale),
            "required_scale_to_fit": float(max_footprint_scale),
        }
    return {
        "accepted": True,
        "reason": "",
        "native_size_m": native_size,
        "final_size_m": final_size,
        "scale": float(scale),
        "required_scale_to_fit": float(max_footprint_scale),
    }


def _dominant_building_road_type(
    road_segment_graph: object | None,
    resolved_program: object,
) -> str:
    highway_counts: Dict[str, int] = {}
    for node in getattr(road_segment_graph, "nodes", ()) or ():
        highway_type = str(getattr(node, "highway_type", "") or "").strip().lower()
        if highway_type:
            highway_counts[highway_type] = highway_counts.get(highway_type, 0) + 1
    if highway_counts:
        return max(sorted(highway_counts), key=lambda key: highway_counts[key])
    return str(getattr(resolved_program, "road_type", "") or "").strip().lower()


def _building_size_class(frontage_width_m: float, depth_m: float) -> str:
    major = max(float(frontage_width_m), float(depth_m))
    if major >= 24.0:
        return "large"
    if major >= 14.0:
        return "medium"
    return "small"


def _footprint_target_records(footprints: Sequence[BuildingFootprint]) -> List[Dict[str, object]]:
    return [
        {
            "target_id": str(footprint.footprint_id),
            "target_kind": "footprint",
            "source": str(footprint.source),
            "polygon_xz": tuple((float(x), float(z)) for x, z in footprint.polygon_xz),
            "center_xz": (float(footprint.centroid_xz[0]), float(footprint.centroid_xz[1])),
            "placement_xz": (float(footprint.placement_xz[0]), float(footprint.placement_xz[1])),
            "street_edge_xz": (float(footprint.street_edge_xz[0]), float(footprint.street_edge_xz[1])),
            "frontage_width_m": float(footprint.frontage_width_m),
            "depth_m": float(footprint.building_depth_m or footprint.depth_m),
            "parcel_depth_m": float(footprint.depth_m),
            "yaw_deg": float(footprint.yaw_deg),
            "theme_id": str(footprint.theme_id),
            "land_use_type": str(footprint.land_use_type),
            "side": str(footprint.side),
            "height_class": str(footprint.height_class),
            "target_height_m": float(footprint.target_height_m),
            "anchor_geom_id": str(footprint.anchor_geom_id),
            "size_class": str(footprint.size_class),
            "front_setback_m": float(footprint.front_setback_m),
            "placement_strategy": str(footprint.placement_strategy),
        }
        for footprint in footprints
    ]


def _lot_target_records(lots: Sequence[GeneratedLot]) -> List[Dict[str, object]]:
    return [
        {
            "target_id": str(lot.lot_id),
            "target_kind": "lot",
            "source": str(lot.source),
            "polygon_xz": tuple((float(x), float(z)) for x, z in lot.polygon_xz),
            "center_xz": (float(lot.center_xz[0]), float(lot.center_xz[1])),
            "placement_xz": (float(lot.placement_xz[0]), float(lot.placement_xz[1])),
            "street_edge_xz": (float(lot.street_edge_xz[0]), float(lot.street_edge_xz[1])),
            "frontage_width_m": float(lot.frontage_width_m),
            "depth_m": float(lot.building_depth_m or lot.depth_m),
            "parcel_depth_m": float(lot.depth_m),
            "yaw_deg": float(lot.yaw_deg),
            "theme_id": str(lot.theme_id),
            "height_class": str(lot.height_class),
            "target_height_m": float(lot.target_height_m),
            "anchor_geom_id": str(lot.lot_id),
            "size_class": str(_building_size_class(lot.frontage_width_m, lot.building_depth_m or lot.depth_m)),
            "land_use_type": str(lot.land_use_type),
            "side": str(lot.side),
            "front_setback_m": float(lot.front_setback_m),
            "placement_strategy": str(lot.placement_strategy),
        }
        for lot in lots
    ]


def _evenly_sample_lots(lots: Sequence[GeneratedLot], count: int) -> List[GeneratedLot]:
    if count <= 0:
        return []
    ordered = sorted(
        lots,
        key=lambda lot: (
            float(lot.street_edge_xz[0]),
            float(lot.center_xz[0]),
            str(lot.lot_id),
        ),
    )
    if int(count) >= len(ordered):
        return list(ordered)
    if int(count) == 1:
        return [ordered[len(ordered) // 2]]
    last_index = len(ordered) - 1
    selected_indices = {
        int(round(float(idx) * float(last_index) / float(count - 1)))
        for idx in range(int(count))
    }
    selected = [ordered[idx] for idx in sorted(selected_indices)]
    cursor = 0
    while len(selected) < int(count) and cursor < len(ordered):
        candidate = ordered[cursor]
        if candidate not in selected:
            selected.append(candidate)
        cursor += 1
    return sorted(selected[: int(count)], key=lambda lot: str(lot.lot_id))


def _select_building_lots_for_density(
    lots: Sequence[GeneratedLot],
    *,
    density: float,
    max_per_100m: float,
    buildable_frontage_by_side: Mapping[str, object],
) -> Tuple[Tuple[GeneratedLot, ...], Dict[str, object]]:
    lot_list = list(lots)
    density_value = max(0.0, min(1.0, float(density)))
    max_per_100m_value = max(0.1, float(max_per_100m))
    if not lot_list or density_value <= 0.0:
        return tuple(), {
            "enabled": True,
            "density": float(density_value),
            "max_per_100m": float(max_per_100m_value),
            "input_lot_count": int(len(lot_list)),
            "selected_lot_count": 0,
            "removed_lot_count": int(len(lot_list)),
            "selected_by_side": {},
            "target_by_side": {},
        }

    selected: List[GeneratedLot] = []
    target_by_side: Dict[str, int] = {}
    selected_by_side: Dict[str, int] = {}
    for side in ("left", "right", ""):
        side_lots = [
            lot
            for lot in lot_list
            if (str(lot.side) in {"left", "right"} and str(lot.side) == side)
            or (side == "" and str(lot.side) not in {"left", "right"})
        ]
        if not side_lots:
            continue
        side_frontage_m = float(buildable_frontage_by_side.get(side, 0.0) or 0.0)
        if side_frontage_m <= 0.0:
            side_frontage_m = sum(float(max(lot.frontage_width_m, 0.0)) for lot in side_lots)
        density_target = int(round(float(len(side_lots)) * density_value))
        length_cap = int(math.ceil((side_frontage_m / 100.0) * max_per_100m_value)) if side_frontage_m > 0.0 else len(side_lots)
        target_count = max(1, min(len(side_lots), density_target, max(length_cap, 1)))
        if density_value >= 0.999:
            target_count = min(len(side_lots), max(length_cap, len(side_lots)))
        side_selected = _evenly_sample_lots(side_lots, target_count)
        selected.extend(side_selected)
        side_key = side or "unknown"
        target_by_side[side_key] = int(target_count)
        selected_by_side[side_key] = int(len(side_selected))

    selected_ids = {str(lot.lot_id) for lot in selected}
    return tuple(lot for lot in lot_list if str(lot.lot_id) in selected_ids), {
        "enabled": True,
        "density": float(density_value),
        "max_per_100m": float(max_per_100m_value),
        "input_lot_count": int(len(lot_list)),
        "selected_lot_count": int(len(selected_ids)),
        "removed_lot_count": int(max(len(lot_list) - len(selected_ids), 0)),
        "selected_by_side": selected_by_side,
        "target_by_side": target_by_side,
    }


def _summarize_building_region_direct_footprints(
    footprints: Sequence[BuildingFootprint],
) -> Dict[str, object]:
    land_use_counts = Counter(
        str(footprint.land_use_type or "")
        for footprint in footprints
        if str(footprint.land_use_type or "")
    )
    source_counts = Counter(
        str(footprint.source or "")
        for footprint in footprints
        if str(footprint.source or "")
    )
    return {
        "mode": "building_region_direct",
        "cell_counts": {},
        "buildable_cell_counts": {},
        "lane_role_counts": {},
        "buildable_cell_count": 0,
        "non_buildable_cell_count": 0,
        "building_region_count": int(len(footprints)),
        "footprint_count": int(len(footprints)),
        "footprint_land_use_counts": {
            key: int(value)
            for key, value in sorted(land_use_counts.items())
        },
        "footprint_source_counts": {
            key: int(value)
            for key, value in sorted(source_counts.items())
        },
    }


def _place_building_targets(
    *,
    targets: Sequence[Mapping[str, object]],
    config: StreetComposeConfig,
    theme_segments: Sequence[ThemeSegment],
    resolved_program: object,
    placement_ctx: object | None,
    embedder: ClipTextEmbedder,
    index_store: FaissIndexStore,
    asset_by_id: Dict[str, Dict[str, object]],
    mesh_cache: _LazyMeshCache,
    rng: random.Random,
    start_instance_index: int,
    road_type: str,
) -> Tuple[Tuple[StreetPlacement, ...], Tuple[BuildingPlacementPlan, ...], Tuple[Dict[str, object], ...], Dict[str, object], int]:
    theme_by_id = {segment.theme_id: segment for segment in theme_segments}
    placements: List[StreetPlacement] = []
    plans: List[BuildingPlacementPlan] = []
    retrieval_predictions: List[Dict[str, object]] = []
    fallback_count = 0
    asset_count = 0
    instance_index = int(start_instance_index)
    source_counts: Dict[str, int] = {}
    placement_strategy_counts: Dict[str, int] = {}
    front_setbacks: List[float] = []
    door_count_by_side: Dict[str, int] = {}
    door_missing_reason_counts: Dict[str, int] = {}
    door_required_count = 0
    door_skipped_existing_asset_count = 0
    building_asset_rejected_size_mismatch_count = 0
    lane_intrusion_adjusted_count = 0
    lane_intrusion_rejected_count = 0
    mesh_origin_centered_count = 0
    total_lane_guard_push_m = 0.0
    building_forbidden_geom = building_forbidden_geometry(placement_ctx)

    for target_idx, target in enumerate(targets):
        theme_id = str(target.get("theme_id", "") or "")
        theme_segment = theme_by_id.get(theme_id, theme_segments[0] if theme_segments else None)
        force_analytical_procedural_building = (
            str(getattr(config, "style_preset", "") or "").strip().lower() == "analytical_diorama_v1"
        )
        theme_name = (
            str(target.get("land_use_type", "") or "")
            or (theme_segment.theme_name if theme_segment is not None else "commercial")
        )
        if force_analytical_procedural_building:
            theme_name = "analytical"
        frontage_width_m = float(target.get("frontage_width_m", 12.0) or 12.0)
        depth_m = float(target.get("depth_m", 10.0) or 10.0)
        _target_height_m = float(target.get("target_height_m", 0.0) or 0.0)
        ranked_candidates, retrieval_payload = _rank_building_candidates_for_target(
            query=config.query,
            theme_name=theme_name,
            frontage_width_m=frontage_width_m,
            depth_m=depth_m,
            road_type=str(road_type),
            height_class=str(target.get("height_class", "") or ""),
            embedder=embedder,
            index_store=index_store,
            asset_by_id=asset_by_id,
            search_topk=int(getattr(config, "building_search_topk", 5)),
        )
        retrieval_payload.update(
            {
                f"{str(target.get('target_kind', 'footprint'))}_id": str(target.get("target_id", "") or ""),
                "theme_id": theme_id,
                "source": str(target.get("source", "") or ""),
                "height_class": str(target.get("height_class", "") or ""),
                "target_height_m": float(target.get("target_height_m", 0.0) or 0.0),
            }
        )
        if force_analytical_procedural_building:
            ranked_candidates = ()
            retrieval_payload["forced_procedural_fallback"] = "analytical_diorama_v1"

        row: Optional[Dict[str, object]] = None
        score = 0.0
        source = "procedural_fallback"
        scale_decision: Dict[str, object] = {}
        rejected_candidates: List[Dict[str, object]] = []
        for candidate_idx, (candidate_row, candidate_score) in enumerate(ranked_candidates):
            candidate_entry = mesh_cache.get_metadata(candidate_row["asset_id"])
            candidate_scale = _resolve_real_building_scale(
                entry=candidate_entry,
                frontage_width_m=frontage_width_m,
                depth_m=depth_m,
                target_height_m=_target_height_m,
            )
            if bool(candidate_scale.get("accepted", False)):
                row = candidate_row
                score = float(candidate_score)
                source = "building_asset"
                scale_decision = candidate_scale
                retrieval_payload["chosen_index"] = int(candidate_idx)
                retrieval_payload["scale_decision"] = dict(candidate_scale)
                break
            building_asset_rejected_size_mismatch_count += 1
            rejected_candidates.append(
                {
                    "asset_id": str(candidate_row.get("asset_id", "") or ""),
                    "reason": str(candidate_scale.get("reason", "building_asset_rejected_size_mismatch") or "building_asset_rejected_size_mismatch"),
                    "scale": float(candidate_scale.get("scale", 1.0) or 1.0),
                    "native_size_m": dict(candidate_scale.get("native_size_m", {}) or {}),
                    "final_size_m": dict(candidate_scale.get("final_size_m", {}) or {}),
                }
            )
        if rejected_candidates:
            retrieval_payload["rejected_candidates"] = rejected_candidates
        retrieval_predictions.append(retrieval_payload)

        if row is not None:
            entry = mesh_cache.get_metadata(row["asset_id"])
            uniform_scale = float(scale_decision.get("scale", 1.0) or 1.0)
            scale_xyz = [float(uniform_scale), float(uniform_scale), float(uniform_scale)]
            asset_id = str(row["asset_id"])
            asset_count += 1
            fallback_reason = ""
        else:
            asset_id = f"building_fallback_{str(target.get('target_kind', 'footprint'))}_{target_idx:03d}"
            fallback_entry = _placeholder_building_entry(
                asset_id=asset_id,
                frontage_width_m=frontage_width_m,
                depth_m=depth_m,
                height_class=str(target.get("height_class", "midrise") or "midrise"),
                theme_name=theme_name,
                target_height_m=_target_height_m,
            )
            # Store the fallback entry (includes full mesh)
            mesh_cache.set_full_entry(asset_id, fallback_entry)
            asset_by_id[asset_id] = {
                "asset_id": asset_id,
                "category": "building",
                "text_desc": f"{theme_name} {target.get('height_class', 'midrise')} procedural building",
                "asset_role": "building",
                "theme_tags": [theme_name, str(target.get("size_class", ""))],
                "height_class": str(target.get("height_class", "midrise") or "midrise"),
            }
            entry = mesh_cache.get_metadata(asset_id)
            scale_xyz = [1.0, 1.0, 1.0]
            fallback_count += 1
            fallback_reason = "no_building_asset_match"

        target_center_xz_raw = target.get("placement_xz", target.get("center_xz", (0.0, 0.0))) or (0.0, 0.0)
        target_center_xz = (
            float(target_center_xz_raw[0]),
            float(target_center_xz_raw[1]),
        )
        street_edge_xz_raw = target.get("street_edge_xz", ()) or ()
        if len(street_edge_xz_raw) >= 2:
            street_edge_xz = (float(street_edge_xz_raw[0]), float(street_edge_xz_raw[1]))
        else:
            street_edge_xz = (float(target_center_xz[0]), float(target_center_xz[1]))
        safe_pose = resolve_building_pose(
            target_center_xz=target_center_xz,
            street_edge_xz=street_edge_xz if len(street_edge_xz_raw) >= 2 else None,
            side=str(target.get("side", "") or ""),
            yaw_deg=float(target.get("yaw_deg", 0.0) or 0.0),
            half_x=float(entry.half_x),
            half_z=float(entry.half_z),
            center_x=float(getattr(entry, "center_x", 0.0) or 0.0),
            center_z=float(getattr(entry, "center_z", 0.0) or 0.0),
            scale=scale_xyz,
            placement_ctx=placement_ctx,
            forbidden_geometry=building_forbidden_geom,
            config=config,
            bbox_clearance_m=0.15,
            vehicle_clearance_m=0.40,
        )
        if safe_pose.rejected:
            lane_intrusion_rejected_count += 1
            if row is not None:
                asset_count = max(asset_count - 1, 0)
            else:
                fallback_count = max(fallback_count - 1, 0)
            retrieval_payload["placement_rejected_reason"] = safe_pose.reject_reason
            retrieval_payload["target_center_xz"] = [float(value) for value in target_center_xz]
            retrieval_payload["bbox_xz"] = [float(value) for value in safe_pose.bbox_xz]
            continue
        if safe_pose.adjusted:
            lane_intrusion_adjusted_count += 1
            total_lane_guard_push_m += float(safe_pose.push_distance_m)
            retrieval_payload["lane_guard_push_m"] = float(round(safe_pose.push_distance_m, 3))
        if abs(float(getattr(entry, "center_x", 0.0) or 0.0)) > 1e-6 or abs(float(getattr(entry, "center_z", 0.0) or 0.0)) > 1e-6:
            mesh_origin_centered_count += 1
        center_xz = (
            float(safe_pose.placement_xz[0]),
            float(safe_pose.placement_xz[1]),
        )
        visual_center_xz = (
            float(safe_pose.visual_center_xz[0]),
            float(safe_pose.visual_center_xz[1]),
        )
        adjusted_target = dict(target)
        adjusted_target["placement_xz"] = center_xz
        adjusted_target["center_xz"] = visual_center_xz
        adjusted_target["street_edge_xz"] = street_edge_xz
        placement_xz_raw = center_xz
        center_xz = (
            float(placement_xz_raw[0]),
            float(placement_xz_raw[1]),
        )
        placement_strategy = str(target.get("placement_strategy", "") or "")
        front_setback_m = float(target.get("front_setback_m", 0.0) or 0.0)
        bbox = tuple(float(value) for value in safe_pose.bbox_xz)
        y = -entry.min_y * float(scale_xyz[1])
        building_native_size = _native_size_for_entry(entry)
        building_final_size = dict(scale_decision.get("final_size_m", {}) or {})
        if not building_final_size:
            uniform_scale_for_size = float(scale_xyz[0]) if scale_xyz else 1.0
            building_final_size = {
                key: float(value) * uniform_scale_for_size
                for key, value in building_native_size.items()
            }
        building_asset_scale_mode = "building_real_preserve" if row is not None else "procedural_fallback_fit"
        instance_id = f"inst_{instance_index:04d}"
        should_attach_door = (
            str(source).strip().lower() == "procedural_fallback"
            or str(asset_id).startswith("building_fallback_")
            or str(fallback_reason).strip() == "no_building_asset_match"
        )
        if should_attach_door:
            door_required_count += 1
            door_spec = _resolve_building_door_spec(target=adjusted_target, entry=entry, scale_xyz=scale_xyz)
        else:
            door_skipped_existing_asset_count += 1
            door_spec = {
                "door_added": False,
                "door_facing": "",
                "door_center_local_x": 0.0,
                "door_width_m": 0.0,
                "door_height_m": 0.0,
                "door_dims_m": {},
                "door_center_world_xyz": [],
                "door_missing_reason": "real_building_asset_has_native_door",
            }
        if bool(door_spec.get("door_added")):
            side_key = str(target.get("side", "") or "").strip().lower() or "unknown"
            door_count_by_side[side_key] = door_count_by_side.get(side_key, 0) + 1
        elif should_attach_door:
            reason_key = str(door_spec.get("door_missing_reason", "") or "").strip()
            if reason_key:
                door_missing_reason_counts[reason_key] = door_missing_reason_counts.get(reason_key, 0) + 1
        plans.append(
            BuildingPlacementPlan(
                instance_id=instance_id,
                footprint_id=str(target.get("target_id", "") or ""),
                theme_id=theme_id,
                asset_id=asset_id,
                selection_source=source,
                position_xyz=[float(center_xz[0]), float(y), float(center_xz[1])],
                yaw_deg=float(target.get("yaw_deg", 0.0) or 0.0),
                scale=float(scale_xyz[0]),
                scale_xyz=[float(value) for value in scale_xyz],
                bbox_xz=[float(value) for value in bbox],
                frontage_width_m=frontage_width_m,
                depth_m=depth_m,
                side=str(target.get("side", "") or ""),
                land_use_type=str(target.get("land_use_type", "") or ""),
                street_edge_xz=street_edge_xz,
                placement_xz=(float(center_xz[0]), float(center_xz[1])),
                anchor_geom_id=str(target.get("anchor_geom_id", "") or ""),
                retrieval_score=float(score),
                fallback_reason=fallback_reason,
                target_height_m=_target_height_m,
                placement_strategy=placement_strategy,
                front_setback_m=front_setback_m,
                asset_scale_mode=building_asset_scale_mode,
                native_size_m=building_native_size,
                final_size_m=building_final_size,
                raw_size_m=_raw_size_for_entry(entry),
                metric_size_m=_metric_size_for_entry(entry),
                source_scale=float(getattr(entry, "source_scale", 1.0) or 1.0),
                source_scale_source=str(getattr(entry, "source_scale_source", "") or ""),
                source_scale_confidence=str(getattr(entry, "source_scale_confidence", "") or ""),
                source_scale_rejected_reason=str(getattr(entry, "source_scale_rejected_reason", "") or ""),
                door_added=bool(door_spec.get("door_added", False)),
                door_facing=str(door_spec.get("door_facing", "") or ""),
                door_center_local_x=float(door_spec.get("door_center_local_x", 0.0) or 0.0),
                door_width_m=float(door_spec.get("door_width_m", 0.0) or 0.0),
                door_height_m=float(door_spec.get("door_height_m", 0.0) or 0.0),
                door_dims_m=dict(door_spec.get("door_dims_m", {}) or {}),
                door_center_world_xyz=[float(value) for value in (door_spec.get("door_center_world_xyz", []) or [])],
                door_missing_reason=str(door_spec.get("door_missing_reason", "") or ""),
            )
        )
        placements.append(
            StreetPlacement(
                instance_id=instance_id,
                asset_id=asset_id,
                category="building",
                score=float(score),
                position_xyz=[float(center_xz[0]), float(y), float(center_xz[1])],
                yaw_deg=float(target.get("yaw_deg", 0.0) or 0.0),
                scale=float(scale_xyz[0]),
                bbox_xz=[float(value) for value in bbox],
                selection_source=source,
                placement_group="building",
                theme_id=theme_id,
                anchor_geom_id=str(target.get("anchor_geom_id", "") or ""),
                scale_xyz=[float(value) for value in scale_xyz],
                native_size_m=building_native_size,
                raw_size_m=_raw_size_for_entry(entry),
                metric_size_m=_metric_size_for_entry(entry),
                final_size_m=building_final_size,
                canonical_target={
                    "frontage_width_m": float(frontage_width_m),
                    "depth_m": float(depth_m),
                    "target_height_m": float(_target_height_m),
                },
                asset_scale_mode=building_asset_scale_mode,
                source_scale=float(getattr(entry, "source_scale", 1.0) or 1.0),
                source_scale_source=str(getattr(entry, "source_scale_source", "") or ""),
                source_scale_confidence=str(getattr(entry, "source_scale_confidence", "") or ""),
                source_scale_rejected_reason=str(getattr(entry, "source_scale_rejected_reason", "") or ""),
            )
        )
        source_name = str(target.get("source", "") or "")
        source_counts[source_name] = source_counts.get(source_name, 0) + 1
        if placement_strategy:
            placement_strategy_counts[placement_strategy] = placement_strategy_counts.get(placement_strategy, 0) + 1
        if front_setback_m > 0.0:
            front_setbacks.append(front_setback_m)
        instance_index += 1

    summary = {
        "enabled": True,
        "target_count": int(len(targets)),
        "placed_count": int(len(placements)),
        "asset_count": int(asset_count),
        "fallback_count": int(fallback_count),
        "building_asset_rejected_size_mismatch_count": int(building_asset_rejected_size_mismatch_count),
        "building_lane_intrusion_adjusted_count": int(lane_intrusion_adjusted_count),
        "building_lane_intrusion_rejected_count": int(lane_intrusion_rejected_count),
        "building_mesh_origin_centered_count": int(mesh_origin_centered_count),
        "building_lane_guard_total_push_m": float(round(total_lane_guard_push_m, 3)),
        "building_forbidden_geometry_mode": "road_occupancy_v1",
        "procedural_building_fallback_count": int(fallback_count),
        "sources": source_counts,
        "placement_strategy_counts": placement_strategy_counts,
        "door_enabled": True,
        "door_strategy": "attached_3d_v1",
        "door_policy": "procedural_fallback_only",
        "door_count": int(sum(1 for plan in plans if bool(plan.door_added))),
        "door_count_by_side": dict(door_count_by_side),
        "door_required_count": int(door_required_count),
        "door_skipped_existing_asset_count": int(door_skipped_existing_asset_count),
        "door_missing_building_count": int(max(door_required_count - sum(1 for plan in plans if bool(plan.door_added)), 0)),
        "door_missing_reason_counts": dict(door_missing_reason_counts),
    }
    if front_setbacks:
        summary["front_setback_stats"] = {
            "min_m": round(min(front_setbacks), 3),
            "max_m": round(max(front_setbacks), 3),
            "mean_m": round(sum(front_setbacks) / len(front_setbacks), 3),
        }
    return tuple(placements), tuple(plans), tuple(retrieval_predictions), summary, instance_index


def _place_surrounding_buildings(
    *,
    config: StreetComposeConfig,
    projected_features: object | None,
    placement_ctx: object | None,
    road_segment_graph: object | None,
    theme_segments: Sequence[ThemeSegment],
    resolved_program,
    embedder: ClipTextEmbedder,
    index_store: FaissIndexStore,
    asset_by_id: Dict[str, Dict[str, object]],
    mesh_cache: _LazyMeshCache,
    rng: random.Random,
    start_instance_index: int,
) -> _SurroundingBuildingResult:
    if not bool(getattr(config, "enable_surrounding_buildings", True)) or not _is_corridor_layout_mode(config.layout_mode):
        return _SurroundingBuildingResult(
            building_footprints=tuple(),
            generated_lots=tuple(),
            placements=tuple(),
            plans=tuple(),
            retrieval_predictions=tuple(),
            building_summary={
                "enabled": False,
                "generation_mode_requested": str(getattr(config, "surrounding_building_mode", "grid_growth") or "grid_growth"),
                "generation_mode_used": "",
                "generation_fallback_reason": "",
                "footprint_count": 0,
                "lot_count": 0,
                "placed_count": 0,
                "fallback_count": 0,
                "building_asset_rejected_size_mismatch_count": 0,
                "procedural_building_fallback_count": 0,
                "door_enabled": True,
                "door_count": 0,
                "door_count_by_side": {},
                "door_strategy": "attached_3d_v1",
                "door_missing_building_count": 0,
                "door_missing_reason_counts": {},
            },
            land_use_summary={},
            lot_generation_summary={"lot_count": 0},
            zoning_grid=tuple(),
            zoning_preview_summary={"enabled": False, "cell_count": 0},
            instance_index=int(start_instance_index),
        )

    requested_mode = str(getattr(config, "surrounding_building_mode", "grid_growth") or "grid_growth").strip().lower()
    mode = requested_mode
    generation_fallback_reason = ""
    building_regions_present = bool(getattr(placement_ctx, "building_regions", ()) or ())
    auto_land_use_mode = str(getattr(config, "auto_land_use_mode", "road_buffer") or "road_buffer").strip().lower()
    road_buffer_m = float(getattr(config, "land_use_buffer_m", 35.0) or 35.0)
    if building_regions_present and auto_land_use_mode == "off":
        mode = "footprint_based"
        generation_fallback_reason = (
            "building_regions present; bypassed land_use_zoning/grid generation and used region-only footprints."
        )
    elif str(config.layout_mode).strip().lower() == "metaurban" and mode == "footprint_based":
        mode = "grid_growth"
        generation_fallback_reason = (
            "metaurban v1 does not align real footprint imports yet; fell back to grid_growth."
        )
    road_type = _dominant_building_road_type(road_segment_graph, resolved_program)
    building_footprints: Tuple[BuildingFootprint, ...] = tuple()
    generated_lots: Tuple[GeneratedLot, ...] = tuple()
    zoning_granularity = str(getattr(config, "zoning_granularity", "fine") or "fine")
    streetwall_continuity = float(getattr(config, "streetwall_continuity", 0.95) or 0.95)
    infill_policy = str(getattr(config, "infill_policy", "aggressive") or "aggressive")
    region_direct_mode = bool(building_regions_present and auto_land_use_mode == "off")
    footprint_frontage_summary: Dict[str, object] = {
        "real_footprint_count": 0,
        "infill_footprint_count": 0,
        "frontage_coverage_by_side": {"left": {}, "right": {}},
        "frontage_gap_stats_by_side": {"left": {}, "right": {}},
    }

    if mode == "footprint_based":
        asymmetry_raw = getattr(config, "land_use_asymmetry_strength", 0.0)
        bias_raw = getattr(config, "left_right_bias", 0.0)
        setback_min_raw = getattr(config, "building_front_setback_min_m", DEFAULT_BUILDING_FRONT_SETBACK_MIN_M)
        setback_max_raw = getattr(config, "building_front_setback_max_m", DEFAULT_BUILDING_FRONT_SETBACK_MAX_M)
        building_footprints = tuple(
            collect_building_footprints(
                projected_features,
                placement_context=placement_ctx,
                theme_segments=theme_segments,
                road_segment_graph=road_segment_graph,
                road_buffer_m=road_buffer_m,
                seed=int(getattr(config, "seed", 0) or 0),
                height_mode=str(getattr(config, "building_height_mode", "theme_random") or "theme_random"),
                height_profile=str(getattr(config, "building_height_profile", "urban_default_v1") or "urban_default_v1"),
                asymmetry_strength=float(0.0 if asymmetry_raw is None else asymmetry_raw),
                left_right_bias=float(0.0 if bias_raw is None else bias_raw),
                front_setback_min_m=float(DEFAULT_BUILDING_FRONT_SETBACK_MIN_M if setback_min_raw is None else setback_min_raw),
                front_setback_max_m=float(DEFAULT_BUILDING_FRONT_SETBACK_MAX_M if setback_max_raw is None else setback_max_raw),
                zoning_granularity=zoning_granularity,
                streetwall_continuity=streetwall_continuity,
            )
        )

    zoning_grid_base: Tuple[Dict[str, object], ...] = tuple()
    zoning_preview_summary: Dict[str, object]
    if region_direct_mode:
        zoning_preview_summary = {
            "enabled": False,
            "cell_count": 0,
            "theme_cell_counts": {},
            "building_cell_counts": {},
            "occupied_building_cells": 0,
            "buildable_cell_count": 0,
            "side_land_use_counts": {"left": {}, "right": {}},
            "active_side_counts": {},
            "building_buffer_width_m": {"left": 0.0, "right": 0.0},
            "streetwall_reference_width_m": {"left": 0.0, "right": 0.0},
            "streetwall_reference_gap_ratio": 0.0,
            "asymmetry_strength": 0.0,
            "left_right_bias": 0.0,
            "building_region_count": int(len(building_footprints)),
            "active_building_region_count": int(len(building_footprints)),
            "zoning_preview_mode": "building_region_direct",
            "frontage_cell_count": 0,
            "theme_segment_count": int(len(theme_segments)),
            "buildable_frontage_by_side": {"left": 0.0, "right": 0.0},
            "generated_lot_count": 0,
            "frontage_parcel_count": 0,
        }
    else:
        zoning_grid_base, zoning_preview_summary = build_zoning_grid_preview(
            config=config,
            placement_context=placement_ctx,
            road_segment_graph=road_segment_graph,
            theme_segments=theme_segments,
            building_footprints=building_footprints,
            road_buffer_m=road_buffer_m,
        )
    zoning_grid = zoning_grid_base
    lot_generation_summary: Dict[str, object] = {"lot_count": 0}
    if mode == "footprint_based" and not building_regions_present:
        setback_min_raw = getattr(config, "building_front_setback_min_m", DEFAULT_BUILDING_FRONT_SETBACK_MIN_M)
        setback_max_raw = getattr(config, "building_front_setback_max_m", DEFAULT_BUILDING_FRONT_SETBACK_MAX_M)
        infill_footprints, footprint_frontage_summary = generate_frontage_infill_footprints(
            zoning_grid_base,
            building_footprints,
            seed=int(getattr(config, "seed", 0) or 0),
            height_mode=str(getattr(config, "building_height_mode", "theme_random") or "theme_random"),
            height_profile=str(getattr(config, "building_height_profile", "urban_default_v1") or "urban_default_v1"),
            zoning_granularity=zoning_granularity,
            streetwall_continuity=streetwall_continuity,
            infill_policy=infill_policy,
            front_setback_min_m=float(DEFAULT_BUILDING_FRONT_SETBACK_MIN_M if setback_min_raw is None else setback_min_raw),
            front_setback_max_m=float(DEFAULT_BUILDING_FRONT_SETBACK_MAX_M if setback_max_raw is None else setback_max_raw),
        )
        if infill_footprints:
            building_footprints = tuple(list(building_footprints) + list(infill_footprints))
            zoning_grid_base, zoning_preview_summary = build_zoning_grid_preview(
                config=config,
                placement_context=placement_ctx,
                road_segment_graph=road_segment_graph,
                theme_segments=theme_segments,
                building_footprints=building_footprints,
                road_buffer_m=road_buffer_m,
            )
            zoning_grid = zoning_grid_base
    if mode == "grid_growth":
        setback_min_raw = getattr(config, "building_front_setback_min_m", DEFAULT_BUILDING_FRONT_SETBACK_MIN_M)
        setback_max_raw = getattr(config, "building_front_setback_max_m", DEFAULT_BUILDING_FRONT_SETBACK_MAX_M)
        zoning_grid, generated_lots, lot_generation_summary = generate_grid_growth_lots(
            zoning_grid_base,
            road_type=road_type,
            seed=int(getattr(config, "seed", 0) or 0),
            height_mode=str(getattr(config, "building_height_mode", "theme_random") or "theme_random"),
            height_profile=str(getattr(config, "building_height_profile", "urban_default_v1") or "urban_default_v1"),
            front_setback_min_m=float(DEFAULT_BUILDING_FRONT_SETBACK_MIN_M if setback_min_raw is None else setback_min_raw),
            front_setback_max_m=float(DEFAULT_BUILDING_FRONT_SETBACK_MAX_M if setback_max_raw is None else setback_max_raw),
            zoning_granularity=zoning_granularity,
            streetwall_continuity=streetwall_continuity,
            max_frontage_lot_length_m=float(getattr(config, "max_frontage_lot_length_m", 18.0) or 18.0),
        )
    if region_direct_mode:
        land_use_summary = _summarize_building_region_direct_footprints(building_footprints)
    else:
        land_use_summary = summarize_land_use_grid(zoning_grid)

    building_density_summary: Dict[str, object] = {
        "enabled": False,
        "density": float(getattr(config, "building_density", 0.55) or 0.55),
        "max_per_100m": float(getattr(config, "building_max_per_100m", 10.0) or 10.0),
        "input_lot_count": int(len(generated_lots)),
        "selected_lot_count": int(len(generated_lots)),
        "removed_lot_count": 0,
        "selected_by_side": {},
        "target_by_side": {},
    }
    placement_lots = tuple(generated_lots)
    if mode == "grid_growth":
        placement_lots, building_density_summary = _select_building_lots_for_density(
            generated_lots,
            density=float(getattr(config, "building_density", 0.55) or 0.55),
            max_per_100m=float(getattr(config, "building_max_per_100m", 10.0) or 10.0),
            buildable_frontage_by_side=dict(lot_generation_summary.get("buildable_frontage_by_side", {}) or {}),
        )
    if mode == "grid_growth":
        target_records = _lot_target_records(placement_lots)
    else:
        target_records = _footprint_target_records(building_footprints)
    building_placements, building_plans, building_retrieval_predictions, placement_summary, instance_index = _place_building_targets(
        targets=target_records,
        config=config,
        theme_segments=theme_segments,
        resolved_program=resolved_program,
        placement_ctx=placement_ctx,
        embedder=embedder,
        index_store=index_store,
        asset_by_id=asset_by_id,
        mesh_cache=mesh_cache,
        rng=rng,
        start_instance_index=start_instance_index,
        road_type=road_type,
    )

    occupied_building_cells = sum(
        1
        for cell in zoning_grid
        if "building_buffer" in str(cell.get("lane_role", "") or "")
        and (
            (cell.get("footprint_ids", []) or [])
            or str(cell.get("lot_id", "") or "")
        )
    )
    zoning_preview_summary = {
        **dict(zoning_preview_summary),
        "occupied_building_cells": int(occupied_building_cells),
        "generated_lot_count": int(len(generated_lots)),
        "zoning_preview_mode": str(
            zoning_preview_summary.get(
                "zoning_preview_mode",
                "building_region_direct" if region_direct_mode else "parcel_first",
            )
            or ("building_region_direct" if region_direct_mode else "parcel_first")
        ),
        "frontage_cell_count": int(zoning_preview_summary.get("frontage_cell_count", 0) or 0),
        "theme_segment_count": int(zoning_preview_summary.get("theme_segment_count", len(theme_segments)) or len(theme_segments)),
        "frontage_parcel_count": int(
            lot_generation_summary.get("frontage_parcel_count", len(generated_lots))
            if mode == "grid_growth"
            else 0
        ),
    }
    asymmetry_raw = getattr(config, "land_use_asymmetry_strength", 0.0)
    bias_raw = getattr(config, "left_right_bias", 0.0)
    setback_min_raw = getattr(config, "building_front_setback_min_m", DEFAULT_BUILDING_FRONT_SETBACK_MIN_M)
    setback_max_raw = getattr(config, "building_front_setback_max_m", DEFAULT_BUILDING_FRONT_SETBACK_MAX_M)
    frontage_metrics_source = footprint_frontage_summary if mode == "footprint_based" else lot_generation_summary
    building_summary = {
        **dict(placement_summary),
        "enabled": True,
        "generation_mode": "building_region_direct" if region_direct_mode else mode,
        "generation_mode_requested": requested_mode,
        "generation_mode_used": "building_region_direct" if region_direct_mode else mode,
        "generation_fallback_reason": generation_fallback_reason,
        "footprint_count": int(len(building_footprints)),
        "lot_count": int(len(generated_lots)),
        "target_type": "lot" if mode == "grid_growth" else "footprint",
        "land_use_asymmetry_strength": float(0.0 if asymmetry_raw is None else asymmetry_raw),
        "left_right_bias": float(0.0 if bias_raw is None else bias_raw),
        "building_front_setback_min_m": float(DEFAULT_BUILDING_FRONT_SETBACK_MIN_M if setback_min_raw is None else setback_min_raw),
        "building_front_setback_max_m": float(DEFAULT_BUILDING_FRONT_SETBACK_MAX_M if setback_max_raw is None else setback_max_raw),
        "auto_land_use_mode": str(auto_land_use_mode),
        "land_use_buffer_m": float(road_buffer_m),
        "min_land_use_polygon_area_m2": float(getattr(config, "min_land_use_polygon_area_m2", 12.0) or 12.0),
        "max_frontage_lot_length_m": float(getattr(config, "max_frontage_lot_length_m", 18.0) or 18.0),
        "zoning_granularity": str(zoning_granularity),
        "streetwall_continuity": float(streetwall_continuity),
        "building_density": float(getattr(config, "building_density", 0.55) or 0.55),
        "building_max_per_100m": float(getattr(config, "building_max_per_100m", 10.0) or 10.0),
        "building_density_summary": dict(building_density_summary),
        "building_target_lot_count": int(len(placement_lots) if mode == "grid_growth" else len(building_footprints)),
        "building_density_removed_lot_count": int(building_density_summary.get("removed_lot_count", 0) or 0),
        "infill_policy": str(infill_policy),
        "building_balance_policy": str(
            lot_generation_summary.get("building_balance_policy", "balanced_default")
            if mode == "grid_growth"
            else ("building_region_direct" if region_direct_mode else "manual_realistic_mode")
        ),
        "building_balance_ok": bool(
            lot_generation_summary.get("building_balance_ok", False)
            if mode == "grid_growth"
            else region_direct_mode
        ),
        "building_balance_reason": str(
            lot_generation_summary.get("building_balance_reason", "")
            if mode == "grid_growth"
            else ("building_region_direct mode" if region_direct_mode else "footprint_based mode")
        ),
        "frontage_balance_gap": float(
            lot_generation_summary.get("frontage_balance_gap", 0.0)
            if mode == "grid_growth"
            else 0.0
        ),
        "buildable_frontage_by_side": dict(
            lot_generation_summary.get(
                "buildable_frontage_by_side",
                zoning_preview_summary.get("buildable_frontage_by_side", {}),
            )
            or {}
        ),
        "frontage_parcel_count": int(
            lot_generation_summary.get("frontage_parcel_count", len(generated_lots))
            if mode == "grid_growth"
            else 0
        ),
        "zoning_preview_mode": str(zoning_preview_summary.get("zoning_preview_mode", "parcel_first") or "parcel_first"),
        "frontage_cell_count": int(zoning_preview_summary.get("frontage_cell_count", 0) or 0),
        "real_footprint_count": int(footprint_frontage_summary.get("real_footprint_count", 0) or 0),
        "infill_footprint_count": int(footprint_frontage_summary.get("infill_footprint_count", 0) or 0),
        "frontage_coverage_by_side": dict(frontage_metrics_source.get("frontage_coverage_by_side", {}) or {}),
        "frontage_gap_stats_by_side": dict(frontage_metrics_source.get("frontage_gap_stats_by_side", {}) or {}),
        "building_region_count": int(len(getattr(placement_ctx, "building_regions", ()) or ())),
        "region_direct_mode": bool(region_direct_mode),
    }
    # Attach continuous height stats when available
    _all_heights: list[float] = []
    for fp in building_footprints:
        if fp.target_height_m > 0.0:
            _all_heights.append(fp.target_height_m)
    for lot in generated_lots:
        if lot.target_height_m > 0.0:
            _all_heights.append(lot.target_height_m)
    if _all_heights:
        building_summary["height_stats"] = {
            "min_m": round(min(_all_heights), 1),
            "max_m": round(max(_all_heights), 1),
            "mean_m": round(sum(_all_heights) / len(_all_heights), 1),
        }
    return _SurroundingBuildingResult(
        building_footprints=tuple(building_footprints),
        generated_lots=tuple(generated_lots),
        placements=tuple(building_placements),
        plans=tuple(building_plans),
        retrieval_predictions=tuple(building_retrieval_predictions),
        building_summary=building_summary,
        land_use_summary=land_use_summary,
        lot_generation_summary=lot_generation_summary,
        zoning_grid=tuple(zoning_grid),
        zoning_preview_summary=zoning_preview_summary,
        instance_index=int(instance_index),
    )


_LIGHTING_PARAMS: Dict[str, Dict[str, Any]] = {
    "bright_day": {
        "exposure": 1.3,
        "keyLightIntensity": 1.2,
        "fillLightIntensity": 0.8,
        "warmth": -0.1,
        "shadowStrength": 0.3,
    },
    "overcast": {
        "exposure": 1.05,
        "keyLightIntensity": 0.75,
        "fillLightIntensity": 0.95,
        "warmth": -0.15,
        "shadowStrength": 0.15,
    },
    "golden_hour": {
        "exposure": 1.18,
        "keyLightIntensity": 1.05,
        "fillLightIntensity": 0.48,
        "warmth": 0.85,
        "shadowStrength": 0.58,
    },
    "night_presentation": {
        "exposure": 1.05,
        "keyLightIntensity": 1.05,
        "fillLightIntensity": 0.24,
        "warmth": 0.2,
        "shadowStrength": 0.72,
    },
}


def _derive_lighting_preset(sky_selection: Any) -> str:
    """Map sky selection to a viewer lighting preset."""
    if sky_selection is None:
        return "bright_day"
    time_of_day = str(getattr(sky_selection, "time_of_day", "day") or "day").lower()
    weather_tags = [
        str(tag).lower()
        for tag in (getattr(sky_selection, "weather_tags", ()) or ())
    ]
    illumination_tags = [
        str(tag).lower()
        for tag in (getattr(sky_selection, "illumination_tags", ()) or ())
    ]
    all_tags = set(weather_tags) | set(illumination_tags)
    if time_of_day == "night":
        return "night_presentation"
    if time_of_day == "evening":
        return "golden_hour"
    if any(tag in all_tags for tag in ("overcast", "cloudy", "foggy", "rainy")):
        return "overcast"
    return "bright_day"


def _derive_lighting_params(sky_selection: Any) -> Dict[str, Any]:
    """Map sky selection to concrete lighting parameter values."""
    preset = _derive_lighting_preset(sky_selection)
    params = dict(_LIGHTING_PARAMS.get(preset, _LIGHTING_PARAMS["bright_day"]))
    return {"preset": preset, **params}


def compose_street_scene(
    config: StreetComposeConfig,
    manifest_path: Path,
    artifacts_dir: Path,
    model_name: str = "openai/clip-vit-base-patch32",
    model_dir: Optional[Path] = None,
    local_files_only: bool = False,
    device: str = "auto",
    export_format: str = "glb",
    out_dir: Path = Path("artifacts/real"),
    placement_policy: str = "rule",
    policy_ckpt: Optional[Path] = None,
    program_ckpt: Optional[Path] = None,
    policy_temperature: float = SOFTMAX_TEMPERATURE,
    object_asset_backend: ObjectAssetBackend | None = None,
    ground_material_backend: GroundMaterialBackend | None = None,
    sky_backend: SkyBackend | None = None,
    road_segment_graph_override: object | None = None,
    projected_features_override: object | None = None,
    placement_context_override: object | None = None,
    build_production_artifacts: bool = True,
    render_presentation_artifacts: bool = True,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
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

    def _emit_progress(stage: str, progress: int, message: str, **detail: Any) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback({
                "stage": stage,
                "progress": int(progress),
                "message": message,
                "detail": dict(detail),
            })
        except Exception:
            return

    def _compact_mapping(mapping: Mapping[str, Any] | None, *, limit: int = 20) -> Dict[str, Any]:
        items = sorted((mapping or {}).items(), key=lambda item: str(item[0]))
        return {str(key): value for key, value in items[:limit]}

    def _compact_theme_segments(segments: Sequence[Any], *, limit: int = 30) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for segment in list(segments)[:limit]:
            payload = segment.to_dict() if hasattr(segment, "to_dict") else dict(getattr(segment, "__dict__", {}))
            records.append({
                "theme_id": str(payload.get("theme_id", "")),
                "theme_name": str(payload.get("theme_name", "")),
                "x_start_m": round(float(payload.get("x_start_m", 0.0) or 0.0), 2),
                "x_end_m": round(float(payload.get("x_end_m", 0.0) or 0.0), 2),
                "length_m": round(float(payload.get("length_m", 0.0) or 0.0), 2),
                "dominant_poi_types": list(payload.get("dominant_poi_types", []) or [])[:8],
                "design_rule_profile": str(payload.get("design_rule_profile", "")),
                "style_preset": str(payload.get("style_preset", "")),
                "segment_ids": list(payload.get("segment_ids", []) or [])[:8],
            })
        return records

    def _compact_program(program: Any) -> Dict[str, Any]:
        payload = program.to_dict() if hasattr(program, "to_dict") else {}
        bands = []
        for band in list(payload.get("bands", []) or [])[:20]:
            if not isinstance(band, Mapping):
                continue
            bands.append({
                "name": str(band.get("name", "")),
                "kind": str(band.get("kind", "")),
                "side": str(band.get("side", "")),
                "width_m": round(float(band.get("width_m", 0.0) or 0.0), 2),
                "z_center_m": round(float(band.get("z_center_m", 0.0) or 0.0), 2),
                "allowed_categories": list(band.get("allowed_categories", []) or [])[:8],
            })
        return {
            "cross_section_type": str(payload.get("cross_section_type", "")),
            "lane_count": int(payload.get("lane_count", 0) or 0),
            "road_width_m": round(float(payload.get("road_width_m", 0.0) or 0.0), 2),
            "sidewalk_width_m": round(float(payload.get("sidewalk_width_m", 0.0) or 0.0), 2),
            "row_width_m": round(float(payload.get("row_width_m", 0.0) or 0.0), 2),
            "width_expanded": bool(payload.get("width_expanded", False)),
            "width_reallocation_reason": str(payload.get("width_reallocation_reason", "")),
            "poi_fit_feasible": bool(payload.get("poi_fit_feasible", True)),
            "furniture_requirements": _compact_mapping(payload.get("furniture_requirements", {}), limit=20),
            "throughput_requirements": _compact_mapping(payload.get("throughput_requirements", {}), limit=20),
            "design_goals": list(payload.get("design_goals", []) or [])[:12],
            "bands": bands,
        }

    def _summarize_slot_plans(slots: Sequence[Any], *, sample_limit: int = 16) -> Dict[str, Any]:
        category_counts = Counter(str(getattr(slot, "category", "") or "") for slot in slots)
        side_counts = Counter(str(getattr(slot, "side", "") or "") for slot in slots)
        anchor_counts = Counter(str(getattr(slot, "anchor_poi_type", "") or "") for slot in slots if str(getattr(slot, "anchor_poi_type", "") or ""))
        sample = []
        for slot in list(slots)[:sample_limit]:
            sample.append({
                "slot_id": str(getattr(slot, "slot_id", "") or ""),
                "category": str(getattr(slot, "category", "") or ""),
                "theme_id": str(getattr(slot, "theme_id", "") or ""),
                "band_name": str(getattr(slot, "band_name", "") or ""),
                "side": str(getattr(slot, "side", "") or ""),
                "x_center_m": round(float(getattr(slot, "x_center_m", 0.0) or 0.0), 2),
                "z_center_m": round(float(getattr(slot, "z_center_m", 0.0) or 0.0), 2),
                "spacing_m": round(float(getattr(slot, "spacing_m", 0.0) or 0.0), 2),
                "required": bool(getattr(slot, "required", False)),
                "anchor_poi_type": str(getattr(slot, "anchor_poi_type", "") or ""),
            })
        return {
            "total_slots": int(len(slots)),
            "required_slots": int(sum(1 for slot in slots if bool(getattr(slot, "required", False)))),
            "anchored_slots": int(sum(1 for slot in slots if str(getattr(slot, "anchor_poi_type", "") or ""))),
            "category_counts": dict(category_counts),
            "side_counts": dict(side_counts),
            "anchor_poi_counts": dict(anchor_counts),
            "sample_slots": sample,
        }

    def _summarize_solver(result: Any, *, zone_programs: Sequence[Mapping[str, Any]] = ()) -> Dict[str, Any]:
        rule_counts = Counter(str(evaluation.status) for evaluation in getattr(result, "rule_evaluations", ()) or ())
        flagged_rules = [
            evaluation.to_dict()
            for evaluation in list(getattr(result, "rule_evaluations", ()) or [])
            if str(getattr(evaluation, "status", "")).lower() not in {"pass", "passed", "satisfied", "ok"}
        ][:20]
        return {
            "algorithm": {
                "solver_backend_requested": str(getattr(result, "backend_requested", "")),
                "solver_backend_used": str(getattr(result, "backend_used", "")),
                "fallback_reason": str(getattr(result, "fallback_reason", "") or ""),
                "objective_profile": str(getattr(result, "objective_profile", "")),
            },
            "metrics": {
                "topology_validity": round(float(getattr(result, "topology_validity", 0.0) or 0.0), 4),
                "cross_section_feasibility": round(float(getattr(result, "cross_section_feasibility", 0.0) or 0.0), 4),
                "rule_satisfaction_rate": round(float(getattr(result, "rule_satisfaction_rate", 0.0) or 0.0), 4),
                "editability": round(float(getattr(result, "editability", 0.0) or 0.0), 4),
                "conflict_explainability": round(float(getattr(result, "conflict_explainability", 0.0) or 0.0), 4),
            },
            "active_constraints": list(getattr(result, "active_constraints", ()) or ())[:40],
            "rule_evaluation_counts": dict(rule_counts),
            "flagged_rule_evaluations": flagged_rules,
            "band_solutions": [band.to_dict() for band in list(getattr(result, "band_solutions", ()) or [])[:30]],
            "edits": [edit.to_dict() for edit in list(getattr(result, "edits", ()) or [])[:20]],
            "conflicts": [conflict.to_dict() for conflict in list(getattr(result, "conflicts", ()) or [])[:20]],
            "throughput_feasibility": dict(getattr(result, "throughput_feasibility", {}) or {}),
            "objective_score_breakdown": dict(getattr(result, "objective_score_breakdown", {}) or {}),
            "zone_programs": [dict(item) for item in list(zone_programs)[:30]],
            "slot_plan_summary": _summarize_slot_plans(list(getattr(result, "slot_plans", ()) or [])),
        }

    def _summarize_attempt_records(records: Mapping[str, Mapping[str, Any]], *, sample_limit: int = 12) -> Dict[str, Any]:
        blocked_counts: Counter[str] = Counter()
        category_status: Dict[str, Dict[str, int]] = {}
        search_tier_counts: Counter[str] = Counter()
        samples: List[Dict[str, Any]] = []
        placed = 0
        unplaced = 0
        for record in records.values():
            category = str(record.get("category", "") or "")
            bucket = category_status.setdefault(category, {"placed": 0, "unplaced": 0})
            if bool(record.get("placed", False)):
                placed += 1
                bucket["placed"] += 1
            else:
                unplaced += 1
                bucket["unplaced"] += 1
                failure = str(record.get("failure_reason", "") or "no_candidate_after_search")
                if failure:
                    blocked_counts[failure] += 1
                if len(samples) < sample_limit:
                    samples.append({
                        "slot_id": str(record.get("slot_id", "") or ""),
                        "category": category,
                        "theme_id": str(record.get("theme_id", "") or ""),
                        "side": str(record.get("side", "") or ""),
                        "band_name": str(record.get("band_name", "") or ""),
                        "failure_reason": failure,
                        "blocked_reason_counts": dict(record.get("blocked_reason_counts", {}) or {}),
                        "required_like": bool(record.get("required_like", False)),
                        "search_tier_reached": str(record.get("search_tier_reached", "") or ""),
                    })
            tier = str(record.get("search_tier_reached", "") or "")
            if tier:
                search_tier_counts[tier] += 1
            for reason, count in dict(record.get("blocked_reason_counts", {}) or {}).items():
                blocked_counts[str(reason)] += int(count)
        return {
            "placed_slot_records": int(placed),
            "unplaced_slot_records": int(unplaced),
            "blocked_reason_counts": dict(blocked_counts),
            "search_tier_counts": dict(search_tier_counts),
            "category_status_counts": category_status,
            "unplaced_samples": samples,
        }

    def _placement_algorithm_detail() -> Dict[str, Any]:
        return {
            "policy_used": str(policy_used),
            "placement_policy_requested": str(policy_mode),
            "topk_per_category": int(config.topk_per_category),
            "max_trials_per_slot": int(config.max_trials_per_slot),
            "policy_temperature": float(policy_temperature),
            "candidate_pipeline": [
                "category pool filter",
                "_pick_category_candidate retrieval or policy ranking",
                "_iter_slot_candidate_groups pose generation",
                "_evaluate_slot_candidate geometry and rule filters",
                "placement_energy ranking",
                "optional balance repair",
            ],
            "intercept_filters": [
                "intrudes_carriageway",
                "overlap_blocked",
                "constraint_vetoed",
                "out_of_sidewalk",
                "out_of_target_strip",
                "out_of_theme_range",
                "side_mismatch",
                "no_candidate_after_search",
            ],
        }

    _emit_progress(
        "asset_loading",
        12,
        "Loading object, material, and sky assets.",
        layout_mode=str(config.layout_mode),
    )

    object_backend_name = "manifest_legacy"
    if object_asset_backend is not None:
        object_backend_name, rows = object_asset_backend.load_rows(manifest_path=manifest_path)
    else:
        rows = _load_real_manifest(manifest_path)
    rows = _ensure_default_sky_dome_row(rows)
    ground_selection = None
    if ground_material_backend is not None:
        try:
            ground_selection = ground_material_backend.select_for_config(config)
        except Exception as exc:
            logger.warning("Ground material backend selection failed: %s", exc)
    sky_selection = None
    if sky_backend is not None:
        try:
            sky_selection = sky_backend.select_for_config(config)
        except Exception as exc:
            logger.warning("Sky backend selection failed: %s", exc)
    texture_overrides = dict(ground_selection.texture_overrides) if ground_selection is not None else {}
    environment_source_datasets = collect_environment_source_datasets(ground_selection, sky_selection)
    environment_source_dataset = ""
    if len(environment_source_datasets) == 1:
        environment_source_dataset = environment_source_datasets[0]
    elif environment_source_datasets:
        environment_source_dataset = "mixed"
    mesh_cache = _load_mesh_cache(rows)
    curated_asset_profile = _normalize_curated_street_assets_profile(
        getattr(config, "curated_street_assets_profile", "fixed_hq_v1")
    )
    rows = _inject_curated_virtual_assets(rows, mesh_cache, profile=curated_asset_profile)
    asset_by_id = {row["asset_id"]: row for row in rows}
    default_sky_dome_placement = _default_sky_dome_placement(
        config,
        asset_by_id.get(DEFAULT_SKY_DOME_ASSET_ID),
        mesh_cache.get(DEFAULT_SKY_DOME_ASSET_ID) if DEFAULT_SKY_DOME_ASSET_ID in mesh_cache else None,
    )
    configured_locked_asset_ids = _curated_locked_asset_ids_for_profile(curated_asset_profile)
    curated_asset_fallback_ids = _validate_curated_locked_assets(
        asset_by_id=asset_by_id,
        profile=curated_asset_profile,
    )
    locked_asset_ids: Dict[str, str] = {}

    category_to_rows: Dict[str, List[Dict[str, str]]] = {category: [] for category in DEFAULT_CATEGORIES}
    raw_tree_inventory_count = sum(1 for row in rows if str(row.get("category", "")).strip().lower() == "tree")
    for row in rows:
        category = row["category"]
        if category in category_to_rows:
            if not _row_scene_eligible(row):
                continue
            if category == "tree":
                if not _is_external_tree_asset(row):
                    continue
            category_to_rows[category].append(row)
    tree_assets_unavailable = not bool(category_to_rows.get("tree"))
    curated_asset_allowlist_ids = _curated_allowlist_ids_by_category(
        category_to_rows,
        config=config,
    )

    available_categories = [category for category, pool in category_to_rows.items() if pool]
    fallback_blocked_categories = sorted(
        str(category)
        for category in configured_locked_asset_ids
        if str(category) not in curated_asset_allowlist_ids and not category_to_rows.get(str(category), [])
    )
    if not available_categories:
        raise RuntimeError(
            f"No supported categories found in manifest: {manifest_path}. "
            f"Expected at least one of {DEFAULT_CATEGORIES}."
        )

    parametric_tree_count = 0

    embedder = ClipTextEmbedder(
        model_name=model_name,
        model_dir=model_dir,
        local_files_only=bool(local_files_only),
        device=device,
    )
    # FAISS index: use artifacts_dir if present, otherwise fallback to artifacts/m1
    index_path = artifacts_dir / "index_ip.faiss"
    id_map_path = artifacts_dir / "id_map.json"
    if not index_path.exists():
        fallback_index = ROOT / "artifacts" / "m1" / "index_ip.faiss"
        fallback_id_map = ROOT / "artifacts" / "m1" / "id_map.json"
        if fallback_index.exists():
            index_path = fallback_index
            id_map_path = fallback_id_map
    index_store = FaissIndexStore.load(
        index_path=index_path,
        id_map_path=id_map_path,
    )

    # Load building assets from UrbanVerse manifest
    building_manifest_path = ROOT / "assets" / "building" / "buildings_manifest.jsonl"
    building_rows = _load_building_manifest(building_manifest_path)
    building_asset_count = 0
    if building_rows:
        logger.info("Loaded %d building assets from UrbanVerse manifest", len(building_rows))
        # Filter scene-eligible buildings
        scene_eligible_rows = [row for row in building_rows if bool(row.get("scene_eligible", True))]
        logger.info("After filtering scene-eligible: %d building assets", len(scene_eligible_rows))

        if scene_eligible_rows:
            # Load mesh metadata for building assets and add to mesh cache
            building_mesh_metadata = _load_mesh_metadata(scene_eligible_rows)
            for asset_id, metadata in building_mesh_metadata.items():
                mesh_cache._metadata[asset_id] = metadata  # Add to metadata dict

            # Add building rows to asset_by_id
            for row in scene_eligible_rows:
                asset_by_id[row["asset_id"]] = row

            # Generate CLIP embeddings for building assets and add to index
            building_embeddings = _generate_building_text_embeddings(scene_eligible_rows, embedder)
            if building_embeddings:
                asset_ids = list(building_embeddings.keys())
                embeddings_list = [building_embeddings[aid] for aid in asset_ids]
                embedding_matrix = np.stack(embeddings_list)
                if hasattr(index_store, "add"):
                    index_store.add(embedding_matrix, asset_ids)
                    building_asset_count = len(asset_ids)
                    logger.info("Added %d building assets to retrieval index", building_asset_count)
                else:
                    logger.debug("Skipping building asset index add; index store has no add() method")
    building_index_enabled = building_asset_count > 0

    _emit_progress(
        "asset_loading",
        25,
        "Loaded retrieval index and building assets.",
        object_asset_count=len(rows),
        building_asset_count=building_asset_count,
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

    program_runtime = ProgramGeneratorRuntime(backend="heuristic_v1", device=device)
    program_used = "heuristic_v1"
    program_fallback_reasons: List[str] = []
    if str(config.program_generator).strip().lower() == "learned_v1":
        ckpt_path = Path(program_ckpt).expanduser().resolve() if program_ckpt else None
        if ckpt_path is None or not ckpt_path.exists():
            program_fallback_reasons.append(
                "Program generator checkpoint missing; fallback to heuristic_v1."
                if ckpt_path is None
                else f"Program generator checkpoint not found: {ckpt_path}. Fallback to heuristic_v1."
            )
        else:
            try:
                program_runtime = ProgramGeneratorRuntime.from_checkpoint(ckpt_path, device=device)
                program_used = "learned_v1"
            except Exception as exc:
                program_fallback_reasons.append(f"Program generator load failed ({exc}); fallback to heuristic_v1.")

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
    road_segment_graph = road_segment_graph_override if road_segment_graph_override is not None else None
    effective_poi_counts: Dict[str, int] = normalize_poi_counts({})
    _emit_progress(
        "context_resolving",
        30,
        "Resolving road graph, POI, and placement context.",
        layout_mode=str(config.layout_mode),
    )
    if config.layout_mode == "osm":
        from .osm_ingest import fetch_osm_data, parse_osm_features, project_to_local
        from .placement_zones import evaluate_projected_road_context

        raw = fetch_osm_data(bbox=config.aoi_bbox, cache_dir=Path(config.osm_cache_dir))
        features = parse_osm_features(raw)
        projected = project_to_local(features, config.aoi_bbox)
        projected, placement_ctx, effective_poi_counts = evaluate_projected_road_context(
            projected,
            config,
            road_segment_graph=road_segment_graph,
        )
        if not getattr(placement_ctx, "poi_fit_feasible", True):
            raise RuntimeError(
                "Selected road failed POI fit synthesis: "
                f"{json.dumps(getattr(placement_ctx, 'poi_fit_report', {}), ensure_ascii=True)}"
            )
        if not qualifies_poi_counts(effective_poi_counts):
            raise RuntimeError(
                "Selected road does not retain enough effective POIs after compose filtering "
                "(requires weighted POI score >= 2.0 and at least 1 core POI)."
            )
    elif config.layout_mode in {"metaurban", "graph_template"}:
        if road_segment_graph_override is None or projected_features_override is None or placement_context_override is None:
            raise ValueError(
                f"{config.layout_mode} layout_mode requires road_segment_graph_override, "
                "projected_features_override, and placement_context_override"
            )
        projected = projected_features_override
        placement_ctx = placement_context_override

    _emit_progress(
        "layout_generation",
        35,
        "Building spatial context and theme segments.",
        layout_mode=str(config.layout_mode),
    )

    poi_ctx = None
    rule_set = None
    from .poi_rules import PoiContext, build_poi_context
    if placement_ctx is not None:
        poi_ctx = build_poi_context(placement_ctx)
    else:
        poi_ctx = PoiContext((), (), ())
    if poi_ctx is not None:
        rule_set = load_rule_set(config.poi_rule_set)

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
    if road_segment_graph is None and projected is not None:
        road_segment_graph = build_segment_graph(projected, config)
    spatial_ctx = build_spatial_context(config, road_segment_graph, poi_ctx)
    theme_segments = infer_theme_segments(
        road_segment_graph,
        query=config.query,
        target_street_type=config.target_street_type,
        fallback_length_m=float(config.length_m),
    )
    theme_by_id = {segment.theme_id: segment for segment in theme_segments}

    _emit_progress(
        "layout_generation",
        42,
        "Generating street program from layout and guidance.",
        theme_segment_count=len(theme_segments),
        algorithm={
            "theme_inference": "infer_theme_segments",
            "theme_inference_mode": str(getattr(config, "theme_inference_mode", "deterministic_auto")),
            "theme_vocab_name": str(getattr(config, "theme_vocab_name", "fixed_v1")),
            "program_generator_requested": str(config.program_generator),
            "design_rule_profile": str(config.design_rule_profile),
            "objective_profile": str(getattr(config, "objective_profile", "balanced")),
        },
        theme_segments=_compact_theme_segments(theme_segments),
        inventory_category_counts=_compact_mapping(inventory_summary.category_counts, limit=30),
        config_parameters={
            "layout_mode": str(config.layout_mode),
            "length_m": float(config.length_m),
            "road_width_m": float(config.road_width_m),
            "sidewalk_width_m": float(config.sidewalk_width_m),
            "density": float(config.density),
            "ped_demand_level": str(getattr(config, "ped_demand_level", "")),
            "bike_demand_level": str(getattr(config, "bike_demand_level", "")),
            "transit_demand_level": str(getattr(config, "transit_demand_level", "")),
            "vehicle_demand_level": str(getattr(config, "vehicle_demand_level", "")),
        },
    )

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
    if program_result.fallback_reason:
        program_fallback_reasons.append(program_result.fallback_reason)
    base_program = shape_program_for_style(program_result.program, config)
    _emit_progress(
        "layout_generation",
        45,
        "Generated base street program.",
        theme_segment_count=len(theme_segments),
        algorithm={
            "program_generator_requested": str(program_result.backend_requested),
            "program_generator_used": str(program_result.backend_used),
            "fallback_reason": str(program_result.fallback_reason or ""),
            "post_processor": "shape_program_for_style",
        },
        theme_segments=_compact_theme_segments(theme_segments),
        street_program=_compact_program(base_program),
        inventory_category_counts=_compact_mapping(inventory_summary.category_counts, limit=30),
    )
    base_constraint_set = load_constraint_set(config.design_rule_profile)
    solver_runtime = LayoutSolverRuntime(backend=str(config.layout_solver))

    zone_solver_results: List[LayoutSolverResult] = []
    slot_plans: List[object] = []
    slot_segment_lookup: Dict[str, object] = {}
    slot_band_lookup: Dict[str, object] = {}
    theme_zone_programs: List[Dict[str, object]] = []
    composition_pass_reports: List[Dict[str, object]] = []

    _emit_progress(
        "constraint_solving",
        50,
        "Solving themed layout constraints.",
        theme_segment_count=len(theme_segments),
        algorithm={
            "solver_backend_requested": str(config.layout_solver),
            "design_rule_profile": str(config.design_rule_profile),
            "constraint_mode": str(config.constraint_mode),
            "rule_count": int(len(base_constraint_set.rules)),
            "zone_solver_strategy": "solve each theme segment, then aggregate",
        },
        theme_segments=_compact_theme_segments(theme_segments),
        active_constraint_names=[rule.name for rule in list(base_constraint_set.rules)[:40]],
    )

    for theme_segment in theme_segments:
        theme_spec = theme_profile_style(theme_segment.theme_name)
        zone_query = f"{config.query}, {theme_segment.theme_name} streetscape"
        zone_design_rule_profile = (
            str(theme_spec["design_rule_profile"])
            if _is_corridor_layout_mode(config.layout_mode)
            else str(config.design_rule_profile)
        )
        zone_style_preset = (
            str(theme_spec["style_preset"])
            if _is_corridor_layout_mode(config.layout_mode)
            else str(config.style_preset)
        )
        zone_config = replace(
            config,
            query=zone_query,
            length_m=float(max(theme_segment.length_m, min(float(config.segment_length_m), float(config.length_m)))),
            design_rule_profile=zone_design_rule_profile,
            style_preset=zone_style_preset,
            target_street_type=(
                str(theme_segment.theme_name)
                if _is_corridor_layout_mode(config.layout_mode)
                else str(config.target_street_type)
            ),
        )
        zone_program_result = program_runtime.generate(
            ProgramGenerationInput(
                query=zone_query,
                compose_config=zone_config,
                available_categories=tuple(available_categories),
                constraint_profile=str(zone_config.design_rule_profile),
                placement_context=placement_ctx,
                inventory_summary=inventory_summary,
                road_segment_graph=road_segment_graph,
                poi_context=poi_ctx,
            )
        )
        if zone_program_result.backend_used == "learned_v1":
            program_used = "learned_v1"
        if zone_program_result.fallback_reason:
            program_fallback_reasons.append(zone_program_result.fallback_reason)
        zone_program = shape_program_for_style(zone_program_result.program, zone_config)
        zone_constraint_set = load_constraint_set(zone_config.design_rule_profile)
        zone_solver_result = solver_runtime.solve(
            LayoutSolverInput(
                program=zone_program,
                config=zone_config,
                available_categories=tuple(available_categories),
                constraint_set=zone_constraint_set,
                placement_context=placement_ctx,
                inventory_summary=inventory_summary,
                road_segment_graph=road_segment_graph,
            )
        )
        zone_slots = list(zone_solver_result.slot_plans)
        zone_slots, zone_composition = apply_composition_pass(
            zone_slots,
            config=zone_config,
            poi_context=poi_ctx,
        )
        zone_slots, zone_slot_segments = _globalize_theme_slot_plans(
            zone_slots,
            theme_segment=theme_segment,
            road_segment_graph=road_segment_graph,
        )
        zone_solver_result = replace(zone_solver_result, slot_plans=tuple(zone_slots))
        zone_solver_results.append(zone_solver_result)
        slot_plans.extend(zone_slots)
        slot_segment_lookup.update(zone_slot_segments)
        for slot in zone_slots:
            slot_band_lookup[str(slot.slot_id)] = resolve_band_by_alias(
                zone_solver_result.resolved_program.bands,
                band_name=str(getattr(slot, "band_name", "") or ""),
                side=str(getattr(slot, "side", "") or ""),
                profile_name=str(zone_config.design_rule_profile),
            )
            if slot_band_lookup[str(slot.slot_id)] is None:
                slot_band_lookup[str(slot.slot_id)] = resolve_band_by_alias(
                    resolved_program.bands,
                    band_name=str(getattr(slot, "band_name", "") or ""),
                    side=str(getattr(slot, "side", "") or ""),
                    profile_name=str(config.design_rule_profile),
                )
        composition_pass_reports.append(dict(zone_composition))
        theme_zone_programs.append(
            {
                "theme_id": theme_segment.theme_id,
                "theme_name": theme_segment.theme_name,
                "query": zone_query,
                "cross_section_type": zone_solver_result.resolved_program.cross_section_type,
                "design_rule_profile": zone_config.design_rule_profile,
                "style_preset": zone_config.style_preset,
                "slot_count": len(zone_slots),
                "backend_used": zone_program_result.backend_used,
                "solver_backend_used": zone_solver_result.backend_used,
            }
        )

    # -- Inject explicit annotation furniture instances --
    if _is_corridor_layout_mode(config.layout_mode) and road_segment_graph is not None:
        annot_slots, annot_seg_map = _annotation_furniture_to_slot_plans(
            road_segment_graph, theme_segments,
        )
        if annot_slots:
            slot_plans.extend(annot_slots)
            slot_segment_lookup.update(annot_seg_map)
            for aslot in annot_slots:
                slot_band_lookup[str(aslot.slot_id)] = resolve_band_by_alias(
                    base_program.bands if base_program is not None else (),
                    band_name=str(aslot.band_name),
                    side=str(aslot.side),
                    profile_name=str(config.design_rule_profile),
                )
            logger.info("Injected %d annotation furniture slots.", len(annot_slots))

        if "tree" in set(str(category) for category in available_categories):
            center_tree_slots, center_tree_seg_map = _center_planting_tree_slot_plans(
                road_segment_graph=road_segment_graph,
                theme_segments=theme_segments,
                placement_ctx=placement_ctx,
            )
            if center_tree_slots:
                slot_plans.extend(center_tree_slots)
                slot_segment_lookup.update(center_tree_seg_map)
                for cslot in center_tree_slots:
                    slot_band_lookup[str(cslot.slot_id)] = resolve_band_by_alias(
                        base_program.bands if base_program is not None else (),
                        band_name=str(cslot.band_name),
                        side=str(cslot.side),
                        profile_name=str(config.design_rule_profile),
                    ) or resolve_band_by_alias(
                        resolved_program.bands,
                        band_name=str(cslot.band_name),
                        side=str(cslot.side),
                        profile_name=str(config.design_rule_profile),
                    )
                logger.info("Injected %d center planting tree slots.", len(center_tree_slots))

    if not slot_plans:
        raise RuntimeError(
            "Layout solver produced zero slots. "
            "Check the design rule profile, theme inference, asset coverage, or scene length."
        )

    building_strategy_summary = {
        "theme_segment_count": int(len(theme_segments)),
        "theme_names": [segment.theme_name for segment in theme_segments],
        "theme_inference_mode": str(getattr(config, "theme_inference_mode", "deterministic_auto")),
        "theme_vocab_name": str(getattr(config, "theme_vocab_name", "fixed_v1")),
    }
    resolved_program = replace(
        base_program,
        theme_segments=tuple(theme_segments),
        building_strategy_summary=dict(building_strategy_summary),
        notes=tuple(dict.fromkeys(list(base_program.notes) + ["multitheme_street_v1"])),
    )
    graph_summary = (
        road_segment_graph.summary()
        if road_segment_graph is not None and hasattr(road_segment_graph, "summary")
        else None
    )
    if graph_summary is not None:
        graph_summary = {
            **dict(graph_summary),
            "theme_segment_count": int(len(theme_segments)),
            "theme_names": [segment.theme_name for segment in theme_segments],
            "theme_vocab_name": str(getattr(config, "theme_vocab_name", "fixed_v1")),
        }
    solver_result = _aggregate_solver_results(
        resolved_program=resolved_program,
        solver_results=zone_solver_results,
        slot_plans=slot_plans,
        road_segment_graph_summary=graph_summary,
    )
    composition_pass_report = {
        "trimmed_optional_slots": int(sum(int(report.get("trimmed_optional_slots", 0)) for report in composition_pass_reports)),
        "required_slots_preserved": int(sum(int(report.get("required_slots_preserved", 0)) for report in composition_pass_reports)),
        "composition_slot_count": int(sum(int(report.get("composition_slot_count", 0)) for report in composition_pass_reports)),
        "composition_optional_count": int(sum(int(report.get("composition_optional_count", 0)) for report in composition_pass_reports)),
        "theme_segment_count": int(len(theme_segments)),
    }
    _emit_progress(
        "constraint_solving",
        58,
        "Solved layout constraints and produced slot plans.",
        theme_segment_count=len(theme_segments),
        solver_summary=_summarize_solver(solver_result, zone_programs=theme_zone_programs),
        composition_pass_report=dict(composition_pass_report),
        theme_segments=_compact_theme_segments(theme_segments),
    )

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

    placement_field_config = load_placement_field_config()
    spatial_hash = UniformSpatialHash(cell_size_m=float(placement_field_config["cell_size_m"]))

    # Initialize decomposition cache for multi-box collision detection
    # Uses LRU eviction to prevent OOM on large scenes
    from .asset_decomposition import get_decomposition_cache
    decomposition_cache = get_decomposition_cache()
    ordered_slot_plans = sorted(slot_plans, key=_slot_placement_sort_key)
    theme_poi_cache: Dict[str, Dict[str, Tuple[Tuple[float, float], ...]]] = {
        segment.theme_id: _theme_poi_points(
            theme_segment=segment,
            theme_segments=theme_segments,
            poi_ctx=poi_ctx,
            road_segment_graph=road_segment_graph,
        )
        for segment in theme_segments
    }
    category_slot_counts: Dict[str, int] = {}
    for slot in ordered_slot_plans:
        category_slot_counts[slot.category] = category_slot_counts.get(slot.category, 0) + 1
    total_scene_slots = max(len(ordered_slot_plans), 1)
    placement_progress_interval = max(1, total_scene_slots // 10)
    _emit_progress(
        "asset_composition",
        60,
        "Composing street furniture and asset placements.",
        total_slots=total_scene_slots,
        algorithm={
            **_placement_algorithm_detail(),
            "spatial_hash_cell_size_m": float(placement_field_config["cell_size_m"]),
            "tree_species_policy": str(getattr(config, "tree_species_policy", "per_theme_single_species")),
            "furniture_balance_policy": str(getattr(config, "furniture_balance_policy", "overall_balanced")),
        },
        slot_plan_summary=_summarize_slot_plans(ordered_slot_plans),
        category_slot_counts=dict(category_slot_counts),
        composition_pass_report=dict(composition_pass_report),
    )
    placed_score_sums: Dict[str, float] = {category: 0.0 for category in DEFAULT_CATEGORIES}
    placed_counts: Dict[str, int] = {category: 0 for category in DEFAULT_CATEGORIES}
    slot_index_by_category: Dict[str, int] = {category: 0 for category in DEFAULT_CATEGORIES}
    total_required_slots = sum(
        1 for slot in ordered_slot_plans if bool(getattr(slot, "required", False)) or str(getattr(slot, "anchor_poi_type", "") or "").strip()
    )
    realized_required_slots = 0
    anchor_resolution_summary = {
        "total_anchor_slots": int(sum(1 for slot in ordered_slot_plans if str(getattr(slot, "anchor_poi_type", "") or "").strip())),
        "anchored_exact": 0,
        "anchored_relaxed": 0,
        "unplaced_required": 0,
    }
    unplaced_slot_diagnostics: List[Dict[str, object]] = []
    placement_logging_mode = str(getattr(config, "placement_logging_mode", "full_with_ui_summary") or "full_with_ui_summary").strip().lower()
    tree_species_policy = str(getattr(config, "tree_species_policy", "per_theme_single_species") or "per_theme_single_species").strip().lower()
    furniture_balance_policy = str(getattr(config, "furniture_balance_policy", "overall_balanced") or "overall_balanced").strip().lower()
    core_furniture_categories = {
        category
        for category, side_pref in SIDE_PREF.items()
        if str(side_pref) == "both"
    }
    slot_side_by_id = {
        str(slot.slot_id): str(getattr(slot, "side", "") or "")
        for slot in ordered_slot_plans
        if str(getattr(slot, "slot_id", "") or "")
    }
    available_core_categories_by_side: Dict[str, set[str]] = {
        "left": set(),
        "right": set(),
    }
    core_band_candidates_by_side: Dict[str, List[object]] = {
        "left": [],
        "right": [],
    }
    seen_core_band_keys: set[Tuple[str, str, str, int]] = set()
    for band in list(slot_band_lookup.values()) + list(getattr(resolved_program, "bands", ())):
        if band is None:
            continue
        side = str(getattr(band, "side", "") or "")
        if side not in {"left", "right"}:
            continue
        allowed_categories = tuple(
            str(category)
            for category in tuple(getattr(band, "allowed_categories", ()) or ())
            if str(category)
        )
        band_kind = str(getattr(band, "kind", "") or "")
        if allowed_categories:
            compatible_categories = {
                category
                for category in allowed_categories
                if category in core_furniture_categories
            }
            if not compatible_categories:
                continue
            available_core_categories_by_side.setdefault(side, set()).update(compatible_categories)
        elif band_kind not in {"furnishing", "transit_edge"}:
            continue
        band_key = (
            side,
            str(getattr(band, "name", "") or ""),
            band_kind,
            int(round(float(getattr(band, "z_center_m", 0.0) or 0.0) * 1000.0)),
        )
        if band_key in seen_core_band_keys:
            continue
        seen_core_band_keys.add(band_key)
        core_band_candidates_by_side.setdefault(side, []).append(band)
    for slot in ordered_slot_plans:
        category = str(getattr(slot, "category", "") or "")
        side = str(getattr(slot, "side", "") or "")
        if category in core_furniture_categories and side in {"left", "right"}:
            available_core_categories_by_side.setdefault(side, set()).add(category)
    compatible_core_sides = {
        side
        for side, categories in available_core_categories_by_side.items()
        if categories or core_band_candidates_by_side.get(side)
    }
    available_core_categories_global = set(available_core_categories_by_side.get("left", set()))
    available_core_categories_global.update(available_core_categories_by_side.get("right", set()))
    decision_events: List[Dict[str, Any]] = []
    theme_tree_asset_lock: Dict[str, str] = {}
    theme_tree_attempted_assets: Dict[str, List[str]] = {}
    tree_theme_reselection_count = 0
    synthetic_repair_slot_counter = 0
    slot_attempt_records: Dict[str, Dict[str, Any]] = {}
    balance_repair_summary: Dict[str, Any] = {
        "attempt_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "attempted_slot_ids": [],
        "successful_slot_ids": [],
        "failed_slot_ids": [],
        "target_sides": [],
        "synthetic_slot_count": 0,
        "reason": "",
    }

    def _attempt_place_slot(
        slot: object,
        *,
        repair_phase: bool = False,
        excluded_asset_ids: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        nonlocal instance_counter, tree_theme_reselection_count
        category = str(getattr(slot, "category", "") or "")
        theme_id = str(getattr(slot, "theme_id", "") or "")
        anchor_poi_type = str(getattr(slot, "anchor_poi_type", "") or "")
        required_like = bool(getattr(slot, "required", False)) or bool(anchor_poi_type)
        band = slot_band_lookup.get(str(getattr(slot, "slot_id", "")))
        side = str(getattr(slot, "side", "") or (getattr(band, "side", "") if band is not None else ""))
        band_name = str(getattr(slot, "band_name", "") or (getattr(band, "name", "") if band is not None else ""))
        pool = category_to_rows.get(category, [])
        if not pool:
            _append_placement_decision_event(
                decision_events,
                event_type="placement_skipped",
                slot_id=str(getattr(slot, "slot_id", "")),
                category=category,
                theme_id=theme_id,
                side=side,
                band_name=band_name,
                reason_code="no_candidate_after_search",
                reason_detail="category pool empty",
                anchor_poi_type=anchor_poi_type,
                extra={"repair_phase": bool(repair_phase)},
            )
            return {
                "placed": False,
                "required_like": bool(required_like),
                "failure_reason": "no_candidate_after_search",
                "blocked_reason_counts": {},
                "search_tier_reached": "tier_optional_sampling",
                "best_anchor_distance_m": -1.0,
                "side": side,
                "band_name": band_name,
                "theme_id": theme_id,
                "anchor_poi_type": anchor_poi_type,
                "placement_status": _placement_status(None, required=bool(required_like), placed=False),
                "asset_id": "",
            }

        theme_segment = theme_by_id.get(theme_id)
        slot_query = (
            f"{config.query}, {theme_segment.theme_name} streetscape"
            if theme_segment is not None
            else config.query
        )
        asset_id_whitelist: Optional[set[str]] = None
        if category == "tree" and tree_species_policy == "per_theme_single_species" and theme_id:
            locked_asset_id = str(theme_tree_asset_lock.get(theme_id, "") or "")
            if locked_asset_id:
                asset_id_whitelist = {locked_asset_id}
        filtered_pool = (
            [row for row in pool if row["asset_id"] in asset_id_whitelist]
            if asset_id_whitelist
            else list(pool)
        )
        if not filtered_pool:
            filtered_pool = list(pool)

        used_asset_ids_for_pick = set(used_asset_ids_by_category.setdefault(category, set()))
        if excluded_asset_ids:
            used_asset_ids_for_pick.update(str(asset_id) for asset_id in excluded_asset_ids if str(asset_id))

        feature_ctx = PolicyFeatureContext(
            query=slot_query,
            category=category,
            slot_idx=int(slot_index_by_category.get(category, 0)),
            slot_x=float(getattr(slot, "x_center_m", 0.0) or 0.0),
            slot_z=float(getattr(slot, "z_center_m", 0.0) or 0.0),
            length_m=float(config.length_m),
            road_width_m=float(resolved_program.road_width_m),
            sidewalk_width_m=float(resolved_program.sidewalk_width_m),
            lane_count=int(resolved_program.lane_count),
            density=float(config.density),
            topk=int(config.topk_per_category),
            used_asset_ids=used_asset_ids_for_pick,
            placed_count_in_category=placed_counts.get(category, 0),
            total_slots_in_category=category_slot_counts.get(category, 1),
            category_pool_size=len(filtered_pool),
            mean_score_placed=(
                placed_score_sums[category] / placed_counts[category]
                if placed_counts.get(category, 0) > 0
                else 0.0
            ),
            total_slots_in_scene=total_scene_slots,
            **_slot_spatial_kwargs(slot, spatial_ctx),
        )
        try:
            row, score, source, decision_details = _pick_category_candidate(
                query=slot_query,
                category=category,
                topk=config.topk_per_category,
                embedder=embedder,
                index_store=index_store,
                asset_by_id=asset_by_id,
                category_pool=filtered_pool,
                used_asset_ids=used_asset_ids_for_pick,
                rng=rng,
                config=config,
                placement_policy=policy_used,
                policy_runtime=policy_runtime,
                policy_temperature=policy_temperature,
                feature_context=feature_ctx,
                return_details=True,
                asset_id_whitelist=asset_id_whitelist,
                stable_selection_key=(
                    f"{int(getattr(config, 'seed', 0))}:"
                    f"{category}:"
                    f"{theme_id or 'scene'}"
                ),
            )
        except RuntimeError:
            _append_placement_decision_event(
                decision_events,
                event_type="placement_skipped",
                slot_id=str(getattr(slot, "slot_id", "")),
                category=category,
                theme_id=theme_id,
                side=side,
                band_name=band_name,
                reason_code="no_candidate_after_search",
                reason_detail="candidate retrieval failed",
                anchor_poi_type=anchor_poi_type,
                extra={"repair_phase": bool(repair_phase)},
            )
            return {
                "placed": False,
                "required_like": bool(required_like),
                "failure_reason": "no_candidate_after_search",
                "blocked_reason_counts": {},
                "search_tier_reached": "tier_optional_sampling",
                "best_anchor_distance_m": -1.0,
                "side": side,
                "band_name": band_name,
                "theme_id": theme_id,
                "anchor_poi_type": anchor_poi_type,
                "placement_status": _placement_status(None, required=bool(required_like), placed=False),
                "asset_id": "",
            }

        retrieval_predictions.append(
            {
                "target_category": category,
                "theme_id": getattr(slot, "theme_id", ""),
                "hits": decision_details.get("candidates", []),
            }
        )
        _append_placement_decision_event(
            decision_events,
            event_type="candidate_retrieved",
            slot_id=str(getattr(slot, "slot_id", "")),
            category=category,
            theme_id=theme_id,
            side=side,
            band_name=band_name,
            reason_code=str(source),
            reason_detail=f"candidate_count={len(decision_details.get('candidates', []))}",
            candidate_asset_id=str(row["asset_id"]),
            anchor_poi_type=anchor_poi_type,
            extra={
                "repair_phase": bool(repair_phase),
                "top3_hit": bool(decision_details.get("top3_hit", False)),
                "curated_asset_allowlist": bool(decision_details.get("curated_asset_allowlist", False)),
                "allowlist_candidate_count": int(decision_details.get("allowlist_candidate_count", 0) or 0),
                "stable_selection_key": str(decision_details.get("stable_selection_key", "") or ""),
                "tree_locked_asset_id": (
                    str(next(iter(asset_id_whitelist))) if asset_id_whitelist else ""
                ),
            },
        )

        if category == "tree" and theme_id:
            attempted_assets = theme_tree_attempted_assets.setdefault(theme_id, [])
            if str(row["asset_id"]) not in attempted_assets:
                attempted_assets.append(str(row["asset_id"]))

        if band is None:
            strip_kind = detailed_strip_kind_from_band_name(band_name)
            if strip_kind:
                band = StreetBand(
                    name=band_name or detailed_strip_band_name(side, strip_kind),
                    kind=detailed_strip_band_kind(strip_kind, side=side, profile_name=str(config.design_rule_profile)),
                    side=side,
                    width_m=1.5,
                    z_center_m=0.0,
                    allowed_categories=(),
                )
                logger.debug(
                    "Synthesized fallback band for slot %s band_name=%s strip_kind=%s",
                    str(getattr(slot, "slot_id", "")),
                    band_name,
                    strip_kind,
                )
            else:
                _append_placement_decision_event(
                    decision_events,
                    event_type="placement_skipped",
                    slot_id=str(getattr(slot, "slot_id", "")),
                    category=category,
                    theme_id=theme_id,
                    side=side,
                    band_name=band_name,
                    reason_code="no_candidate_after_search",
                    reason_detail="slot band missing",
                    candidate_asset_id=str(row["asset_id"]),
                    anchor_poi_type=anchor_poi_type,
                    extra={"repair_phase": bool(repair_phase)},
                )
                return {
                    "placed": False,
                    "required_like": bool(required_like),
                    "failure_reason": "no_candidate_after_search",
                    "blocked_reason_counts": {},
                    "search_tier_reached": "tier_optional_sampling",
                    "best_anchor_distance_m": -1.0,
                    "side": side,
                    "band_name": band_name,
                    "theme_id": theme_id,
                    "anchor_poi_type": anchor_poi_type,
                    "placement_status": _placement_status(None, required=bool(required_like), placed=False),
                    "asset_id": str(row["asset_id"]),
                }

        entry = mesh_cache.get_metadata(row["asset_id"])
        scale_info = _street_furniture_scale_info(
            category=category,
            entry=entry,
            config=config,
        )
        segment_node = slot_segment_lookup.get(str(getattr(slot, "slot_id", "")))
        candidate_groups = _iter_slot_candidate_groups(
            slot=slot,
            category=category,
            config=config,
            placement_ctx=placement_ctx,
            segment_node=segment_node,
            theme_segment=theme_segment,
            road_segment_graph=road_segment_graph,
            band_width_m=float(getattr(band, "width_m", 1.0)),
            rng=rng,
        )
        blocked_reason_counts = {
            "intrudes_carriageway": 0,
            "overlap_blocked": 0,
            "constraint_vetoed": 0,
            "out_of_sidewalk": 0,
            "out_of_target_strip": 0,
            "out_of_theme_range": 0,
            "side_mismatch": 0,
            "scale_gate_failed": 0,
            "no_candidate_after_search": 0,
        }
        chosen_candidate: Optional[Dict[str, object]] = None
        best_anchor_distance_m = float("inf")
        search_tier_reached = ""
        for candidate_group in candidate_groups:
            if not candidate_group:
                continue
            search_tier_reached = str(candidate_group[0]["tier"])
            feasible_candidates: List[Dict[str, object]] = []
            for candidate in candidate_group:
                if candidate.get("anchor_distance_m") is not None:
                    best_anchor_distance_m = min(best_anchor_distance_m, float(candidate["anchor_distance_m"]))
                resolved_candidate, blocked_reason = _evaluate_slot_candidate(
                    candidate=candidate,
                    slot=slot,
                    category=category,
                    band_width_m=float(getattr(band, "width_m", 1.0)),
                    entry=entry,
                    scale_info=scale_info,
                    placements=placements,
                    spatial_hash=spatial_hash,
                    existing_bboxes=existing_bboxes,
                    placement_ctx=placement_ctx,
                    theme_segment=theme_segment,
                    road_segment_graph=road_segment_graph,
                    theme_poi_points=theme_poi_cache.get(theme_id, {}),
                    poi_ctx=poi_ctx,
                    rule_set=rule_set,
                    config=config,
                    entrance_registry=entrance_registry,
                    carriageway_boundary=carriageway_boundary,
                    entrance_points_xz=entrance_points_xz,
                    segment_node=segment_node,
                    decomposition_cache=decomposition_cache,
                )
                if blocked_reason is not None:
                    blocked_reason_counts[blocked_reason] = blocked_reason_counts.get(blocked_reason, 0) + 1
                    _append_placement_decision_event(
                        decision_events,
                        event_type="candidate_rejected",
                        slot_id=str(getattr(slot, "slot_id", "")),
                        category=category,
                        theme_id=theme_id,
                        side=side,
                        band_name=band_name,
                        reason_code=str(blocked_reason),
                        reason_detail="candidate pose rejected",
                        candidate_asset_id=str(row["asset_id"]),
                        anchor_poi_type=anchor_poi_type,
                        blocked_reason=str(blocked_reason),
                        search_tier=search_tier_reached,
                        extra={"repair_phase": bool(repair_phase)},
                    )
                    continue
                assert resolved_candidate is not None
                feasible_candidates.append(resolved_candidate)
            if feasible_candidates:
                chosen_candidate = max(
                    feasible_candidates,
                    key=lambda item: (
                        float(item["placement_energy"]),
                        -float(item["anchor_distance_m"]) if item.get("anchor_distance_m") is not None else 0.0,
                        -abs(float(item["x"]) - float(getattr(slot, "x_center_m", 0.0) or 0.0)),
                        -abs(float(item["z"]) - float(getattr(slot, "z_center_m", 0.0) or 0.0)),
                    ),
                )
                break

        if chosen_candidate is None:
            blocked_nonzero = {
                key: int(value)
                for key, value in blocked_reason_counts.items()
                if int(value) > 0
            }
            failure_reason = (
                sorted(
                    blocked_nonzero.items(),
                    key=lambda item: (-int(item[1]), item[0]),
                )[0][0]
                if blocked_nonzero
                else "no_candidate_after_search"
            )
            _append_placement_decision_event(
                decision_events,
                event_type="placement_skipped",
                slot_id=str(getattr(slot, "slot_id", "")),
                category=category,
                theme_id=theme_id,
                side=side,
                band_name=band_name,
                reason_code=str(failure_reason),
                reason_detail="no feasible candidate survived evaluation",
                candidate_asset_id=str(row["asset_id"]),
                anchor_poi_type=anchor_poi_type,
                blocked_reason=str(failure_reason),
                search_tier=search_tier_reached or "tier_optional_sampling",
                extra={
                    "repair_phase": bool(repair_phase),
                    "blocked_reason_counts": blocked_nonzero,
                },
            )
            return {
                "placed": False,
                "required_like": bool(required_like),
                "failure_reason": str(failure_reason),
                "blocked_reason_counts": blocked_nonzero,
                "search_tier_reached": search_tier_reached or "tier_optional_sampling",
                "best_anchor_distance_m": float(best_anchor_distance_m) if math.isfinite(best_anchor_distance_m) else -1.0,
                "side": side,
                "band_name": band_name,
                "theme_id": theme_id,
                "anchor_poi_type": anchor_poi_type,
                "placement_status": _placement_status(None, required=bool(required_like), placed=False),
                "asset_id": str(row["asset_id"]),
            }

        bx = float(chosen_candidate["x"])
        bz = float(chosen_candidate["z"])
        byaw = float(chosen_candidate["yaw_deg"])
        bbbox = tuple(float(value) for value in chosen_candidate["bbox"])
        bpenalty = float(chosen_candidate["constraint_penalty"])
        bfeas = float(chosen_candidate["feasibility_score"])
        bviolated = tuple(chosen_candidate["violated_rules"])
        bscale = float(chosen_candidate.get("scale", 1.0) or 1.0)
        anchor_distance_m = (
            float(chosen_candidate["anchor_distance_m"])
            if chosen_candidate.get("anchor_distance_m") is not None
            else None
        )
        existing_bboxes.append(bbbox)
        spatial_hash.insert(bbbox, len(existing_bboxes) - 1)
        y = -entry.min_y * bscale
        placement_status = _placement_status(
            anchor_distance_m,
            required=bool(required_like),
            placed=True,
        )
        placement = StreetPlacement(
            instance_id=f"inst_{instance_counter:04d}",
            asset_id=row["asset_id"],
            category=category,
            score=float(score),
            position_xyz=[float(bx), float(y), float(bz)],
            yaw_deg=float(byaw),
            scale=float(bscale),
            bbox_xz=[float(bbbox[0]), float(bbbox[1]), float(bbbox[2]), float(bbbox[3])],
            selection_source=source,
            slot_id=str(getattr(slot, "slot_id", "")),
            required=bool(getattr(slot, "required", False)),
            theme_id=theme_id,
            anchor_poi_type=anchor_poi_type,
            anchor_geom_id=str(band_name),
            anchor_target_xz=(
                tuple(float(v) for v in getattr(slot, "anchor_position_xz"))
                if getattr(slot, "anchor_position_xz", None) is not None
                else None
            ),
            anchor_distance_m=float(anchor_distance_m) if anchor_distance_m is not None else -1.0,
            placement_energy=float(chosen_candidate["placement_energy"]),
            placement_status=placement_status,
            native_size_m=dict(chosen_candidate.get("native_size_m", {}) or {}),
            raw_size_m=dict(chosen_candidate.get("raw_size_m", {}) or {}),
            metric_size_m=dict(chosen_candidate.get("metric_size_m", {}) or {}),
            final_size_m=dict(chosen_candidate.get("final_size_m", {}) or {}),
            canonical_target=dict(chosen_candidate.get("canonical_target", {}) or {}),
            asset_scale_mode=str(chosen_candidate.get("asset_scale_mode", "")),
            scale_fallback_used=bool(chosen_candidate.get("scale_fallback_used", False)),
            source_scale=float(chosen_candidate.get("source_scale", 1.0) or 1.0),
            source_scale_source=str(chosen_candidate.get("source_scale_source", "") or ""),
            source_scale_confidence=str(chosen_candidate.get("source_scale_confidence", "") or ""),
            source_scale_rejected_reason=str(chosen_candidate.get("source_scale_rejected_reason", "") or ""),
            scale_gate_failed=bool(chosen_candidate.get("scale_gate_failed", False)),
            scale_gate_reason=str(chosen_candidate.get("scale_gate_reason", "") or ""),
            constraint_penalty=float(bpenalty),
            feasibility_score=float(bfeas),
            violated_rules=bviolated,
            **_slot_spatial_kwargs(slot, spatial_ctx),
        )
        placements.append(placement)
        used_asset_ids_by_category.setdefault(category, set()).add(row["asset_id"])
        placed_score_sums[category] = placed_score_sums.get(category, 0.0) + float(score)
        placed_counts[category] = placed_counts.get(category, 0) + 1
        instance_counter += 1
        entrance_registry.add(
            position_xz=(float(bx), float(bz)),
            category=category,
            bbox_xz=(float(bbbox[0]), float(bbbox[1]), float(bbbox[2]), float(bbbox[3])),
        )
        if category == "tree" and theme_id and tree_species_policy == "per_theme_single_species":
            if theme_id not in theme_tree_asset_lock:
                first_attempt = theme_tree_attempted_assets.get(theme_id, [str(row["asset_id"])])[0]
                theme_tree_asset_lock[theme_id] = str(row["asset_id"])
                if str(first_attempt) != str(row["asset_id"]):
                    tree_theme_reselection_count += 1
                    _append_placement_decision_event(
                        decision_events,
                        event_type="tree_theme_lock_reselected",
                        slot_id=str(getattr(slot, "slot_id", "")),
                        category=category,
                        theme_id=theme_id,
                        side=side,
                        band_name=band_name,
                        reason_code="per_theme_single_species",
                        reason_detail=f"theme tree lock reselected to {row['asset_id']}",
                        candidate_asset_id=str(row["asset_id"]),
                        anchor_poi_type=anchor_poi_type,
                    )
                else:
                    _append_placement_decision_event(
                        decision_events,
                        event_type="tree_theme_lock_created",
                        slot_id=str(getattr(slot, "slot_id", "")),
                        category=category,
                        theme_id=theme_id,
                        side=side,
                        band_name=band_name,
                        reason_code="per_theme_single_species",
                        reason_detail=f"theme tree lock created for {row['asset_id']}",
                        candidate_asset_id=str(row["asset_id"]),
                        anchor_poi_type=anchor_poi_type,
                    )
        _append_placement_decision_event(
            decision_events,
            event_type="placement_selected",
            slot_id=str(getattr(slot, "slot_id", "")),
            category=category,
            theme_id=theme_id,
            side=side,
            band_name=band_name,
            reason_code="feasible_candidate_selected",
            reason_detail=str(placement_status),
            candidate_asset_id=str(row["asset_id"]),
            anchor_poi_type=anchor_poi_type,
            placement_energy=float(chosen_candidate["placement_energy"]),
            feasibility_score=float(bfeas),
            violated_rules=bviolated,
            search_tier=search_tier_reached,
            extra={"repair_phase": bool(repair_phase)},
        )
        return {
            "placed": True,
            "required_like": bool(required_like),
            "failure_reason": "",
            "blocked_reason_counts": {},
            "search_tier_reached": search_tier_reached,
            "best_anchor_distance_m": float(best_anchor_distance_m) if math.isfinite(best_anchor_distance_m) else -1.0,
            "side": side,
            "band_name": band_name,
            "theme_id": theme_id,
            "anchor_poi_type": anchor_poi_type,
            "placement_status": str(placement_status),
            "placement": placement,
            "asset_id": str(row["asset_id"]),
        }

    def _repair_band_for_side(
        category: str,
        target_side: str,
        *,
        source_slot: object | None = None,
    ) -> object | None:
        source_band = None
        if source_slot is not None:
            source_band = slot_band_lookup.get(str(getattr(source_slot, "slot_id", "") or ""))
        candidates: List[Tuple[Tuple[int, float, float, str], object]] = []
        for band in core_band_candidates_by_side.get(str(target_side), []):
            allowed_categories = {
                str(value)
                for value in tuple(getattr(band, "allowed_categories", ()) or ())
                if str(value)
            }
            if allowed_categories and str(category) not in allowed_categories:
                continue
            band_kind = str(getattr(band, "kind", "") or "")
            source_kind = str(getattr(source_band, "kind", "") or "")
            z_distance = abs(
                abs(float(getattr(band, "z_center_m", 0.0) or 0.0))
                - abs(float(getattr(source_slot, "z_center_m", 0.0) or 0.0))
            )
            candidates.append(
                (
                    (
                        0 if source_kind and band_kind == source_kind else 1,
                        float(z_distance),
                        abs(float(getattr(band, "width_m", 1.0) or 1.0) - float(getattr(source_band, "width_m", 1.0) or 1.0)),
                        str(getattr(band, "name", "") or ""),
                    ),
                    band,
                )
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _repair_x_center_for_side(target_side: str, preferred_x: float) -> float:
        existing_x = [
            float(placement.position_xyz[0])
            for placement in placements
            if str(placement.placement_group) == "street_furniture"
            and _slot_side_for_placement(placement, slot_side_by_id=slot_side_by_id) == str(target_side)
        ]
        length_m = float(config.length_m)
        candidate_xs = {float(preferred_x)}
        grid_count = max(10, len(existing_x) + 6)
        step = length_m / float(max(grid_count, 1))
        for idx in range(grid_count):
            candidate_xs.add(-length_m / 2.0 + (idx + 0.5) * step)
        return max(
            candidate_xs,
            key=lambda value: (
                min(abs(float(value) - item) for item in existing_x) if existing_x else float("inf"),
                min(float(value) + length_m / 2.0, length_m / 2.0 - float(value)),
                -abs(float(value) - float(preferred_x)),
            ),
        )

    def _make_synthetic_repair_record(
        source_record: Mapping[str, Any],
        *,
        target_side: str,
        category_override: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        nonlocal synthetic_repair_slot_counter
        source_slot = source_record.get("slot")
        if source_slot is None:
            return None
        category = str(category_override or source_record.get("category", "") or "")
        if category not in core_furniture_categories:
            return None
        if str(source_record.get("anchor_poi_type", "") or "").strip():
            return None
        target_band = _repair_band_for_side(category, str(target_side), source_slot=source_slot)
        if target_band is None:
            return None
        synthetic_repair_slot_counter += 1
        source_slot_id = str(source_record.get("slot_id", "") or getattr(source_slot, "slot_id", ""))
        synthetic_slot_id = (
            f"{source_slot_id}__side_swap_{target_side}_{synthetic_repair_slot_counter:03d}"
        )
        repair_x_center = _repair_x_center_for_side(
            str(target_side),
            float(getattr(source_slot, "x_center_m", 0.0) or 0.0),
        )
        synthetic_slot = LayoutSlotPlan(
            slot_id=synthetic_slot_id,
            category=category,
            band_name=str(getattr(target_band, "name", "") or ""),
            x_center_m=float(repair_x_center),
            z_center_m=float(getattr(target_band, "z_center_m", 0.0) or 0.0),
            spacing_m=float(getattr(source_slot, "spacing_m", 1.0) or 1.0),
            side=str(target_side),
            priority=float(getattr(source_slot, "priority", 0.0) or 0.0),
            required=False,
            anchor_poi_type="",
            anchor_position_xz=None,
            theme_id=str(getattr(source_slot, "theme_id", "") or source_record.get("theme_id", "") or ""),
        )
        slot_band_lookup[synthetic_slot_id] = target_band
        slot_side_by_id[synthetic_slot_id] = str(target_side)
        if source_slot_id in slot_segment_lookup:
            slot_segment_lookup[synthetic_slot_id] = slot_segment_lookup[source_slot_id]
        synthetic_record: Dict[str, Any] = {
            "slot": synthetic_slot,
            "slot_id": synthetic_slot_id,
            "category": category,
            "theme_id": str(getattr(synthetic_slot, "theme_id", "") or ""),
            "side": str(target_side),
            "band_name": str(getattr(target_band, "name", "") or ""),
            "anchor_poi_type": "",
            "required_like": False,
            "placed": False,
            "placement_status": "",
            "failure_reason": "",
            "blocked_reason_counts": {},
            "search_tier_reached": "",
            "best_anchor_distance_m": -1.0,
            "attempted_asset_ids": set(),
            "required_failure_counted": False,
            "unplaced_diagnostic": None,
        }
        slot_attempt_records[synthetic_slot_id] = synthetic_record
        balance_repair_summary["synthetic_slot_count"] = int(balance_repair_summary.get("synthetic_slot_count", 0)) + 1
        _append_placement_decision_event(
            decision_events,
            event_type="slot_repaired_side_swap",
            slot_id=synthetic_slot_id,
            category=category,
            theme_id=str(getattr(synthetic_slot, "theme_id", "") or ""),
            side=str(target_side),
            band_name=str(getattr(target_band, "name", "") or ""),
            reason_code="overall_balanced",
            reason_detail=f"mirrored from {source_slot_id} to {target_side}",
            extra={
                "source_slot_id": source_slot_id,
                "source_side": str(source_record.get("side", "") or ""),
                "source_band_name": str(source_record.get("band_name", "") or ""),
                "repair_x_center_m": float(repair_x_center),
            },
        )
        return synthetic_record

    for slot_index, slot in enumerate(ordered_slot_plans, start=1):
        _append_placement_decision_event(
            decision_events,
            event_type="slot_generated",
            slot_id=str(getattr(slot, "slot_id", "")),
            category=str(getattr(slot, "category", "") or ""),
            theme_id=str(getattr(slot, "theme_id", "") or ""),
            side=str(getattr(slot, "side", "") or ""),
            band_name=str(getattr(slot, "band_name", "") or ""),
            reason_code="solver_slot_plan",
            reason_detail="slot emitted by layout solver",
            anchor_poi_type=str(getattr(slot, "anchor_poi_type", "") or ""),
            extra={
                "priority": float(getattr(slot, "priority", 0.0) or 0.0),
                "required": bool(getattr(slot, "required", False)),
            },
        )
        result = _attempt_place_slot(slot)
        category = str(getattr(slot, "category", "") or "")
        required_like = bool(result.get("required_like", False))
        if bool(result.get("placed", False)):
            if required_like:
                realized_required_slots += 1
            if str(result.get("placement_status", "")) == "anchored_exact":
                anchor_resolution_summary["anchored_exact"] += 1
            elif str(result.get("placement_status", "")) == "anchored_relaxed":
                anchor_resolution_summary["anchored_relaxed"] += 1
        else:
            dropped_slots += 1
        attempt_record: Dict[str, Any] = {
            "slot": slot,
            "slot_id": str(getattr(slot, "slot_id", "")),
            "category": category,
            "theme_id": str(result.get("theme_id", getattr(slot, "theme_id", "") or "")),
            "side": str(result.get("side", getattr(slot, "side", "") or "")),
            "band_name": str(result.get("band_name", getattr(slot, "band_name", "") or "")),
            "anchor_poi_type": str(result.get("anchor_poi_type", getattr(slot, "anchor_poi_type", "") or "")),
            "required_like": bool(required_like),
            "placed": bool(result.get("placed", False)),
            "placement_status": str(result.get("placement_status", "")),
            "failure_reason": str(result.get("failure_reason", "") or ""),
            "blocked_reason_counts": dict(result.get("blocked_reason_counts", {}) or {}),
            "search_tier_reached": str(result.get("search_tier_reached", "") or ""),
            "best_anchor_distance_m": float(result.get("best_anchor_distance_m", -1.0) or -1.0),
            "attempted_asset_ids": set(
                [str(result.get("asset_id", ""))]
                if str(result.get("asset_id", "")).strip()
                else []
            ),
            "required_failure_counted": False,
            "unplaced_diagnostic": None,
        }
        if not bool(result.get("placed", False)) and required_like:
            blocked_nonzero = {
                key: int(value)
                for key, value in dict(result.get("blocked_reason_counts", {}) or {}).items()
                if int(value) > 0
            }
            anchor_resolution_summary["unplaced_required"] += 1
            attempt_record["required_failure_counted"] = True
            attempt_record["unplaced_diagnostic"] = {
                "slot_id": str(getattr(slot, "slot_id", "")),
                "category": str(category),
                "theme_id": str(getattr(slot, "theme_id", "") or ""),
                "anchor_poi_type": str(getattr(slot, "anchor_poi_type", "") or ""),
                "search_tier_reached": str(result.get("search_tier_reached", "") or "tier_optional_sampling"),
                "best_anchor_distance_m": float(result.get("best_anchor_distance_m", -1.0) or -1.0),
                "failure_reason": str(result.get("failure_reason", "no_candidate_after_search") or "no_candidate_after_search"),
                "blocked_reason_counts": blocked_nonzero,
            }
        slot_attempt_records[str(getattr(slot, "slot_id", ""))] = attempt_record
        slot_index_by_category[category] = slot_index_by_category.get(category, 0) + 1
        if slot_index == total_scene_slots or slot_index % placement_progress_interval == 0:
            progress = 60 + int(round((slot_index / total_scene_slots) * 12))
            _emit_progress(
                "asset_composition",
                min(progress, 72),
                "Placing street assets.",
                placed_slots=slot_index,
                total_slots=total_scene_slots,
                placement_progress={
                    "processed_slots": int(slot_index),
                    "total_slots": int(total_scene_slots),
                    "placed_count": int(len(placements)),
                    "dropped_slots": int(dropped_slots),
                    "placed_counts_by_category": {
                        key: int(value)
                        for key, value in placed_counts.items()
                        if int(value) > 0
                    },
                },
                blocker_summary=_summarize_attempt_records(slot_attempt_records),
                algorithm=_placement_algorithm_detail(),
            )

    def _balance_targets_met() -> bool:
        if furniture_balance_policy != "overall_balanced":
            return True
        if not {"left", "right"} <= compatible_core_sides:
            return True
        balance_state = _street_furniture_balance_state(placements, slot_side_by_id=slot_side_by_id)
        counts = balance_state["street_furniture_core_side_counts"]
        category_counts = balance_state["street_furniture_core_category_count_by_side"]
        total = int(balance_state["core_total_count"])
        diff = abs(int(counts.get("left", 0)) - int(counts.get("right", 0)))
        global_categories = set(balance_state["street_furniture_core_categories_by_side"].get("left", []))
        global_categories.update(balance_state["street_furniture_core_categories_by_side"].get("right", []))
        global_target = min(3, len(available_core_categories_global), total)
        left_target = min(
            2,
            len(available_core_categories_by_side.get("left", set())),
            int(counts.get("left", 0)),
        )
        right_target = min(
            2,
            len(available_core_categories_by_side.get("right", set())),
            int(counts.get("right", 0)),
        )
        if int(counts.get("left", 0)) <= 0 or int(counts.get("right", 0)) <= 0:
            return False
        if total >= 4 and diff > 2:
            return False
        if total >= 6 and (diff / max(total, 1)) > 0.25:
            return False
        if global_target >= 3 and len(global_categories) < global_target:
            return False
        if left_target >= 2 and int(category_counts.get("left", 0)) < left_target:
            return False
        if right_target >= 2 and int(category_counts.get("right", 0)) < right_target:
            return False
        return True

    def _target_repair_side() -> str:
        balance_state = _street_furniture_balance_state(placements, slot_side_by_id=slot_side_by_id)
        counts = balance_state["street_furniture_core_side_counts"]
        category_counts = balance_state["street_furniture_core_category_count_by_side"]
        left_count = int(counts.get("left", 0))
        right_count = int(counts.get("right", 0))
        if left_count <= 0:
            return "left"
        if right_count <= 0:
            return "right"
        if left_count != right_count:
            return "left" if left_count < right_count else "right"
        return "left" if int(category_counts.get("left", 0)) < int(category_counts.get("right", 0)) else "right"

    if furniture_balance_policy == "overall_balanced" and {"left", "right"} <= compatible_core_sides:
        core_slot_total = sum(
            1
            for slot in ordered_slot_plans
            if str(getattr(slot, "category", "") or "") in core_furniture_categories
            and str(getattr(slot, "side", "") or "") in {"left", "right"}
        )
        max_repair_rounds = max(
            6,
            len(
                [
                    record
                    for record in slot_attempt_records.values()
                    if not bool(record.get("placed", False))
                    and str(record.get("category", "")) in core_furniture_categories
                    and str(record.get("side", "")) in {"left", "right"}
                    and not str(record.get("anchor_poi_type", "")).strip()
                ]
            ),
            min(24, int(core_slot_total)),
        )
        for _ in range(max_repair_rounds):
            if _balance_targets_met():
                break
            target_side = _target_repair_side()
            dense_side = "left" if str(target_side) == "right" else "right"
            balance_repair_summary["target_sides"].append(str(target_side))
            balance_state = _street_furniture_balance_state(placements, slot_side_by_id=slot_side_by_id)
            global_missing_categories = [
                category
                for category in ("lamp", "tree", "bench", "trash", "bollard")
                if category in available_core_categories_global
                and category
                not in (
                    set(balance_state["street_furniture_core_categories_by_side"].get("left", []))
                    | set(balance_state["street_furniture_core_categories_by_side"].get("right", []))
                )
            ]
            missing_categories = [
                category
                for category in ("lamp", "tree", "bench", "trash", "bollard")
                if category in available_core_categories_by_side.get(target_side, set())
                and category not in balance_state["street_furniture_core_categories_by_side"].get(target_side, [])
            ]
            candidate_records = [
                record
                for record in slot_attempt_records.values()
                if not bool(record.get("placed", False))
                and str(record.get("category", "")) in core_furniture_categories
                and str(record.get("side", "")) == str(target_side)
                and not str(record.get("anchor_poi_type", "")).strip()
                and "__side_swap_" not in str(record.get("slot_id", ""))
            ]
            candidate_records.sort(
                key=lambda record: (
                    0
                    if str(record.get("category", "")) in global_missing_categories
                    else 1,
                    0
                    if str(record.get("category", "")) in missing_categories
                    else 1,
                    {
                        "lamp": 0,
                        "tree": 1,
                        "bench": 2,
                        "trash": 3,
                        "bollard": 4,
                    }.get(str(record.get("category", "")), 99),
                    float(getattr(record.get("slot"), "x_center_m", 0.0) or 0.0),
                )
            )
            synthetic_candidates: List[Dict[str, Any]] = []
            synthetic_source_records = [
                record
                for record in slot_attempt_records.values()
                if str(record.get("category", "")) in core_furniture_categories
                and str(record.get("side", "")) == str(dense_side)
                and not str(record.get("anchor_poi_type", "")).strip()
            ]
            synthetic_source_records.extend(
                [
                    record
                    for record in slot_attempt_records.values()
                    if str(record.get("category", "")) in global_missing_categories
                    and str(record.get("side", "")) in {"left", "right"}
                    and not str(record.get("anchor_poi_type", "")).strip()
                ]
            )
            synthetic_source_records.sort(
                key=lambda record: (
                    0 if str(record.get("category", "")) in global_missing_categories else 1,
                    0 if str(record.get("category", "")) in missing_categories else 1,
                    0 if str(record.get("side", "")) == str(dense_side) else 1,
                    0 if bool(record.get("placed", False)) else 1,
                    {
                        "lamp": 0,
                        "tree": 1,
                        "bench": 2,
                        "trash": 3,
                        "bollard": 4,
                    }.get(str(record.get("category", "")), 99),
                    float(getattr(record.get("slot"), "x_center_m", 0.0) or 0.0),
                )
            )
            synthetic_budget = max(2, min(10, len(missing_categories) + 4))
            for source_record in synthetic_source_records:
                if len(synthetic_candidates) >= synthetic_budget:
                    break
                synthetic_record = _make_synthetic_repair_record(
                    source_record,
                    target_side=str(target_side),
                )
                if synthetic_record is None:
                    continue
                synthetic_candidates.append(synthetic_record)
            dense_reference_records = [
                record
                for record in slot_attempt_records.values()
                if bool(record.get("placed", False))
                and str(record.get("side", "")) == str(dense_side)
                and not str(record.get("anchor_poi_type", "")).strip()
            ]
            dense_reference_records.sort(
                key=lambda record: (
                    0 if str(record.get("category", "")) in {"lamp", "tree", "bench", "trash", "bollard"} else 1,
                    float(getattr(record.get("slot"), "x_center_m", 0.0) or 0.0),
                )
            )
            for missing_category in global_missing_categories:
                if missing_category not in available_core_categories_by_side.get(target_side, set()):
                    continue
                if len(synthetic_candidates) >= synthetic_budget:
                    break
                reference_record = next(iter(dense_reference_records), None)
                if reference_record is None:
                    break
                synthetic_record = _make_synthetic_repair_record(
                    reference_record,
                    target_side=str(target_side),
                    category_override=str(missing_category),
                )
                if synthetic_record is None:
                    continue
                synthetic_candidates.append(synthetic_record)
            candidate_records.extend(synthetic_candidates)
            candidate_records.sort(
                key=lambda record: (
                    0
                    if str(record.get("category", "")) in global_missing_categories
                    else 1,
                    0
                    if str(record.get("category", "")) in missing_categories
                    else 1,
                    0 if "__side_swap_" in str(record.get("slot_id", "")) else 1,
                    {
                        "lamp": 0,
                        "tree": 1,
                        "bench": 2,
                        "trash": 3,
                        "bollard": 4,
                    }.get(str(record.get("category", "")), 99),
                    float(getattr(record.get("slot"), "x_center_m", 0.0) or 0.0),
                )
            )
            if not candidate_records:
                balance_repair_summary["reason"] = f"all repair candidates blocked on {target_side}"
                break
            made_progress = False
            for record in candidate_records:
                balance_repair_summary["attempt_count"] += 1
                balance_repair_summary["attempted_slot_ids"].append(str(record.get("slot_id", "")))
                _append_placement_decision_event(
                    decision_events,
                    event_type="balance_repair_attempt",
                    slot_id=str(record.get("slot_id", "")),
                    category=str(record.get("category", "")),
                    theme_id=str(record.get("theme_id", "")),
                    side=str(record.get("side", "")),
                    band_name=str(record.get("band_name", "")),
                    reason_code="overall_balanced",
                    reason_detail=f"attempt repair on {target_side}",
                    anchor_poi_type=str(record.get("anchor_poi_type", "")),
                )
                repair_result = _attempt_place_slot(
                    record["slot"],
                    repair_phase=True,
                    excluded_asset_ids=set(record.get("attempted_asset_ids", set()) or set()),
                )
                asset_id = str(repair_result.get("asset_id", "") or "")
                if asset_id:
                    record.setdefault("attempted_asset_ids", set()).add(asset_id)
                record["failure_reason"] = str(repair_result.get("failure_reason", "") or "")
                record["blocked_reason_counts"] = dict(repair_result.get("blocked_reason_counts", {}) or {})
                record["search_tier_reached"] = str(repair_result.get("search_tier_reached", "") or "")
                record["best_anchor_distance_m"] = float(repair_result.get("best_anchor_distance_m", -1.0) or -1.0)
                if bool(repair_result.get("placed", False)):
                    record["placed"] = True
                    record["placement_status"] = str(repair_result.get("placement_status", ""))
                    record["unplaced_diagnostic"] = None
                    if dropped_slots > 0:
                        dropped_slots -= 1
                    if bool(record.get("required_failure_counted", False)):
                        anchor_resolution_summary["unplaced_required"] = max(
                            0,
                            int(anchor_resolution_summary["unplaced_required"]) - 1,
                        )
                        realized_required_slots += 1
                        record["required_failure_counted"] = False
                    balance_repair_summary["success_count"] += 1
                    balance_repair_summary["successful_slot_ids"].append(str(record.get("slot_id", "")))
                    _append_placement_decision_event(
                        decision_events,
                        event_type="balance_repair_selected",
                        slot_id=str(record.get("slot_id", "")),
                        category=str(record.get("category", "")),
                        theme_id=str(record.get("theme_id", "")),
                        side=str(record.get("side", "")),
                        band_name=str(record.get("band_name", "")),
                        reason_code="overall_balanced",
                        reason_detail=f"repair succeeded on {target_side}",
                        candidate_asset_id=str(asset_id),
                        anchor_poi_type=str(record.get("anchor_poi_type", "")),
                    )
                    made_progress = True
                    break
                balance_repair_summary["failure_count"] += 1
                balance_repair_summary["failed_slot_ids"].append(str(record.get("slot_id", "")))
                _append_placement_decision_event(
                    decision_events,
                    event_type="balance_repair_failed",
                    slot_id=str(record.get("slot_id", "")),
                    category=str(record.get("category", "")),
                    theme_id=str(record.get("theme_id", "")),
                    side=str(record.get("side", "")),
                    band_name=str(record.get("band_name", "")),
                    reason_code=str(repair_result.get("failure_reason", "no_candidate_after_search") or "no_candidate_after_search"),
                    reason_detail=f"repair failed on {target_side}",
                    candidate_asset_id=str(asset_id),
                    anchor_poi_type=str(record.get("anchor_poi_type", "")),
                )
            if not made_progress:
                if not str(balance_repair_summary.get("reason", "") or "").strip():
                    balance_repair_summary["reason"] = f"all repair candidates blocked on {target_side}"
                break

    unplaced_slot_diagnostics = [
        dict(record["unplaced_diagnostic"])
        for record in slot_attempt_records.values()
        if record.get("unplaced_diagnostic")
    ]

    _emit_progress(
        "asset_composition",
        73,
        "Finished asset placement and interception checks.",
        placed_slots=int(total_scene_slots),
        total_slots=int(total_scene_slots),
        placement_progress={
            "processed_slots": int(total_scene_slots),
            "total_slots": int(total_scene_slots),
            "placed_count": int(len(placements)),
            "dropped_slots": int(dropped_slots),
            "placed_counts_by_category": {
                key: int(value)
                for key, value in placed_counts.items()
                if int(value) > 0
            },
        },
        blocker_summary=_summarize_attempt_records(slot_attempt_records, sample_limit=20),
        anchor_resolution_summary={
            **dict(anchor_resolution_summary),
            "total_required_slots": int(total_required_slots),
            "realized_required_slots": int(realized_required_slots),
        },
        balance_repair_summary=dict(balance_repair_summary),
        algorithm={
            **_placement_algorithm_detail(),
            "spatial_hash_cell_size_m": float(placement_field_config["cell_size_m"]),
            "tree_species_policy": str(getattr(config, "tree_species_policy", "per_theme_single_species")),
            "furniture_balance_policy": str(getattr(config, "furniture_balance_policy", "overall_balanced")),
        },
    )

    if not placements:
        raise RuntimeError(
            "Street composition produced zero furniture placements. "
            "Try a different design-rule profile, larger length/density, or check category coverage in manifest."
        )

    _emit_progress(
        "mesh_generation",
        76,
        "Generating surrounding buildings and base scene meshes.",
        placement_count=len(placements),
    )

    surrounding_buildings = _place_surrounding_buildings(
        config=config,
        projected_features=projected,
        placement_ctx=placement_ctx,
        road_segment_graph=road_segment_graph,
        theme_segments=theme_segments,
        resolved_program=resolved_program,
        embedder=embedder,
        index_store=index_store,
        asset_by_id=asset_by_id,
        mesh_cache=mesh_cache,
        rng=rng,
        start_instance_index=instance_counter,
    )
    building_footprints = surrounding_buildings.building_footprints
    generated_lots = surrounding_buildings.generated_lots
    building_plans = list(surrounding_buildings.plans)
    building_retrieval_predictions = list(surrounding_buildings.retrieval_predictions)
    building_summary = dict(surrounding_buildings.building_summary)
    land_use_summary = dict(surrounding_buildings.land_use_summary)
    lot_generation_summary = dict(surrounding_buildings.lot_generation_summary)
    zoning_grid = surrounding_buildings.zoning_grid
    zoning_preview_summary = dict(surrounding_buildings.zoning_preview_summary)
    instance_counter = int(surrounding_buildings.instance_index)
    placements.extend(list(surrounding_buildings.placements))
    resolved_program = replace(
        resolved_program,
        building_strategy_summary={
            **dict(building_strategy_summary),
            **dict(building_summary),
            "land_use_summary": dict(land_use_summary),
            "lot_generation_summary": dict(lot_generation_summary),
        },
    )
    solver_result = replace(solver_result, resolved_program=resolved_program)

    # Free CLIP model + FAISS index (~1.7 GB) – no longer needed after placement
    del embedder, index_store
    gc.collect()

    dominant_palette_style = (
        theme_segments[0].style_preset
        if theme_segments and _is_corridor_layout_mode(config.layout_mode)
        else getattr(config, "style_preset", None)
    )
    palette = style_palette(dominant_palette_style)
    rough = surface_roughness(dominant_palette_style)
    scene_texture_tracker = create_scene_texture_tracker(str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")))
    if _is_corridor_layout_mode(config.layout_mode) and placement_ctx is not None:
        scene = _build_osm_base_scene(
            placement_ctx,
            palette=palette,
            roughness=rough,
            texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
            texture_tracker=scene_texture_tracker,
            texture_overrides=texture_overrides,
        )
    else:
        left_side_width = sum(float(band.width_m) for band in resolved_program.bands if band.side == "left")
        right_side_width = sum(float(band.width_m) for band in resolved_program.bands if band.side == "right")
        scene = _build_base_scene(
            length_m=float(config.length_m),
            road_width_m=float(resolved_program.road_width_m),
            left_side_width_m=float(left_side_width),
            right_side_width_m=float(right_side_width),
            street_program=resolved_program,
            palette=palette,
            roughness=rough,
            texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
            texture_tracker=scene_texture_tracker,
            texture_overrides=texture_overrides,
        )
    _add_beauty_scene_proxies(
        scene,
        config=config,
        street_program=resolved_program,
        placement_ctx=placement_ctx,
        poi_ctx=poi_ctx,
        placements=placements,
        texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
        texture_tracker=scene_texture_tracker,
        texture_overrides=texture_overrides,
    )
    _add_final_land_use_zoning_proxies(
        scene,
        zoning_grid,
        roughness=rough,
        texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
        texture_tracker=scene_texture_tracker,
        texture_overrides=texture_overrides,
    )
    final_render_placements = list(placements)
    if default_sky_dome_placement is not None:
        final_render_placements.append(default_sky_dome_placement)

    # Trim mesh_cache to only assets that are actually placed in the final scene
    used_asset_ids = {placement.asset_id for placement in final_render_placements}
    trimmed_mesh_cache = mesh_cache.get_trimmed_cache(used_asset_ids)
    _add_instance_meshes(
        scene=scene,
        placements=final_render_placements,
        mesh_cache=trimmed_mesh_cache,
        building_plans_by_instance={
            str(plan.instance_id): plan
            for plan in building_plans
        },
    )

    exclusion_zones: tuple = ()
    debug_scene_overlays_enabled = _should_embed_debug_scene_overlays(config)
    if rule_set is not None and poi_ctx is not None and config.constraint_mode != "off":
        from .poi_rules import build_exclusion_zones as _build_exclusion_zones

        exclusion_zones = _build_exclusion_zones(poi_ctx, rule_set)
        if debug_scene_overlays_enabled:
            _add_poi_markers_and_zones(scene, extract_poi_points_by_type(poi_ctx, suffix="xz"), exclusion_zones)
    elif poi_ctx is not None:
        if debug_scene_overlays_enabled:
            _add_poi_markers_and_zones(scene, extract_poi_points_by_type(poi_ctx, suffix="xz"), ())

    _emit_progress(
        "glb_export",
        88,
        "Exporting scene geometry.",
        export_format=str(export_format),
    )
    outputs = _export_scene(scene=scene, out_dir=out_dir, export_format=export_format)
    serialized_osm_geometry = (
        _serialize_osm_geometry(placement_ctx)
        if _is_corridor_layout_mode(config.layout_mode) and placement_ctx is not None
        else None
    )

    production_steps_dir = (out_dir / "production_steps").resolve()
    production_steps_manifest = (production_steps_dir / "production_steps.json").resolve()
    if build_production_artifacts:
        _emit_progress(
            "scene_rendering",
            92,
            "Building production step artifacts.",
            placement_count=len(placements),
        )
        production_steps = _build_production_steps(
            out_dir=out_dir,
            config=config,
            resolved_program=resolved_program,
            placement_ctx=placement_ctx,
            poi_ctx=poi_ctx,
            spatial_ctx=spatial_ctx,
            placements=placements,
            zoning_grid=zoning_grid,
            building_footprints=building_footprints,
            generated_lots=generated_lots,
            building_plans=building_plans,
            mesh_cache=trimmed_mesh_cache,
            exclusion_zones=exclusion_zones,
            palette=palette,
            osm_geometry=serialized_osm_geometry,
            overall_texture_tracker=scene_texture_tracker,
            texture_overrides=texture_overrides,
        )
        outputs["production_steps_dir"] = str(production_steps_dir)
        if production_steps_manifest.exists():
            outputs["production_steps_manifest"] = str(production_steps_manifest)
    else:
        _emit_progress(
            "scene_rendering",
            92,
            "Skipping production step artifacts.",
            placement_count=len(placements),
        )
        production_steps = tuple()

    _emit_progress(
        "finalizing",
        95,
        "Computing scene metrics and layout payload.",
        production_step_count=len(production_steps),
    )

    elapsed_ms_total = (time.perf_counter() - start_perf) * 1000.0

    # Clear decomposition cache to free memory
    decomposition_cache.clear()

    unique_asset_count = len({placement.asset_id for placement in placements})
    diversity_ratio = float(unique_asset_count / len(placements)) if placements else 0.0
    dropped_slot_rate = compute_dropped_slot_rate(instance_count=len(placements), dropped_slots=int(dropped_slots))
    overlap_rate = compute_overlap_rate([placement.bbox_xz for placement in placements])
    retrieval_top3_category_hit = evaluate_topk_category_hits(retrieval_predictions, topk=3)
    latency_ms_per_instance = compute_latency_ms_per_instance(
        latency_ms_total=elapsed_ms_total,
        instance_count=len(placements),
    )

    furniture_placements = [placement for placement in placements if placement.placement_group == "street_furniture"]
    furniture_dicts = [placement.to_dict() for placement in furniture_placements]
    spacing_uniformity = compute_spacing_uniformity(furniture_dicts)
    style_consistency = compute_style_consistency(furniture_dicts)
    balance_score = compute_balance_score(furniture_dicts)
    furniture_balance_state = _street_furniture_balance_state(
        furniture_placements,
        slot_side_by_id=slot_side_by_id,
    )
    street_furniture_side_counts = dict(furniture_balance_state["street_furniture_side_counts"])
    street_furniture_core_side_counts = dict(furniture_balance_state["street_furniture_core_side_counts"])
    street_furniture_core_categories_by_side = dict(furniture_balance_state["street_furniture_core_categories_by_side"])
    street_furniture_core_category_count_by_side = dict(
        furniture_balance_state["street_furniture_core_category_count_by_side"]
    )
    compatible_furnishing_sides = set(compatible_core_sides)
    core_total_count = int(furniture_balance_state["core_total_count"])
    core_diff = abs(
        int(street_furniture_core_side_counts.get("left", 0))
        - int(street_furniture_core_side_counts.get("right", 0))
    )
    core_global_categories = set(street_furniture_core_categories_by_side.get("left", []))
    core_global_categories.update(street_furniture_core_categories_by_side.get("right", []))
    core_global_target = min(3, len(available_core_categories_global), core_total_count)
    core_left_target = min(
        2,
        len(available_core_categories_by_side.get("left", set())),
        int(street_furniture_core_side_counts.get("left", 0)),
    )
    core_right_target = min(
        2,
        len(available_core_categories_by_side.get("right", set())),
        int(street_furniture_core_side_counts.get("right", 0)),
    )
    if furniture_balance_policy != "overall_balanced":
        street_furniture_balance_ok = True
        street_furniture_balance_reason = "manual side-biased mode"
    elif not {"left", "right"} <= compatible_furnishing_sides:
        missing_side = "left" if "left" not in compatible_furnishing_sides else "right"
        street_furniture_balance_ok = False
        street_furniture_balance_reason = f"no compatible {missing_side} furnishing band"
    elif core_total_count <= 0:
        street_furniture_balance_ok = False
        street_furniture_balance_reason = "anchor-only constraints dominated placement"
    else:
        street_furniture_balance_ok = True
        if int(street_furniture_core_side_counts.get("left", 0)) <= 0 or int(street_furniture_core_side_counts.get("right", 0)) <= 0:
            street_furniture_balance_ok = False
        if core_total_count >= 4 and core_diff > 2:
            street_furniture_balance_ok = False
        if core_total_count >= 6 and (core_diff / max(core_total_count, 1)) > 0.25:
            street_furniture_balance_ok = False
        if core_global_target >= 3 and len(core_global_categories) < core_global_target:
            street_furniture_balance_ok = False
        if (
            core_left_target >= 2
            and int(street_furniture_core_category_count_by_side.get("left", 0)) < core_left_target
        ):
            street_furniture_balance_ok = False
        if (
            core_right_target >= 2
            and int(street_furniture_core_category_count_by_side.get("right", 0)) < core_right_target
        ):
            street_furniture_balance_ok = False
        if street_furniture_balance_ok:
            street_furniture_balance_reason = ""
        elif str(balance_repair_summary.get("reason", "") or "").strip():
            street_furniture_balance_reason = str(balance_repair_summary["reason"])
        elif int(street_furniture_core_side_counts.get("left", 0)) <= 0:
            street_furniture_balance_reason = "all repair candidates blocked on left"
        elif int(street_furniture_core_side_counts.get("right", 0)) <= 0:
            street_furniture_balance_reason = "all repair candidates blocked on right"
        else:
            sparse_side = "left" if int(street_furniture_core_side_counts.get("left", 0)) < int(street_furniture_core_side_counts.get("right", 0)) else "right"
            street_furniture_balance_reason = f"all repair candidates blocked on {sparse_side}"
    per_category_unique = {
        category: len({placement.asset_id for placement in placements if placement.category == category})
        for category in sorted({placement.category for placement in placements})
        if any(placement.category == category for placement in placements)
    }
    selection_source_counts: Dict[str, int] = {}
    asset_generator_type_counts: Dict[str, int] = {}
    asset_source_counts: Dict[str, int] = {}
    asset_source_unique_assets: Dict[str, set[str]] = {}
    asset_source_categories: Dict[str, set[str]] = {}
    asset_source_generator_types: Dict[str, set[str]] = {}
    parametric_instance_count = 0
    for placement in placements:
        selection_source_counts[placement.selection_source] = selection_source_counts.get(placement.selection_source, 0) + 1
        generator_key = (
            asset_generator_type(asset_by_id[placement.asset_id])
            if placement.asset_id in asset_by_id
            else "procedural_fallback" if placement.selection_source == "procedural_fallback" else "unknown"
        )
        source_key = _placement_asset_source_key(
            asset_by_id.get(placement.asset_id),
            selection_source=placement.selection_source,
        )
        asset_generator_type_counts[generator_key] = asset_generator_type_counts.get(generator_key, 0) + 1
        asset_source_counts[source_key] = asset_source_counts.get(source_key, 0) + 1
        asset_source_unique_assets.setdefault(source_key, set()).add(placement.asset_id)
        asset_source_categories.setdefault(source_key, set()).add(str(placement.category))
        asset_source_generator_types.setdefault(source_key, set()).add(str(generator_key))
        if generator_key == "parametric":
            parametric_instance_count += 1
    # Count Scene-type assets using metadata only (no mesh loading needed)
    asset_library_scene_instances = sum(
        1
        for placement in placements
        if mesh_cache.get(placement.asset_id) is not None and mesh_cache.get(placement.asset_id).is_scene
    )

    violations_total = sum(1 for placement in furniture_placements if placement.violated_rules)
    compliance_rate_total = 1.0 - (violations_total / len(furniture_placements)) if furniture_placements else 1.0
    avg_constraint_penalty = (
        sum(placement.constraint_penalty for placement in furniture_placements) / len(furniture_placements)
        if furniture_placements
        else 0.0
    )
    avg_feasibility_score = (
        sum(placement.feasibility_score for placement in furniture_placements) / len(furniture_placements)
        if furniture_placements
        else 1.0
    )
    rule_violation_counts: Dict[str, int] = {}
    for placement in furniture_placements:
        for rule_name in placement.violated_rules:
            rule_violation_counts[rule_name] = rule_violation_counts.get(rule_name, 0) + 1

    rule_satisfaction_rate = compute_rule_satisfaction_rate(solver_result.rule_evaluations)
    entrance_report = evaluate_all_entrances(
        entrance_points_xz=entrance_points_xz,
        registry=entrance_registry,
        carriageway_boundary=carriageway_boundary,
    )
    presentation_report = compute_presentation_report(
        placements,
        asset_by_id=asset_by_id,
        config=config,
        poi_context=poi_ctx,
        composition_report=composition_pass_report,
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

    program_fallback_reason = " | ".join(dict.fromkeys(reason for reason in program_fallback_reasons if reason))
    layout_path = (out_dir / "scene_layout.json").resolve()
    selected_highway_type = ""
    if projected is not None and getattr(projected, "roads", None):
        selected_highway_type = str(getattr(projected.roads[0], "highway_type", "") or "").strip().lower()
    from .placement_zones import summarize_road_selection

    road_selection_summary = summarize_road_selection(
        strategy=str(getattr(config, "road_selection", "walkable_neighborhood")),
        selected_highway_type=selected_highway_type,
    )
    asset_scale_summary = summarize_asset_scales([placement.to_dict() for placement in placements])
    asset_scale_summary.setdefault("_diagnostics", {})
    asset_scale_summary["_diagnostics"].update(
        {
            "building_asset_rejected_size_mismatch_count": int(building_summary.get("building_asset_rejected_size_mismatch_count", 0) or 0),
            "procedural_building_fallback_count": int(building_summary.get("procedural_building_fallback_count", building_summary.get("fallback_count", 0)) or 0),
        }
    )
    locked_asset_selection_counts = {
        category: int(
            sum(
                1
                for placement in placements
                if str(placement.category).strip().lower() == str(category)
                and str(placement.asset_id) == str(asset_id)
            )
        )
        for category, asset_id in locked_asset_ids.items()
    }
    curated_allowlist_selection_counts = {
        category: int(
            sum(
                1
                for placement in placements
                if str(placement.category).strip().lower() == str(category)
                and str(placement.selection_source).strip().lower() == _CURATED_ALLOWLIST_SELECTION_SOURCE
            )
        )
        for category in curated_asset_allowlist_ids
    }
    curated_allowlist_selected_asset_counts: Dict[str, Dict[str, int]] = {}
    for placement in placements:
        category = str(placement.category).strip().lower()
        if category not in curated_asset_allowlist_ids:
            continue
        if str(placement.selection_source).strip().lower() != _CURATED_ALLOWLIST_SELECTION_SOURCE:
            continue
        by_asset = curated_allowlist_selected_asset_counts.setdefault(category, {})
        asset_id = str(placement.asset_id)
        by_asset[asset_id] = int(by_asset.get(asset_id, 0)) + 1
    asset_lock_fallback_violations = {
        category: int(
            sum(
                1
                for placement in placements
                if str(placement.category).strip().lower() == str(category)
                and (
                    str(placement.asset_id) != str(asset_id)
                    or str(placement.selection_source).strip().lower() != "curated_asset_lock"
                )
            )
        )
        for category, asset_id in locked_asset_ids.items()
    }
    placement_log_summary = _summarize_placement_decision_events(decision_events)
    placement_log_path = ""
    if placement_logging_mode == "full_with_ui_summary":
        placement_log_file = (out_dir / "placement_decisions.jsonl").resolve()
        _write_placement_decision_log(placement_log_file, decision_events)
        placement_log_path = str(placement_log_file)
    asymmetry_raw = getattr(config, "land_use_asymmetry_strength", 0.0)
    bias_raw = getattr(config, "left_right_bias", 0.0)
    setback_min_raw = getattr(config, "building_front_setback_min_m", DEFAULT_BUILDING_FRONT_SETBACK_MIN_M)
    setback_max_raw = getattr(config, "building_front_setback_max_m", DEFAULT_BUILDING_FRONT_SETBACK_MAX_M)
    zoning_granularity_raw = getattr(config, "zoning_granularity", "fine")
    streetwall_continuity_raw = getattr(config, "streetwall_continuity", 0.95)
    infill_policy_raw = getattr(config, "infill_policy", "aggressive")
    style_preset_used = str(
        resolved_program.context_conditions.get("style_preset", getattr(config, "style_preset", "civic_clean_v1"))
    )
    visual_lighting_preset = (
        "analytical_diorama"
        if style_preset_used.strip().lower() == "analytical_diorama_v1"
        else _derive_lighting_preset(sky_selection)
    )
    visual_surface_roles = (
        "carriageway",
        "sidewalk",
        "clear_path",
        "furnishing",
        "bike_lane",
        "bus_lane",
        "grass",
        "grass_belt",
        "crossing",
        "lane_mark",
        "context_ground",
        "building_buffer",
        "tree_pit",
        "planting_soil",
        "transit_pad",
        "curb",
        "parking_lane",
        "median_green",
        "safety_island",
        "shared_street_surface",
        "colored_pavement",
    )
    visual_palette = style_palette(style_preset_used)
    visual_roughness = surface_roughness(style_preset_used)
    visual_style_payload = {
        "preset": style_preset_used,
        "lighting_preset": visual_lighting_preset,
        "surface_palette": {
            role: list(visual_palette[role])
            for role in visual_surface_roles
            if role in visual_palette
        },
        "surface_roughness": {
            role: float(visual_roughness[role])
            for role in visual_surface_roles
            if role in visual_roughness
        },
        "building_profile": {
            "mode": (
                "procedural_background"
                if style_preset_used.strip().lower() == "analytical_diorama_v1"
                else str(building_summary.get("generation_mode_used") or getattr(config, "surrounding_building_mode", "grid_growth"))
            ),
            "profile": (
                "low_saturation_parametric_facade_v1"
                if style_preset_used.strip().lower() == "analytical_diorama_v1"
                else "default_building_profile"
            ),
            "preferred_theme": "analytical" if style_preset_used.strip().lower() == "analytical_diorama_v1" else "",
            "background_layer": bool(style_preset_used.strip().lower() == "analytical_diorama_v1"),
            "procedural_fallback_count": int(building_summary.get("procedural_building_fallback_count", building_summary.get("fallback_count", 0)) or 0),
        },
        "material_finish_version": (
            "analytical_diorama_finish_v1"
            if style_preset_used.strip().lower() == "analytical_diorama_v1"
            else "presentation_material_finish_v1"
        ),
        "scene_texture_pack": scene_texture_pack_name(str(getattr(config, "scene_texture_mode", "topdown_tiles_v1"))),
        "default_sky_dome_asset_id": (
            DEFAULT_SKY_DOME_ASSET_ID if default_sky_dome_placement is not None else ""
        ),
        "default_sky_dome_enabled": bool(default_sky_dome_placement is not None),
    }

    summary_payload = {
        "instance_count": len(placements),
        "environment_instance_count": 1 if default_sky_dome_placement is not None else 0,
        "default_sky_dome_asset_id": (
            DEFAULT_SKY_DOME_ASSET_ID if default_sky_dome_placement is not None else ""
        ),
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
        "asset_generator_type_counts": asset_generator_type_counts,
        "asset_source_counts": asset_source_counts,
        "asset_source_unique_counts": {
            source_key: int(len(asset_ids))
            for source_key, asset_ids in asset_source_unique_assets.items()
        },
        "asset_scale_mode": str(getattr(config, "asset_scale_mode", "canonical_v1")),
        "curated_asset_lock_enabled": bool(locked_asset_ids),
        "curated_asset_selection_enabled": bool(curated_asset_profile != "disabled"),
        "curated_asset_selection_policy": (
            "fixed_hq_allowlist_seeded" if curated_asset_profile != "disabled" else "disabled"
        ),
        "asset_lock_profile": str(curated_asset_profile),
        "curated_street_assets_profile": str(curated_asset_profile),
        "locked_asset_ids": dict(locked_asset_ids),
        "locked_asset_selection_counts": dict(locked_asset_selection_counts),
        "locked_asset_counts": dict(locked_asset_selection_counts),
        "curated_asset_fallback_ids": dict(curated_asset_fallback_ids),
        "curated_asset_allowlist_ids": dict(curated_asset_allowlist_ids),
        "curated_asset_allowlist_counts": {
            category: int(len(asset_ids))
            for category, asset_ids in curated_asset_allowlist_ids.items()
        },
        "curated_allowlist_selection_counts": dict(curated_allowlist_selection_counts),
        "curated_allowlist_selected_asset_counts": dict(curated_allowlist_selected_asset_counts),
        "fallback_blocked_categories": list(fallback_blocked_categories),
        "asset_lock_fallback_violations": dict(asset_lock_fallback_violations),
        "asset_scale_summary": asset_scale_summary,
        "selected_object_backend": str(object_backend_name),
        "selected_ground_materials": (
            dict(ground_selection.material_ids_by_role)
            if ground_selection is not None
            else {}
        ),
        "selected_ground_material_backend": (
            str(ground_selection.backend_name)
            if ground_selection is not None
            else ""
        ),
        "selected_sky_id": str(sky_selection.sky_id) if sky_selection is not None else "",
        "selected_sky_backend": str(sky_selection.backend_name) if sky_selection is not None else "",
        "environment_source_dataset": str(environment_source_dataset),
        "environment_source_datasets": list(environment_source_datasets),
        "tree_species_policy": str(getattr(config, "tree_species_policy", "per_theme_single_species")),
        "furniture_balance_policy": str(getattr(config, "furniture_balance_policy", "overall_balanced")),
        "placement_logging_mode": str(getattr(config, "placement_logging_mode", "full_with_ui_summary")),
        "tree_asset_by_theme": dict(theme_tree_asset_lock),
        "tree_theme_reselection_count": int(tree_theme_reselection_count),
        "placement_log_path": str(placement_log_path),
        "placement_log_summary": dict(placement_log_summary),
        "placement_log_reason_counts": dict(placement_log_summary.get("reason_counts", {})),
        "carriageway_intrusion_blocked_count": int(
            (placement_log_summary.get("reason_counts", {}) or {}).get("intrudes_carriageway", 0) or 0
        ),
        "balance_repair_summary": dict(balance_repair_summary),
        "asset_usage_by_source": [
            {
                "source": str(source_key),
                "instance_count": int(asset_source_counts.get(source_key, 0)),
                "unique_asset_count": int(len(asset_source_unique_assets.get(source_key, set()))),
                "categories": sorted(category for category in asset_source_categories.get(source_key, set()) if category),
                "generator_types": sorted(
                    generator_type
                    for generator_type in asset_source_generator_types.get(source_key, set())
                    if generator_type
                ),
                "asset_ids": sorted(asset_source_unique_assets.get(source_key, set())),
            }
            for source_key in sorted(
                asset_source_counts,
                key=lambda key: (-int(asset_source_counts.get(key, 0)), str(key)),
            )
        ],
        "parametric_instance_count": int(parametric_instance_count),
        "asset_library_scene_instances": int(asset_library_scene_instances),
        "production_step_count": int(len(production_steps)),
        "production_step_ids": [record.step_id for record in production_steps],
        "final_production_step_id": production_steps[-1].step_id if production_steps else "",
        "scene_texture_mode": str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
        "scene_texture_pack": scene_texture_pack_name(str(getattr(config, "scene_texture_mode", "topdown_tiles_v1"))),
        "scene_texture_fallback_used": bool(scene_texture_tracker.fallback_used),
        "scene_texture_missing_assets": sorted(scene_texture_tracker.missing_assets),
        "visual_style_preset": style_preset_used,
        "visual_lighting_preset": visual_lighting_preset,
        "visual_surface_role_count": dict(sorted(scene_texture_tracker.surface_role_counts.items())),
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
        "objective_profile": str(getattr(config, "objective_profile", "balanced")),
        "ped_demand_level": str(getattr(config, "ped_demand_level", "medium")),
        "bike_demand_level": str(getattr(config, "bike_demand_level", "low")),
        "transit_demand_level": str(getattr(config, "transit_demand_level", "medium")),
        "vehicle_demand_level": str(getattr(config, "vehicle_demand_level", "medium")),
        "program_generator_requested": str(config.program_generator),
        "program_generator_used": str(program_used),
        "layout_solver_requested": str(config.layout_solver),
        "layout_solver_used": str(solver_result.backend_used),
        "selected_highway_type": road_selection_summary["selected_highway_type"],
        "road_selection_requested": road_selection_summary["road_selection_requested"],
        "road_selection_used": road_selection_summary["road_selection_used"],
        "road_selection_fallback_reason": road_selection_summary["road_selection_fallback_reason"],
        "solver_backend_requested": str(solver_result.backend_requested),
        "solver_backend_used": str(solver_result.backend_used),
        "cross_section_type": str(resolved_program.cross_section_type),
        "road_width_m": float(resolved_program.road_width_m),
        "sidewalk_width_m": float(resolved_program.sidewalk_width_m),
        "length_m": float(config.length_m),
        "carriageway_width_m": float(resolved_program.road_width_m),
        "left_clear_path_width_m": float(resolved_program.left_clear_path_width_m),
        "right_clear_path_width_m": float(resolved_program.right_clear_path_width_m),
        "left_furnishing_width_m": float(resolved_program.left_furnishing_width_m),
        "right_furnishing_width_m": float(resolved_program.right_furnishing_width_m),
        "row_width_m": float(resolved_program.row_width_m),
        "width_expanded": bool(resolved_program.width_expanded),
        "width_reallocation_reason": str(resolved_program.width_reallocation_reason),
        "poi_fit_feasible": bool(resolved_program.poi_fit_feasible),
        "poi_fit_report": dict(resolved_program.poi_fit_report),
        "rule_satisfaction_rate": float(rule_satisfaction_rate),
        "topology_validity": float(topology_validity),
        "cross_section_feasibility": float(cross_section_feasibility),
        "editability": float(editability),
        "conflict_explainability": float(conflict_explainability),
        "active_constraints": list(solver_result.active_constraints),
        "throughput_feasibility": dict(solver_result.throughput_feasibility),
        "objective_score_breakdown": dict(solver_result.objective_score_breakdown),
        "band_solution_count": int(len(solver_result.band_solutions)),
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
        "selected_road_required_left_width_m": float(getattr(placement_ctx, "required_left_width_m", 0.0) or 0.0),
        "selected_road_required_right_width_m": float(getattr(placement_ctx, "required_right_width_m", 0.0) or 0.0),
        "selected_road_final_row_width_m": float(getattr(placement_ctx, "row_width_m", resolved_program.row_width_m) or 0.0),
        "observed_poi_counts": dict(resolved_program.observed_poi_counts),
        "style_preset": style_preset_used,
        "beauty_mode": str(getattr(config, "beauty_mode", "presentation_v1")),
        "render_preset": str(getattr(config, "render_preset", "axonometric_board_v1")),
        "asset_curation_mode": str(getattr(config, "asset_curation_mode", "scene_ready_first")),
        "tree_assets_unavailable": bool(tree_assets_unavailable),
        "tree_inventory_raw_count": int(raw_tree_inventory_count),
        "tree_inventory_scene_ready_count": int(len(category_to_rows.get("tree", ()))),
        "parametric_tree_fallback_count": int(parametric_tree_count),
        "scene_debug_overlays_enabled": bool(debug_scene_overlays_enabled),
        "theme_segments": [segment.to_dict() for segment in theme_segments],
        "theme_segment_count": int(len(theme_segments)),
        "theme_diagnostics": {
            "theme_inference_mode": str(getattr(config, "theme_inference_mode", "deterministic_auto")),
            "theme_vocab_name": str(getattr(config, "theme_vocab_name", "fixed_v1")),
            "zone_programs": theme_zone_programs,
        },
        "placement_force_model": {
            "version": str(placement_field_config.get("version", "placement_field_v1")),
            "config_path": str(placement_field_path()),
            "cell_size_m": float(placement_field_config.get("cell_size_m", 4.0)),
            "constraint_mode": str(config.constraint_mode),
        },
        "anchor_resolution_summary": {
            **dict(anchor_resolution_summary),
            "total_required_slots": int(total_required_slots),
            "realized_required_slots": int(realized_required_slots),
        },
        "required_slot_realization_rate": (
            float(realized_required_slots / total_required_slots)
            if total_required_slots > 0
            else 1.0
        ),
        "unplaced_required_slot_count": int(anchor_resolution_summary["unplaced_required"]),
        "building_generation_mode": str(
            building_summary.get("generation_mode_used") or getattr(config, "surrounding_building_mode", "grid_growth")
        ),
        "building_generation_mode_requested": str(
            building_summary.get("generation_mode_requested") or getattr(config, "surrounding_building_mode", "grid_growth")
        ),
        "building_generation_mode_used": str(
            building_summary.get("generation_mode_used") or getattr(config, "surrounding_building_mode", "grid_growth")
        ),
        "building_generation_fallback_reason": str(building_summary.get("generation_fallback_reason", "") or ""),
        "building_footprint_count": int(len(building_footprints)),
        "building_region_count": int(building_summary.get("building_region_count", 0) or 0),
        "surface_annotation_count": int(len(getattr(placement_ctx, "surface_annotations", []) or [])),
        "land_use_asymmetry_strength": float(0.0 if asymmetry_raw is None else asymmetry_raw),
        "left_right_bias": float(0.0 if bias_raw is None else bias_raw),
        "building_front_setback_min_m": float(DEFAULT_BUILDING_FRONT_SETBACK_MIN_M if setback_min_raw is None else setback_min_raw),
        "building_front_setback_max_m": float(DEFAULT_BUILDING_FRONT_SETBACK_MAX_M if setback_max_raw is None else setback_max_raw),
        "zoning_granularity": str("fine" if zoning_granularity_raw is None else zoning_granularity_raw),
        "streetwall_continuity": float(0.95 if streetwall_continuity_raw is None else streetwall_continuity_raw),
        "building_density": float(building_summary.get("building_density", getattr(config, "building_density", 0.55)) or 0.55),
        "building_max_per_100m": float(building_summary.get("building_max_per_100m", getattr(config, "building_max_per_100m", 10.0)) or 10.0),
        "building_density_summary": dict(building_summary.get("building_density_summary", {}) or {}),
        "building_target_lot_count": int(building_summary.get("building_target_lot_count", 0) or 0),
        "building_density_removed_lot_count": int(building_summary.get("building_density_removed_lot_count", 0) or 0),
        "building_asset_rejected_size_mismatch_count": int(building_summary.get("building_asset_rejected_size_mismatch_count", 0) or 0),
        "procedural_building_fallback_count": int(building_summary.get("procedural_building_fallback_count", building_summary.get("fallback_count", 0)) or 0),
        "infill_policy": str("aggressive" if infill_policy_raw is None else infill_policy_raw),
        "building_balance_policy": str(building_summary.get("building_balance_policy", "")),
        "building_balance_ok": bool(building_summary.get("building_balance_ok", False)),
        "building_balance_reason": str(building_summary.get("building_balance_reason", "") or ""),
        "frontage_balance_gap": float(building_summary.get("frontage_balance_gap", 0.0) or 0.0),
        "buildable_frontage_by_side": dict(building_summary.get("buildable_frontage_by_side", {}) or {}),
        "door_enabled": bool(building_summary.get("door_enabled", True)),
        "door_count": int(building_summary.get("door_count", 0) or 0),
        "door_count_by_side": dict(building_summary.get("door_count_by_side", {}) or {}),
        "door_strategy": str(building_summary.get("door_strategy", "attached_3d_v1") or "attached_3d_v1"),
        "door_policy": str(building_summary.get("door_policy", "") or ""),
        "door_required_count": int(building_summary.get("door_required_count", 0) or 0),
        "door_skipped_existing_asset_count": int(building_summary.get("door_skipped_existing_asset_count", 0) or 0),
        "door_missing_building_count": int(building_summary.get("door_missing_building_count", 0) or 0),
        "door_missing_reason_counts": dict(building_summary.get("door_missing_reason_counts", {}) or {}),
        "zoning_preview_mode": str(zoning_preview_summary.get("zoning_preview_mode", "parcel_first") or "parcel_first"),
        "frontage_cell_count": int(zoning_preview_summary.get("frontage_cell_count", 0) or 0),
        "frontage_parcel_count": int(lot_generation_summary.get("frontage_parcel_count", len(generated_lots)) or 0),
        "infill_footprint_count": int(building_summary.get("infill_footprint_count", 0) or 0),
        "frontage_coverage_by_side": dict(building_summary.get("frontage_coverage_by_side", {}) or {}),
        "frontage_gap_stats_by_side": dict(building_summary.get("frontage_gap_stats_by_side", {}) or {}),
        "street_furniture_side_counts": dict(street_furniture_side_counts),
        "street_furniture_core_side_counts": dict(street_furniture_core_side_counts),
        "street_furniture_core_categories_by_side": dict(street_furniture_core_categories_by_side),
        "street_furniture_core_category_count_by_side": dict(street_furniture_core_category_count_by_side),
        "street_furniture_balance_ok": bool(street_furniture_balance_ok),
        "street_furniture_balance_reason": str(street_furniture_balance_reason),
        "building_summary": dict(building_summary),
        "land_use_summary": dict(land_use_summary),
        "lot_generation_summary": dict(lot_generation_summary),
        "building_retrieval_coverage": {
            "footprint_count": int(building_summary.get("footprint_count", 0)),
            "lot_count": int(building_summary.get("lot_count", 0)),
            "target_count": int(building_summary.get("target_count", 0)),
            "target_type": str(building_summary.get("target_type", "")),
            "placed_count": int(building_summary.get("placed_count", 0)),
            "asset_count": int(building_summary.get("asset_count", 0)),
            "fallback_count": int(building_summary.get("fallback_count", 0)),
            "real_footprint_count": int(building_summary.get("real_footprint_count", 0)),
            "infill_footprint_count": int(building_summary.get("infill_footprint_count", 0)),
        },
        "zoning_preview_summary": dict(zoning_preview_summary),
        "composition_report": {
            **dict(composition_pass_report),
            **dict(presentation_report),
        },
        "spatial_context": {
            "junction_points_xz": [list(p) for p in spatial_ctx.junction_points_xz],
            "entrance_points_xz": [list(p) for p in spatial_ctx.entrance_points_xz],
            "bus_stop_points_xz": [list(p) for p in spatial_ctx.bus_stop_points_xz],
            "fire_points_xz": [list(p) for p in spatial_ctx.fire_points_xz],
            "poi_points_by_type_xz": {
                poi_type: [list(point) for point in points]
                for poi_type, points in nonempty_poi_points(spatial_ctx.poi_points_by_type_xz).items()
            },
            "road_half_width_m": float(resolved_program.road_width_m / 2.0),
            "length_m": float(spatial_ctx.length_m),
        },
    }
    if serialized_osm_geometry is not None:
        summary_payload["osm_geometry"] = serialized_osm_geometry

    summary_payload["poi_exclusion_zones"] = [
        {
            "poi_type": z.poi_type,
            "position_xz": [round(z.position_xz[0], 3), round(z.position_xz[1], 3)],
            "radius_m": round(z.radius_m, 3),
            "rule_name": z.rule_name,
        }
        for z in exclusion_zones
    ]
    summary_payload["poi_conflict_assets"] = [
        {
            "instance_id": p.instance_id,
            "slot_id": p.slot_id,
            "category": p.category,
            "position_xz": [round(float(p.position_xyz[0]), 3), round(float(p.position_xyz[2]), 3)],
            "violated_rules": list(p.violated_rules),
            "constraint_penalty": round(float(p.constraint_penalty), 4),
        }
        for p in placements
        if p.violated_rules
    ]

    program_generation_payload = program_result.to_dict()
    program_generation_payload["theme_zone_programs"] = list(theme_zone_programs)
    layout_payload = {
        "query": config.query,
        "config": config.to_dict(),
        "selected_object_backend": str(object_backend_name),
        "selected_ground_materials": ground_selection.to_dict() if ground_selection is not None else {},
        "selected_sky": sky_selection.to_dict() if sky_selection is not None else {},
        "environment_source_dataset": str(environment_source_dataset),
        "environment_source_datasets": list(environment_source_datasets),
        "program_generation": program_generation_payload,
        "street_program": resolved_program.to_dict(),
        "constraint_set": base_constraint_set.to_dict(),
        "solver": solver_result.to_dict(),
        "summary": summary_payload,
        "visual_style": visual_style_payload,
        "placements": [placement.to_dict() for placement in placements],
        "environment_placements": (
            [default_sky_dome_placement.to_dict()]
            if default_sky_dome_placement is not None
            else []
        ),
        "building_footprints": [footprint.to_dict() for footprint in building_footprints],
        "generated_lots": [lot.to_dict() for lot in generated_lots],
        "building_placements": [plan.to_dict() for plan in building_plans],
        "building_retrieval_predictions": building_retrieval_predictions,
        "zoning_grid": list(zoning_grid),
        "regions": list(getattr(placement_ctx, "regions", []) or []),
        "derived_regions": list(getattr(placement_ctx, "derived_regions", []) or []),
        "building_regions": [
            {
                key: value
                for key, value in dict(record).items()
                if key != "geometry"
            }
            for record in getattr(placement_ctx, "building_regions", []) or []
        ],
        "region_derivation_summary": dict(getattr(placement_ctx, "region_derivation_summary", {}) or {}),
        "functional_zones": list(getattr(placement_ctx, "functional_zones", []) or []),
        "surface_annotations": [
            {
                key: value
                for key, value in dict(record).items()
                if key != "geometry"
            }
            for record in getattr(placement_ctx, "surface_annotations", []) or []
        ],
        "production_steps": [record.to_dict() for record in production_steps],
        "unplaced_slot_diagnostics": list(unplaced_slot_diagnostics),
        "placement_decision_log": {
            "path": str(placement_log_path),
            "summary": dict(placement_log_summary),
        },
        "outputs": outputs,
        "supervision_sample": {
            "inputs": {
                "config": config.to_dict(),
                "inventory_summary": inventory_summary.to_dict(),
                "constraint_set": base_constraint_set.to_dict(),
                "road_segment_graph_summary": solver_result.road_segment_graph_summary,
                "observed_poi_counts": dict(resolved_program.observed_poi_counts),
            },
            "labels": {
                "resolved_program": resolved_program.to_dict(),
                "band_solutions": [band.to_dict() for band in solver_result.band_solutions],
                "slot_plans": [slot.to_dict() for slot in solver_result.slot_plans],
                "objective_profile": str(resolved_program.objective_profile),
            },
        },
    }
    layout_payload["summary"].update(presentation_report)
    scene_graph = build_scene_graph(layout_payload, road_segment_graph=road_segment_graph)
    layout_payload["scene_graph"] = scene_graph
    layout_payload["summary"]["scene_graph_node_count"] = int(len(scene_graph.get("nodes", []) or []))
    layout_payload["summary"]["scene_graph_edge_count"] = int(len(scene_graph.get("edges", []) or []))
    layout_payload["summary"]["scene_graph_available_categories"] = list(
        scene_graph.get("filters", {}).get("categories", []) or []
    )
    render_views: list[Mapping[str, object]] = []
    if render_presentation_artifacts:
        _emit_progress(
            "scene_rendering",
            97,
            "Rendering presentation views.",
            layout_path=str(layout_path),
        )
        render_views = render_presentation_views(layout_payload, out_dir=out_dir, config=config)
    else:
        _emit_progress(
            "scene_rendering",
            97,
            "Skipping presentation views.",
            layout_path=str(layout_path),
        )
    layout_payload["summary"]["render_views"] = render_views
    render_preset_used = str(getattr(config, "render_preset", "axonometric_board_v1") or "axonometric_board_v1")
    final_render_views = [
        {
            "name": str(view.get("name", "") or ""),
            "title": str(view.get("title", "") or ""),
            "path": str(view.get("path", "") or ""),
        }
        for view in render_views
        if str(view.get("name", "") or "").startswith("final_")
    ]
    layout_payload["summary"]["render_preset_used"] = render_preset_used
    layout_payload["summary"]["final_render_views"] = final_render_views
    layout_payload["summary"]["final_render_style"] = (
        "axonometric_board"
        if render_preset_used.strip().lower() == "axonometric_board_v1"
        else "jury_default"
    )
    preferred_final_companion_path = _preferred_final_render_companion_path(render_views)
    if preferred_final_companion_path:
        production_steps = tuple(
            replace(record, companion_path=preferred_final_companion_path)
            if str(record.step_id).strip() == "scene_preview"
            else record
            for record in production_steps
        )
        layout_payload["production_steps"] = [record.to_dict() for record in production_steps]
        if production_steps_manifest.exists():
            production_steps_manifest.write_text(
                json.dumps([record.to_dict() for record in production_steps], indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
    for view in render_views:
        if str(view.get("path", "")).strip():
            outputs[f"presentation_{view.get('name', 'view')}"] = str(view["path"])

    outputs["lighting_preset"] = str(visual_style_payload.get("lighting_preset") or _derive_lighting_preset(sky_selection))
    outputs["lighting_params"] = _derive_lighting_params(sky_selection)

    _emit_progress(
        "finalizing",
        99,
        "Writing final scene layout.",
        layout_path=str(layout_path),
    )
    layout_path.write_text(json.dumps(layout_payload, indent=2, ensure_ascii=True), encoding="utf-8")

    outputs["scene_layout"] = str(layout_path)
    if placement_log_path:
        outputs["placement_decisions"] = str(placement_log_path)
    outputs["policy_used"] = policy_used
    outputs["selected_object_backend"] = str(object_backend_name)
    outputs["selected_ground_materials"] = (
        json.dumps(dict(ground_selection.material_ids_by_role), ensure_ascii=True)
        if ground_selection is not None
        else "{}"
    )
    outputs["selected_sky_id"] = str(sky_selection.sky_id) if sky_selection is not None else ""
    outputs["environment_source_dataset"] = str(environment_source_dataset)
    outputs["design_rule_profile"] = str(config.design_rule_profile)
    outputs["objective_profile"] = str(getattr(config, "objective_profile", "balanced"))
    outputs["program_cross_section_type"] = str(resolved_program.cross_section_type)
    outputs["program_generator_requested"] = str(config.program_generator)
    outputs["program_generator_used"] = str(program_used)
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
