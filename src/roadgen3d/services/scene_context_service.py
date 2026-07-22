"""Shared runtime helpers for workbench scene context and OSM road selection."""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ..china_cities import CHINA_CITY_REGISTRY
from ..osm_ingest import fetch_osm_data, parse_osm_features, project_to_local
from ..osm_segment_graph import build_segment_graph
from ..osm_semantics import (
    OSM_SEMANTIC_RULESET_VERSION,
    apply_osm_bus_stop_constraints,
    evaluate_osm_context_fit,
    prepare_multiblock_projected_features,
    road_display_name,
    road_length_m,
    segment_semantic_profile_payload,
)
from ..placement_zones import (
    EFFECTIVE_POI_EVALUATOR_VERSION,
    evaluate_projected_road_context,
    is_walkable_neighborhood_highway,
)
from ..poi_taxonomy import core_poi_count, poi_weighted_score, qualifies_poi_counts
from ..road_discovery import discover_poi_roads, write_discovered_roads_jsonl
from ..semantic_design_layers import resolve_semantic_design_layers
from ..types import StreetComposeConfig
from .design_types import SceneContext, sanitize_scene_context


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OSM_CACHE_DIR = (ROOT / "artifacts" / "m5" / "osm_cache").resolve()
DEFAULT_ROAD_SELECTION = "walkable_neighborhood"


@dataclass(frozen=True)
class ResolvedSceneContext:
    """Resolved runtime scene setup after optional OSM road auto-selection."""

    scene_context: SceneContext
    requested_aoi_bbox: Tuple[float, float, float, float] | None = None
    effective_aoi_bbox: Tuple[float, float, float, float] | None = None
    city_name_en: str | None = None
    osm_cache_dir: Path = DEFAULT_OSM_CACHE_DIR
    road_selection: str = DEFAULT_ROAD_SELECTION
    selected_road_osm_id: int | None = None
    selected_road_discovered_poi_count: int | None = None
    selected_road_discovered_poi_score: float | None = None
    selected_road_discovered_core_poi_count: int | None = None
    selected_road_source: str = ""
    probe_metrics: Dict[str, Any] = field(default_factory=dict)

    def to_summary_metadata(self) -> Dict[str, Any]:
        scenario_variant = self.scene_context.scenario_design_variant
        return {
            "layout_mode": str(self.scene_context.layout_mode),
            "graph_template_id": self.scene_context.graph_template_id,
            "base_graph_template_id": self.scene_context.graph_template_id if self.scene_context.scenario_id else None,
            "scenario_id": self.scene_context.scenario_id,
            "scenario_title": self.scene_context.scenario_title,
            "scenario_design_variant": dict(scenario_variant) if isinstance(scenario_variant, Mapping) else None,
            "requested_aoi_bbox": list(self.requested_aoi_bbox) if self.requested_aoi_bbox is not None else None,
            "effective_aoi_bbox": list(self.effective_aoi_bbox) if self.effective_aoi_bbox is not None else None,
            "city_name_en": self.city_name_en,
            "selected_road_source": self.selected_road_source,
            "selected_road_osm_id": self.selected_road_osm_id,
            "selected_road_discovered_poi_count": self.selected_road_discovered_poi_count,
            "selected_road_discovered_poi_score": self.selected_road_discovered_poi_score,
            "selected_road_discovered_core_poi_count": self.selected_road_discovered_core_poi_count,
            "selected_road_probe_metrics": dict(self.probe_metrics),
        }


def list_china_cities_payload() -> List[Dict[str, Any]]:
    """Serialize the built-in China city registry for the workbench API."""

    return [
        {
            "name_zh": str(city.name_zh),
            "name_en": str(city.name_en),
            "province": str(city.province),
            "bbox": [float(value) for value in city.bbox],
        }
        for city in CHINA_CITY_REGISTRY
    ]


