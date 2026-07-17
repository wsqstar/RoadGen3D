#!/usr/bin/env python3
"""Build the immutable Guangzhou road-skeleton starter package from fixed inputs.

This command never contacts Overpass. It normalizes the checked-in OSM snapshot,
selects the fixed Fazheng Road one-hop study area, and packages the deterministic
seed-42 road-base GLB produced by the reference-annotation generator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.json_safe import make_json_safe  # noqa: E402
from roadgen3d.services.osm_road_study import (  # noqa: E402
    preview_bundle_from_raw,
    select_osm_road_study_area,
)
from roadgen3d.services.osm_scene_source import osm_scene_source_response  # noqa: E402
from roadgen3d.web_viewer_dev import build_layout_manifest  # noqa: E402

SCENE_ID = "guangzhou_road_skeleton_v1"
BUNDLED_DIR = ROOT / "assets" / "starter_scenes" / SCENE_ID
RETRIEVAL_BBOX = [113.26616931271059, 23.13367933500995, 113.27325598728942, 23.13728296499005]
SEED_LOGICAL_ROAD_ID = "logical-road-582a625e6adf"
SNAPSHOT_TIMESTAMP = "2026-07-17T00:00:00Z"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(make_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _copy_if_distinct(source: Path, destination: Path) -> None:
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)


def _strip_machine_paths(value: Any) -> Any:
    """Remove build-machine paths while preserving package-relative resources."""
    if isinstance(value, dict):
        return {str(key): _strip_machine_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_machine_paths(item) for item in value]
    if isinstance(value, str) and (value.startswith("/") or value.startswith("artifacts/")):
        return ""
    return value


def _starter_layout(source_layout: dict[str, Any]) -> dict[str, Any]:
    summary = dict(source_layout.get("summary") or {})
    for key in list(summary):
        if any(token in key for token in ("asset_", "manifest", "retrieval_prediction", "render_views")):
            summary.pop(key, None)
    summary.update({
        "instance_count": 0,
        "street_furniture_instance_count": 0,
        "building_instance_count": 0,
        "starter_scene_id": SCENE_ID,
        "starter_scene_kind": "osm_road_skeleton",
        "random_seed": 42,
    })
    config = dict(source_layout.get("config") or {})
    config.update({
        "layout_mode": "reference_annotation",
        "street_furniture_profile": "none",
        "amenity_coverage_mode": "off",
        "curated_street_assets_profile": "disabled",
        "enable_surrounding_buildings": False,
        "seed": 42,
    })
    step = next(
        dict(item)
        for item in source_layout.get("production_steps") or []
        if str(item.get("step_id") or "") == "road_base"
    )
    step.update({"glb_path": "road_base.glb", "companion_path": ""})
    return _strip_machine_paths({
        "schema_version": "roadgen3d.scene_layout.v1",
        "query": "Bundled Guangzhou OSM road skeleton starter demo.",
        "config": config,
        "summary": summary,
        "street_program": source_layout.get("street_program") or {},
        "semantic_design_layers": source_layout.get("semantic_design_layers") or {},
        "visual_style": source_layout.get("visual_style") or {},
        "environment_state": source_layout.get("environment_state") or {},
        "placements": [],
        "building_footprints": [],
        "generated_lots": [],
        "building_placements": [],
        "regions": source_layout.get("regions") or [],
        "derived_regions": source_layout.get("derived_regions") or [],
        "building_regions": source_layout.get("building_regions") or [],
        "functional_zones": source_layout.get("functional_zones") or [],
        "surface_annotations": source_layout.get("surface_annotations") or [],
        "segment_semantic_profiles": source_layout.get("segment_semantic_profiles") or [],
        "scene_graph": source_layout.get("scene_graph") or {},
        "production_steps": [step],
        "outputs": {"scene_glb": "road_base.glb", "scene_layout": "scene_layout.json"},
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-osm", type=Path, default=BUNDLED_DIR / "osm_snapshot.json")
    parser.add_argument("--source-layout", type=Path, default=BUNDLED_DIR / "scene_layout.json")
    parser.add_argument("--source-road-glb", type=Path, default=BUNDLED_DIR / "road_base.glb")
    parser.add_argument("--output", type=Path, default=BUNDLED_DIR)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    raw = json.loads(args.raw_osm.read_text(encoding="utf-8"))
    preview = preview_bundle_from_raw(
        raw_osm=raw,
        aoi_bbox=RETRIEVAL_BBOX,
        source_id=SCENE_ID,
        preview_id=f"bundled-{SCENE_ID}",
    )
    selection = select_osm_road_study_area(
        preview,
        seed_logical_road_id=SEED_LOGICAL_ROAD_ID,
        hop_count=1,
        context_buffer_m=100.0,
        source_id=SCENE_ID,
    )
    response = osm_scene_source_response({
        "bbox": tuple(selection["study"]["annotation_bbox"]),
        "raw_osm": raw,
        "geojson": selection["filtered_geojson"],
        "normalized": selection["normalized"],
        "provenance": {
            "provider": "OpenStreetMap/Overpass",
            "attribution": "© OpenStreetMap contributors",
            "bbox": RETRIEVAL_BBOX,
            "raw_element_count": len(raw.get("elements", [])),
        },
    })
    response["osm_study"] = selection["study"]
    response["warnings"] = list(selection["study"]["warnings"])
    response["source"] = {
        **dict(response.get("source") or {}),
        "source_id": SCENE_ID,
        "kind": "geojson",
        "producer": "osm",
        "starter_scene": True,
    }
    response.setdefault("geojson", {}).setdefault("roadgen3d", {})["normalized_at"] = SNAPSHOT_TIMESTAMP
    response = _strip_machine_paths(response)

    _copy_if_distinct(args.raw_osm, output / "osm_snapshot.json")
    _copy_if_distinct(args.source_road_glb, output / "road_base.glb")
    _write_json(output / "osm_snapshot.geojson", response["geojson"])
    _write_json(output / "normalized_source.json", response)

    source_layout = json.loads(args.source_layout.read_text(encoding="utf-8"))
    starter_layout = _starter_layout(source_layout)
    _write_json(output / "scene_layout.json", starter_layout)

    # Build the compact 2D/Graph overlay from the same deterministic layout.
    bootstrap_dir = output / ".build"
    bootstrap_dir.mkdir(exist_ok=True)
    bootstrap_layout = bootstrap_dir / "scene_layout.json"
    bootstrap_glb = bootstrap_dir / "road_base.glb"
    bootstrap_payload = dict(starter_layout)
    bootstrap_payload["outputs"] = {"scene_glb": str(bootstrap_glb), "scene_layout": str(bootstrap_layout)}
    bootstrap_payload["production_steps"] = [{**starter_layout["production_steps"][0], "glb_path": str(bootstrap_glb)}]
    shutil.copyfile(output / "road_base.glb", bootstrap_glb)
    _write_json(bootstrap_layout, bootstrap_payload)
    manifest = _strip_machine_paths(build_layout_manifest(bootstrap_layout))
    manifest["instances"] = {}
    manifest["layout_path"] = f"/api/starter-scenes/{SCENE_ID}/manifest"
    manifest["final_scene"] = {"label": "广州道路骨架", "glb_url": "road_base.glb"}
    manifest["production_steps"] = [{"step_id": "road_base", "title": "Road Base / 道路骨架", "glb_url": "road_base.glb"}]
    _write_json(output / "viewer_manifest.json", manifest)
    shutil.rmtree(bootstrap_dir)

    package = {
        "id": SCENE_ID,
        "version": "1.0.0",
        "label": "广州道路骨架",
        "retrieval_bbox": RETRIEVAL_BBOX,
        "seed_logical_road_id": SEED_LOGICAL_ROAD_ID,
        "hop_count": 1,
        "context_buffer_m": 100,
        "random_seed": 42,
        "source_fingerprint": _sha(output / "normalized_source.json"),
        "scene_fingerprint": _sha(output / "road_base.glb"),
        "provenance": {
            "provider": "OpenStreetMap/Overpass",
            "attribution": "© OpenStreetMap contributors",
            "network_required": False,
            "osm_geojson_file": "osm_snapshot.geojson",
        },
    }
    _write_json(output / "package.json", package)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
