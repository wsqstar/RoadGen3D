"""Compatibility imports for the shared SceneSource GeoJSON pipeline.

The implementation lives in :mod:`roadgen3d.scene_source_geojson` so the
course platform and the expert workbench cannot drift into separate OSM and
GeoJSON conversion rules.
"""

from roadgen3d.scene_source_geojson import (
    SCHEMA_VERSION,
    annotation_image_for_bbox,
    canonicalize_geojson,
    normalize_teaching_geojson,
    osm_features_to_geojson,
    raw_osm_to_geojson,
    round_trip_report,
)

__all__ = [
    "SCHEMA_VERSION",
    "annotation_image_for_bbox",
    "canonicalize_geojson",
    "normalize_teaching_geojson",
    "osm_features_to_geojson",
    "raw_osm_to_geojson",
    "round_trip_report",
]