def bbox_hash(bbox: Tuple[float, float, float, float]) -> str:
    key = f"{bbox[0]:.6f},{bbox[1]:.6f},{bbox[2]:.6f},{bbox[3]:.6f}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def _coerce_text_tuple(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = value.replace(";", ",").split(",")
    else:
        items = list(value) if isinstance(value, Sequence) else [value]
    return tuple(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return bool(default)
    if isinstance(value, (int, float)):
        return bool(value)
    return bool(default)


def discovered_metadata_path(discovered_path: Path) -> Path:
    return discovered_path.with_suffix(".meta.json")


def write_discovered_roads_metadata(
    metadata_path: Path,
    aoi_bbox: Tuple[float, float, float, float],
    *,
    min_poi_count: int = 2,
    min_road_length_m: float = 100.0,
    min_poi_score: float = 2.0,
    min_core_poi_count: int = 1,
) -> None:
    metadata = {
        "aoi_bbox": [float(value) for value in aoi_bbox],
        "min_poi_count": int(min_poi_count),
        "min_road_length_m": float(min_road_length_m),
        "min_poi_score": float(min_poi_score),
        "min_core_poi_count": int(min_core_poi_count),
        "poi_evaluator_version": EFFECTIVE_POI_EVALUATOR_VERSION,
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=True), encoding="utf-8")


def load_discovered_roads_metadata(metadata_path: Path) -> Dict[str, Any]:
    if not metadata_path.exists():
        return {}
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def discovered_cache_matches(
    discovered_path: Path,
    aoi_bbox: Tuple[float, float, float, float] | None,
    *,
    min_poi_count: int = 2,
    min_road_length_m: float = 100.0,
    min_poi_score: float = 2.0,
    min_core_poi_count: int = 1,
) -> bool:
    if aoi_bbox is None or not discovered_path.exists():
        return False
    metadata = load_discovered_roads_metadata(discovered_metadata_path(discovered_path))
    if not metadata:
        return False
    return (
        tuple(float(value) for value in metadata.get("aoi_bbox", ())) == tuple(float(value) for value in aoi_bbox)
        and int(metadata.get("min_poi_count", -1)) == int(min_poi_count)
        and float(metadata.get("min_road_length_m", -1.0)) == float(min_road_length_m)
        and float(metadata.get("min_poi_score", -1.0)) == float(min_poi_score)
        and int(metadata.get("min_core_poi_count", -1)) == int(min_core_poi_count)
        and str(metadata.get("poi_evaluator_version", "")) == EFFECTIVE_POI_EVALUATOR_VERSION
    )


def load_discovered_road_records(discovered_path: Path) -> List[Dict[str, Any]]:
    if not discovered_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in discovered_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def probe_discovered_road_context_metrics(
    row: Mapping[str, Any],
    *,
    osm_cache_dir: Path,
    road_width_m: float,
    sidewalk_width_m: float,
    lane_count: int,
    road_selection: str = DEFAULT_ROAD_SELECTION,
) -> Dict[str, Any]:
    candidate_bbox = tuple(float(value) for value in row["bbox"])
    probe_config = StreetComposeConfig(
        query="probe",
        length_m=80.0,
        road_width_m=float(road_width_m),
        sidewalk_width_m=float(sidewalk_width_m),
        lane_count=int(lane_count),
        density=1.0,
        seed=0,
        topk_per_category=1,
        max_trials_per_slot=1,
        layout_mode="osm",
        constraint_mode="off",
        aoi_bbox=candidate_bbox,
        osm_cache_dir=str(osm_cache_dir),
        road_selection=str(road_selection),
        selected_road_osm_id=int(row["osm_id"]),
    )
    raw = fetch_osm_data(bbox=candidate_bbox, cache_dir=Path(osm_cache_dir))
    features = parse_osm_features(raw)
    projected = project_to_local(features, candidate_bbox)
    _filtered, placement_ctx, poi_counts = evaluate_projected_road_context(projected, probe_config)
    return {
        "poi_counts": dict(poi_counts),
        "poi_fit_feasible": bool(getattr(placement_ctx, "poi_fit_feasible", True)),
        "poi_fit_report": dict(getattr(placement_ctx, "poi_fit_report", {}) or {}),
        "required_left_width_m": float(getattr(placement_ctx, "required_left_width_m", 0.0) or 0.0),
        "required_right_width_m": float(getattr(placement_ctx, "required_right_width_m", 0.0) or 0.0),
        "row_width_m": float(getattr(placement_ctx, "row_width_m", 0.0) or 0.0),
    }


