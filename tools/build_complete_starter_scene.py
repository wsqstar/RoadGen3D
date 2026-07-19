#!/usr/bin/env python3
"""Build the complete Guangzhou intersection starter from checked-in inputs.

The package is deliberately generated offline.  It reuses the fixed v2 OSM
snapshot, current ReferenceAnnotation scene generator, transparent building
massing, and a deterministic representative subset of street assets.  The
full generator may create hundreds of repeated objects along the long OSM
corridor; the starter keeps a compact, category-complete set around the main
cross junction so the first camera view reads as an intersection showcase.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.json_safe import make_json_safe  # noqa: E402
from roadgen3d.services.design_runtime import generate_scene_from_draft  # noqa: E402
from roadgen3d.services.design_types import DesignDraft, SceneContext  # noqa: E402
from roadgen3d.web_viewer_dev import build_layout_manifest  # noqa: E402


SCENE_ID = "guangzhou_complete_intersection_v3"
SOURCE_SCENE_ID = "guangzhou_road_skeleton_v2"
SOURCE_DIR = ROOT / "assets" / "starter_scenes" / SOURCE_SCENE_ID
BUNDLED_DIR = ROOT / "assets" / "starter_scenes" / SCENE_ID
SNAPSHOT_TIMESTAMP = "2026-07-17T00:00:00Z"

# Main four-arm junction in the fixed OSM snapshot's local XZ frame.
FOCUS_XZ = (171.94, -84.95)
FOCUS_EXTENT_M = 115.0

# Keep one readable example of every generated street-object category.  Trees,
# lamps and bollards repeat enough to communicate rhythm without overwhelming
# the junction; all nearby OSM building types remain as transparent massing.
REPRESENTATIVE_COUNTS = {
    "bench": 1,
    "bollard": 8,
    "lamp": 8,
    "trash": 2,
    "tree": 8,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(make_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _strip_machine_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _strip_machine_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_machine_paths(item) for item in value]
    if isinstance(value, str) and (value.startswith("/") or value.startswith("artifacts/")):
        return ""
    return value


def _position_xz(placement: Mapping[str, Any]) -> tuple[float, float]:
    position = list(placement.get("position_xyz") or (0.0, 0.0, 0.0))
    return float(position[0]), float(position[2])


def _distance_to_focus(placement: Mapping[str, Any]) -> float:
    x, z = _position_xz(placement)
    return math.hypot(x - FOCUS_XZ[0], z - FOCUS_XZ[1])


def _spatially_distinct(
    placements: Iterable[Mapping[str, Any]],
    count: int,
    *,
    min_spacing_m: float,
) -> list[dict[str, Any]]:
    ordered = sorted((dict(item) for item in placements), key=_distance_to_focus)
    selected: list[dict[str, Any]] = []
    for candidate in ordered:
        x, z = _position_xz(candidate)
        if any(math.hypot(x - sx, z - sz) < min_spacing_m for sx, sz in map(_position_xz, selected)):
            continue
        selected.append(candidate)
        if len(selected) >= count:
            return selected
    for candidate in ordered:
        if candidate not in selected:
            selected.append(candidate)
        if len(selected) >= count:
            break
    return selected


def _select_representative_placements(layout: Mapping[str, Any]) -> list[dict[str, Any]]:
    placements = [dict(item) for item in layout.get("placements") or [] if isinstance(item, Mapping)]
    selected = [item for item in placements if str(item.get("category") or "") == "building"]
    for category, count in REPRESENTATIVE_COUNTS.items():
        candidates = [item for item in placements if str(item.get("category") or "") == category]
        selected.extend(_spatially_distinct(candidates, count, min_spacing_m=8.0))
    return sorted(selected, key=lambda item: str(item.get("instance_id") or ""))


_INSTANCE_NODE_RE = re.compile(r"^(inst_\d+)")
_TREE_PIT_RE = re.compile(r"^tree_pit_(\d+)$")


def _node_is_selected(node_name: str, selected_ids: set[str]) -> bool:
    match = _INSTANCE_NODE_RE.match(node_name)
    if match:
        return match.group(1) in selected_ids
    tree_pit = _TREE_PIT_RE.match(node_name)
    if tree_pit:
        # Tree-pit indices are emitted immediately before their instance ID.
        return f"inst_{int(tree_pit.group(1)) + 1:04d}" in selected_ids
    return True


def _filter_scene(source_glb: Path, destination_glb: Path, selected_ids: set[str]) -> None:
    source_scene = trimesh.load(source_glb, force="scene")
    flattened = source_scene.graph.to_flattened()
    output = trimesh.Scene(base_frame="world")
    for index, node_name in enumerate(source_scene.graph.nodes_geometry):
        clean_node = str(node_name)
        if not _node_is_selected(clean_node, selected_ids):
            continue
        item = flattened[node_name]
        geometry_name = str(item["geometry"])
        output.add_geometry(
            source_scene.geometry[geometry_name].copy(),
            node_name=clean_node,
            geom_name=f"starter_{index:04d}_{geometry_name}",
            transform=np.asarray(item["transform"], dtype=float),
        )
    output.export(destination_glb)


def _filter_scene_graph(graph: Mapping[str, Any], selected_ids: set[str]) -> dict[str, Any]:
    payload = dict(graph)
    nodes = []
    for item in graph.get("nodes") or []:
        if not isinstance(item, Mapping):
            continue
        instance_id = str(item.get("instance_id") or "")
        node_id = str(item.get("node_id") or "")
        match = _INSTANCE_NODE_RE.search(instance_id or node_id)
        if match and match.group(1) not in selected_ids:
            continue
        nodes.append(dict(item))
    node_ids = {str(item.get("node_id") or "") for item in nodes}
    edges = [
        dict(item)
        for item in graph.get("edges") or []
        if isinstance(item, Mapping)
        and str(item.get("source_id") or "") in node_ids
        and str(item.get("target_id") or "") in node_ids
    ]
    payload.update({"nodes": nodes, "edges": edges})
    return payload


def _generate_runtime_scene(source: Mapping[str, Any], build_root: Path) -> tuple[Path, Path]:
    patch = {
        "query": "Guangzhou complete intersection starter showcase",
        "design_rule_profile": "balanced_complete_street_v1",
        "target_street_type": "mixed_use",
        "objective_profile": "balanced",
        "city_context": "guangzhou",
        "style_preset": "civic_clean_v1",
        "scene_texture_mode": "solid_color_legacy",
        "curated_street_assets_profile": "fixed_hq_v1",
        "asset_curation_mode": "curated_first",
        "building_representation": "transparent_massing",
        "street_furniture_profile": "balanced_complete",
        "street_furniture_profile_source": "manual",
        "street_furniture_profile_confidence": 1.0,
        "street_furniture_profile_reasons": ["starter_showcase"],
        "density": 0.12,
        "seed": 42,
        "amenity_coverage_mode": "try",
        "minimum_category_presence": ["tree", "lamp", "bench", "trash", "bollard"],
        "optional_category_presence": ["bus_stop"],
        "allow_demo_bus_stop_when_osm_absent": True,
        "max_bus_stops_per_scene": 1,
        "surrounding_building_mode": "footprint_based",
    }
    draft = DesignDraft(
        normalized_scene_query="广州完整十字路口示范",
        compose_config_patch=patch,
        citations_by_field={},
        design_summary="固定 OSM 十字路口、透明建筑白模与代表性街道设施",
        risk_notes=(),
    )
    context = SceneContext(
        layout_mode="reference_annotation",
        reference_annotation=dict(source["annotation"]),
        source_context={
            "source": dict(source.get("source") or {}),
            "aligned_buildings": list(source.get("aligned_buildings") or []),
            "source_alignment": dict(source.get("source_alignment") or {}),
        },
        scenario_id=SCENE_ID,
        scenario_title="广州完整十字路口示范",
    )
    result = generate_scene_from_draft(
        draft,
        generation_options={
            "out_dir": str(build_root),
            "random_seed": 42,
            "preset_id": "skip_llm",
            "skip_llm": True,
            "render_presentation_artifacts": False,
            "capture_3d_views": False,
            "build_production_artifacts": False,
            "device": "cpu",
            "local_files_only": True,
        },
        scene_context=context,
    )
    return Path(result.scene_layout_path), Path(result.scene_glb_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE_DIR / "normalized_source.json")
    parser.add_argument("--output", type=Path, default=BUNDLED_DIR)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    build_root = output / ".build_runtime"
    if build_root.exists():
        shutil.rmtree(build_root)
    build_root.mkdir(parents=True)

    source = json.loads(args.source.read_text(encoding="utf-8"))
    source["source"] = {
        **dict(source.get("source") or {}),
        "source_id": SCENE_ID,
        "starter_scene": True,
    }
    source.setdefault("geojson", {}).setdefault("roadgen3d", {})["normalized_at"] = SNAPSHOT_TIMESTAMP
    runtime_layout_path, runtime_glb_path = _generate_runtime_scene(source, build_root)
    runtime_layout = json.loads(runtime_layout_path.read_text(encoding="utf-8"))
    selected = _select_representative_placements(runtime_layout)
    selected_ids = {str(item["instance_id"]) for item in selected}
    scene_file = "complete_scene.glb"
    _filter_scene(runtime_glb_path, output / scene_file, selected_ids)

    category_counts = Counter(str(item.get("category") or "") for item in selected)
    summary = dict(runtime_layout.get("summary") or {})
    summary.update({
        "instance_count": len(selected),
        "street_furniture_instance_count": sum(count for category, count in category_counts.items() if category != "building"),
        "building_instance_count": category_counts.get("building", 0),
        "starter_scene_id": SCENE_ID,
        "starter_scene_kind": "osm_complete_intersection",
        "starter_focus_xz": list(FOCUS_XZ),
        "starter_focus_extent_m": FOCUS_EXTENT_M,
        "starter_category_counts": dict(sorted(category_counts.items())),
        "random_seed": 42,
    })
    config = dict(runtime_layout.get("config") or {})
    config.update({"building_representation": "transparent_massing", "seed": 42})
    layout = _strip_machine_paths({
        **runtime_layout,
        "query": "Bundled Guangzhou complete OSM intersection starter demo.",
        "config": config,
        "summary": summary,
        "placements": selected,
        "scene_graph": _filter_scene_graph(runtime_layout.get("scene_graph") or {}, selected_ids),
        "production_steps": [{"step_id": "complete_scene", "title": "Complete Intersection / 完整十字路口", "glb_path": scene_file, "companion_path": ""}],
        "outputs": {"scene_glb": scene_file, "scene_layout": "scene_layout.json"},
    })
    _write_json(output / "scene_layout.json", layout)
    _write_json(output / "normalized_source.json", _strip_machine_paths(source))
    shutil.copyfile(SOURCE_DIR / "osm_snapshot.json", output / "osm_snapshot.json")
    shutil.copyfile(SOURCE_DIR / "osm_snapshot.geojson", output / "osm_snapshot.geojson")

    bootstrap_dir = output / ".build_manifest"
    bootstrap_dir.mkdir(exist_ok=True)
    bootstrap_layout = bootstrap_dir / "scene_layout.json"
    bootstrap_scene = bootstrap_dir / scene_file
    bootstrap_payload = dict(layout)
    bootstrap_payload["outputs"] = {"scene_glb": str(bootstrap_scene), "scene_layout": str(bootstrap_layout)}
    bootstrap_payload["production_steps"] = [{"step_id": "complete_scene", "title": "Complete Intersection / 完整十字路口", "glb_path": str(bootstrap_scene)}]
    shutil.copyfile(output / scene_file, bootstrap_scene)
    _write_json(bootstrap_layout, bootstrap_payload)
    manifest = _strip_machine_paths(build_layout_manifest(bootstrap_layout))
    manifest["layout_path"] = f"/api/starter-scenes/{SCENE_ID}/manifest"
    manifest["final_scene"] = {"label": "广州完整十字路口", "glb_url": scene_file}
    manifest["production_steps"] = [{"step_id": "complete_scene", "title": "完整十字路口", "glb_url": scene_file}]
    manifest["default_selection"] = "final_scene"
    manifest["starter_focus"] = {"center_xz": list(FOCUS_XZ), "extent_m": FOCUS_EXTENT_M}
    _write_json(output / "viewer_manifest.json", manifest)
    shutil.rmtree(bootstrap_dir)
    shutil.rmtree(build_root)

    package = {
        "id": SCENE_ID,
        "version": "3.0.0",
        "label": "广州完整十字路口",
        "scene_file": scene_file,
        "retrieval_bbox": list(source.get("source_alignment", {}).get("source_frame", {}).get("bbox_wgs84") or []),
        "focus_xz": list(FOCUS_XZ),
        "focus_extent_m": FOCUS_EXTENT_M,
        "random_seed": 42,
        "source_fingerprint": _sha(output / "normalized_source.json"),
        "scene_fingerprint": _sha(output / scene_file),
        "category_counts": dict(sorted(category_counts.items())),
        "provenance": {
            "provider": "OpenStreetMap/Overpass",
            "attribution": "© OpenStreetMap contributors",
            "network_required": False,
            "source_starter_scene_id": SOURCE_SCENE_ID,
            "building_representation": "transparent_massing",
            "asset_selection": "deterministic_representative_subset_v1",
        },
    }
    _write_json(output / "package.json", package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
