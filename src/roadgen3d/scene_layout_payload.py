"""Scene layout payload assembly helpers.

This module keeps the final ``scene_layout.json`` contract separate from the
large street-scene composition routine. Generation code should calculate scene
state; this module names the exported schema and assembles the durable records.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, MutableMapping, Sequence

from .beauty import style_palette, surface_roughness
from .json_safe import make_json_safe
from .scene_textures import scene_texture_pack_name

SCENE_LAYOUT_SCHEMA_VERSION = "roadgen3d.scene_layout.v1"

_LIGHTING_PARAMS: Dict[str, Dict[str, Any]] = {
    "bright_day": {
        "exposure": 1.3,
        "keyLightIntensity": 1.2,
        "fillLightIntensity": 0.8,
        "warmth": -0.1,
        "shadowStrength": 0.3,
    },
    "overcast": {
        "exposure": 1.05,
        "keyLightIntensity": 0.75,
        "fillLightIntensity": 0.95,
        "warmth": -0.15,
        "shadowStrength": 0.15,
    },
    "golden_hour": {
        "exposure": 1.18,
        "keyLightIntensity": 1.05,
        "fillLightIntensity": 0.48,
        "warmth": 0.85,
        "shadowStrength": 0.58,
    },
    "night_presentation": {
        "exposure": 1.05,
        "keyLightIntensity": 1.05,
        "fillLightIntensity": 0.24,
        "warmth": 0.2,
        "shadowStrength": 0.72,
    },
}

_VISUAL_SURFACE_ROLES = (
    "carriageway",
    "sidewalk",
    "clear_path",
    "furnishing",
    "bike_lane",
    "bus_lane",
    "grass",
    "grass_belt",
    "crossing",
    "lane_mark",
    "context_ground",
    "building_buffer",
    "tree_pit",
    "planting_soil",
    "transit_pad",
    "curb",
    "parking_lane",
    "median_green",
    "safety_island",
    "shared_street_surface",
    "colored_pavement",
)


def to_dict(value: Any) -> Dict[str, Any]:
    """Return a plain dict for common scene contract objects."""
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def derive_lighting_preset(sky_selection: Any) -> str:
    """Map sky selection to a viewer lighting preset."""
    if sky_selection is None:
        return "bright_day"
    time_of_day = str(getattr(sky_selection, "time_of_day", "day") or "day").lower()
    weather_tags = [
        str(tag).lower()
        for tag in (getattr(sky_selection, "weather_tags", ()) or ())
    ]
    illumination_tags = [
        str(tag).lower()
        for tag in (getattr(sky_selection, "illumination_tags", ()) or ())
    ]
    all_tags = set(weather_tags) | set(illumination_tags)
    if time_of_day == "night":
        return "night_presentation"
    if time_of_day == "evening":
        return "golden_hour"
    if any(tag in all_tags for tag in ("overcast", "cloudy", "foggy", "rainy")):
        return "overcast"
    return "bright_day"


def derive_lighting_params(sky_selection: Any) -> Dict[str, Any]:
    """Map sky selection to concrete lighting parameter values."""
    preset = derive_lighting_preset(sky_selection)
    params = dict(_LIGHTING_PARAMS.get(preset, _LIGHTING_PARAMS["bright_day"]))
    return {"preset": preset, **params}


def derive_environment_state(sky_selection: Any) -> Dict[str, Any]:
    """Build the runtime environment defaults consumed by the Viewer."""
    weather_mode = "clear"
    weather_intensity = 0.0
    if sky_selection is not None:
        weather_tags = {
            str(tag).strip().lower()
            for tag in (getattr(sky_selection, "weather_tags", ()) or ())
            if str(tag).strip()
        }
        if weather_tags & {"rain", "rainy", "shower", "wet"}:
            weather_mode = "rain"
            weather_intensity = 0.55
        elif weather_tags & {"fog", "foggy", "mist", "haze", "hazy"}:
            weather_mode = "fog"
            weather_intensity = 0.5
        elif weather_tags & {"overcast", "cloudy", "soft_light"}:
            weather_mode = "overcast"
            weather_intensity = 0.65
    return {
        "weather_mode": weather_mode,
        "weather_intensity": float(weather_intensity),
        "time_of_day_hours": 14.0,
        "sun_cycle_enabled": False,
        "sun_cycle_speed": "medium",
        "source": "default_runtime",
    }


def build_environment_system_summary(environment_state: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the summary block for the runtime-only environment layer."""
    return {
        "layer": "environment_runtime_v1",
        "weather_modes": ["clear", "overcast", "rain", "fog"],
        "sun_model": "artistic_day_cycle",
        "runtime_only": True,
        "environment_state": dict(environment_state),
    }


