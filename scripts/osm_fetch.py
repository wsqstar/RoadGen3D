#!/usr/bin/env python3
"""M5 Step 1: Fetch and cache OSM data for an AOI bbox."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from roadgen3d.osm_ingest import fetch_osm_data, parse_osm_features, project_to_local


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch OSM road and POI data for M5.")
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        required=True,
        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        help="AOI bounding box in WGS-84.",
    )
    parser.add_argument("--cache-dir", type=str, default="artifacts/m5/osm_cache", help="Cache directory.")
    parser.add_argument("--force-refetch", action="store_true", help="Ignore cache and re-fetch.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    bbox = tuple(args.bbox)
    raw = fetch_osm_data(bbox=bbox, cache_dir=Path(args.cache_dir), force_refetch=args.force_refetch)
    features = parse_osm_features(raw)
    projected = project_to_local(features, bbox)

    print(f"\n--- OSM Fetch Summary ---")
    print(f"bbox          : {bbox}")
    print(f"roads         : {len(features.roads)}")
    print(f"entrances     : {len(features.entrances)}")
    print(f"bus_stops     : {len(features.bus_stops)}")
    print(f"fire_points   : {len(features.fire_points)}")
    print(f"UTM EPSG      : {projected.utm_epsg}")
    print(f"local bbox (m): {projected.bbox_m}")

    # Write a quick features summary
    out_path = Path(args.cache_dir) / "fetch_summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "bbox": list(bbox),
        "roads": len(features.roads),
        "entrances": len(features.entrances),
        "bus_stops": len(features.bus_stops),
        "fire_points": len(features.fire_points),
        "utm_epsg": projected.utm_epsg,
        "local_bbox_m": list(projected.bbox_m),
    }
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary saved : {out_path}")


if __name__ == "__main__":
    main()