def select_auto_discovered_road(
    *,
    artifacts_dir: Path,
    osm_cache_dir: Path,
    aoi_bbox: Tuple[float, float, float, float] | None,
    seed: int,
    road_width_m: float,
    sidewalk_width_m: float,
    lane_count: int,
    road_selection: str = DEFAULT_ROAD_SELECTION,
) -> Tuple[Dict[str, Any], bool, Dict[str, Any]]:
    """Return one deterministic road, preferring POI-rich context when present."""

    discovered_path = artifacts_dir.parent / "m5" / "discovered_poi_roads.jsonl"
    metadata_path = discovered_metadata_path(discovered_path)
    if not discovered_cache_matches(discovered_path, aoi_bbox):
        cached_rows: List[Dict[str, Any]] = []
    else:
        cached_rows = list(load_discovered_road_records(discovered_path))
    auto_discovered = False

    if not cached_rows:
        if aoi_bbox is None:
            raise RuntimeError("OSM mode requires an AOI bbox to auto-discover POI-rich roads.")

        class _AdhocCity:
            def __init__(self, bbox: Tuple[float, float, float, float]) -> None:
                self.name_en = "adhoc"
                self.name_zh = "adhoc"
                self.province = ""
                self.bbox = bbox

        # Sparse POI tagging is common in otherwise valid OSM corridors.  The
        # default source selector therefore discovers roads broadly and ranks
        # POI-rich context first, instead of refusing to generate a baseline.
        roads = discover_poi_roads(
            _AdhocCity(aoi_bbox),
            osm_cache_dir,
            min_poi_count=0,
            min_poi_score=0.0,
            min_core_poi_count=0,
        )
        auto_discovered = True
        write_discovered_roads_jsonl(roads, discovered_path)
        write_discovered_roads_metadata(metadata_path, aoi_bbox)
        cached_rows = list(load_discovered_road_records(discovered_path))

    if not cached_rows:
        raise RuntimeError("No usable roads found for the current area.")

    ordered_rows = sorted(
        cached_rows,
        key=lambda row: (
            int(row.get("osm_id", 0)),
            float(row.get("road_length_m", 0.0)),
            tuple(float(value) for value in row.get("bbox", ())),
        ),
    )
    rng = random.Random(int(seed))
    rng.shuffle(ordered_rows)
    ordered_rows.sort(
        key=lambda row: 0 if qualifies_poi_counts(row.get("poi_types", {})) else 1,
    )
    if str(road_selection).strip().lower() == "walkable_neighborhood":
        preferred_rows = [
            row for row in ordered_rows
            if is_walkable_neighborhood_highway(str(row.get("highway_type", "") or ""))
        ]
        fallback_rows = [
            row for row in ordered_rows
            if not is_walkable_neighborhood_highway(str(row.get("highway_type", "") or ""))
        ]
        ordered_rows = preferred_rows + fallback_rows

    sparse_candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for row in ordered_rows:
        probe_metrics = probe_discovered_road_context_metrics(
            row,
            osm_cache_dir=osm_cache_dir,
            road_width_m=float(road_width_m),
            sidewalk_width_m=float(sidewalk_width_m),
            lane_count=int(lane_count),
            road_selection=str(road_selection),
        )
        effective_counts = dict(probe_metrics.get("poi_counts", {}) or {})
        if qualifies_poi_counts(effective_counts):
            return dict(row), bool(auto_discovered), probe_metrics
        sparse_candidates.append((dict(row), probe_metrics))

    if sparse_candidates:
        row, probe_metrics = sparse_candidates[0]
        probe_metrics = {
            **dict(probe_metrics),
            "poi_context_sparse": True,
            "poi_context_message": "No POI-rich road was available; selected a valid sparse-POI road.",
        }
        return row, bool(auto_discovered), probe_metrics
    raise RuntimeError("Auto-discovered roads could not be evaluated for the current width setup.")