def build_visual_style_payload(
    *,
    style_preset_used: str,
    sky_selection: Any,
    building_summary: Mapping[str, Any],
    config: Any,
    default_sky_dome_asset_id: str,
    default_sky_dome_enabled: bool,
) -> Dict[str, Any]:
    """Assemble the exported visual-style record for ``scene_layout.json``."""
    style_name = str(style_preset_used)
    style_key = style_name.strip().lower()
    visual_lighting_preset = (
        "analytical_diorama"
        if style_key == "analytical_diorama_v1"
        else derive_lighting_preset(sky_selection)
    )
    visual_palette = style_palette(style_name)
    visual_roughness = surface_roughness(style_name)
    return {
        "preset": style_name,
        "lighting_preset": visual_lighting_preset,
        "surface_palette": {
            role: list(visual_palette[role])
            for role in _VISUAL_SURFACE_ROLES
            if role in visual_palette
        },
        "surface_roughness": {
            role: float(visual_roughness[role])
            for role in _VISUAL_SURFACE_ROLES
            if role in visual_roughness
        },
        "building_profile": {
            "mode": (
                "procedural_background"
                if style_key == "analytical_diorama_v1"
                else str(building_summary.get("generation_mode_used") or getattr(config, "surrounding_building_mode", "grid_growth"))
            ),
            "profile": (
                "low_saturation_parametric_facade_v1"
                if style_key == "analytical_diorama_v1"
                else "default_building_profile"
            ),
            "preferred_theme": "analytical" if style_key == "analytical_diorama_v1" else "",
            "background_layer": bool(style_key == "analytical_diorama_v1"),
            "procedural_fallback_count": int(
                building_summary.get("procedural_building_fallback_count", building_summary.get("fallback_count", 0)) or 0
            ),
        },
        "material_finish_version": (
            "analytical_diorama_finish_v1"
            if style_key == "analytical_diorama_v1"
            else "presentation_material_finish_v1"
        ),
        "scene_texture_pack": scene_texture_pack_name(str(getattr(config, "scene_texture_mode", "topdown_tiles_v1"))),
        "default_sky_dome_asset_id": default_sky_dome_asset_id if default_sky_dome_enabled else "",
        "default_sky_dome_enabled": bool(default_sky_dome_enabled),
    }


