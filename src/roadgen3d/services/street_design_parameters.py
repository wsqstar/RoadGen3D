"""Versioned, deterministic street-design parameter contract.

This module is deliberately independent from LLM and retrieval runtimes.  Both
the product UI and an optional parameter-proposal service must pass through the
same validator/compiler before the existing scene composer is invoked.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from ..street_priors import DEFAULT_CATEGORIES


SCHEMA_VERSION_V1 = "roadgen3d.street-design-parameters.v1"
SCHEMA_VERSION = "roadgen3d.street-design-parameters.v2"
CONTROL_SCHEMA_VERSION = "roadgen3d.street-design-parameter-controls.v1"
PARAMETER_SOURCES = frozenset({"source", "manual", "system_default"})
ALLOWED_ZONES = frozenset({"sidewalk", "furnishing", "frontage", "planting", "transit_edge"})
FURNITURE_STYLES = frozenset({"civic_clean", "lush_natural", "transit_modern"})
LEVELS = ("low", "medium", "high")


SKELETON_PARAMETER_CONTROLS: Dict[str, Dict[str, Any]] = {
    "laneCount": {"values": {"low": 2, "medium": 4, "high": 6}, "minimum": 1, "maximum": 8, "unit": "count"},
    # Lane width is a generation parameter, not a preset-only standard.  Keep
    # the presets as guidance but accept every positive, geometrically usable
    # value so an OSM source value is never shown as editable then rejected.
    "laneWidthM": {"values": {"low": 2.75, "medium": 3.25, "high": 3.75}, "minimum": 0.5, "unit": "m"},
    "sidewalkWidthM": {"values": {"low": 1.8, "medium": 3.0, "high": 4.5}, "minimum": 1.0, "maximum": 12.0, "unit": "m"},
    "furnishingWidthM": {"values": {"low": 0.6, "medium": 1.2, "high": 1.8}, "minimum": 0.0, "maximum": 5.0, "unit": "m"},
    "junctionCornerRadiusM": {"values": {"low": 3.0, "medium": 5.5, "high": 8.0}, "minimum": 1.0, "maximum": 20.0, "unit": "m"},
    "medianWidthM": {"values": {"low": 1.2, "medium": 2.0, "high": 3.0}, "minimum": 0.8, "maximum": 8.0, "unit": "m"},
}

GLOBAL_DENSITY_CONTROL: Dict[str, Any] = {
    "values": {"low": 0.6, "medium": 1.0, "high": 1.4},
    "minimum": 0.0,
    "maximum": 2.0,
}

FURNITURE_CATEGORY_CONTROLS: Dict[str, Dict[str, Any]] = {
    "bench": {"values": {"low": 2.0, "medium": 4.0, "high": 6.0}, "minimumSpacingM": 8.0, "roadSetbackM": 0.3, "allowedZones": ["sidewalk", "furnishing", "frontage"]},
    "lamp": {"values": {"low": 4.0, "medium": 6.0, "high": 8.0}, "minimumSpacingM": 10.0, "roadSetbackM": 0.3, "allowedZones": ["furnishing", "sidewalk"]},
    "trash": {"values": {"low": 2.0, "medium": 3.0, "high": 5.0}, "minimumSpacingM": 10.0, "roadSetbackM": 0.3, "allowedZones": ["furnishing", "sidewalk", "frontage"]},
    "tree": {"values": {"low": 5.0, "medium": 8.0, "high": 12.0}, "minimumSpacingM": 6.0, "roadSetbackM": 0.6, "allowedZones": ["planting", "furnishing", "frontage"]},
    "bus_stop": {"values": {"low": 0.5, "medium": 1.0, "high": 2.0}, "minimumSpacingM": 35.0, "roadSetbackM": 0.5, "allowedZones": ["transit_edge", "sidewalk"]},
    "mailbox": {"values": {"low": 0.5, "medium": 1.0, "high": 2.0}, "minimumSpacingM": 30.0, "roadSetbackM": 0.3, "allowedZones": ["frontage", "sidewalk"]},
    "hydrant": {"values": {"low": 1.0, "medium": 2.0, "high": 3.0}, "minimumSpacingM": 20.0, "roadSetbackM": 0.3, "allowedZones": ["furnishing", "sidewalk"]},
    "bollard": {"values": {"low": 4.0, "medium": 8.0, "high": 12.0}, "minimumSpacingM": 2.0, "roadSetbackM": 0.2, "allowedZones": ["furnishing", "sidewalk"]},
}


class ParameterSpecError(ValueError):
    """Raised when a parameter spec tries to escape the supported design space."""


@dataclass(frozen=True)
class CompiledStreetDesignParameters:
    spec: Dict[str, Any]
    fingerprint: str
    compose_config_patch: Dict[str, Any]
    generation_options: Dict[str, Any]
    parameter_sources_by_field: Dict[str, str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec": copy.deepcopy(self.spec),
            "fingerprint": self.fingerprint,
            "compose_config_patch": copy.deepcopy(self.compose_config_patch),
            "generation_options": copy.deepcopy(self.generation_options),
            "parameter_sources_by_field": dict(self.parameter_sources_by_field),
        }


def _category(
    *,
    enabled: bool,
    spacing: float,
    minimum: float,
    zones: tuple[str, ...],
    target: float | None = None,
    setback: float = 0.3,
) -> Dict[str, Any]:
    value: Dict[str, Any] = {
        "enabled": enabled,
        "preferredSpacingM": spacing,
        "minimumSpacingM": minimum,
        "roadSetbackM": setback,
        "allowedZones": list(zones),
    }
    if target is not None:
        value["targetCountPer100M"] = target
    return value


def _furniture_profile(profile_id: str) -> Dict[str, Dict[str, Any]]:
    disabled = profile_id == "none"
    configs = {
        "bench": _category(enabled=not disabled, spacing=24, minimum=8, zones=("sidewalk", "furnishing", "frontage"), target=3),
        "lamp": _category(enabled=not disabled, spacing=18, minimum=10, zones=("furnishing", "sidewalk"), target=6),
        "trash": _category(enabled=not disabled, spacing=28, minimum=10, zones=("furnishing", "sidewalk", "frontage"), target=3),
        "tree": _category(enabled=not disabled, spacing=12, minimum=6, zones=("planting", "furnishing", "frontage"), target=8, setback=0.6),
        "bus_stop": _category(enabled=False, spacing=60, minimum=35, zones=("transit_edge", "sidewalk"), target=0, setback=0.5),
        "mailbox": _category(enabled=False, spacing=60, minimum=30, zones=("frontage", "sidewalk"), target=0),
        "hydrant": _category(enabled=False, spacing=40, minimum=20, zones=("furnishing", "sidewalk"), target=0),
        "bollard": _category(enabled=not disabled, spacing=6, minimum=2, zones=("furnishing", "sidewalk"), target=10, setback=0.2),
    }
    if profile_id == "pedestrian_friendly":
        configs["bench"].update(targetCountPer100M=5, preferredSpacingM=18)
        configs["tree"].update(targetCountPer100M=10, preferredSpacingM=10)
    elif profile_id == "commercial_vitality":
        configs["bench"].update(targetCountPer100M=4)
        configs["trash"].update(targetCountPer100M=5, preferredSpacingM=18)
        configs["mailbox"].update(enabled=True, targetCountPer100M=1)
    elif profile_id == "transit_priority":
        configs["bus_stop"].update(enabled=True, targetCountPer100M=2)
        configs["lamp"].update(targetCountPer100M=8, preferredSpacingM=14)
        configs["tree"].update(targetCountPer100M=5)
    elif profile_id == "park_landscape":
        configs["tree"].update(targetCountPer100M=12, preferredSpacingM=8)
        configs["bench"].update(targetCountPer100M=6, preferredSpacingM=15)
        configs["bollard"].update(targetCountPer100M=4)
    elif profile_id == "quiet_residential":
        configs["tree"].update(targetCountPer100M=9, preferredSpacingM=11)
        configs["bench"].update(targetCountPer100M=2, preferredSpacingM=35)
        configs["trash"].update(targetCountPer100M=2)
        configs["bollard"].update(targetCountPer100M=4)
    return configs


_PROFILE_ROWS = {
    "road_skeleton_none": ("无家具道路骨架", "Road skeleton without furniture", "green_walkable", "none", 0.0, 2, 3.25, 2.4, 0.0),
    "balanced_complete": ("平衡完整街道", "Balanced complete street", "green_walkable", "balanced_complete", 1.0, 2, 3.25, 2.8, 1.0),
    "pedestrian_friendly": ("步行友好", "Pedestrian friendly", "walkable_commercial", "pedestrian_friendly", 1.15, 2, 3.0, 3.5, 1.4),
    "commercial_vitality": ("商业活力", "Commercial vitality", "walkable_commercial", "commercial_vitality", 1.1, 2, 3.1, 3.2, 1.2),
    "transit_priority": ("公交优先", "Transit priority", "transit_priority", "transit_priority", 1.0, 4, 3.25, 2.8, 1.8),
    "park_landscape": ("公园景观", "Park landscape", "green_walkable", "park_landscape", 1.2, 2, 3.0, 3.5, 1.6),
    "quiet_residential": ("安静居住", "Quiet residential", "quiet_residential", "quiet_residential", 0.8, 2, 3.0, 2.8, 1.0),
}


def _profile_payload(profile_id: str) -> Dict[str, Any]:
    label_zh, label_en, skeleton_profile, furniture_profile, density, lanes, lane_width, sidewalk, furnishing = _PROFILE_ROWS[profile_id]
    return {
        "profileId": profile_id,
        "label": {"zh": label_zh, "en": label_en},
        "skeleton": {
            "profileId": skeleton_profile,
            "roadWidthPolicy": "lane_count_x_lane_width",
            "laneCount": lanes,
            "laneWidthM": lane_width,
            "sidewalkWidthM": sidewalk,
            "furnishingWidthM": furnishing,
            "curbWidthM": 0.12,
            "junctionCornerPolicy": "source",
            "curbRamp": {"enabled": False, "side": "right", "positionRatio": 0.5},
        },
        "furniture": {
            "profileId": furniture_profile,
            "globalDensity": density,
            "categories": _furniture_profile(furniture_profile),
        },
        "buildings": {"representation": "transparent_massing", "footprintLocked": True},
        "seed": 42,
    }


PARAMETER_PROFILE_REGISTRY: Dict[str, Dict[str, Any]] = {
    profile_id: _profile_payload(profile_id) for profile_id in _PROFILE_ROWS
}


def list_parameter_profiles() -> list[Dict[str, Any]]:
    return [copy.deepcopy(PARAMETER_PROFILE_REGISTRY[key]) for key in _PROFILE_ROWS]


def _preferred_spacing_for_target(target: float) -> float:
    return round(min(240.0, max(2.0, 100.0 / max(float(target), 1e-6))), 3)


def _default_v2_categories() -> Dict[str, Dict[str, Any]]:
    categories: Dict[str, Dict[str, Any]] = {}
    for category, control in FURNITURE_CATEGORY_CONTROLS.items():
        target = float(control["values"]["medium"])
        categories[category] = {
            "enabled": False,
            "targetCountPer100M": target,
            "preferredSpacingM": _preferred_spacing_for_target(target),
            "minimumSpacingM": float(control["minimumSpacingM"]),
            "roadSetbackM": float(control["roadSetbackM"]),
            "allowedZones": list(control["allowedZones"]),
        }
    return categories


def build_default_street_design_parameter_spec_v2(
    *,
    source_revision: int,
    source_fingerprint: str,
    source_values: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build the explicit no-preset product default.

    ``source_values`` may contain exact normalized annotation values.  Missing
    fields use the public medium control value, while every furniture category
    remains disabled until the user explicitly enables it.
    """

    supplied = dict(source_values or {})
    medium = lambda field: SKELETON_PARAMETER_CONTROLS[field]["values"]["medium"]
    skeleton = {
        "laneCount": supplied.get("laneCount", medium("laneCount")),
        "laneWidthM": supplied.get("laneWidthM", medium("laneWidthM")),
        "sidewalkWidthM": supplied.get("sidewalkWidthM", medium("sidewalkWidthM")),
        "furnishingWidthM": supplied.get("furnishingWidthM", medium("furnishingWidthM")),
        "curbWidthM": supplied.get("curbWidthM", 0.12),
        "junctionCornerPolicy": supplied.get("junctionCornerPolicy", "source"),
        "median": {"enabled": False, "kind": "raised", "widthM": medium("medianWidthM")},
        "busStop": {"enabled": False, "placement": "curbside"},
        "curbRamp": {"enabled": False, "side": "right", "positionRatio": 0.5},
    }
    if skeleton["junctionCornerPolicy"] == "fixed":
        skeleton["junctionCornerRadiusM"] = supplied.get(
            "junctionCornerRadiusM", medium("junctionCornerRadiusM")
        )
    return validate_street_design_parameter_spec({
        "schemaVersion": SCHEMA_VERSION,
        "source": {
            "sourceRevision": int(source_revision),
            "sourceFingerprint": str(source_fingerprint).strip(),
            "geometryLocked": True,
        },
        "skeleton": skeleton,
        "furniture": {
            "globalDensity": GLOBAL_DENSITY_CONTROL["values"]["medium"],
            "style": "civic_clean",
            "categories": _default_v2_categories(),
        },
        "buildings": {"representation": "transparent_massing", "footprintLocked": True},
        "seed": 42,
    })


