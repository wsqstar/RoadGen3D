#!/usr/bin/env python3
"""M5 Step 2: Build carriageway / sidewalk placement zones from OSM data."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from roadgen3d.osm_ingest import fetch_osm_data, parse_osm_features, project_to_local
from roadgen3d.placement_zones import build_placement_context, export_zones_geojson


def main() -> None:
    parser = argparse.ArgumentParser(description="Build M5 placement zones from OSM data.")
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        required=True,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        help="AOI bounding box in WGS-84.",
    )
    parser.add_argument("--sidewalk-width-m", type=float, default=2.5, help="Sidewalk width (metres).")
    parser.add_argument("--cache-dir", type=str, default="artifacts/m5/osm_cache")
    parser.add_argument("--out-dir", type=str, default="artifacts/m5", help="Output directory for GeoJSON.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    bbox = tuple(args.bbox)

    # Re-use cached OSM data
    raw = fetch_osm_data(bbox=bbox, cache_dir=Path(args.cache_dir))
    features = parse_osm_features(raw)
    projected = project_to_local(features, bbox)

    # Minimal config-like object for build_placement_context
    class _Cfg:
        sidewalk_width_m = args.sidewalk_width_m

    ctx = build_placement_context(projected, _Cfg())

    out_dir = Path(args.out_dir)
    geojson_path = export_zones_geojson(ctx, out_dir / "zones.geojson")

    print(f"\n--- Placement Zones ---")
    print(f"carriageway area : {ctx.carriageway.area:.1f} m²")
    print(f"sidewalk area    : {ctx.sidewalk_zone.area:.1f} m²")
    print(f"entrance points  : {len(ctx.entrance_points)}")
    print(f"bus_stop points  : {len(ctx.bus_stop_points)}")
    print(f"fire points      : {len(ctx.fire_points)}")
    print(f"GeoJSON saved    : {geojson_path}")


if __name__ == "__main__":
    main()