def merge_summary_sections(
    base_summary: Mapping[str, Any],
    *sections: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    """Merge shallow summary sections in export order."""
    merged = dict(base_summary)
    for section in sections:
        if section:
            merged.update(dict(section))
    return merged


def _serialized_list(values: Sequence[Any]) -> list[Any]:
    return [to_dict(value) if hasattr(value, "to_dict") else value for value in values]


def _geometry_rings(geometry: Any) -> list[list[list[float]]]:
    if geometry is None or getattr(geometry, "is_empty", True):
        return []
    polygons = []
    geom_type = str(getattr(geometry, "geom_type", "") or "")
    if geom_type == "Polygon":
        polygons = [geometry]
    elif geom_type == "MultiPolygon":
        polygons = list(getattr(geometry, "geoms", ()) or ())
    elif geom_type == "GeometryCollection":
        polygons = [
            item
            for item in getattr(geometry, "geoms", ()) or ()
            if str(getattr(item, "geom_type", "") or "") == "Polygon" and not getattr(item, "is_empty", True)
        ]
    rings: list[list[list[float]]] = []
    for polygon in polygons:
        exterior = getattr(polygon, "exterior", None)
        if exterior is None:
            continue
        coords = [
            [round(float(x), 3), round(float(y), 3)]
            for x, y in list(exterior.coords)
        ]
        if len(coords) >= 4:
            rings.append(coords)
    return rings


def _strip_geometry_records(values: Sequence[Any]) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    for record in values:
        item = dict(record)
        geometry = item.pop("geometry", None)
        if geometry is not None and "rings" not in item:
            item["rings"] = _geometry_rings(geometry)
        records.append(item)
    return records


def build_scene_layout_payload(
    *,
    query: str,
    config: Any,
    selected_object_backend: str,
    ground_selection: Any,
    sky_selection: Any,
    environment_source_dataset: str,
    environment_source_datasets: Sequence[str],
    program_result: Any,
    theme_zone_programs: Sequence[Mapping[str, Any]],
    resolved_program: Any,
    constraint_set: Any,
    solver_result: Any,
    summary: Mapping[str, Any],
    semantic_design_layers: Mapping[str, Any],
    environment_state: Mapping[str, Any],
    osm_semantic_blocks: Sequence[Mapping[str, Any]],
    segment_semantic_profiles: Sequence[Mapping[str, Any]],
    visual_style: Mapping[str, Any],
    placements: Sequence[Any],
    environment_placements: Sequence[Any],
    building_footprints: Sequence[Any],
    generated_lots: Sequence[Any],
    building_placements: Sequence[Any],
    building_retrieval_predictions: Sequence[Mapping[str, Any]],
    zoning_grid: Sequence[Mapping[str, Any]],
    placement_context: Any,
    production_steps: Sequence[Any],
    unplaced_slot_diagnostics: Sequence[Mapping[str, Any]],
    placement_log_path: str,
    placement_log_summary: Mapping[str, Any],
    outputs: MutableMapping[str, Any],
    inventory_summary: Any,
) -> Dict[str, Any]:
    """Build the top-level ``scene_layout.json`` payload."""
    program_generation_payload = to_dict(program_result)
    program_generation_payload["theme_zone_programs"] = list(theme_zone_programs)
    payload = {
        "schema_version": SCENE_LAYOUT_SCHEMA_VERSION,
        "query": query,
        "config": to_dict(config),
        "selected_object_backend": str(selected_object_backend),
        "selected_ground_materials": to_dict(ground_selection),
        "selected_sky": to_dict(sky_selection),
        "environment_source_dataset": str(environment_source_dataset),
        "environment_source_datasets": list(environment_source_datasets),
        "program_generation": program_generation_payload,
        "street_program": to_dict(resolved_program),
        "constraint_set": to_dict(constraint_set),
        "solver": to_dict(solver_result),
        "summary": dict(summary),
        "semantic_design_layers": dict(semantic_design_layers),
        "environment_state": dict(environment_state),
        "osm_semantic_blocks": list(osm_semantic_blocks),
        "segment_semantic_profiles": list(segment_semantic_profiles),
        "visual_style": dict(visual_style),
        "placements": _serialized_list(placements),
        "environment_placements": _serialized_list(environment_placements),
        "building_footprints": _serialized_list(building_footprints),
        "generated_lots": _serialized_list(generated_lots),
        "building_placements": _serialized_list(building_placements),
        "building_retrieval_predictions": list(building_retrieval_predictions),
        "zoning_grid": list(zoning_grid),
        "regions": list(getattr(placement_context, "regions", []) or []),
        "derived_regions": list(getattr(placement_context, "derived_regions", []) or []),
        "building_regions": _strip_geometry_records(getattr(placement_context, "building_regions", []) or []),
        "region_derivation_summary": dict(getattr(placement_context, "region_derivation_summary", {}) or {}),
        "functional_zones": list(getattr(placement_context, "functional_zones", []) or []),
        "surface_annotations": _strip_geometry_records(getattr(placement_context, "surface_annotations", []) or []),
        "surface_diagnostic": dict(
            getattr(placement_context, "surface_diagnostic_manifest", {}) or {}
        ),
        "production_steps": _serialized_list(production_steps),
        "unplaced_slot_diagnostics": list(unplaced_slot_diagnostics),
        "placement_decision_log": {
            "path": str(placement_log_path),
            "summary": dict(placement_log_summary),
        },
        "outputs": outputs,
        "supervision_sample": {
            "inputs": {
                "config": to_dict(config),
                "inventory_summary": to_dict(inventory_summary),
                "constraint_set": to_dict(constraint_set),
                "road_segment_graph_summary": getattr(solver_result, "road_segment_graph_summary", {}),
                "observed_poi_counts": dict(getattr(resolved_program, "observed_poi_counts", {}) or {}),
            },
            "labels": {
                "resolved_program": to_dict(resolved_program),
                "band_solutions": _serialized_list(getattr(solver_result, "band_solutions", []) or []),
                "slot_plans": _serialized_list(getattr(solver_result, "slot_plans", []) or []),
                "objective_profile": str(getattr(resolved_program, "objective_profile", "")),
            },
        },
    }
    return make_json_safe(payload)