def parameter_control_registry() -> Dict[str, Any]:
    """Return the authoritative level-to-value mapping consumed by both UIs."""

    categories = copy.deepcopy(FURNITURE_CATEGORY_CONTROLS)
    for value in categories.values():
        value["unit"] = "count_per_100m"
        value["preferredSpacingByLevelM"] = {
            level: _preferred_spacing_for_target(float(target))
            for level, target in value["values"].items()
        }
    return {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "parameter_schema_version": SCHEMA_VERSION,
        "generation_mode": "parametric",
        "levels": list(LEVELS),
        "source_option": "keep_annotation",
        "skeleton": copy.deepcopy(SKELETON_PARAMETER_CONTROLS),
        "median": {"kinds": ["raised", "planted"], "default_enabled": False},
        "bus_stop": {"placements": ["curbside", "bay"], "default_enabled": False},
        "furniture": {
            "globalDensity": copy.deepcopy(GLOBAL_DENSITY_CONTROL),
            "styles": sorted(FURNITURE_STYLES),
            "categories": categories,
            "default_enabled": False,
        },
        "buildings": {"default_representation": "transparent_massing", "footprint_locked": True},
        "default_seed": 42,
    }


def _deep_merge(base: Dict[str, Any], patch: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(dict(result[key]), value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def build_street_design_parameter_spec(
    profile_id: str,
    *,
    source_revision: int,
    source_fingerprint: str,
    overrides: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    if profile_id not in PARAMETER_PROFILE_REGISTRY:
        raise ParameterSpecError(f"Unknown parameter profile: {profile_id}")
    profile = PARAMETER_PROFILE_REGISTRY[profile_id]
    spec = {
        "schemaVersion": SCHEMA_VERSION_V1,
        "source": {
            "sourceRevision": int(source_revision),
            "sourceFingerprint": str(source_fingerprint).strip(),
            "geometryLocked": True,
        },
        "skeleton": copy.deepcopy(profile["skeleton"]),
        "furniture": copy.deepcopy(profile["furniture"]),
        "buildings": copy.deepcopy(profile["buildings"]),
        "seed": int(profile["seed"]),
    }
    if overrides:
        spec = _deep_merge(spec, overrides)
    return validate_street_design_parameter_spec(spec)


def _finite_number(value: Any, field: str, lower: float, upper: float | None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ParameterSpecError(f"{field} must be numeric.") from exc
    if not math.isfinite(number) or number < lower or (upper is not None and number > upper):
        limit = f"at least {lower}" if upper is None else f"between {lower} and {upper}"
        raise ParameterSpecError(f"{field} must be {limit}.")
    return number


def migrate_street_design_parameter_spec_v1(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Expand a historical named profile into the explicit v2 contract."""

    legacy = copy.deepcopy(dict(payload))
    if legacy.get("schemaVersion") != SCHEMA_VERSION_V1:
        raise ParameterSpecError(f"schemaVersion must be {SCHEMA_VERSION_V1} for migration.")
    skeleton = dict(legacy.get("skeleton") or {})
    furniture = dict(legacy.get("furniture") or {})
    legacy_categories = dict(furniture.get("categories") or {})
    categories = _default_v2_categories()
    for category, config in legacy_categories.items():
        if category in categories:
            categories[category] = _deep_merge(categories[category], dict(config or {}))
    profile_id = str(furniture.get("profileId") or "")
    if profile_id in {"park_landscape", "pedestrian_friendly", "quiet_residential"}:
        style = "lush_natural"
    elif profile_id == "transit_priority":
        style = "transit_modern"
    else:
        style = "civic_clean"
    migrated_skeleton: Dict[str, Any] = {
        "laneCount": skeleton.get("laneCount", SKELETON_PARAMETER_CONTROLS["laneCount"]["values"]["medium"]),
        "laneWidthM": skeleton.get("laneWidthM", SKELETON_PARAMETER_CONTROLS["laneWidthM"]["values"]["medium"]),
        "sidewalkWidthM": skeleton.get("sidewalkWidthM", SKELETON_PARAMETER_CONTROLS["sidewalkWidthM"]["values"]["medium"]),
        "furnishingWidthM": skeleton.get("furnishingWidthM", SKELETON_PARAMETER_CONTROLS["furnishingWidthM"]["values"]["medium"]),
        "curbWidthM": skeleton.get("curbWidthM", 0.12),
        "junctionCornerPolicy": skeleton.get("junctionCornerPolicy", "source"),
        "median": {"enabled": False, "kind": "raised", "widthM": SKELETON_PARAMETER_CONTROLS["medianWidthM"]["values"]["medium"]},
        "busStop": {
            "enabled": bool(categories.get("bus_stop", {}).get("enabled", False)),
            "placement": "curbside",
        },
        "curbRamp": {"enabled": False, "side": "right", "positionRatio": 0.5},
    }
    if "junctionCornerRadiusM" in skeleton:
        migrated_skeleton["junctionCornerRadiusM"] = skeleton["junctionCornerRadiusM"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": copy.deepcopy(dict(legacy.get("source") or {})),
        "skeleton": migrated_skeleton,
        "furniture": {
            "globalDensity": furniture.get("globalDensity", 1.0),
            "style": style,
            "categories": categories,
        },
        "buildings": copy.deepcopy(dict(legacy.get("buildings") or {})),
        "seed": legacy.get("seed", 42),
    }


def validate_street_design_parameter_spec(payload: Mapping[str, Any]) -> Dict[str, Any]:
    spec = copy.deepcopy(dict(payload))
    if spec.get("schemaVersion") == SCHEMA_VERSION_V1:
        spec = migrate_street_design_parameter_spec_v1(spec)
    if spec.get("schemaVersion") != SCHEMA_VERSION:
        raise ParameterSpecError(f"schemaVersion must be {SCHEMA_VERSION}.")
    source = dict(spec.get("source") or {})
    if int(source.get("sourceRevision", -1)) < 0 or not str(source.get("sourceFingerprint") or "").strip():
        raise ParameterSpecError("A source revision and fingerprint are required.")
    if source.get("geometryLocked") is not True:
        raise ParameterSpecError("OSM geometry must remain locked; edit it in 2D annotation.")

    skeleton = dict(spec.get("skeleton") or {})
    lanes = int(_finite_number(skeleton.get("laneCount"), "skeleton.laneCount", 1, 8))
    if float(lanes) != float(skeleton.get("laneCount")):
        raise ParameterSpecError("skeleton.laneCount must be an integer.")
    skeleton["laneCount"] = lanes
    skeleton["laneWidthM"] = _finite_number(skeleton.get("laneWidthM"), "skeleton.laneWidthM", 0.5, None)
    skeleton["sidewalkWidthM"] = _finite_number(skeleton.get("sidewalkWidthM"), "skeleton.sidewalkWidthM", 1.0, 12.0)
    skeleton["furnishingWidthM"] = _finite_number(skeleton.get("furnishingWidthM"), "skeleton.furnishingWidthM", 0.0, 5.0)
    skeleton["curbWidthM"] = _finite_number(skeleton.get("curbWidthM"), "skeleton.curbWidthM", 0.05, 0.4)
    policy = str(skeleton.get("junctionCornerPolicy") or "").strip()
    if policy not in {"source", "auto", "fixed"}:
        raise ParameterSpecError("junctionCornerPolicy must be source, auto, or fixed.")
    if policy == "fixed":
        skeleton["junctionCornerRadiusM"] = _finite_number(
            skeleton.get("junctionCornerRadiusM"), "skeleton.junctionCornerRadiusM", 1.0, 20.0
        )
    elif "junctionCornerRadiusM" in skeleton:
        skeleton.pop("junctionCornerRadiusM", None)
    median = dict(skeleton.get("median") or {})
    if not isinstance(median.get("enabled"), bool):
        raise ParameterSpecError("skeleton.median.enabled must be boolean.")
    if median.get("kind") not in {"raised", "planted"}:
        raise ParameterSpecError("skeleton.median.kind must be raised or planted.")
    median["widthM"] = _finite_number(median.get("widthM"), "skeleton.median.widthM", 0.8, 8.0)
    skeleton["median"] = median
    bus_stop = dict(skeleton.get("busStop") or {})
    if not isinstance(bus_stop.get("enabled"), bool):
        raise ParameterSpecError("skeleton.busStop.enabled must be boolean.")
    if bus_stop.get("placement") not in {"curbside", "bay"}:
        raise ParameterSpecError("skeleton.busStop.placement must be curbside or bay.")
    skeleton["busStop"] = bus_stop
    curb_ramp = dict(skeleton.get("curbRamp") or {})
    curb_ramp.setdefault("enabled", False)
    curb_ramp.setdefault("side", "right")
    curb_ramp.setdefault("positionRatio", 0.5)
    if not isinstance(curb_ramp.get("enabled"), bool):
        raise ParameterSpecError("skeleton.curbRamp.enabled must be boolean.")
    if curb_ramp.get("side") not in {"left", "right"}:
        raise ParameterSpecError("skeleton.curbRamp.side must be left or right.")
    curb_ramp["positionRatio"] = _finite_number(
        curb_ramp.get("positionRatio"), "skeleton.curbRamp.positionRatio", 0.0, 1.0
    )
    skeleton["curbRamp"] = curb_ramp

    furniture = dict(spec.get("furniture") or {})
    furniture["globalDensity"] = _finite_number(furniture.get("globalDensity"), "furniture.globalDensity", 0.0, 2.0)
    style = str(furniture.get("style") or "").strip()
    if style not in FURNITURE_STYLES:
        raise ParameterSpecError("furniture.style must be civic_clean, lush_natural, or transit_modern.")
    furniture["style"] = style
    categories = _deep_merge(_default_v2_categories(), dict(furniture.get("categories") or {}))
    unknown = sorted(set(categories) - set(DEFAULT_CATEGORIES))
    if unknown:
        raise ParameterSpecError(f"Unknown furniture categories: {', '.join(unknown)}")
    normalized_categories: Dict[str, Dict[str, Any]] = {}
    for category, raw in categories.items():
        config = dict(raw or {})
        if not isinstance(config.get("enabled"), bool):
            raise ParameterSpecError(f"furniture.categories.{category}.enabled must be boolean.")
        for key, bounds in {
            "targetCountPer100M": (0.0, 20.0),
            "preferredSpacingM": (2.0, 240.0),
            "minimumSpacingM": (2.0, 240.0),
            "roadSetbackM": (0.0, 10.0),
        }.items():
            if key in config:
                config[key] = _finite_number(config[key], f"furniture.categories.{category}.{key}", *bounds)
        zones = list(config.get("allowedZones") or [])
        if not zones or any(str(zone) not in ALLOWED_ZONES for zone in zones):
            raise ParameterSpecError(f"furniture.categories.{category}.allowedZones contains an unsupported zone.")
        config["allowedZones"] = list(dict.fromkeys(str(zone) for zone in zones))
        refs = list(config.get("assetRefs") or [])
        normalized_refs = []
        for ref in refs:
            item = dict(ref or {})
            if any(key in item for key in ("path", "absolutePath", "latent_path", "model_path")):
                raise ParameterSpecError("Asset references must not contain file paths.")
            if not all(str(item.get(key) or "").strip() for key in ("manifestName", "assetId", "fingerprint")):
                raise ParameterSpecError("Asset references require manifestName, assetId, and fingerprint.")
            normalized_refs.append(item)
        if normalized_refs:
            config["assetRefs"] = normalized_refs
        normalized_categories[category] = config
    if skeleton["busStop"]["enabled"]:
        normalized_categories["bus_stop"]["enabled"] = True
    furniture["categories"] = normalized_categories

    buildings = dict(spec.get("buildings") or {})
    if buildings.get("representation") not in {"transparent_massing", "asset"}:
        raise ParameterSpecError("Unsupported building representation.")
    if buildings.get("footprintLocked") is not True:
        raise ParameterSpecError("Building footprints must remain locked.")
    spec["seed"] = int(_finite_number(spec.get("seed"), "seed", 0, 2_147_483_647))
    spec.update(source=source, skeleton=skeleton, furniture=furniture, buildings=buildings)
    return spec


def _flatten_paths(value: Mapping[str, Any], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping):
            paths.extend(_flatten_paths(item, path))
        else:
            paths.append(path)
    return paths


def compile_street_design_parameter_spec(
    payload: Mapping[str, Any],
    *,
    field_sources: Mapping[str, str] | None = None,
) -> CompiledStreetDesignParameters:
    spec = validate_street_design_parameter_spec(payload)
    skeleton = spec["skeleton"]
    furniture = spec["furniture"]
    buildings = spec["buildings"]
    policy = skeleton["junctionCornerPolicy"]
    style_preset = {
        "civic_clean": "civic_clean_v1",
        "lush_natural": "lush_walkable_v1",
        "transit_modern": "transit_modern_v1",
    }[furniture["style"]]
    lane_carriageway_width = skeleton["laneCount"] * skeleton["laneWidthM"]
    median_width = skeleton["median"]["widthM"] if skeleton["median"]["enabled"] else 0.0
    patch: Dict[str, Any] = {
        "lane_count": skeleton["laneCount"],
        "base_lane_width_m": skeleton["laneWidthM"],
        "road_width_m": lane_carriageway_width + median_width,
        "sidewalk_width_m": skeleton["sidewalkWidthM"],
        "furnishing_width_m": skeleton["furnishingWidthM"],
        "curb_width_m": skeleton["curbWidthM"],
        "median_enabled": skeleton["median"]["enabled"],
        "median_kind": skeleton["median"]["kind"],
        "median_width_m": skeleton["median"]["widthM"],
        "bus_stop_enabled": skeleton["busStop"]["enabled"],
        "bus_stop_placement": skeleton["busStop"]["placement"],
        "curb_ramp_enabled": skeleton["curbRamp"]["enabled"],
        "curb_ramp_side": skeleton["curbRamp"]["side"],
        "curb_ramp_position_ratio": skeleton["curbRamp"]["positionRatio"],
        "max_bus_stops_per_scene": (
            max(1, int(math.ceil(float(furniture["categories"]["bus_stop"]["targetCountPer100M"]))))
            if skeleton["busStop"]["enabled"]
            else 0
        ),
        "density": furniture["globalDensity"],
        "furniture_style": furniture["style"],
        "style_preset": style_preset,
        "amenity_coverage_mode": "off",
        "minimum_category_presence": (),
        "optional_category_presence": (),
        "furniture_category_parameters": furniture["categories"],
        "building_representation": buildings["representation"],
        "seed": spec["seed"],
    }
    if policy != "source":
        patch["junction_corner_radius_mode"] = policy
    if policy == "fixed":
        patch["junction_corner_radius_m"] = skeleton["junctionCornerRadiusM"]
    canonical = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    supplied_sources = dict(field_sources or {})
    for field, source in supplied_sources.items():
        if source not in PARAMETER_SOURCES:
            raise ParameterSpecError(f"Unsupported parameter source for {field}: {source}")
    sources = {
        path: supplied_sources.get(path, "system_default")
        for path in _flatten_paths(spec)
        if not path.startswith("source.")
    }
    generation_options = {
        "generation_mode": "parametric",
        "skip_llm": True,
        "derive_parameters_with_llm": False,
        "knowledge_source": "none",
        "street_design_parameter_spec": copy.deepcopy(spec),
        "street_design_parameter_fingerprint": fingerprint,
        "parameter_sources_by_field": sources,
    }
    return CompiledStreetDesignParameters(spec, fingerprint, patch, generation_options, sources)
