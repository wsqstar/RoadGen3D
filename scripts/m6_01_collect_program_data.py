#!/usr/bin/env python3
"""Collect distilled training data for learned_v1 StreetProgram generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.design_rules import list_constraint_profiles, load_constraint_set  # noqa: E402
from roadgen3d.layout_solver import LayoutSolverRuntime  # noqa: E402
from roadgen3d.osm_ingest import fetch_osm_data, parse_osm_features, project_to_local  # noqa: E402
from roadgen3d.osm_segment_graph import build_segment_graph  # noqa: E402
from roadgen3d.program_generator import program_to_targets, vectorize_program_input  # noqa: E402
from roadgen3d.spatial_features import build_spatial_context  # noqa: E402
from roadgen3d.street_layout import _load_real_manifest  # noqa: E402
from roadgen3d.street_program import infer_street_program  # noqa: E402
from roadgen3d.types import InventorySummary, LayoutSolverInput, ProgramGenerationInput, StreetComposeConfig  # noqa: E402

DEFAULT_QUERIES = [
    "modern clean urban street",
    "tree-lined residential street",
    "pedestrian-friendly boulevard",
    "transit-focused city corridor",
    "compact mixed-use commercial street",
    "wide suburban arterial road",
    "historic downtown main street",
    "bike-friendly neighborhood greenway",
]

# Geometry multiplier variants: (length_m_factor, density_factor)
# Each variant is applied to the base length_m and density values.
GEOMETRY_VARIANTS = [
    (1.0, 1.0),
    (0.85, 1.25),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect M6 program-generator training data.")
    parser.add_argument("--manifest", type=Path, default=Path("data/real/real_assets_manifest.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/m6/program_train.jsonl"))
    parser.add_argument("--queries", type=Path, default=Path("data/eval/queries_m4.txt"))
    parser.add_argument("--layout-modes", nargs="+", default=["template"])
    parser.add_argument("--constraint-profiles", nargs="+", default=list(list_constraint_profiles()))
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--seed-end", type=int, default=4)
    parser.add_argument("--length-m", type=float, default=80.0)
    parser.add_argument("--road-width-m", type=float, default=8.0)
    parser.add_argument("--sidewalk-width-m", type=float, default=2.5)
    parser.add_argument("--lane-count", type=int, default=2)
    parser.add_argument("--density", type=float, default=1.0)
    parser.add_argument("--topk-per-category", type=int, default=20)
    parser.add_argument("--max-trials-per-slot", type=int, default=30)
    parser.add_argument("--layout-solver", choices=["hybrid_milp_v1", "milp_template_v1", "banded"], default="hybrid_milp_v1")
    parser.add_argument("--osm-bboxes-jsonl", type=Path, default=None)
    parser.add_argument("--osm-cache-dir", type=Path, default=Path("artifacts/m5/osm_cache"))
    parser.add_argument("--use-china-cities", action="store_true",
                        help="Use all cities from china_cities registry as OSM bboxes")
    parser.add_argument("--china-cities", nargs="*", default=None,
                        help="Subset of city name_en values; default=all when --use-china-cities is set")
    parser.add_argument("--use-discovered-roads", action="store_true",
                        help="Use POI-rich roads from artifacts/m5/discovered_poi_roads.jsonl")
    return parser.parse_args()


def _load_queries(path: Path) -> List[str]:
    if not path.exists():
        return list(DEFAULT_QUERIES)
    rows = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows or list(DEFAULT_QUERIES)


def _load_bboxes(path: Path | None) -> List[tuple[float, float, float, float]]:
    if path is None or not path.exists():
        return []
    bboxes: List[tuple[float, float, float, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        bbox = tuple(float(v) for v in payload["bbox"])
        if len(bbox) == 4:
            bboxes.append(bbox)  # type: ignore[arg-type]
    return bboxes


def collect_program_data(
    args: argparse.Namespace,
    *,
    progress_callback: Optional[Callable[[Dict[str, float]], None]] = None,
) -> List[Dict[str, object]]:
    rows = _load_real_manifest(Path(args.manifest).resolve())
    inventory = InventorySummary(
        category_counts={},
        asset_ids_by_category={},
    )
    for row in rows:
        inventory.category_counts[row["category"]] = inventory.category_counts.get(row["category"], 0) + 1
        inventory.asset_ids_by_category.setdefault(row["category"], [])
    inventory = InventorySummary(
        category_counts=dict(inventory.category_counts),
        asset_ids_by_category={
            category: tuple(row["asset_id"] for row in rows if row["category"] == category)
            for category in inventory.category_counts
        },
    )

    available_categories = tuple(sorted(inventory.category_counts.keys()))
    queries = _load_queries(Path(args.queries))
    bboxes = _load_bboxes(args.osm_bboxes_jsonl)
    if getattr(args, "use_discovered_roads", False):
        discovered_path = Path("artifacts/m5/discovered_poi_roads.jsonl")
        bboxes.extend(_load_bboxes(discovered_path))
    if getattr(args, "use_china_cities", False):
        from roadgen3d.china_cities import CHINA_CITY_REGISTRY, get_city_by_name
        if args.china_cities:
            cities = [get_city_by_name(c) for c in args.china_cities]
            cities = [c for c in cities if c is not None]
        else:
            cities = list(CHINA_CITY_REGISTRY)
        bboxes.extend(city.bbox for city in cities)
    solver_runtime = LayoutSolverRuntime(backend=str(args.layout_solver))
    samples: List[Dict[str, object]] = []

    # Geometry variants to iterate over
    geo_variants = getattr(args, "geometry_variants", None) or GEOMETRY_VARIANTS

    # Pre-compute total work items for progress reporting
    total_combos = 0
    num_seeds = max(0, int(args.seed_end) - int(args.seed_start) + 1)
    for _profile in args.constraint_profiles:
        for _mode in args.layout_modes:
            _m = str(_mode).strip().lower()
            _nb = len(bboxes) if _m == "osm" else 1
            total_combos += len(queries) * num_seeds * _nb * len(geo_variants)
    total_combos = max(total_combos, 1)
    processed_combos = 0

    base_length_m = float(args.length_m)
    base_density = float(args.density)

    for profile in args.constraint_profiles:
        load_constraint_set(profile)  # validate early
        for layout_mode in args.layout_modes:
            mode = str(layout_mode).strip().lower()
            mode_bboxes = bboxes if mode == "osm" else [None]
            for query_idx, query in enumerate(queries):
                for seed in range(int(args.seed_start), int(args.seed_end) + 1):
                    for bbox_idx, bbox in enumerate(mode_bboxes):
                        for geo_idx, (len_factor, den_factor) in enumerate(geo_variants):
                            eff_length = base_length_m * len_factor
                            eff_density = base_density * den_factor
                            config = StreetComposeConfig(
                                query=query,
                                length_m=eff_length,
                                road_width_m=float(args.road_width_m),
                                sidewalk_width_m=float(args.sidewalk_width_m),
                                lane_count=int(args.lane_count),
                                density=eff_density,
                                seed=int(seed),
                                topk_per_category=int(args.topk_per_category),
                                max_trials_per_slot=int(args.max_trials_per_slot),
                                layout_mode=mode,
                                aoi_bbox=bbox,
                                design_rule_profile=str(profile),
                                layout_solver=str(args.layout_solver),
                            )
                            placement_context = None
                            graph = None
                            if mode == "osm" and bbox is not None:
                                raw = fetch_osm_data(bbox=bbox, cache_dir=Path(args.osm_cache_dir))
                                features = parse_osm_features(raw)
                                projected = project_to_local(features, bbox)
                                graph = build_segment_graph(projected, config)
                            program = infer_street_program(config, available_categories)
                            solver_result = solver_runtime.solve(
                                LayoutSolverInput(
                                    program=program,
                                    config=config,
                                    available_categories=available_categories,
                                    constraint_set=load_constraint_set(profile),
                                    placement_context=placement_context,
                                    inventory_summary=inventory,
                                    road_segment_graph=graph,
                                )
                            )
                            pg_input = ProgramGenerationInput(
                                query=query,
                                compose_config=config,
                                available_categories=available_categories,
                                constraint_profile=str(profile),
                                placement_context=placement_context,
                                inventory_summary=inventory,
                                road_segment_graph=graph,
                                poi_context=None,  # template mode has no POI context
                            )
                            samples.append(
                                {
                                    "scene_id": f"{mode}_{profile}_q{query_idx:03d}_s{seed:04d}_b{bbox_idx:02d}_g{geo_idx}",
                                    "features": vectorize_program_input(pg_input).tolist(),
                                    "targets": {
                                        key: value.tolist()
                                        for key, value in program_to_targets(solver_result.resolved_program).items()
                                    },
                                    "query": query,
                                    "layout_mode": mode,
                                    "constraint_profile": str(profile),
                                    "bbox": list(bbox) if bbox is not None else None,
                                    "street_program": solver_result.resolved_program.to_dict(),
                                    "road_segment_graph_summary": graph.summary() if graph is not None else None,
                                }
                            )
                            processed_combos += 1
                            if progress_callback is not None:
                                progress_callback({
                                    "processed_slots": float(processed_combos),
                                    "total_slots": float(total_combos),
                                    "ratio": float(processed_combos) / float(total_combos),
                                })

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, ensure_ascii=True) + "\n")
    return samples


def main() -> int:
    args = parse_args()
    try:
        rows = collect_program_data(args)
        print(f"Collected {len(rows)} program samples -> {Path(args.out).resolve()}")
        return 0
    except Exception as exc:
        print(f"Collect program data failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
