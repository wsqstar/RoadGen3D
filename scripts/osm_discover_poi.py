#!/usr/bin/env python3
"""Discover POI-rich road segments from Chinese city OSM data.

For each city in the registry, expands the search area to ~2 km x 2 km,
fetches OSM data, and identifies roads that are long enough (>=100 m) and
have sufficient nearby POI (>=2).  Results are written to a JSONL file
compatible with ``m6_01_collect_program_data.py --osm-bboxes-jsonl``.

Usage examples::

    # Dry-run — list cities without downloading
    python scripts/m5_04_discover_poi_roads.py --dry-run

    # Discover for all cities
    python scripts/m5_04_discover_poi_roads.py

    # Specific cities only
    python scripts/m5_04_discover_poi_roads.py --cities beijing,shanghai,shenzhen

    # Custom thresholds
    python scripts/m5_04_discover_poi_roads.py --min-road-length-m 150 --min-poi-count 3
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from roadgen3d.china_cities import CHINA_CITY_REGISTRY, CityRecord, get_city_by_name  # noqa: E402
from roadgen3d.road_discovery import (  # noqa: E402
    discover_poi_roads,
    write_discovered_roads_jsonl,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover POI-rich road segments from Chinese city OSM data",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=ROOT / "artifacts" / "m5" / "osm_cache",
        help="Directory to store cached Overpass responses",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "artifacts" / "m5" / "discovered_poi_roads.jsonl",
        help="Output JSONL path",
    )
    parser.add_argument(
        "--cities",
        type=str,
        default=None,
        help="Comma-separated city name_en subset (e.g. beijing,shanghai). Default: all",
    )
    parser.add_argument("--min-road-length-m", type=float, default=100.0)
    parser.add_argument("--min-poi-count", type=int, default=2)
    parser.add_argument("--road-buffer-m", type=float, default=15.0)
    parser.add_argument("--bbox-padding-m", type=float, default=30.0)
    parser.add_argument("--expand-margin-deg", type=float, default=0.01)
    parser.add_argument(
        "--delay-sec",
        type=float,
        default=2.0,
        help="Delay between Overpass API requests in seconds (default: 2.0)",
    )
    parser.add_argument("--force-refetch", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without downloading")
    return parser.parse_args()


def _resolve_cities(city_filter: str | None) -> list[CityRecord]:
    if city_filter is None:
        return list(CHINA_CITY_REGISTRY)
    names = [n.strip() for n in city_filter.split(",") if n.strip()]
    cities: list[CityRecord] = []
    for name in names:
        city = get_city_by_name(name)
        if city is None:
            print(f"  [WARN] Unknown city: {name!r}, skipping")
        else:
            cities.append(city)
    return cities


def main() -> None:
    args = _parse_args()
    cache_dir = Path(args.cache_dir).resolve()
    out_path = Path(args.out).resolve()
    cities = _resolve_cities(args.cities)

    if not cities:
        print("No cities to process.")
        return

    print(f"Cities: {len(cities)}  Cache dir: {cache_dir}")
    print(f"Criteria: length >= {args.min_road_length_m}m, POI >= {args.min_poi_count}")
    print(f"Buffer: {args.road_buffer_m}m  Padding: {args.bbox_padding_m}m  Expand: ±{args.expand_margin_deg}°")
    print(f"Output: {out_path}")
    print("-" * 60)

    all_roads = []
    seen_osm_ids: dict[int, int] = {}  # osm_id -> index in all_roads
    cities_with_roads = 0
    failed_cities: list[str] = []

    for idx, city in enumerate(cities, 1):
        label = f"[{idx:3d}/{len(cities)}] {city.name_zh} ({city.name_en})"

        if args.dry_run:
            print(f"  {label}  (dry-run)")
            continue

        try:
            print(f"  {label}  discovering...", end="", flush=True)
            roads = discover_poi_roads(
                city,
                cache_dir,
                min_road_length_m=args.min_road_length_m,
                min_poi_count=args.min_poi_count,
                road_buffer_m=args.road_buffer_m,
                bbox_padding_m=args.bbox_padding_m,
                expand_margin_deg=args.expand_margin_deg,
                force_refetch=args.force_refetch,
            )

            # Deduplicate by osm_id, keeping higher poi_count
            for road in roads:
                existing_idx = seen_osm_ids.get(road.osm_id)
                if existing_idx is not None:
                    if road.poi_count > all_roads[existing_idx].poi_count:
                        all_roads[existing_idx] = road
                else:
                    seen_osm_ids[road.osm_id] = len(all_roads)
                    all_roads.append(road)

            if roads:
                cities_with_roads += 1
            print(f"  {len(roads)} roads found")

        except Exception as exc:
            print(f"  FAILED: {exc}")
            failed_cities.append(city.name_en)

        if idx < len(cities) and args.delay_sec > 0 and not args.dry_run:
            time.sleep(args.delay_sec)

    if args.dry_run:
        print("-" * 60)
        print(f"Would process {len(cities)} cities (dry-run, no data fetched)")
        return

    # Write results
    write_discovered_roads_jsonl(all_roads, out_path)

    # Summary statistics
    poi_totals: dict[str, int] = {"entrance": 0, "bus_stop": 0, "fire_hydrant": 0}
    for road in all_roads:
        for poi_type, count in road.poi_types.items():
            poi_totals[poi_type] = poi_totals.get(poi_type, 0) + count

    print("-" * 60)
    print(f"Cities processed: {len(cities)}")
    print(f"Cities with qualifying roads: {cities_with_roads}")
    print(f"Total qualifying roads: {len(all_roads)}")
    print(f"POI distribution: {poi_totals}")
    if failed_cities:
        print(f"Failed cities: {', '.join(failed_cities)}")
    print(f"Output written to: {out_path}")


if __name__ == "__main__":
    main()
