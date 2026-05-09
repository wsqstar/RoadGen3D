#!/usr/bin/env python3
"""Build a lightweight OSM semantic-preview artifact for a configured demo AOI."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.scene_context_service import build_osm_semantic_preview  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "configs" / "osm_demos" / "hkust_gz_350m.json"
SCHEMA_VERSION = "roadgen3d_osm_semantic_preview_v1"


class SemanticPreviewQualityError(RuntimeError):
    """Raised when an OSM semantic preview is too sparse to be a useful demo."""


def _repo_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (ROOT / path).resolve()


def load_demo_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Demo config must be a JSON object: {path}")
    bbox = data.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError(f"Demo config must define bbox as [min_lon, min_lat, max_lon, max_lat]: {path}")
    data["_config_path"] = str(path)
    return data


def _segment_profile_counts(segment_profiles: list[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(
        str(item.get("semantic_profile_id") or "").strip()
        for item in segment_profiles
        if isinstance(item, Mapping) and str(item.get("semantic_profile_id") or "").strip()
    )
    return dict(sorted(counts.items()))


def _semantic_block_count(preview: Mapping[str, Any]) -> int:
    summary = preview.get("summary") if isinstance(preview.get("summary"), Mapping) else {}
    if "semantic_block_count" in summary:
        return int(summary.get("semantic_block_count") or 0)
    blocks = preview.get("osm_semantic_blocks")
    return len(blocks) if isinstance(blocks, list) else 0


def _input_count(preview: Mapping[str, Any], key: str) -> int:
    input_summary = preview.get("input") if isinstance(preview.get("input"), Mapping) else {}
    return int(input_summary.get(key) or 0)


def _config_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    else:
        raw_items = list(value)
    return [str(item).strip() for item in raw_items if str(item).strip()]


def build_commit_ready_payload(config: Mapping[str, Any], preview: Mapping[str, Any]) -> dict[str, Any]:
    segment_profiles = list(preview.get("segment_semantic_profiles") or [])
    segment_counts = _segment_profile_counts(segment_profiles)
    compose_config = dict(config.get("compose_config") or {})
    configured_cache_dir = str(config.get("osm_cache_dir") or "artifacts/m5/osm_cache")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "demo_id": str(config.get("demo_id") or "osm_demo"),
        "name": str(config.get("name") or ""),
        "address": str(config.get("address") or ""),
        "address_source_url": str(config.get("address_source_url") or ""),
        "center": dict(config.get("center") or {}),
        "aoi_bbox": [float(value) for value in preview.get("aoi_bbox", config.get("bbox", []))],
        "layout_mode": str(config.get("layout_mode") or compose_config.get("layout_mode") or "osm_multiblock"),
        "osm_cache_dir": configured_cache_dir,
        "semantic_mode": str(preview.get("semantic_mode") or compose_config.get("osm_semantic_mode") or ""),
        "compose_config": {
            "segment_length_m": float(compose_config.get("segment_length_m") or 35.0),
            "osm_multiblock_max_roads": int(compose_config.get("osm_multiblock_max_roads") or 12),
            "osm_multiblock_max_extent_m": float(compose_config.get("osm_multiblock_max_extent_m") or 350.0),
            "osm_short_road_policy": str(compose_config.get("osm_short_road_policy") or "default_style"),
            "osm_short_road_min_length_m": float(compose_config.get("osm_short_road_min_length_m") or 20.0),
            "osm_context_fit_mode": str(compose_config.get("osm_context_fit_mode") or "auto_design"),
            "bus_stop_eligible_road_names": _config_list(compose_config.get("bus_stop_eligible_road_names")),
            "max_bus_stops_per_scene": int(compose_config.get("max_bus_stops_per_scene") or 0),
            "allow_demo_bus_stop_when_osm_absent": bool(compose_config.get("allow_demo_bus_stop_when_osm_absent", False)),
            "road_width_m": float(compose_config.get("road_width_m") or 7.0),
            "sidewalk_width_m": float(compose_config.get("sidewalk_width_m") or 2.4),
            "lane_count": int(compose_config.get("lane_count") or 2),
            "seed": int(compose_config.get("seed") or 42),
        },
        "road_count": _input_count(preview, "road_count"),
        "building_count": _input_count(preview, "building_count"),
        "land_use_polygon_count": _input_count(preview, "land_use_polygon_count"),
        "semantic_block_count": _semantic_block_count(preview),
        "segment_semantic_profile_counts": segment_counts,
        "summary": {
            **dict(preview.get("summary") or {}),
            "segment_semantic_profile_counts": segment_counts,
        },
        "input": dict(preview.get("input") or {}),
        "selected_roads": list(preview.get("selected_roads") or []),
        "short_roads_default_style": list(preview.get("short_roads_default_style") or []),
        "bus_stop_counts": dict(preview.get("bus_stop_counts") or {}),
        "bus_stop_eligible_road_ids": list(preview.get("bus_stop_eligible_road_ids") or []),
        "bus_stop_provenance": list(preview.get("bus_stop_provenance") or []),
        "osm_context_fit": dict(preview.get("osm_context_fit") or {}),
        "road_segment_graph_summary": dict(preview.get("road_segment_graph_summary") or {}),
        "osm_semantic_blocks": list(preview.get("osm_semantic_blocks") or []),
        "segment_semantic_profiles": segment_profiles,
    }
    return payload


def validate_preview_payload(payload: Mapping[str, Any], quality_gate: Mapping[str, Any] | None = None) -> None:
    gate = dict(quality_gate or {})
    min_road_count = int(gate.get("min_road_count") or 2)
    require_profiles = bool(gate.get("require_segment_semantic_profiles", True))
    road_count = int(payload.get("road_count") or 0)
    segment_profiles = list(payload.get("segment_semantic_profiles") or [])
    if road_count < min_road_count:
        raise SemanticPreviewQualityError(
            f"OSM semantic preview is too sparse: road_count={road_count}, required>={min_road_count}"
        )
    if require_profiles and not segment_profiles:
        raise SemanticPreviewQualityError("OSM semantic preview has no segment_semantic_profiles.")


def generate_semantic_preview_from_config(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    output_path: str | Path | None = None,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    config = load_demo_config(config_path)
    bbox = tuple(float(value) for value in config["bbox"])
    resolved_cache_dir = _repo_path(cache_dir or str(config.get("osm_cache_dir") or "artifacts/m5/osm_cache"))
    compose_config = dict(config.get("compose_config") or {})
    compose_config["layout_mode"] = "osm_multiblock"
    compose_config["osm_cache_dir"] = str(resolved_cache_dir)

    preview = build_osm_semantic_preview(
        aoi_bbox=bbox,
        osm_cache_dir=resolved_cache_dir,
        compose_config_patch=compose_config,
    )
    payload = build_commit_ready_payload(config, preview)
    validate_preview_payload(payload, config.get("quality_gate") if isinstance(config.get("quality_gate"), Mapping) else None)

    resolved_output_path = _repo_path(output_path or str(config.get("output_path") or "semantic_preview.json"))
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a configured OSM semantic-preview JSON artifact.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="OSM demo config JSON path.")
    parser.add_argument("--out", type=Path, default=None, help="Override output JSON path.")
    parser.add_argument("--cache-dir", type=Path, default=None, help="Override raw Overpass cache directory.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        payload = generate_semantic_preview_from_config(
            args.config,
            output_path=args.out,
            cache_dir=args.cache_dir,
        )
    except SemanticPreviewQualityError as exc:
        raise SystemExit(f"Quality gate failed: {exc}") from exc

    print("\n--- OSM Semantic Preview ---")
    print(f"demo_id                    : {payload['demo_id']}")
    print(f"bbox                       : {payload['aoi_bbox']}")
    print(f"roads                      : {payload['road_count']}")
    print(f"land_use_polygons          : {payload['land_use_polygon_count']}")
    print(f"semantic_blocks            : {payload['semantic_block_count']}")
    print(f"segment_semantic_profiles  : {payload['segment_semantic_profile_counts']}")
    print(f"bus_stop_counts            : {payload.get('bus_stop_counts', {})}")
    context_fit = dict(payload.get("osm_context_fit") or {})
    print(f"context_fit_direction      : {context_fit.get('dominant_design_direction', '')}")
    print(f"context_fit_under_segments : {context_fit.get('under_provisioned_segment_count', 0)}/{context_fit.get('assessed_segment_count', 0)}")
    print(f"osm_cache_dir              : {payload['osm_cache_dir']}")


if __name__ == "__main__":
    main()