def resolve_scene_context(
    scene_context: Mapping[str, Any] | SceneContext | None,
    *,
    config: StreetComposeConfig,
    artifacts_dir: Path,
    osm_cache_dir: Path | None = None,
) -> ResolvedSceneContext:
    """Resolve workbench scene context into runtime compose-config overrides."""

    normalized = sanitize_scene_context(scene_context)
    requested_bbox = normalized.aoi_bbox
    cache_dir = Path(osm_cache_dir or getattr(config, "osm_cache_dir", DEFAULT_OSM_CACHE_DIR)).expanduser().resolve()
    if normalized.layout_mode not in {"osm", "osm_multiblock"}:
        return ResolvedSceneContext(
            scene_context=normalized,
            requested_aoi_bbox=requested_bbox,
            effective_aoi_bbox=None,
            city_name_en=normalized.city_name_en,
            osm_cache_dir=cache_dir,
        )

    if requested_bbox is None:
        raise RuntimeError("OSM scene context requires an AOI bbox.")

    if normalized.layout_mode == "osm_multiblock":
        return ResolvedSceneContext(
            scene_context=normalized,
            requested_aoi_bbox=requested_bbox,
            effective_aoi_bbox=requested_bbox,
            city_name_en=normalized.city_name_en,
            osm_cache_dir=cache_dir,
            road_selection="all",
            selected_road_source="multiblock_aoi",
            probe_metrics={"semantic_mode": OSM_SEMANTIC_RULESET_VERSION},
        )

    selected_road, auto_discovered, probe_metrics = select_auto_discovered_road(
        artifacts_dir=Path(artifacts_dir).expanduser().resolve(),
        osm_cache_dir=cache_dir,
        aoi_bbox=requested_bbox,
        seed=int(config.seed),
        road_width_m=float(config.road_width_m),
        sidewalk_width_m=float(config.sidewalk_width_m),
        lane_count=int(config.lane_count),
        road_selection=DEFAULT_ROAD_SELECTION,
    )
    effective_bbox = tuple(float(value) for value in selected_road.get("bbox", requested_bbox))
    return ResolvedSceneContext(
        scene_context=normalized,
        requested_aoi_bbox=requested_bbox,
        effective_aoi_bbox=effective_bbox,
        city_name_en=normalized.city_name_en,
        osm_cache_dir=cache_dir,
        road_selection=DEFAULT_ROAD_SELECTION,
        selected_road_osm_id=int(selected_road["osm_id"]),
        selected_road_discovered_poi_count=int(selected_road.get("poi_count", 0)),
        selected_road_discovered_poi_score=float(
            selected_road.get("poi_score", poi_weighted_score(selected_road.get("poi_types", {})))
        ),
        selected_road_discovered_core_poi_count=int(
            selected_road.get("core_poi_count", core_poi_count(selected_road.get("poi_types", {})))
        ),
        selected_road_source="auto_discovered" if auto_discovered else "cached_discovery",
        probe_metrics=probe_metrics,
    )


