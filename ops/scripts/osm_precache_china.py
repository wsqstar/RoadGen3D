#!/usr/bin/env python3
"""Batch pre-cache OSM data for major Chinese cities.

Usage examples::

    # Dry-run — list cities and cache status without downloading
    python scripts/m5_03_precache_china_cities.py --dry-run

    # Download all cities
    python scripts/m5_03_precache_china_cities.py

    # Download specific cities only
    python scripts/m5_03_precache_china_cities.py --cities beijing,shanghai,shenzhen

    # Force re-download everything
    python scripts/m5_03_precache_china_cities.py --force-refetch --delay-sec 3.0
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from roadgen3d.china_cities import CHINA_CITY_REGISTRY, CityRecord, get_city_by_name  # noqa: E402
from roadgen3d.osm_ingest import fetch_osm_data  # noqa: E402


def _bbox_hash(bbox: tuple[float, float, float, float]) -> str:
    key = f"{bbox[0]:.6f},{bbox[1]:.6f},{bbox[2]:.6f},{bbox[3]:.6f}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pre-cache OSM data for Chinese cities")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=ROOT / "artifacts" / "m5" / "osm_cache",
        help="Directory to store cached Overpass responses",
    )
    parser.add_argument(
        "--cities",
        type=str,
        default=None,
        help="Comma-separated city name_en subset (e.g. beijing,shanghai). Default: all",
    )
    parser.add_argument(
        "--force-refetch",
        action="store_true",
        help="Force re-download even if cache exists",
    )
    parser.add_argument(
        "--delay-sec",
        type=float,
        default=2.0,
        help="Delay between Overpass API requests in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan without downloading",
    )
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
    cities = _resolve_cities(args.cities)

    if not cities:
        print("No cities to process.")
        return

    print(f"Cities: {len(cities)}  Cache dir: {cache_dir}  Force: {args.force_refetch}")
    print("-" * 60)

    cached = 0
    downloaded = 0
    failed = 0
    failed_cities: list[str] = []

    for idx, city in enumerate(cities, 1):
        bbox_hash = _bbox_hash(city.bbox)
        cache_path = cache_dir / f"overpass_{bbox_hash}.json"
        status = "cached" if cache_path.exists() and not args.force_refetch else "pending"

        label = f"[{idx:3d}/{len(cities)}] {city.name_zh} ({city.name_en})"
        bbox_str = f"({city.bbox[0]:.4f}, {city.bbox[1]:.4f}, {city.bbox[2]:.4f}, {city.bbox[3]:.4f})"

        if args.dry_run:
            print(f"  {label}  bbox={bbox_str}  status={status}")
            if status == "cached":
                cached += 1
            continue

        if status == "cached":
            print(f"  {label}  [cached]")
            cached += 1
            continue

        try:
            print(f"  {label}  downloading...", end="", flush=True)
            raw = fetch_osm_data(bbox=city.bbox, cache_dir=cache_dir, force_refetch=args.force_refetch)
            n_elements = len(raw.get("elements", []))
            print(f"  OK ({n_elements} elements)")
            downloaded += 1
        except Exception as exc:
            print(f"  FAILED: {exc}")
            failed += 1
            failed_cities.append(city.name_en)

        if idx < len(cities) and args.delay_sec > 0:
            time.sleep(args.delay_sec)

    print("-" * 60)
    action = "Would process" if args.dry_run else "Done"
    print(f"{action}: {cached} cached, {downloaded} downloaded, {failed} failed")
    if failed_cities:
        print(f"Failed cities: {', '.join(failed_cities)}")


if __name__ == "__main__":
    main()