def _preview_compose_config(
    aoi_bbox: Tuple[float, float, float, float],
    compose_config_patch: Mapping[str, Any] | None = None,
    *,
    osm_cache_dir: Path | None = None,
) -> StreetComposeConfig:
    patch = dict(compose_config_patch or {})
    return StreetComposeConfig(
        query=str(patch.get("query", "OSM semantic multiblock preview") or "OSM semantic multiblock preview"),
        length_m=float(patch.get("length_m", 80.0) or 80.0),
        road_width_m=float(patch.get("road_width_m", 7.0) or 7.0),
        sidewalk_width_m=float(patch.get("sidewalk_width_m", 2.4) or 2.4),
        lane_count=int(patch.get("lane_count", 2) or 2),
        density=float(patch.get("density", 1.0) or 1.0),
        seed=int(patch.get("seed", 42) or 42),
        topk_per_category=int(patch.get("topk_per_category", 5) or 5),
        max_trials_per_slot=int(patch.get("max_trials_per_slot", 10) or 10),
        layout_mode="osm_multiblock",
        aoi_bbox=tuple(float(value) for value in aoi_bbox),
        osm_cache_dir=str(osm_cache_dir or patch.get("osm_cache_dir", DEFAULT_OSM_CACHE_DIR)),
        road_selection="all",
        segment_length_m=float(patch.get("segment_length_m", 35.0) or 35.0),
        osm_semantic_mode=str(patch.get("osm_semantic_mode", OSM_SEMANTIC_RULESET_VERSION) or OSM_SEMANTIC_RULESET_VERSION),
        osm_multiblock_max_roads=int(patch.get("osm_multiblock_max_roads", 12) or 12),
        osm_multiblock_max_extent_m=float(patch.get("osm_multiblock_max_extent_m", 350.0) or 350.0),
        osm_short_road_policy=str(patch.get("osm_short_road_policy", "default_style") or "default_style"),
        osm_short_road_min_length_m=float(patch.get("osm_short_road_min_length_m", 20.0) or 20.0),
        osm_context_fit_mode=str(patch.get("osm_context_fit_mode", "auto_design") or "auto_design"),
        bus_stop_eligible_road_names=_coerce_text_tuple(patch.get("bus_stop_eligible_road_names", ())),
        max_bus_stops_per_scene=int(patch.get("max_bus_stops_per_scene", 0) or 0),
        allow_demo_bus_stop_when_osm_absent=_coerce_bool(patch.get("allow_demo_bus_stop_when_osm_absent"), False),
    )


def _semantic_profile_counts_by_road(segment_profiles: Sequence[Mapping[str, Any]]) -> Dict[int, Counter[str]]:
    counts: Dict[int, Counter[str]] = defaultdict(Counter)
    for item in segment_profiles:
        profile_id = str(item.get("semantic_profile_id", "") or "").strip()
        if not profile_id:
            continue
        counts[int(item.get("road_id", 0) or 0)][profile_id] += 1
    return counts


def _selected_road_payload(
    roads: Sequence[Any],
    segment_profiles: Sequence[Mapping[str, Any]],
    *,
    config: StreetComposeConfig,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    segment_counts = Counter(int(item.get("road_id", 0) or 0) for item in segment_profiles)
    profile_counts = _semantic_profile_counts_by_road(segment_profiles)
    short_policy = str(getattr(config, "osm_short_road_policy", "semantic") or "semantic").strip().lower()
    short_min = float(getattr(config, "osm_short_road_min_length_m", 0.0) or 0.0)
    selected: List[Dict[str, Any]] = []
    short_roads: List[Dict[str, Any]] = []
    for road in roads:
        road_id = int(getattr(road, "osm_id", 0) or 0)
        length_m = float(road_length_m(road))
        segment_count = int(segment_counts.get(road_id, 0))
        is_short_default = short_policy == "default_style" and short_min > 0.0 and length_m < short_min
        payload = {
            "osm_id": road_id,
            "name": road_display_name(road),
            "highway_type": str(getattr(road, "highway_type", "") or ""),
            "point_count": int(len(getattr(road, "coords", []) or [])),
            "length_m": length_m,
            "segment_count": segment_count,
            "avg_segment_length_m": (length_m / float(segment_count)) if segment_count > 0 else 0.0,
            "semantic_profile_counts": dict(profile_counts.get(road_id, Counter())),
            "short_road_policy": "default_style" if is_short_default else "semantic",
        }
        selected.append(payload)
        if is_short_default:
            short_roads.append(payload)
    return selected, short_roads


def build_osm_semantic_preview(
    *,
    aoi_bbox: Tuple[float, float, float, float],
    osm_cache_dir: Path | None = None,
    compose_config_patch: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return semantic multiblock OSM context without running 3D generation."""

    cache_dir = Path(osm_cache_dir or DEFAULT_OSM_CACHE_DIR).expanduser().resolve()
    config = _preview_compose_config(aoi_bbox, compose_config_patch, osm_cache_dir=cache_dir)
    raw = fetch_osm_data(bbox=tuple(float(value) for value in aoi_bbox), cache_dir=cache_dir)
    features = parse_osm_features(raw)
    projected = project_to_local(features, tuple(float(value) for value in aoi_bbox))
    projected, semantic_summary = prepare_multiblock_projected_features(projected, config)
    projected, bus_stop_summary = apply_osm_bus_stop_constraints(projected, config)
    graph = build_segment_graph(projected, config)
    osm_context_fit = evaluate_osm_context_fit(graph, config)
    osm_context_fit_summary = {key: value for key, value in dict(osm_context_fit).items() if key != "segments"}
    semantic_design_layers = resolve_semantic_design_layers(config=config, road_segment_graph=graph)
    segment_profiles = segment_semantic_profile_payload(graph.nodes)
    selected_roads, short_roads = _selected_road_payload(projected.roads, segment_profiles, config=config)
    segment_profile_counts = dict(
        Counter(
            str(item.get("semantic_profile_id", "") or "").strip()
            for item in segment_profiles
            if str(item.get("semantic_profile_id", "") or "").strip()
        )
    )
    semantic_summary = {
        **dict(semantic_summary),
        "segment_length_m": float(getattr(config, "segment_length_m", 35.0) or 35.0),
        "segment_semantic_profile_counts": segment_profile_counts,
        "skeleton_design_profile_counts": segment_profile_counts,
        "semantic_design_layers": dict(semantic_design_layers),
        "short_roads_default_style": list(short_roads),
        "bus_stop_counts": dict(bus_stop_summary.get("counts", {}) or {}),
        "bus_stop_eligible_road_ids": list(bus_stop_summary.get("eligible_road_ids", []) or []),
        "bus_stop_provenance": list(bus_stop_summary.get("provenance", []) or []),
        "osm_context_fit": dict(osm_context_fit_summary),
    }
    return {
        "semantic_mode": OSM_SEMANTIC_RULESET_VERSION,
        "aoi_bbox": [float(value) for value in aoi_bbox],
        "osm_cache_dir": str(cache_dir),
        "input": {
            "road_count": int(len(features.roads)),
            "building_count": int(len(features.buildings)),
            "land_use_polygon_count": int(len(features.land_use_polygons)),
            "semantic_point_counts": {
                point_type: int(len(points))
                for point_type, points in getattr(features, "semantic_points_by_type", {}).items()
                if points
            },
        },
        "summary": dict(semantic_summary),
        "selected_roads": selected_roads,
        "short_roads_default_style": short_roads,
        "bus_stop_counts": dict(bus_stop_summary.get("counts", {}) or {}),
        "bus_stop_eligible_road_ids": list(bus_stop_summary.get("eligible_road_ids", []) or []),
        "bus_stop_provenance": list(bus_stop_summary.get("provenance", []) or []),
        "osm_context_fit": dict(osm_context_fit),
        "semantic_design_layers": dict(semantic_design_layers),
        "osm_semantic_blocks": [block.to_dict() for block in getattr(projected, "semantic_blocks", []) or []],
        "segment_semantic_profiles": list(segment_profiles),
        "road_segment_graph_summary": graph.summary(),
    }
