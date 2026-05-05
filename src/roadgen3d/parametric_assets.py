"""Deterministic parametric asset generation for near-term street furniture."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, Literal, Mapping, Optional, Tuple

import numpy as np

from .runtime_device import resolve_device_backend

_STYLE_TAGS = frozenset(
    {
        "modern",
        "classic",
        "industrial",
        "minimalist",
        "ornate",
        "retro",
        "modular",
        "eco",
        "brutalist",
        "nordic",
        "japan_scandi",
        "victorian",
        "contemporary",
        "tactical",
        "art_deco",
    }
)
_BENCH_LEG_TYPES = frozenset({"dual_frame", "pedestal", "four_leg"})
_BENCH_MATERIALS = frozenset({"metal", "wood", "metal_wood", "concrete"})
_LAMP_MATERIALS = frozenset({"metal", "painted_steel", "cast_iron"})
_LUMINAIRE_TYPES = frozenset({"flat_led", "globe", "box", "cone"})
_LAMP_ARM_TYPES = frozenset({"single", "double"})
_LIGHT_DIRECTIONS = frozenset({"roadside", "bidirectional", "downward"})
_BUILDING_MATERIALS = frozenset({"concrete", "brick", "stone", "glass_curtain", "stucco"})
_TREE_CANOPY_STYLES = frozenset({"sphere", "cone", "oval", "flat_disc", "multi_blob"})
_TREE_TRUNK_COLOR = (101, 67, 33, 255)
_TREE_CANOPY_COLORS = {
    "deciduous_green": (62, 120, 50, 255),
    "dark_green": (38, 85, 40, 255),
    "light_green": (95, 155, 70, 255),
    "autumn_orange": (188, 120, 42, 255),
    "autumn_red": (165, 55, 40, 255),
    "spring_pink": (200, 130, 150, 255),
}
_FOOTPRINT_SHAPES = frozenset({"RECT", "L", "U"})
_HEIGHT_CLASSES = frozenset({"lowrise", "midrise", "highrise"})
_BUILDING_BASE_DARKEN = 0.85
_BUILDING_SPANDREL_DARKEN = 0.92
_DEFAULT_PROFILE = "default_v1"
_MIN_FACES = {
    "bench": 300,
    "lamp": 500,
    "building": 8,
    "tree": 12,
    "amphitheater": 24,
    "playground": 20,
    "outdoor_seating": 15,
    "kiosk": 20,
    "sculpture": 15,
}
_POLY_BUDGET_K = {
    "bench": {"preview": 8, "production": 15},
    "lamp": {"preview": 10, "production": 20},
    "building": {"preview": 30, "production": 80},
    "tree": {"preview": 12, "production": 30},
    "amphitheater": {"preview": 15, "production": 35},
    "playground": {"preview": 10, "production": 25},
    "outdoor_seating": {"preview": 8, "production": 20},
    "kiosk": {"preview": 8, "production": 20},
    "sculpture": {"preview": 6, "production": 15},
}


@dataclass(frozen=True)
class GenerationRequest:
    asset_kind: Literal["bench", "lamp", "building", "tree", "amphitheater", "playground", "outdoor_seating", "kiosk", "sculpture"]
    runtime_profile: Literal["preview", "production"] = "preview"
    device_backend: Literal["auto", "mps", "cuda", "cpu"] = "auto"
    seed: int = 42
    quality_profile: str = _DEFAULT_PROFILE
    physics_profile: str = _DEFAULT_PROFILE
    design_profile: str = _DEFAULT_PROFILE
    precision: Literal["fp32"] = "fp32"
    allow_fallback: bool = True
    params: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchParams:
    width_m: float = 1.80
    depth_m: float = 0.55
    seat_height_m: float = 0.45
    backrest_height_m: float = 0.35
    backrest_angle_deg: float = 12.0
    leg_type: str = "dual_frame"
    armrest_enabled: bool = False
    slat_count: int = 5
    material_family: str = "metal_wood"
    style_tag: str = "modern"
    detail_level: int = 2


@dataclass(frozen=True)
class LampParams:
    pole_height_m: float = 5.00
    pole_radius_m: float = 0.06
    base_diameter_m: float = 0.35
    arm_length_m: float = 0.80
    luminaire_type: str = "flat_led"
    single_or_double_arm: str = "single"
    light_direction: str = "roadside"
    material_family: str = "metal"
    style_tag: str = "modern"
    detail_level: int = 2


@dataclass(frozen=True)
class BuildingParams:
    frontage_width_m: float = 14.0
    depth_m: float = 10.0
    height_m: float = 0.0
    footprint_shape: str = "rect"
    wing_b_width_m: float = 0.0
    wing_b_depth_m: float = 0.0
    wing_c_width_m: float = 0.0
    wing_c_depth_m: float = 0.0
    floor_height_m: float = 3.2
    ground_floor_height_m: float = 4.0
    window_width_m: float = 1.2
    window_height_m: float = 1.4
    window_recess_m: float = 0.10
    window_sill_height_m: float = 0.9
    mullion_width_m: float = 0.8
    wall_thickness_m: float = 0.30
    theme_name: str = "commercial"
    height_class: str = "midrise"
    material_family: str = "concrete"
    style_tag: str = "modern"
    detail_level: int = 2


@dataclass(frozen=True)
class TreeParams:
    trunk_height_m: float = 3.0
    trunk_radius_m: float = 0.18
    canopy_radius_m: float = 1.10
    canopy_style: str = "sphere"
    canopy_color_name: str = "deciduous_green"
    style_tag: str = "modern"
    detail_level: int = 2


@dataclass(frozen=True)
class AmphitheaterParams:
    width_m: float = 10.0
    depth_m: float = 5.0
    tier_count: int = 4
    tier_height_m: float = 0.35
    style_tag: str = "modern"
    detail_level: int = 2


@dataclass(frozen=True)
class PlaygroundParams:
    width_m: float = 3.5
    depth_m: float = 4.0
    platform_height_m: float = 0.8
    slide_length_m: float = 2.5
    style_tag: str = "modern"
    detail_level: int = 2


@dataclass(frozen=True)
class OutdoorSeatingParams:
    table_radius_m: float = 0.55
    chair_count: int = 4
    style_tag: str = "modern"
    detail_level: int = 2


@dataclass(frozen=True)
class KioskParams:
    width_m: float = 2.0
    depth_m: float = 2.0
    height_m: float = 2.8
    style_tag: str = "modern"
    detail_level: int = 2


@dataclass(frozen=True)
class SculptureParams:
    height_m: float = 2.0
    base_width_m: float = 0.8
    style_tag: str = "modern"
    detail_level: int = 2


@dataclass(frozen=True)
class GenerationQualityMetrics:
    face_count: int
    poly_budget_k: int
    dimension_error_ratio: float
    ground_contact_ok: bool
    support_count: Optional[int] = None
    stability_check_ok: Optional[bool] = None
    slenderness_ratio: Optional[float] = None
    clearance_ok: Optional[bool] = None
    meets_min_faces: bool = False
    within_poly_budget: bool = True

    def to_dict(self) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "face_count": int(self.face_count),
            "poly_budget_k": int(self.poly_budget_k),
            "dimension_error_ratio": float(self.dimension_error_ratio),
            "ground_contact_ok": bool(self.ground_contact_ok),
            "meets_min_faces": bool(self.meets_min_faces),
            "within_poly_budget": bool(self.within_poly_budget),
        }
        if self.support_count is not None:
            payload["support_count"] = int(self.support_count)
        if self.stability_check_ok is not None:
            payload["stability_check_ok"] = bool(self.stability_check_ok)
        if self.slenderness_ratio is not None:
            payload["slenderness_ratio"] = float(self.slenderness_ratio)
        if self.clearance_ok is not None:
            payload["clearance_ok"] = bool(self.clearance_ok)
        return payload


@dataclass(frozen=True)
class ParametricAssetResult:
    asset_kind: str
    runtime_profile: str
    resolved_device_backend: str
    mesh: Any
    bbox_size_xyz: Tuple[float, float, float]
    bbox_bounds: Tuple[Tuple[float, float, float], Tuple[float, float, float]]
    parameter_snapshot: Dict[str, object]
    quality_metrics: GenerationQualityMetrics
    warnings: Tuple[str, ...] = ()
    generator_type: str = "parametric_v1"
    material_family: str = ""
    style_tags: Tuple[str, ...] = ()

    def to_metadata(self) -> Dict[str, object]:
        return {
            "asset_kind": self.asset_kind,
            "runtime_profile": self.runtime_profile,
            "resolved_device_backend": self.resolved_device_backend,
            "generator_type": self.generator_type,
            "material_family": self.material_family,
            "style_tags": list(self.style_tags),
            "bbox": {
                "size_xyz": [float(v) for v in self.bbox_size_xyz],
                "bounds": [
                    [float(v) for v in self.bbox_bounds[0]],
                    [float(v) for v in self.bbox_bounds[1]],
                ],
            },
            "parameter_snapshot": dict(self.parameter_snapshot),
            "quality_metrics": self.quality_metrics.to_dict(),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class _BenchAudit:
    expected_dims: Tuple[float, float, float]
    support_count: int
    support_bounds_xz: Tuple[float, float, float, float]


@dataclass(frozen=True)
class _LampAudit:
    expected_dims: Tuple[float, float, float]
    lowest_luminaire_y: float
    slenderness_ratio: float


@dataclass(frozen=True)
class _BuildingAudit:
    expected_dims: Tuple[float, float, float]
    floor_count: int
    window_count: int
    wing_count: int
    footprint_shape: str


@dataclass(frozen=True)
class _TreeAudit:
    expected_dims: Tuple[float, float, float]
    canopy_style: str
    trunk_height_m: float


@dataclass(frozen=True)
class _Wing:
    wing_id: str
    width_m: float
    depth_m: float
    height_m: float
    offset_x: float
    offset_z: float
    exposed_faces: Tuple[str, ...]


def _require_trimesh():
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("trimesh is required for parametric asset generation") from exc
    return trimesh


def _rgba(values: Tuple[int, int, int]) -> Tuple[int, int, int, int]:
    return values[0], values[1], values[2], 255


_STYLE_ACCENTS = {
    "modern": _rgba((70, 70, 70)),
    "classic": _rgba((88, 55, 33)),
    "industrial": _rgba((95, 95, 95)),
    "minimalist": _rgba((210, 210, 210)),
    "ornate": _rgba((182, 146, 45)),
    "retro": _rgba((201, 84, 63)),
    "modular": _rgba((65, 133, 184)),
    "eco": _rgba((92, 126, 46)),
    "brutalist": _rgba((90, 98, 107)),
    "nordic": _rgba((201, 176, 138)),
    "japan_scandi": _rgba((190, 154, 132)),
    "victorian": _rgba((54, 54, 54)),
    "contemporary": _rgba((168, 168, 168)),
    "tactical": _rgba((103, 111, 58)),
    "art_deco": _rgba((166, 141, 56)),
}
_MATERIAL_PRIMARY = {
    "metal": _rgba((130, 136, 143)),
    "wood": _rgba((139, 96, 64)),
    "metal_wood": _rgba((158, 128, 98)),
    "concrete": _rgba((148, 148, 148)),
    "painted_steel": _rgba((104, 118, 132)),
    "cast_iron": _rgba((76, 76, 76)),
}
_BUILDING_WALL_COLORS = {
    "residential": _rgba((188, 174, 153)),
    "commercial": _rgba((176, 184, 192)),
    "transit": _rgba((151, 165, 182)),
    "green": _rgba((166, 171, 148)),
    "analytical": _rgba((186, 193, 190)),
}
_BUILDING_WINDOW_COLOR = _rgba((68, 82, 96))


def _rotation_x(theta_rad: float) -> np.ndarray:
    c = math.cos(theta_rad)
    s = math.sin(theta_rad)
    return np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, c, -s, 0.0],
            [0.0, s, c, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _rotation_y(theta_rad: float) -> np.ndarray:
    c = math.cos(theta_rad)
    s = math.sin(theta_rad)
    return np.array(
        [
            [c, 0.0, s, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [-s, 0.0, c, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _translate(x: float, y: float, z: float) -> np.ndarray:
    mat = np.eye(4, dtype=np.float64)
    mat[0, 3] = float(x)
    mat[1, 3] = float(y)
    mat[2, 3] = float(z)
    return mat


def _color(mesh, rgba: Tuple[int, int, int, int]):
    mesh.visual.face_colors = list(rgba)
    return mesh


def _concat(parts):
    trimesh = _require_trimesh()
    return trimesh.util.concatenate(parts)


def _ground(mesh):
    mesh.apply_translation([0.0, -float(mesh.bounds[0][1]), 0.0])
    return mesh


def _bbox_size(mesh) -> Tuple[float, float, float]:
    span = mesh.bounds[1] - mesh.bounds[0]
    return float(span[0]), float(span[1]), float(span[2])


def _coerce_float(value: object, default: float, *, field_name: str) -> float:
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc


def _coerce_int(value: object, default: int, *, field_name: str) -> int:
    if value is None:
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _coerce_bool(value: object, default: bool, *, field_name: str) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{field_name} must be a boolean")


def _clamp(value: float, minimum: float, maximum: float, *, field_name: str, warnings_list: list[str]) -> float:
    bounded = min(max(float(value), float(minimum)), float(maximum))
    if not math.isclose(bounded, float(value), rel_tol=0.0, abs_tol=1e-9):
        warnings_list.append(f"{field_name} was clamped into [{minimum}, {maximum}]")
    return bounded


def _clamp_int(value: int, minimum: int, maximum: int, *, field_name: str, warnings_list: list[str]) -> int:
    bounded = min(max(int(value), int(minimum)), int(maximum))
    if bounded != int(value):
        warnings_list.append(f"{field_name} was clamped into [{minimum}, {maximum}]")
    return bounded


def _validate_style_tag(value: object, *, warnings_list: list[str]) -> str:
    style = str(value or "modern").strip().lower() or "modern"
    if style not in _STYLE_TAGS:
        warnings_list.append(f"Unknown style_tag '{style}' was replaced with 'modern'")
        return "modern"
    return style


def _validate_profile(value: object, *, field_name: str, warnings_list: list[str]) -> str:
    profile = str(value or _DEFAULT_PROFILE).strip() or _DEFAULT_PROFILE
    if profile != _DEFAULT_PROFILE:
        warnings_list.append(f"Unknown {field_name} '{profile}' was replaced with '{_DEFAULT_PROFILE}'")
        return _DEFAULT_PROFILE
    return profile


def _material_palette(material_family: str, style_tag: str) -> Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int]]:
    primary = _MATERIAL_PRIMARY.get(material_family, _rgba((128, 128, 128)))
    accent = _STYLE_ACCENTS.get(style_tag, _STYLE_ACCENTS["modern"])
    return primary, accent


def _effective_detail_level(runtime_profile: str, requested_level: int) -> int:
    requested = max(0, min(int(requested_level), 3))
    if runtime_profile == "preview":
        return min(requested, 1)
    return max(requested, 2)


def _to_request(payload: GenerationRequest | Mapping[str, object]) -> GenerationRequest:
    if isinstance(payload, GenerationRequest):
        return payload
    if not isinstance(payload, Mapping):
        raise TypeError("Generation request must be a mapping or GenerationRequest")
    raw_params = payload.get("params", {})
    if raw_params is None:
        params = {}
    elif isinstance(raw_params, Mapping):
        params = dict(raw_params)
    elif is_dataclass(raw_params):
        params = asdict(raw_params)
    else:
        raise TypeError("request.params must be a mapping")
    return GenerationRequest(
        asset_kind=str(payload.get("asset_kind", "")).strip().lower(),  # type: ignore[arg-type]
        runtime_profile=str(payload.get("runtime_profile", "preview")).strip().lower(),  # type: ignore[arg-type]
        device_backend=str(payload.get("device_backend", "auto")).strip().lower(),  # type: ignore[arg-type]
        seed=_coerce_int(payload.get("seed", 42), 42, field_name="seed"),
        quality_profile=str(payload.get("quality_profile", _DEFAULT_PROFILE)),
        physics_profile=str(payload.get("physics_profile", _DEFAULT_PROFILE)),
        design_profile=str(payload.get("design_profile", _DEFAULT_PROFILE)),
        precision=str(payload.get("precision", "fp32")).strip().lower(),  # type: ignore[arg-type]
        allow_fallback=_coerce_bool(payload.get("allow_fallback", True), True, field_name="allow_fallback"),
        params=params,
    )


def _validate_request(request: GenerationRequest, warnings_list: list[str]) -> GenerationRequest:
    asset_kind = str(request.asset_kind).strip().lower()
    if asset_kind not in {"bench", "lamp", "building", "tree", "amphitheater", "playground", "outdoor_seating", "kiosk", "sculpture"}:
        raise ValueError("asset_kind must be one of bench, lamp, building, tree, amphitheater, playground, outdoor_seating, kiosk, sculpture")
    runtime_profile = str(request.runtime_profile).strip().lower()
    if runtime_profile not in {"preview", "production"}:
        raise ValueError("runtime_profile must be 'preview' or 'production'")
    device_backend = str(request.device_backend).strip().lower()
    if device_backend not in {"auto", "cpu", "mps", "cuda"}:
        raise ValueError("device_backend must be one of auto/cpu/mps/cuda")
    precision = str(request.precision).strip().lower()
    if precision != "fp32":
        warnings_list.append(f"Unsupported precision '{precision}' was replaced with 'fp32'")
        precision = "fp32"
    return GenerationRequest(
        asset_kind=asset_kind,  # type: ignore[arg-type]
        runtime_profile=runtime_profile,  # type: ignore[arg-type]
        device_backend=device_backend,  # type: ignore[arg-type]
        seed=int(request.seed),
        quality_profile=_validate_profile(request.quality_profile, field_name="quality_profile", warnings_list=warnings_list),
        physics_profile=_validate_profile(request.physics_profile, field_name="physics_profile", warnings_list=warnings_list),
        design_profile=_validate_profile(request.design_profile, field_name="design_profile", warnings_list=warnings_list),
        precision=precision,  # type: ignore[arg-type]
        allow_fallback=bool(request.allow_fallback),
        params=dict(request.params),
    )


def _validate_bench_params(raw_params: Mapping[str, object], warnings_list: list[str]) -> BenchParams:
    defaults = BenchParams()
    leg_type = str(raw_params.get("leg_type", defaults.leg_type)).strip().lower() or defaults.leg_type
    if leg_type not in _BENCH_LEG_TYPES:
        raise ValueError(f"leg_type must be one of {sorted(_BENCH_LEG_TYPES)}")
    material_family = str(raw_params.get("material_family", defaults.material_family)).strip().lower() or defaults.material_family
    if material_family not in _BENCH_MATERIALS:
        raise ValueError(f"material_family must be one of {sorted(_BENCH_MATERIALS)}")
    return BenchParams(
        width_m=_clamp(
            _coerce_float(raw_params.get("width_m"), defaults.width_m, field_name="width_m"),
            1.20,
            2.40,
            field_name="width_m",
            warnings_list=warnings_list,
        ),
        depth_m=_clamp(
            _coerce_float(raw_params.get("depth_m"), defaults.depth_m, field_name="depth_m"),
            0.40,
            0.75,
            field_name="depth_m",
            warnings_list=warnings_list,
        ),
        seat_height_m=_clamp(
            _coerce_float(raw_params.get("seat_height_m"), defaults.seat_height_m, field_name="seat_height_m"),
            0.38,
            0.50,
            field_name="seat_height_m",
            warnings_list=warnings_list,
        ),
        backrest_height_m=_clamp(
            _coerce_float(raw_params.get("backrest_height_m"), defaults.backrest_height_m, field_name="backrest_height_m"),
            0.20,
            0.55,
            field_name="backrest_height_m",
            warnings_list=warnings_list,
        ),
        backrest_angle_deg=_clamp(
            _coerce_float(raw_params.get("backrest_angle_deg"), defaults.backrest_angle_deg, field_name="backrest_angle_deg"),
            5.0,
            20.0,
            field_name="backrest_angle_deg",
            warnings_list=warnings_list,
        ),
        leg_type=leg_type,
        armrest_enabled=_coerce_bool(raw_params.get("armrest_enabled"), defaults.armrest_enabled, field_name="armrest_enabled"),
        slat_count=_clamp_int(
            _coerce_int(raw_params.get("slat_count"), defaults.slat_count, field_name="slat_count"),
            3,
            8,
            field_name="slat_count",
            warnings_list=warnings_list,
        ),
        material_family=material_family,
        style_tag=_validate_style_tag(raw_params.get("style_tag", defaults.style_tag), warnings_list=warnings_list),
        detail_level=_clamp_int(
            _coerce_int(raw_params.get("detail_level"), defaults.detail_level, field_name="detail_level"),
            0,
            3,
            field_name="detail_level",
            warnings_list=warnings_list,
        ),
    )


def _validate_lamp_params(raw_params: Mapping[str, object], warnings_list: list[str]) -> LampParams:
    defaults = LampParams()
    luminaire_type = str(raw_params.get("luminaire_type", defaults.luminaire_type)).strip().lower() or defaults.luminaire_type
    if luminaire_type not in _LUMINAIRE_TYPES:
        raise ValueError(f"luminaire_type must be one of {sorted(_LUMINAIRE_TYPES)}")
    arm_mode = str(raw_params.get("single_or_double_arm", defaults.single_or_double_arm)).strip().lower() or defaults.single_or_double_arm
    if arm_mode not in _LAMP_ARM_TYPES:
        raise ValueError(f"single_or_double_arm must be one of {sorted(_LAMP_ARM_TYPES)}")
    light_direction = str(raw_params.get("light_direction", defaults.light_direction)).strip().lower() or defaults.light_direction
    if light_direction not in _LIGHT_DIRECTIONS:
        raise ValueError(f"light_direction must be one of {sorted(_LIGHT_DIRECTIONS)}")
    material_family = str(raw_params.get("material_family", defaults.material_family)).strip().lower() or defaults.material_family
    if material_family not in _LAMP_MATERIALS:
        raise ValueError(f"material_family must be one of {sorted(_LAMP_MATERIALS)}")
    return LampParams(
        pole_height_m=_clamp(
            _coerce_float(raw_params.get("pole_height_m"), defaults.pole_height_m, field_name="pole_height_m"),
            3.50,
            8.00,
            field_name="pole_height_m",
            warnings_list=warnings_list,
        ),
        pole_radius_m=_clamp(
            _coerce_float(raw_params.get("pole_radius_m"), defaults.pole_radius_m, field_name="pole_radius_m"),
            0.04,
            0.12,
            field_name="pole_radius_m",
            warnings_list=warnings_list,
        ),
        base_diameter_m=_clamp(
            _coerce_float(raw_params.get("base_diameter_m"), defaults.base_diameter_m, field_name="base_diameter_m"),
            0.25,
            0.60,
            field_name="base_diameter_m",
            warnings_list=warnings_list,
        ),
        arm_length_m=_clamp(
            _coerce_float(raw_params.get("arm_length_m"), defaults.arm_length_m, field_name="arm_length_m"),
            0.40,
            1.80,
            field_name="arm_length_m",
            warnings_list=warnings_list,
        ),
        luminaire_type=luminaire_type,
        single_or_double_arm=arm_mode,
        light_direction=light_direction,
        material_family=material_family,
        style_tag=_validate_style_tag(raw_params.get("style_tag", defaults.style_tag), warnings_list=warnings_list),
        detail_level=_clamp_int(
            _coerce_int(raw_params.get("detail_level"), defaults.detail_level, field_name="detail_level"),
            0,
            3,
            field_name="detail_level",
            warnings_list=warnings_list,
        ),
    )


def _detail_sections(detail_level: int, *, base: int, step: int) -> int:
    return int(base + step * int(detail_level))


def _build_bench_mesh(params: BenchParams, *, detail_level: int):
    trimesh = _require_trimesh()
    primary, accent = _material_palette(params.material_family, params.style_tag)
    parts = []
    seat_top = float(params.seat_height_m)
    seat_thickness = 0.035 + 0.006 * detail_level
    seat_width = float(params.width_m)
    seat_depth = float(params.depth_m)
    slat_gap = max(0.008, min(0.022, seat_depth * 0.04))
    slat_width = seat_width * (0.94 if params.armrest_enabled else 0.97)
    slat_depth = max(0.03, (seat_depth - slat_gap * (params.slat_count + 1)) / params.slat_count)
    slat_start = -seat_depth / 2.0 + slat_gap + slat_depth / 2.0
    for index in range(params.slat_count):
        slat = _color(trimesh.creation.box(extents=(slat_width, seat_thickness, slat_depth)), primary)
        slat.apply_translation([0.0, seat_top - seat_thickness / 2.0, slat_start + index * (slat_depth + slat_gap)])
        parts.append(slat)

    rail_radius = 0.011 + 0.002 * detail_level
    rail_sections = _detail_sections(detail_level, base=20, step=6)
    for z in (-seat_depth * 0.36, seat_depth * 0.36):
        rail = _color(trimesh.creation.cylinder(radius=rail_radius, height=seat_width * 0.9, sections=rail_sections), accent)
        rail.apply_transform(_rotation_y(math.pi / 2.0))
        rail.apply_translation([0.0, seat_top - seat_thickness * 0.9, z])
        parts.append(rail)

    back_width = slat_width
    back_thickness = 0.026 + 0.003 * detail_level
    back_angle = math.radians(float(params.backrest_angle_deg))
    back_panel = _color(
        trimesh.creation.box(extents=(back_width, params.backrest_height_m, back_thickness)),
        accent,
    )
    back_panel.apply_transform(_rotation_x(-back_angle))
    back_center_y = seat_top + params.backrest_height_m * 0.5 * math.cos(back_angle)
    back_center_z = -seat_depth / 2.0 - params.backrest_height_m * 0.5 * math.sin(back_angle)
    back_panel.apply_translation([0.0, back_center_y, back_center_z])
    parts.append(back_panel)

    contact_points: list[Tuple[float, float]] = []
    support_height = max(0.18, seat_top - seat_thickness)
    leg_sections = _detail_sections(detail_level, base=24, step=6)
    leg_radius = 0.018 + 0.003 * detail_level
    if params.leg_type == "dual_frame":
        x_positions = (-seat_width * 0.38, seat_width * 0.38)
        z_positions = (-seat_depth * 0.30, seat_depth * 0.30)
        for x_pos in x_positions:
            for z_pos in z_positions:
                leg = _color(trimesh.creation.cylinder(radius=leg_radius, height=support_height, sections=leg_sections), accent)
                leg.apply_transform(_rotation_x(math.pi / 2.0))
                leg.apply_translation([x_pos, support_height / 2.0, z_pos])
                parts.append(leg)
                contact_points.append((x_pos, z_pos))
            stretcher = _color(
                trimesh.creation.cylinder(radius=rail_radius, height=seat_depth * 0.72, sections=leg_sections),
                accent,
            )
            stretcher.apply_translation([x_pos, support_height * 0.5, 0.0])
            parts.append(stretcher)
        lower_rail = _color(
            trimesh.creation.cylinder(radius=rail_radius, height=seat_width * 0.70, sections=leg_sections),
            accent,
        )
        lower_rail.apply_transform(_rotation_y(math.pi / 2.0))
        lower_rail.apply_translation([0.0, support_height * 0.35, -seat_depth * 0.18])
        parts.append(lower_rail)
    elif params.leg_type == "four_leg":
        for x_pos in (-seat_width * 0.42, seat_width * 0.42):
            for z_pos in (-seat_depth * 0.33, seat_depth * 0.33):
                leg = _color(trimesh.creation.cylinder(radius=leg_radius, height=support_height, sections=leg_sections), accent)
                leg.apply_transform(_rotation_x(math.pi / 2.0))
                leg.apply_translation([x_pos, support_height / 2.0, z_pos])
                parts.append(leg)
                contact_points.append((x_pos, z_pos))
        for z_pos in (-seat_depth * 0.28, seat_depth * 0.28):
            rail = _color(
                trimesh.creation.cylinder(radius=rail_radius, height=seat_width * 0.72, sections=leg_sections),
                accent,
            )
            rail.apply_transform(_rotation_y(math.pi / 2.0))
            rail.apply_translation([0.0, support_height * 0.28, z_pos])
            parts.append(rail)
    else:
        post_radius = max(0.055, seat_width * 0.04)
        post = _color(trimesh.creation.cylinder(radius=post_radius, height=support_height, sections=leg_sections), accent)
        post.apply_transform(_rotation_x(math.pi / 2.0))
        post.apply_translation([0.0, support_height / 2.0, 0.0])
        parts.append(post)
        base_height = 0.045 + 0.008 * detail_level
        base_diameter = max(0.24, min(seat_width * 0.34, 0.48))
        pedestal_base = _color(
            trimesh.creation.cylinder(radius=base_diameter / 2.0, height=base_height, sections=leg_sections),
            accent,
        )
        pedestal_base.apply_transform(_rotation_x(math.pi / 2.0))
        pedestal_base.apply_translation([0.0, base_height / 2.0, 0.0])
        parts.append(pedestal_base)
        contact_points.append((0.0, 0.0))
        for offset in (-0.08, 0.08):
            ring = _color(
                trimesh.creation.cylinder(radius=post_radius * 1.2, height=0.015, sections=leg_sections),
                accent,
            )
            ring.apply_transform(_rotation_x(math.pi / 2.0))
            ring.apply_translation([0.0, support_height * 0.35 + offset, 0.0])
            parts.append(ring)

    if params.armrest_enabled:
        arm_height = seat_top + 0.18
        upright_height = max(0.18, arm_height - seat_top + seat_thickness / 2.0)
        arm_radius = rail_radius * 1.2
        arm_length = seat_depth * 0.58
        for x_pos in (-seat_width * 0.44, seat_width * 0.44):
            upright = _color(
                trimesh.creation.cylinder(radius=arm_radius, height=upright_height, sections=leg_sections),
                accent,
            )
            upright.apply_transform(_rotation_x(math.pi / 2.0))
            upright.apply_translation([x_pos, seat_top + upright_height / 2.0 - seat_thickness / 2.0, 0.0])
            parts.append(upright)
            arm = _color(
                trimesh.creation.cylinder(radius=arm_radius, height=arm_length, sections=leg_sections),
                accent,
            )
            arm.apply_transform(_rotation_x(math.pi / 2.0))
            arm.apply_translation([x_pos, arm_height, -seat_depth * 0.02])
            parts.append(arm)

    mesh = _ground(_concat(parts))
    total_height = max(
        seat_top,
        seat_top + params.backrest_height_m * math.cos(back_angle),
        seat_top + (0.18 if params.armrest_enabled else 0.0),
    )
    total_depth = seat_depth + params.backrest_height_m * math.sin(back_angle)
    if params.leg_type == "pedestal":
        support_bounds = (-base_diameter / 2.0, base_diameter / 2.0, -base_diameter / 2.0, base_diameter / 2.0)
    else:
        xs = [point[0] for point in contact_points]
        zs = [point[1] for point in contact_points]
        support_bounds = (min(xs), max(xs), min(zs), max(zs))
    return mesh, _BenchAudit(
        expected_dims=(seat_width, total_height, total_depth),
        support_count=len(contact_points),
        support_bounds_xz=support_bounds,
    )


def _luminaire_dims(luminaire_type: str, detail_level: int) -> Tuple[float, float, float]:
    if luminaire_type == "flat_led":
        return 0.34 + detail_level * 0.02, 0.16 + detail_level * 0.01, 0.10 + detail_level * 0.01
    if luminaire_type == "globe":
        diameter = 0.20 + detail_level * 0.03
        return diameter, diameter, diameter
    if luminaire_type == "box":
        return 0.28 + detail_level * 0.02, 0.22 + detail_level * 0.01, 0.16 + detail_level * 0.01
    return 0.24 + detail_level * 0.02, 0.24 + detail_level * 0.02, 0.20 + detail_level * 0.02


def _build_lamp_mesh(params: LampParams, *, detail_level: int):
    trimesh = _require_trimesh()
    primary, accent = _material_palette(params.material_family, params.style_tag)
    parts = []
    pole_sections = _detail_sections(detail_level, base=40, step=8)
    base_sections = _detail_sections(detail_level, base=36, step=8)
    arm_sections = _detail_sections(detail_level, base=32, step=8)
    base_height = 0.12 + 0.02 * detail_level
    pole = _color(
        trimesh.creation.cylinder(radius=params.pole_radius_m, height=params.pole_height_m, sections=pole_sections),
        primary,
    )
    pole.apply_transform(_rotation_x(math.pi / 2.0))
    pole.apply_translation([0.0, params.pole_height_m / 2.0, 0.0])
    parts.append(pole)

    base = _color(
        trimesh.creation.cylinder(radius=params.base_diameter_m / 2.0, height=base_height, sections=base_sections),
        accent,
    )
    base.apply_transform(_rotation_x(math.pi / 2.0))
    base.apply_translation([0.0, base_height / 2.0, 0.0])
    parts.append(base)

    collar = _color(
        trimesh.creation.cylinder(radius=params.pole_radius_m * 1.55, height=0.05, sections=base_sections),
        accent,
    )
    collar.apply_transform(_rotation_x(math.pi / 2.0))
    collar.apply_translation([0.0, base_height + 0.06, 0.0])
    parts.append(collar)

    cap = _color(
        trimesh.creation.cylinder(radius=params.pole_radius_m * 1.25, height=0.08, sections=base_sections),
        accent,
    )
    cap.apply_transform(_rotation_x(math.pi / 2.0))
    cap.apply_translation([0.0, params.pole_height_m - 0.18, 0.0])
    parts.append(cap)

    luminaire_width, luminaire_height, luminaire_depth = _luminaire_dims(params.luminaire_type, detail_level)
    arm_height = params.pole_height_m - max(0.40, luminaire_height * 0.8)
    arm_radius = max(0.02, params.pole_radius_m * 0.68)
    luminaire_centers: list[Tuple[float, float, float]] = []
    arm_directions = [1.0]
    if params.single_or_double_arm == "double" or params.light_direction == "bidirectional":
        arm_directions = [-1.0, 1.0]

    for direction in arm_directions:
        arm = _color(
            trimesh.creation.cylinder(radius=arm_radius, height=params.arm_length_m, sections=arm_sections),
            primary,
        )
        arm.apply_transform(_rotation_y(math.pi / 2.0))
        arm.apply_translation([direction * params.arm_length_m / 2.0, arm_height, 0.0])
        parts.append(arm)

        lum_x = direction * (params.arm_length_m + luminaire_width * 0.35)
        lum_y = arm_height - (luminaire_height * (0.42 if params.light_direction == "downward" else 0.18))
        lum_z = 0.0
        if params.luminaire_type == "flat_led":
            luminaire = _color(
                trimesh.creation.box(extents=(luminaire_width, luminaire_height, luminaire_depth)),
                accent,
            )
            if params.light_direction == "downward":
                luminaire.apply_transform(_rotation_x(math.radians(18.0)))
        elif params.luminaire_type == "globe":
            luminaire = _color(
                trimesh.creation.icosphere(subdivisions=max(2, detail_level), radius=luminaire_width / 2.0),
                accent,
            )
        elif params.luminaire_type == "box":
            luminaire = _color(
                trimesh.creation.box(extents=(luminaire_width, luminaire_height, luminaire_depth)),
                accent,
            )
        else:
            luminaire = _color(
                trimesh.creation.cone(radius=luminaire_width / 2.0, height=luminaire_height, sections=arm_sections),
                accent,
            )
            luminaire.apply_transform(_rotation_x(math.pi))
        luminaire.apply_translation([lum_x, lum_y, lum_z])
        parts.append(luminaire)
        luminaire_centers.append((lum_x, lum_y, lum_z))

    mesh = _ground(_concat(parts))
    lowest_luminaire_y = min(center[1] - luminaire_height / 2.0 for center in luminaire_centers)
    if len(arm_directions) == 2:
        expected_width = max(params.base_diameter_m, params.arm_length_m * 2.0 + luminaire_width * 1.2)
    else:
        expected_width = max(params.base_diameter_m, params.arm_length_m + luminaire_width + params.pole_radius_m * 2.0)
    expected_height = max(params.pole_height_m, arm_height + luminaire_height * 0.6)
    expected_depth = max(params.base_diameter_m, luminaire_depth)
    slenderness_ratio = params.pole_height_m / max(params.pole_radius_m * 2.0, 1e-6)
    return mesh, _LampAudit(
        expected_dims=(expected_width, expected_height, expected_depth),
        lowest_luminaire_y=float(lowest_luminaire_y),
        slenderness_ratio=float(slenderness_ratio),
    )


# ---------------------------------------------------------------------------
# Building generation helpers
# ---------------------------------------------------------------------------


def _darken(rgba: Tuple[int, int, int, int], factor: float) -> Tuple[int, int, int, int]:
    return (
        max(0, min(255, int(rgba[0] * factor))),
        max(0, min(255, int(rgba[1] * factor))),
        max(0, min(255, int(rgba[2] * factor))),
        rgba[3],
    )


def _resolve_building_height(params: BuildingParams) -> float:
    if params.height_m > 0.0:
        return float(params.height_m)
    fw = float(params.frontage_width_m)
    return {
        "lowrise": max(fw * 0.8, 8.0),
        "midrise": max(fw * 1.4, 14.0),
        "highrise": max(fw * 2.0, 22.0),
    }.get(str(params.height_class), max(fw * 1.2, 12.0))


def _decompose_footprint(params: BuildingParams, height_m: float) -> Tuple[_Wing, ...]:
    fw = float(params.frontage_width_m)
    dp = float(params.depth_m)
    shape = str(params.footprint_shape).strip().upper()

    if shape == "L":
        bw = float(params.wing_b_width_m) if params.wing_b_width_m > 0.0 else fw * 0.4
        bd = float(params.wing_b_depth_m) if params.wing_b_depth_m > 0.0 else dp * 0.6
        bw = min(bw, fw)
        bd = min(bd, dp)
        # Wing A: main body, right face partially exposed (only the part not covered by B)
        a_right_exposed_depth = dp - bd
        wing_a = _Wing(
            wing_id="A",
            width_m=fw,
            depth_m=dp,
            height_m=height_m,
            offset_x=0.0,
            offset_z=0.0,
            exposed_faces=("front", "left", "back"),
        )
        # Wing B: side wing extending from the right of A, towards the back
        wing_b = _Wing(
            wing_id="B",
            width_m=bw,
            depth_m=bd,
            height_m=height_m,
            offset_x=fw / 2.0 + bw / 2.0,
            offset_z=-(dp / 2.0 - bd / 2.0),
            exposed_faces=("front", "right", "back"),
        )
        # Extra partial face for Wing A right side (the portion above Wing B along Z)
        wings: list[_Wing] = [wing_a, wing_b]
        if a_right_exposed_depth > 1.0:
            wing_a_right_stub = _Wing(
                wing_id="A_right",
                width_m=a_right_exposed_depth,
                depth_m=0.0,
                height_m=height_m,
                offset_x=fw / 2.0,
                offset_z=dp / 2.0 - a_right_exposed_depth / 2.0,
                exposed_faces=("right",),
            )
            wings.append(wing_a_right_stub)
        return tuple(wings)

    if shape == "U":
        bw = float(params.wing_b_width_m) if params.wing_b_width_m > 0.0 else fw * 0.3
        bd = float(params.wing_b_depth_m) if params.wing_b_depth_m > 0.0 else dp * 0.7
        cw = float(params.wing_c_width_m) if params.wing_c_width_m > 0.0 else bw
        cd = float(params.wing_c_depth_m) if params.wing_c_depth_m > 0.0 else bd
        bw = min(bw, fw * 0.45)
        cw = min(cw, fw * 0.45)
        bd = min(bd, dp)
        cd = min(cd, dp)
        inner_width = fw - bw - cw
        inner_width = max(inner_width, 3.0)
        # Wing A: bottom bar
        wing_a = _Wing(
            wing_id="A",
            width_m=fw,
            depth_m=dp - bd,
            height_m=height_m,
            offset_x=0.0,
            offset_z=(dp - (dp - bd)) / 2.0,
            exposed_faces=("front",),
        )
        # Wing B: right arm
        wing_b = _Wing(
            wing_id="B",
            width_m=bw,
            depth_m=bd,
            height_m=height_m,
            offset_x=(fw - bw) / 2.0,
            offset_z=-(dp - bd) / 2.0,
            exposed_faces=("right", "back"),
        )
        # Wing C: left arm
        wing_c = _Wing(
            wing_id="C",
            width_m=cw,
            depth_m=cd,
            height_m=height_m,
            offset_x=-(fw - cw) / 2.0,
            offset_z=-(dp - cd) / 2.0,
            exposed_faces=("left", "back"),
        )
        return (wing_a, wing_b, wing_c)

    # Default: rect
    return (_Wing(
        wing_id="A",
        width_m=fw,
        depth_m=dp,
        height_m=height_m,
        offset_x=0.0,
        offset_z=0.0,
        exposed_faces=("front", "back", "left", "right"),
    ),)


def _build_facade(
    *,
    facade_width: float,
    facade_height: float,
    wall_thickness: float,
    wall_color: Tuple[int, int, int, int],
    window_color: Tuple[int, int, int, int],
    params: BuildingParams,
    detail_level: int,
) -> list:
    trimesh = _require_trimesh()
    parts: list = []
    base_color = _darken(wall_color, _BUILDING_BASE_DARKEN)
    spandrel_color = _darken(wall_color, _BUILDING_SPANDREL_DARKEN)

    floor_h = float(params.floor_height_m)
    gf_h = float(params.ground_floor_height_m)
    win_w = float(params.window_width_m)
    win_h = float(params.window_height_m)
    recess = float(params.window_recess_m)
    sill_h = float(params.window_sill_height_m)
    mullion_w = float(params.mullion_width_m)
    wt = float(wall_thickness)

    # Calculate floors
    base_height = 0.6
    parapet_height = 0.5
    usable_height = facade_height - base_height - parapet_height
    if usable_height < gf_h:
        num_floors = 1
        actual_floor_h = usable_height
    else:
        num_floors = max(1, 1 + int((usable_height - gf_h) / floor_h))
        actual_floor_h = floor_h

    # Calculate windows per floor
    edge_margin = mullion_w
    usable_width = facade_width - 2.0 * edge_margin
    if usable_width < win_w + mullion_w:
        windows_per_floor = 0
    else:
        windows_per_floor = max(1, int(usable_width / (win_w + mullion_w)))

    total_window_count = 0

    # detail_level 0: solid wall only
    if detail_level <= 0:
        wall = _color(trimesh.creation.box(extents=(facade_width, facade_height, wt)), wall_color)
        wall.apply_translation([0.0, facade_height / 2.0, 0.0])
        parts.append(wall)
        return parts

    # Base strip
    base_strip = _color(trimesh.creation.box(extents=(facade_width, base_height, wt + 0.02)), base_color)
    base_strip.apply_translation([0.0, base_height / 2.0, 0.0])
    parts.append(base_strip)

    # Parapet strip
    parapet_y = facade_height - parapet_height / 2.0
    parapet_strip = _color(trimesh.creation.box(extents=(facade_width, parapet_height, wt + 0.01)), wall_color)
    parapet_strip.apply_translation([0.0, parapet_y, 0.0])
    parts.append(parapet_strip)

    # detail_level 1: floor bands with colored window zones (no recess)
    if detail_level == 1:
        current_y = base_height
        for fi in range(num_floors):
            fh = gf_h if fi == 0 else actual_floor_h
            if current_y + fh > facade_height - parapet_height + 0.01:
                fh = facade_height - parapet_height - current_y
                if fh < 1.0:
                    break
            # Spandrel band at floor base
            sp_h = max(0.2, fh - win_h - sill_h)
            sp_h = min(sp_h, fh * 0.3)
            spandrel = _color(trimesh.creation.box(extents=(facade_width, sp_h, wt)), spandrel_color)
            spandrel.apply_translation([0.0, current_y + sp_h / 2.0, 0.0])
            parts.append(spandrel)
            # Wall zone (with window color bands)
            wall_zone_h = fh - sp_h
            if windows_per_floor > 0:
                total_windows_width = windows_per_floor * win_w
                total_mullions_width = facade_width - total_windows_width
                actual_mullion = total_mullions_width / (windows_per_floor + 1)
                for wi in range(windows_per_floor):
                    wx = -facade_width / 2.0 + actual_mullion + wi * (win_w + actual_mullion) + win_w / 2.0
                    wy = current_y + sp_h + sill_h + win_h / 2.0
                    if wy + win_h / 2.0 > current_y + fh:
                        continue
                    win_panel = _color(trimesh.creation.box(extents=(win_w, win_h, wt)), window_color)
                    win_panel.apply_translation([wx, wy, 0.0])
                    parts.append(win_panel)
                    total_window_count += 1
            # Fill remaining wall area
            wall_panel = _color(trimesh.creation.box(extents=(facade_width, wall_zone_h, wt * 0.98)), wall_color)
            wall_panel.apply_translation([0.0, current_y + sp_h + wall_zone_h / 2.0, -0.005])
            parts.append(wall_panel)
            current_y += fh
        return parts

    # detail_level 2-3: full window grid with recess
    current_y = base_height
    for fi in range(num_floors):
        fh = gf_h if fi == 0 else actual_floor_h
        if current_y + fh > facade_height - parapet_height + 0.01:
            fh = facade_height - parapet_height - current_y
            if fh < 1.0:
                break

        # Spandrel band
        sp_h = max(0.15, fh - win_h - sill_h)
        sp_h = min(sp_h, fh * 0.3)
        spandrel = _color(trimesh.creation.box(extents=(facade_width, sp_h, wt)), spandrel_color)
        spandrel.apply_translation([0.0, current_y + sp_h / 2.0, 0.0])
        parts.append(spandrel)

        # Window zone
        win_zone_y_base = current_y + sp_h
        win_zone_h = fh - sp_h
        if windows_per_floor <= 0:
            # No windows fit: solid wall
            wall_fill = _color(trimesh.creation.box(extents=(facade_width, win_zone_h, wt)), wall_color)
            wall_fill.apply_translation([0.0, win_zone_y_base + win_zone_h / 2.0, 0.0])
            parts.append(wall_fill)
            current_y += fh
            continue

        # Calculate even spacing
        total_windows_width = windows_per_floor * win_w
        total_mullions_width = facade_width - total_windows_width
        actual_mullion = total_mullions_width / (windows_per_floor + 1)

        for wi in range(windows_per_floor):
            wx = -facade_width / 2.0 + actual_mullion + wi * (win_w + actual_mullion) + win_w / 2.0
            wy = win_zone_y_base + sill_h + win_h / 2.0
            if wy + win_h / 2.0 > current_y + fh:
                continue

            # Window recess panel (recessed inward)
            win_depth = max(wt - recess, 0.02)
            win_panel = _color(trimesh.creation.box(extents=(win_w, win_h, win_depth)), window_color)
            win_panel.apply_translation([wx, wy, -recess / 2.0])
            parts.append(win_panel)
            total_window_count += 1

            # detail_level 3: sill and lintel micro-details
            if detail_level >= 3:
                sill_thickness = 0.04
                sill_protrusion = 0.03
                # Window sill
                sill = _color(
                    trimesh.creation.box(extents=(win_w + 0.06, sill_thickness, wt + sill_protrusion)),
                    base_color,
                )
                sill.apply_translation([wx, wy - win_h / 2.0 - sill_thickness / 2.0, sill_protrusion / 2.0])
                parts.append(sill)
                # Lintel
                lintel = _color(
                    trimesh.creation.box(extents=(win_w + 0.04, sill_thickness, wt + sill_protrusion * 0.5)),
                    spandrel_color,
                )
                lintel.apply_translation([wx, wy + win_h / 2.0 + sill_thickness / 2.0, sill_protrusion * 0.25])
                parts.append(lintel)

        # Mullion (pier) strips between and beside windows
        for mi in range(windows_per_floor + 1):
            if mi == 0:
                mx = -facade_width / 2.0 + actual_mullion / 2.0
                mw = actual_mullion
            elif mi == windows_per_floor:
                mx = facade_width / 2.0 - actual_mullion / 2.0
                mw = actual_mullion
            else:
                mx = -facade_width / 2.0 + actual_mullion + mi * (win_w + actual_mullion) - actual_mullion / 2.0
                mw = actual_mullion
            mullion = _color(trimesh.creation.box(extents=(mw, win_zone_h, wt)), wall_color)
            mullion.apply_translation([mx, win_zone_y_base + win_zone_h / 2.0, 0.0])
            parts.append(mullion)

        # Sill band (below windows)
        sill_band_h = sill_h
        if sill_band_h > 0.1:
            for wi in range(windows_per_floor):
                wx = -facade_width / 2.0 + actual_mullion + wi * (win_w + actual_mullion) + win_w / 2.0
                sband = _color(trimesh.creation.box(extents=(win_w, sill_band_h, wt)), wall_color)
                sband.apply_translation([wx, win_zone_y_base + sill_band_h / 2.0, 0.0])
                parts.append(sband)

        # Above-window fill (lintel zone)
        above_win_y = win_zone_y_base + sill_h + win_h
        above_win_h = fh - sp_h - sill_h - win_h
        if above_win_h > 0.05:
            for wi in range(windows_per_floor):
                wx = -facade_width / 2.0 + actual_mullion + wi * (win_w + actual_mullion) + win_w / 2.0
                above = _color(trimesh.creation.box(extents=(win_w, above_win_h, wt)), wall_color)
                above.apply_translation([wx, above_win_y + above_win_h / 2.0, 0.0])
                parts.append(above)

        current_y += fh

    # Fill gap between last floor and parapet if any
    gap = facade_height - parapet_height - current_y
    if gap > 0.05:
        gap_fill = _color(trimesh.creation.box(extents=(facade_width, gap, wt)), wall_color)
        gap_fill.apply_translation([0.0, current_y + gap / 2.0, 0.0])
        parts.append(gap_fill)

    return parts


_FACE_ROTATIONS = {
    "front": 0.0,
    "back": math.pi,
    "left": math.pi / 2.0,
    "right": -math.pi / 2.0,
}

_FACE_AXIS = {
    "front": (0.0, 1.0),   # +Z face
    "back": (0.0, -1.0),   # -Z face
    "left": (-1.0, 0.0),   # -X face
    "right": (1.0, 0.0),   # +X face
}


def _build_building_mesh(params: BuildingParams, *, detail_level: int):
    trimesh = _require_trimesh()
    height_m = _resolve_building_height(params)
    wings = _decompose_footprint(params, height_m)
    wall_color = _BUILDING_WALL_COLORS.get(str(params.theme_name), _rgba((178, 180, 178)))
    window_color = _BUILDING_WINDOW_COLOR
    wt = float(params.wall_thickness_m)
    parts: list = []
    total_window_count = 0

    for wing in wings:
        ox = float(wing.offset_x)
        oz = float(wing.offset_z)
        ww = float(wing.width_m)
        wd = float(wing.depth_m)
        wh = float(wing.height_m)

        # Special stub wing for partial face (L-shape right face)
        if wing.wing_id.endswith("_right") and wing.depth_m == 0.0:
            facade_w = float(wing.width_m)
            facade_parts = _build_facade(
                facade_width=facade_w,
                facade_height=wh,
                wall_thickness=wt,
                wall_color=wall_color,
                window_color=window_color,
                params=params,
                detail_level=detail_level,
            )
            rot_angle = _FACE_ROTATIONS["right"]
            for part in facade_parts:
                part.apply_transform(_rotation_y(rot_angle))
                part.apply_translation([ox, 0.0, oz])
            parts.extend(facade_parts)
            for p in facade_parts:
                fc = int(len(p.faces))
                total_window_count += 0  # counted inside _build_facade
            continue

        if detail_level <= 0:
            # Solid box for the wing
            box = _color(trimesh.creation.box(extents=(ww, wh, wd)), wall_color)
            box.apply_translation([ox, wh / 2.0, oz])
            parts.append(box)
            continue

        # Solid core volume — facades are decorative outer layer on top of this
        core = _color(trimesh.creation.box(extents=(ww, wh, wd)), wall_color)
        core.apply_translation([ox, wh / 2.0, oz])
        parts.append(core)

        # Roof slab on top of core, covering outer face of facade walls
        roof_w = ww + wt + 0.02
        roof_d = wd + wt + 0.02
        roof = _color(trimesh.creation.box(extents=(roof_w, 0.15, roof_d)), _darken(wall_color, 0.9))
        roof.apply_translation([ox, wh + 0.075, oz])
        parts.append(roof)

        for face_name in wing.exposed_faces:
            if face_name in ("front", "back"):
                facade_w = ww
            else:
                facade_w = wd

            if facade_w < 1.0:
                continue

            facade_parts = _build_facade(
                facade_width=facade_w,
                facade_height=wh,
                wall_thickness=wt,
                wall_color=wall_color,
                window_color=window_color,
                params=params,
                detail_level=detail_level,
            )

            rot_angle = _FACE_ROTATIONS[face_name]
            nx, nz = _FACE_AXIS[face_name]
            if face_name in ("front", "back"):
                face_offset_x = ox
                face_offset_z = oz + nz * wd / 2.0
            else:
                face_offset_x = ox + nx * ww / 2.0
                face_offset_z = oz

            for part in facade_parts:
                part.apply_transform(_rotation_y(rot_angle))
                part.apply_translation([face_offset_x, 0.0, face_offset_z])
            parts.extend(facade_parts)

        # Non-exposed faces: thin wall panels (no windows)
        all_faces = {"front", "back", "left", "right"}
        hidden_faces = all_faces - set(wing.exposed_faces)
        for face_name in hidden_faces:
            if face_name in ("front", "back"):
                fw = ww
            else:
                fw = wd
            if fw < 0.5:
                continue
            panel = _color(trimesh.creation.box(extents=(fw, wh, 0.05)), wall_color)
            panel.apply_translation([0.0, wh / 2.0, 0.0])
            rot_angle = _FACE_ROTATIONS[face_name]
            panel.apply_transform(_rotation_y(rot_angle))
            nx, nz = _FACE_AXIS[face_name]
            if face_name in ("front", "back"):
                panel.apply_translation([ox, 0.0, oz + nz * wd / 2.0])
            else:
                panel.apply_translation([ox + nx * ww / 2.0, 0.0, oz])
            parts.append(panel)

    if not parts:
        # Absolute fallback: single box
        box = _color(trimesh.creation.box(extents=(params.frontage_width_m, height_m, params.depth_m)), wall_color)
        box.apply_translation([0.0, height_m / 2.0, 0.0])
        parts.append(box)

    mesh = _ground(_concat(parts))

    # Count windows from facade parts (approximate from mesh face count relative to detail level)
    # For audit purposes, calculate expected window count
    audit_window_count = 0
    for wing in wings:
        if wing.depth_m == 0.0 and wing.wing_id.endswith("_right"):
            continue
        for face_name in wing.exposed_faces:
            fw_face = wing.width_m if face_name in ("front", "back") else wing.depth_m
            if fw_face < 1.0:
                continue
            edge_margin = float(params.mullion_width_m)
            usable = fw_face - 2.0 * edge_margin
            wpf = max(0, int(usable / (float(params.window_width_m) + float(params.mullion_width_m)))) if usable >= float(params.window_width_m) + float(params.mullion_width_m) else 0
            base_h = 0.6
            parapet_h = 0.5
            usable_h = height_m - base_h - parapet_h
            gf_h = float(params.ground_floor_height_m)
            fh = float(params.floor_height_m)
            if usable_h < gf_h:
                nf = 1
            else:
                nf = max(1, 1 + int((usable_h - gf_h) / fh))
            audit_window_count += wpf * nf

    # Compute overall bounding extent for expected dims
    bounds = mesh.bounds
    span = bounds[1] - bounds[0]
    expected_width = float(span[0])
    expected_height = float(span[1])
    expected_depth = float(span[2])

    wing_count = len([w for w in wings if not w.wing_id.endswith("_right")])

    return mesh, _BuildingAudit(
        expected_dims=(expected_width, expected_height, expected_depth),
        floor_count=max(1, int((height_m - 0.6 - 0.5) / float(params.floor_height_m)) + 1) if detail_level > 0 else 1,
        window_count=audit_window_count if detail_level >= 1 else 0,
        wing_count=wing_count,
        footprint_shape=str(params.footprint_shape),
    )


def _validate_building_params(raw_params: Mapping[str, object], warnings_list: list[str]) -> BuildingParams:
    defaults = BuildingParams()
    footprint_shape = str(raw_params.get("footprint_shape", defaults.footprint_shape)).strip().upper() or "RECT"
    if footprint_shape not in _FOOTPRINT_SHAPES:
        raise ValueError(f"footprint_shape must be one of {sorted(_FOOTPRINT_SHAPES)}")
    height_class = str(raw_params.get("height_class", defaults.height_class)).strip().lower() or defaults.height_class
    if height_class not in _HEIGHT_CLASSES:
        raise ValueError(f"height_class must be one of {sorted(_HEIGHT_CLASSES)}")
    material_family = str(raw_params.get("material_family", defaults.material_family)).strip().lower() or defaults.material_family
    if material_family not in _BUILDING_MATERIALS:
        warnings_list.append(f"Unknown building material_family '{material_family}' was replaced with 'concrete'")
        material_family = "concrete"
    theme_name = str(raw_params.get("theme_name", defaults.theme_name)).strip().lower() or defaults.theme_name
    return BuildingParams(
        frontage_width_m=_clamp(
            _coerce_float(raw_params.get("frontage_width_m"), defaults.frontage_width_m, field_name="frontage_width_m"),
            6.0, 60.0, field_name="frontage_width_m", warnings_list=warnings_list,
        ),
        depth_m=_clamp(
            _coerce_float(raw_params.get("depth_m"), defaults.depth_m, field_name="depth_m"),
            6.0, 40.0, field_name="depth_m", warnings_list=warnings_list,
        ),
        height_m=_clamp(
            _coerce_float(raw_params.get("height_m"), defaults.height_m, field_name="height_m"),
            0.0, 80.0, field_name="height_m", warnings_list=warnings_list,
        ),
        footprint_shape=footprint_shape,
        wing_b_width_m=_coerce_float(raw_params.get("wing_b_width_m"), defaults.wing_b_width_m, field_name="wing_b_width_m"),
        wing_b_depth_m=_coerce_float(raw_params.get("wing_b_depth_m"), defaults.wing_b_depth_m, field_name="wing_b_depth_m"),
        wing_c_width_m=_coerce_float(raw_params.get("wing_c_width_m"), defaults.wing_c_width_m, field_name="wing_c_width_m"),
        wing_c_depth_m=_coerce_float(raw_params.get("wing_c_depth_m"), defaults.wing_c_depth_m, field_name="wing_c_depth_m"),
        floor_height_m=_clamp(
            _coerce_float(raw_params.get("floor_height_m"), defaults.floor_height_m, field_name="floor_height_m"),
            2.8, 5.0, field_name="floor_height_m", warnings_list=warnings_list,
        ),
        ground_floor_height_m=_clamp(
            _coerce_float(raw_params.get("ground_floor_height_m"), defaults.ground_floor_height_m, field_name="ground_floor_height_m"),
            3.0, 6.0, field_name="ground_floor_height_m", warnings_list=warnings_list,
        ),
        window_width_m=_clamp(
            _coerce_float(raw_params.get("window_width_m"), defaults.window_width_m, field_name="window_width_m"),
            0.6, 2.4, field_name="window_width_m", warnings_list=warnings_list,
        ),
        window_height_m=_clamp(
            _coerce_float(raw_params.get("window_height_m"), defaults.window_height_m, field_name="window_height_m"),
            0.8, 2.2, field_name="window_height_m", warnings_list=warnings_list,
        ),
        window_recess_m=_clamp(
            _coerce_float(raw_params.get("window_recess_m"), defaults.window_recess_m, field_name="window_recess_m"),
            0.05, 0.25, field_name="window_recess_m", warnings_list=warnings_list,
        ),
        window_sill_height_m=_clamp(
            _coerce_float(raw_params.get("window_sill_height_m"), defaults.window_sill_height_m, field_name="window_sill_height_m"),
            0.6, 1.2, field_name="window_sill_height_m", warnings_list=warnings_list,
        ),
        mullion_width_m=_clamp(
            _coerce_float(raw_params.get("mullion_width_m"), defaults.mullion_width_m, field_name="mullion_width_m"),
            0.4, 2.0, field_name="mullion_width_m", warnings_list=warnings_list,
        ),
        wall_thickness_m=_clamp(
            _coerce_float(raw_params.get("wall_thickness_m"), defaults.wall_thickness_m, field_name="wall_thickness_m"),
            0.15, 0.50, field_name="wall_thickness_m", warnings_list=warnings_list,
        ),
        theme_name=theme_name,
        height_class=height_class,
        material_family=material_family,
        style_tag=_validate_style_tag(raw_params.get("style_tag", defaults.style_tag), warnings_list=warnings_list),
        detail_level=_clamp_int(
            _coerce_int(raw_params.get("detail_level"), defaults.detail_level, field_name="detail_level"),
            0, 3, field_name="detail_level", warnings_list=warnings_list,
        ),
    )


def _building_quality_metrics(mesh, params: BuildingParams, runtime_profile: str, audit: _BuildingAudit) -> GenerationQualityMetrics:
    actual_dims = _bbox_size(mesh)
    face_count = int(len(mesh.faces))
    poly_budget_k = int(_POLY_BUDGET_K["building"][runtime_profile])
    ground_contact_ok = abs(float(mesh.bounds[0][1])) <= 0.01
    return GenerationQualityMetrics(
        face_count=face_count,
        poly_budget_k=poly_budget_k,
        dimension_error_ratio=_dimension_error_ratio(actual_dims, audit.expected_dims),
        ground_contact_ok=ground_contact_ok,
        meets_min_faces=face_count >= _MIN_FACES["building"],
        within_poly_budget=face_count <= poly_budget_k * 1000,
    )


def _dimension_error_ratio(actual_dims: Tuple[float, float, float], expected_dims: Tuple[float, float, float]) -> float:
    ratios = []
    for actual, expected in zip(actual_dims, expected_dims):
        denom = max(float(expected), 1e-6)
        ratios.append(abs(float(actual) - float(expected)) / denom)
    return float(max(ratios))


def _bench_quality_metrics(mesh, params: BenchParams, runtime_profile: str, audit: _BenchAudit) -> GenerationQualityMetrics:
    actual_dims = _bbox_size(mesh)
    face_count = int(len(mesh.faces))
    poly_budget_k = int(_POLY_BUDGET_K["bench"][runtime_profile])
    com_x, _com_y, com_z = [float(value) for value in mesh.center_mass]
    min_x, max_x, min_z, max_z = audit.support_bounds_xz
    stable = (min_x - 0.03 <= com_x <= max_x + 0.03) and (min_z - 0.03 <= com_z <= max_z + 0.03)
    if params.leg_type == "pedestal":
        stable = stable and params.width_m <= max(params.depth_m * 4.5, 2.3)
    ground_contact_ok = abs(float(mesh.bounds[0][1])) <= 1e-6
    return GenerationQualityMetrics(
        face_count=face_count,
        poly_budget_k=poly_budget_k,
        dimension_error_ratio=_dimension_error_ratio(actual_dims, audit.expected_dims),
        ground_contact_ok=ground_contact_ok,
        support_count=audit.support_count,
        stability_check_ok=stable,
        meets_min_faces=face_count >= _MIN_FACES["bench"],
        within_poly_budget=face_count <= poly_budget_k * 1000,
    )


def _lamp_quality_metrics(mesh, params: LampParams, runtime_profile: str, audit: _LampAudit) -> GenerationQualityMetrics:
    actual_dims = _bbox_size(mesh)
    face_count = int(len(mesh.faces))
    poly_budget_k = int(_POLY_BUDGET_K["lamp"][runtime_profile])
    ground_contact_ok = abs(float(mesh.bounds[0][1])) <= 1e-6
    minimum_base = max(0.25, min(0.60, params.pole_height_m * 0.055))
    minimum_clearance = 3.0
    clearance_ok = audit.lowest_luminaire_y >= minimum_clearance
    slender_ok = 18.0 <= audit.slenderness_ratio <= 70.0
    base_ok = params.base_diameter_m >= minimum_base
    arm_ok = params.arm_length_m <= max(0.6, params.pole_height_m * 0.32)
    return GenerationQualityMetrics(
        face_count=face_count,
        poly_budget_k=poly_budget_k,
        dimension_error_ratio=_dimension_error_ratio(actual_dims, audit.expected_dims),
        ground_contact_ok=ground_contact_ok,
        slenderness_ratio=audit.slenderness_ratio,
        clearance_ok=clearance_ok and base_ok and arm_ok and slender_ok,
        meets_min_faces=face_count >= _MIN_FACES["lamp"],
        within_poly_budget=face_count <= poly_budget_k * 1000,
    )


def _validate_tree_params(raw_params: Mapping[str, object], warnings_list: list[str]) -> TreeParams:
    defaults = TreeParams()
    canopy_style = str(raw_params.get("canopy_style", defaults.canopy_style)).strip().lower() or defaults.canopy_style
    if canopy_style not in _TREE_CANOPY_STYLES:
        warnings_list.append(f"Unknown canopy_style '{canopy_style}' was replaced with 'sphere'")
        canopy_style = "sphere"
    canopy_color_name = str(raw_params.get("canopy_color_name", defaults.canopy_color_name)).strip().lower() or defaults.canopy_color_name
    if canopy_color_name not in _TREE_CANOPY_COLORS:
        warnings_list.append(f"Unknown canopy_color_name '{canopy_color_name}' was replaced with 'deciduous_green'")
        canopy_color_name = "deciduous_green"
    return TreeParams(
        trunk_height_m=_clamp(
            _coerce_float(raw_params.get("trunk_height_m"), defaults.trunk_height_m, field_name="trunk_height_m"),
            1.5, 8.0, field_name="trunk_height_m", warnings_list=warnings_list,
        ),
        trunk_radius_m=_clamp(
            _coerce_float(raw_params.get("trunk_radius_m"), defaults.trunk_radius_m, field_name="trunk_radius_m"),
            0.06, 0.40, field_name="trunk_radius_m", warnings_list=warnings_list,
        ),
        canopy_radius_m=_clamp(
            _coerce_float(raw_params.get("canopy_radius_m"), defaults.canopy_radius_m, field_name="canopy_radius_m"),
            0.50, 3.00, field_name="canopy_radius_m", warnings_list=warnings_list,
        ),
        canopy_style=canopy_style,
        canopy_color_name=canopy_color_name,
        style_tag=_validate_style_tag(raw_params.get("style_tag", defaults.style_tag), warnings_list=warnings_list),
        detail_level=_clamp_int(
            _coerce_int(raw_params.get("detail_level"), defaults.detail_level, field_name="detail_level"),
            0, 3, field_name="detail_level", warnings_list=warnings_list,
        ),
    )


def _validate_amphitheater_params(raw_params: Mapping[str, object], warnings_list: list[str]) -> AmphitheaterParams:
    defaults = AmphitheaterParams()
    return AmphitheaterParams(
        width_m=_clamp(
            _coerce_float(raw_params.get("width_m"), defaults.width_m, field_name="width_m"),
            4.0, 20.0, field_name="width_m", warnings_list=warnings_list,
        ),
        depth_m=_clamp(
            _coerce_float(raw_params.get("depth_m"), defaults.depth_m, field_name="depth_m"),
            2.0, 10.0, field_name="depth_m", warnings_list=warnings_list,
        ),
        tier_count=_clamp_int(
            _coerce_int(raw_params.get("tier_count"), defaults.tier_count, field_name="tier_count"),
            2, 8, field_name="tier_count", warnings_list=warnings_list,
        ),
        tier_height_m=_clamp(
            _coerce_float(raw_params.get("tier_height_m"), defaults.tier_height_m, field_name="tier_height_m"),
            0.20, 0.60, field_name="tier_height_m", warnings_list=warnings_list,
        ),
        style_tag=_validate_style_tag(raw_params.get("style_tag", defaults.style_tag), warnings_list=warnings_list),
        detail_level=_clamp_int(
            _coerce_int(raw_params.get("detail_level"), defaults.detail_level, field_name="detail_level"),
            0, 3, field_name="detail_level", warnings_list=warnings_list,
        ),
    )


def _validate_playground_params(raw_params: Mapping[str, object], warnings_list: list[str]) -> PlaygroundParams:
    defaults = PlaygroundParams()
    return PlaygroundParams(
        width_m=_clamp(
            _coerce_float(raw_params.get("width_m"), defaults.width_m, field_name="width_m"),
            2.0, 6.0, field_name="width_m", warnings_list=warnings_list,
        ),
        depth_m=_clamp(
            _coerce_float(raw_params.get("depth_m"), defaults.depth_m, field_name="depth_m"),
            2.0, 6.0, field_name="depth_m", warnings_list=warnings_list,
        ),
        platform_height_m=_clamp(
            _coerce_float(raw_params.get("platform_height_m"), defaults.platform_height_m, field_name="platform_height_m"),
            0.4, 1.5, field_name="platform_height_m", warnings_list=warnings_list,
        ),
        slide_length_m=_clamp(
            _coerce_float(raw_params.get("slide_length_m"), defaults.slide_length_m, field_name="slide_length_m"),
            1.5, 4.0, field_name="slide_length_m", warnings_list=warnings_list,
        ),
        style_tag=_validate_style_tag(raw_params.get("style_tag", defaults.style_tag), warnings_list=warnings_list),
        detail_level=_clamp_int(
            _coerce_int(raw_params.get("detail_level"), defaults.detail_level, field_name="detail_level"),
            0, 3, field_name="detail_level", warnings_list=warnings_list,
        ),
    )


def _validate_outdoor_seating_params(raw_params: Mapping[str, object], warnings_list: list[str]) -> OutdoorSeatingParams:
    defaults = OutdoorSeatingParams()
    return OutdoorSeatingParams(
        table_radius_m=_clamp(
            _coerce_float(raw_params.get("table_radius_m"), defaults.table_radius_m, field_name="table_radius_m"),
            0.30, 0.80, field_name="table_radius_m", warnings_list=warnings_list,
        ),
        chair_count=_clamp_int(
            _coerce_int(raw_params.get("chair_count"), defaults.chair_count, field_name="chair_count"),
            2, 6, field_name="chair_count", warnings_list=warnings_list,
        ),
        style_tag=_validate_style_tag(raw_params.get("style_tag", defaults.style_tag), warnings_list=warnings_list),
        detail_level=_clamp_int(
            _coerce_int(raw_params.get("detail_level"), defaults.detail_level, field_name="detail_level"),
            0, 3, field_name="detail_level", warnings_list=warnings_list,
        ),
    )


def _validate_kiosk_params(raw_params: Mapping[str, object], warnings_list: list[str]) -> KioskParams:
    defaults = KioskParams()
    return KioskParams(
        width_m=_clamp(
            _coerce_float(raw_params.get("width_m"), defaults.width_m, field_name="width_m"),
            1.2, 3.5, field_name="width_m", warnings_list=warnings_list,
        ),
        depth_m=_clamp(
            _coerce_float(raw_params.get("depth_m"), defaults.depth_m, field_name="depth_m"),
            1.2, 3.5, field_name="depth_m", warnings_list=warnings_list,
        ),
        height_m=_clamp(
            _coerce_float(raw_params.get("height_m"), defaults.height_m, field_name="height_m"),
            2.0, 4.0, field_name="height_m", warnings_list=warnings_list,
        ),
        style_tag=_validate_style_tag(raw_params.get("style_tag", defaults.style_tag), warnings_list=warnings_list),
        detail_level=_clamp_int(
            _coerce_int(raw_params.get("detail_level"), defaults.detail_level, field_name="detail_level"),
            0, 3, field_name="detail_level", warnings_list=warnings_list,
        ),
    )


def _validate_sculpture_params(raw_params: Mapping[str, object], warnings_list: list[str]) -> SculptureParams:
    defaults = SculptureParams()
    return SculptureParams(
        height_m=_clamp(
            _coerce_float(raw_params.get("height_m"), defaults.height_m, field_name="height_m"),
            1.0, 4.0, field_name="height_m", warnings_list=warnings_list,
        ),
        base_width_m=_clamp(
            _coerce_float(raw_params.get("base_width_m"), defaults.base_width_m, field_name="base_width_m"),
            0.4, 1.5, field_name="base_width_m", warnings_list=warnings_list,
        ),
        style_tag=_validate_style_tag(raw_params.get("style_tag", defaults.style_tag), warnings_list=warnings_list),
        detail_level=_clamp_int(
            _coerce_int(raw_params.get("detail_level"), defaults.detail_level, field_name="detail_level"),
            0, 3, field_name="detail_level", warnings_list=warnings_list,
        ),
    )


def _pbr_color(mesh, rgba: Tuple[int, int, int, int]):
    """Assign a non-metallic PBR material to a mesh so it renders correctly in GLB viewers."""
    trimesh = _require_trimesh()
    from trimesh.visual.material import PBRMaterial

    mat = PBRMaterial(
        baseColorFactor=[rgba[0] / 255.0, rgba[1] / 255.0, rgba[2] / 255.0, rgba[3] / 255.0],
        metallicFactor=0.0,
        roughnessFactor=0.9,
    )
    mesh.visual = trimesh.visual.TextureVisuals(material=mat)
    return mesh


def _build_tree_mesh(params: TreeParams, *, detail_level: int):
    trimesh = _require_trimesh()
    trunk_h = float(params.trunk_height_m)
    trunk_r = float(params.trunk_radius_m)
    canopy_r = float(params.canopy_radius_m)
    canopy_color = _TREE_CANOPY_COLORS.get(params.canopy_color_name, _TREE_CANOPY_COLORS["deciduous_green"])
    trunk_sections = {0: 6, 1: 10, 2: 16, 3: 24}.get(detail_level, 16)
    canopy_subdiv = {0: 1, 1: 2, 2: 3, 3: 3}.get(detail_level, 2)

    # Collect parts grouped by material colour
    trunk_parts = []
    canopy_parts = []

    # Trunk
    trunk = trimesh.creation.cylinder(radius=trunk_r, height=trunk_h, sections=trunk_sections)
    trunk.apply_translation([0.0, trunk_h / 2.0, 0.0])
    trunk_parts.append(trunk)

    canopy_base = trunk_h
    style = params.canopy_style

    if style == "sphere":
        canopy = trimesh.creation.icosphere(subdivisions=canopy_subdiv, radius=canopy_r)
        canopy.apply_translation([0.0, canopy_base + canopy_r, 0.0])
        canopy_parts.append(canopy)

    elif style == "cone":
        cone_h = canopy_r * 2.5
        canopy = trimesh.creation.cone(radius=canopy_r, height=cone_h, sections=trunk_sections)
        canopy.apply_translation([0.0, canopy_base + cone_h / 2.0, 0.0])
        canopy_parts.append(canopy)

    elif style == "oval":
        canopy = trimesh.creation.icosphere(subdivisions=canopy_subdiv, radius=canopy_r)
        canopy.apply_translation([0.0, canopy_base + canopy_r * 1.2, 0.0])
        canopy.apply_transform(np.diag([0.8, 1.3, 0.8, 1.0]))
        canopy_parts.append(canopy)

    elif style == "flat_disc":
        canopy = trimesh.creation.cylinder(radius=canopy_r, height=max(0.25, canopy_r * 0.3), sections=trunk_sections + 4)
        canopy.apply_translation([0.0, canopy_base + canopy_r * 0.15, 0.0])
        canopy_parts.append(canopy)

    elif style == "multi_blob":
        rng = np.random.default_rng(42)
        blob_count = {0: 2, 1: 3, 2: 5, 3: 7}.get(detail_level, 4)
        for bi in range(blob_count):
            br = canopy_r * float(rng.uniform(0.35, 0.65))
            theta = float(rng.uniform(0.0, math.tau))
            radial = canopy_r * 0.4 * float(rng.uniform(0.0, 1.0))
            bx = radial * math.cos(theta)
            bz = radial * math.sin(theta)
            by = canopy_base + canopy_r * 0.5 + float(rng.uniform(0.0, canopy_r * 0.8))
            blob = trimesh.creation.icosphere(subdivisions=max(1, canopy_subdiv - 1), radius=br)
            blob.apply_translation([bx, by, bz])
            canopy_parts.append(blob)

    else:
        # fallback to sphere
        canopy = trimesh.creation.icosphere(subdivisions=canopy_subdiv, radius=canopy_r)
        canopy.apply_translation([0.0, canopy_base + canopy_r, 0.0])
        canopy_parts.append(canopy)

    # Detail: branches at higher detail levels
    if detail_level >= 2:
        branch_count = 3 if detail_level == 2 else 5
        for bi in range(branch_count):
            angle = (360.0 / branch_count) * bi
            br = max(0.03, trunk_r * 0.45)
            bl = max(0.25, canopy_r * 0.35)
            branch = trimesh.creation.cylinder(radius=br, height=bl, sections=max(6, trunk_sections // 2))
            branch.apply_translation([bl * 0.5, trunk_h * 0.65, 0.0])
            branch.apply_transform(
                trimesh.transformations.rotation_matrix(math.radians(45.0), [0, 0, 1])
            )
            branch.apply_transform(
                trimesh.transformations.rotation_matrix(math.radians(angle), [0, 1, 0])
            )
            trunk_parts.append(branch)

    # Merge each colour group into a single mesh with PBR material
    trunk_merged = trimesh.util.concatenate(trunk_parts) if trunk_parts else None
    canopy_merged = trimesh.util.concatenate(canopy_parts) if canopy_parts else None

    if trunk_merged is not None:
        _pbr_color(trunk_merged, _TREE_TRUNK_COLOR)
    if canopy_merged is not None:
        _pbr_color(canopy_merged, canopy_color)

    # Assemble as a Scene so each material group stays separate in GLB
    scene = trimesh.Scene()
    if trunk_merged is not None:
        scene.add_geometry(trunk_merged, node_name="trunk", geom_name="trunk")
    if canopy_merged is not None:
        scene.add_geometry(canopy_merged, node_name="canopy", geom_name="canopy")

    # Ground the scene (shift so min_y == 0)
    bounds = np.asarray(scene.bounds, dtype=np.float64)
    if abs(bounds[0][1]) > 1e-6:
        shift = np.eye(4, dtype=np.float64)
        shift[1, 3] = -float(bounds[0][1])
        scene.apply_transform(shift)

    # Also produce a concatenated flat mesh for quality metrics
    all_parts_flat = []
    for part in trunk_parts + canopy_parts:
        p = part.copy()
        all_parts_flat.append(p)
    flat_mesh = _ground(_concat(all_parts_flat))

    total_height = trunk_h + canopy_r * 2.0
    canopy_diameter = canopy_r * 2.0
    expected_dims = (canopy_diameter, total_height, canopy_diameter)
    audit = _TreeAudit(
        expected_dims=expected_dims,
        canopy_style=params.canopy_style,
        trunk_height_m=trunk_h,
    )
    return scene, flat_mesh, audit


class _AmphitheaterAudit:
    def __init__(self, expected_dims: Tuple[float, float, float]) -> None:
        self.expected_dims = expected_dims


def _build_amphitheater_mesh(params: AmphitheaterParams, *, detail_level: int):
    trimesh = _require_trimesh()
    primary, _ = _material_palette("concrete", params.style_tag)
    parts = []
    width = float(params.width_m)
    depth = float(params.depth_m)
    tiers = max(2, int(params.tier_count))
    tier_h = float(params.tier_height_m)
    # Stack tiers: each tier is a box, wider toward back
    for i in range(tiers):
        t_width = width * (0.5 + 0.5 * (i + 1) / tiers)
        t_depth = depth / tiers
        box = trimesh.creation.box(extents=(t_width, tier_h, t_depth))
        z_offset = -depth * 0.5 + t_depth * 0.5 + i * t_depth
        box.apply_translation([0.0, tier_h * 0.5 + i * tier_h, z_offset])
        parts.append(_color(box, primary))
    flat = _ground(trimesh.util.concatenate(parts))
    audit = _AmphitheaterAudit(expected_dims=_bbox_size(flat))
    return flat, audit


class _PlaygroundAudit:
    def __init__(self, expected_dims: Tuple[float, float, float]) -> None:
        self.expected_dims = expected_dims


def _build_playground_mesh(params: PlaygroundParams, *, detail_level: int):
    trimesh = _require_trimesh()
    primary, accent = _material_palette("metal_wood", params.style_tag)
    parts = []
    w = float(params.width_m)
    d = float(params.depth_m)
    ph = float(params.platform_height_m)
    sl = float(params.slide_length_m)
    # platform
    plat = trimesh.creation.box(extents=(w * 0.6, ph, d * 0.5))
    plat.apply_translation([0.0, ph * 0.5, -d * 0.25])
    parts.append(_color(plat, primary))
    # slide board
    board = trimesh.creation.box(extents=(w * 0.25, 0.06, sl))
    board.apply_translation([0.0, ph + 0.03, sl * 0.25])
    board.apply_transform(
        trimesh.transformations.rotation_matrix(math.radians(-25.0), [1, 0, 0], [0, ph, 0])
    )
    parts.append(_color(board, accent))
    # ladder rails
    for sx in (-w * 0.22, w * 0.22):
        rail = trimesh.creation.cylinder(radius=0.03, height=ph, sections=8)
        rail.apply_translation([sx, ph * 0.5, -d * 0.45])
        parts.append(_color(rail, accent))
    flat = _ground(trimesh.util.concatenate(parts))
    audit = _PlaygroundAudit(expected_dims=_bbox_size(flat))
    return flat, audit


class _OutdoorSeatingAudit:
    def __init__(self, expected_dims: Tuple[float, float, float]) -> None:
        self.expected_dims = expected_dims


def _build_outdoor_seating_mesh(params: OutdoorSeatingParams, *, detail_level: int):
    trimesh = _require_trimesh()
    primary, accent = _material_palette("metal_wood", params.style_tag)
    parts = []
    r = float(params.table_radius_m)
    n = max(2, int(params.chair_count))
    # table
    table = trimesh.creation.cylinder(radius=r, height=0.05, sections=16)
    table.apply_translation([0.0, 0.75, 0.0])
    parts.append(_color(table, accent))
    # chairs
    for i in range(n):
        angle = (2 * math.pi / n) * i
        cx = math.cos(angle) * r * 1.6
        cz = math.sin(angle) * r * 1.6
        seat = trimesh.creation.box(extents=(0.45, 0.05, 0.45))
        seat.apply_translation([cx, 0.45, cz])
        parts.append(_color(seat, primary))
        back = trimesh.creation.box(extents=(0.45, 0.40, 0.05))
        back.apply_translation([cx, 0.70, cz + 0.20])
        parts.append(_color(back, primary))
    flat = _ground(trimesh.util.concatenate(parts))
    audit = _OutdoorSeatingAudit(expected_dims=_bbox_size(flat))
    return flat, audit


class _KioskAudit:
    def __init__(self, expected_dims: Tuple[float, float, float]) -> None:
        self.expected_dims = expected_dims


def _build_kiosk_mesh(params: KioskParams, *, detail_level: int):
    trimesh = _require_trimesh()
    primary, accent = _material_palette("metal", params.style_tag)
    parts = []
    w = float(params.width_m)
    d = float(params.depth_m)
    h = float(params.height_m)
    # 4 posts
    for sx, sz in ((-w*0.45, -d*0.45), (w*0.45, -d*0.45), (w*0.45, d*0.45), (-w*0.45, d*0.45)):
        post = trimesh.creation.cylinder(radius=0.04, height=h, sections=8)
        post.apply_translation([sx, h * 0.5, sz])
        parts.append(_color(post, primary))
    # roof pyramid (cone with 4 sections looks pyramid-ish)
    roof = trimesh.creation.cone(radius=max(w, d) * 0.55, height=h * 0.25, sections=4)
    roof.apply_translation([0.0, h + h * 0.125, 0.0])
    parts.append(_color(roof, accent))
    flat = _ground(trimesh.util.concatenate(parts))
    audit = _KioskAudit(expected_dims=_bbox_size(flat))
    return flat, audit


class _SculptureAudit:
    def __init__(self, expected_dims: Tuple[float, float, float]) -> None:
        self.expected_dims = expected_dims


def _build_sculpture_mesh(params: SculptureParams, *, detail_level: int):
    trimesh = _require_trimesh()
    primary, _ = _material_palette("metal", params.style_tag)
    parts = []
    h = float(params.height_m)
    bw = float(params.base_width_m)
    # base
    base = trimesh.creation.box(extents=(bw, h * 0.25, bw))
    base.apply_translation([0.0, h * 0.125, 0.0])
    parts.append(_color(base, primary))
    # twisted upper body (cylinder + sphere)
    body = trimesh.creation.cylinder(radius=bw * 0.35, height=h * 0.75, sections=8)
    body.apply_translation([0.0, h * 0.25 + h * 0.375, 0.0])
    parts.append(_color(body, primary))
    top = trimesh.creation.icosphere(subdivisions=1, radius=bw * 0.4)
    top.apply_translation([0.0, h - bw * 0.2, 0.0])
    parts.append(_color(top, primary))
    flat = _ground(trimesh.util.concatenate(parts))
    audit = _SculptureAudit(expected_dims=_bbox_size(flat))
    return flat, audit


def _amphitheater_quality_metrics(mesh, params: AmphitheaterParams, runtime_profile: str, audit: _AmphitheaterAudit) -> GenerationQualityMetrics:
    actual_dims = _bbox_size(mesh)
    face_count = int(len(mesh.faces))
    poly_budget_k = int(_POLY_BUDGET_K["amphitheater"][runtime_profile])
    ground_contact_ok = abs(float(mesh.bounds[0][1])) <= 0.01
    return GenerationQualityMetrics(
        face_count=face_count,
        poly_budget_k=poly_budget_k,
        dimension_error_ratio=_dimension_error_ratio(actual_dims, audit.expected_dims),
        ground_contact_ok=ground_contact_ok,
        meets_min_faces=face_count >= _MIN_FACES["amphitheater"],
        within_poly_budget=face_count <= poly_budget_k * 1000,
    )


def _playground_quality_metrics(mesh, params: PlaygroundParams, runtime_profile: str, audit: _PlaygroundAudit) -> GenerationQualityMetrics:
    actual_dims = _bbox_size(mesh)
    face_count = int(len(mesh.faces))
    poly_budget_k = int(_POLY_BUDGET_K["playground"][runtime_profile])
    ground_contact_ok = abs(float(mesh.bounds[0][1])) <= 0.01
    return GenerationQualityMetrics(
        face_count=face_count,
        poly_budget_k=poly_budget_k,
        dimension_error_ratio=_dimension_error_ratio(actual_dims, audit.expected_dims),
        ground_contact_ok=ground_contact_ok,
        meets_min_faces=face_count >= _MIN_FACES["playground"],
        within_poly_budget=face_count <= poly_budget_k * 1000,
    )


def _outdoor_seating_quality_metrics(mesh, params: OutdoorSeatingParams, runtime_profile: str, audit: _OutdoorSeatingAudit) -> GenerationQualityMetrics:
    actual_dims = _bbox_size(mesh)
    face_count = int(len(mesh.faces))
    poly_budget_k = int(_POLY_BUDGET_K["outdoor_seating"][runtime_profile])
    ground_contact_ok = abs(float(mesh.bounds[0][1])) <= 0.01
    return GenerationQualityMetrics(
        face_count=face_count,
        poly_budget_k=poly_budget_k,
        dimension_error_ratio=_dimension_error_ratio(actual_dims, audit.expected_dims),
        ground_contact_ok=ground_contact_ok,
        meets_min_faces=face_count >= _MIN_FACES["outdoor_seating"],
        within_poly_budget=face_count <= poly_budget_k * 1000,
    )


def _kiosk_quality_metrics(mesh, params: KioskParams, runtime_profile: str, audit: _KioskAudit) -> GenerationQualityMetrics:
    actual_dims = _bbox_size(mesh)
    face_count = int(len(mesh.faces))
    poly_budget_k = int(_POLY_BUDGET_K["kiosk"][runtime_profile])
    ground_contact_ok = abs(float(mesh.bounds[0][1])) <= 0.01
    return GenerationQualityMetrics(
        face_count=face_count,
        poly_budget_k=poly_budget_k,
        dimension_error_ratio=_dimension_error_ratio(actual_dims, audit.expected_dims),
        ground_contact_ok=ground_contact_ok,
        meets_min_faces=face_count >= _MIN_FACES["kiosk"],
        within_poly_budget=face_count <= poly_budget_k * 1000,
    )


def _sculpture_quality_metrics(mesh, params: SculptureParams, runtime_profile: str, audit: _SculptureAudit) -> GenerationQualityMetrics:
    actual_dims = _bbox_size(mesh)
    face_count = int(len(mesh.faces))
    poly_budget_k = int(_POLY_BUDGET_K["sculpture"][runtime_profile])
    ground_contact_ok = abs(float(mesh.bounds[0][1])) <= 0.01
    return GenerationQualityMetrics(
        face_count=face_count,
        poly_budget_k=poly_budget_k,
        dimension_error_ratio=_dimension_error_ratio(actual_dims, audit.expected_dims),
        ground_contact_ok=ground_contact_ok,
        meets_min_faces=face_count >= _MIN_FACES["sculpture"],
        within_poly_budget=face_count <= poly_budget_k * 1000,
    )


def _tree_quality_metrics(mesh, params: TreeParams, runtime_profile: str, audit: _TreeAudit) -> GenerationQualityMetrics:
    actual_dims = _bbox_size(mesh)
    face_count = int(len(mesh.faces))
    poly_budget_k = int(_POLY_BUDGET_K["tree"][runtime_profile])
    ground_contact_ok = abs(float(mesh.bounds[0][1])) <= 0.01
    return GenerationQualityMetrics(
        face_count=face_count,
        poly_budget_k=poly_budget_k,
        dimension_error_ratio=_dimension_error_ratio(actual_dims, audit.expected_dims),
        ground_contact_ok=ground_contact_ok,
        meets_min_faces=face_count >= _MIN_FACES["tree"],
        within_poly_budget=face_count <= poly_budget_k * 1000,
    )


def _quality_gate(asset_kind: str, metrics: GenerationQualityMetrics) -> None:
    if not metrics.ground_contact_ok:
        raise RuntimeError(f"{asset_kind} generation failed ground contact check")
    if not metrics.meets_min_faces:
        raise RuntimeError(
            f"{asset_kind} generation failed minimum face count: {metrics.face_count} < {_MIN_FACES[asset_kind]}"
        )
    if not metrics.within_poly_budget:
        raise RuntimeError(
            f"{asset_kind} generation exceeded poly budget: {metrics.face_count} > {metrics.poly_budget_k * 1000}"
        )
    if asset_kind == "bench" and metrics.stability_check_ok is False:
        raise RuntimeError("bench generation failed stability check")
    if asset_kind == "lamp" and metrics.clearance_ok is False:
        raise RuntimeError("lamp generation failed clearance/stability checks")


def generate_parametric_asset(request: GenerationRequest | Mapping[str, object]) -> ParametricAssetResult:
    """Generate one deterministic bench, lamp, building, or tree mesh from explicit parameters."""
    warnings_list: list[str] = []
    normalized_request = _validate_request(_to_request(request), warnings_list)
    resolved_device_backend = resolve_device_backend(
        preferred=normalized_request.device_backend,
        allow_fallback=normalized_request.allow_fallback,
    )

    if normalized_request.asset_kind == "bench":
        params = _validate_bench_params(normalized_request.params, warnings_list)
        effective_detail_level = _effective_detail_level(normalized_request.runtime_profile, params.detail_level)
        mesh, audit = _build_bench_mesh(params, detail_level=effective_detail_level)
        metrics = _bench_quality_metrics(mesh, params, normalized_request.runtime_profile, audit)
        if normalized_request.runtime_profile == "production" and effective_detail_level < 3 and not metrics.meets_min_faces:
            warnings_list.append("production bench detail preset was upshifted once to satisfy quality gates")
            effective_detail_level = 3
            mesh, audit = _build_bench_mesh(params, detail_level=effective_detail_level)
            metrics = _bench_quality_metrics(mesh, params, normalized_request.runtime_profile, audit)
        _quality_gate("bench", metrics)
        snapshot = asdict(params)
        snapshot["effective_detail_level"] = int(effective_detail_level)
        return ParametricAssetResult(
            asset_kind="bench",
            runtime_profile=normalized_request.runtime_profile,
            resolved_device_backend=resolved_device_backend,
            mesh=mesh,
            bbox_size_xyz=_bbox_size(mesh),
            bbox_bounds=(
                tuple(float(v) for v in mesh.bounds[0]),
                tuple(float(v) for v in mesh.bounds[1]),
            ),
            parameter_snapshot=snapshot,
            quality_metrics=metrics,
            warnings=tuple(warnings_list),
            material_family=params.material_family,
            style_tags=(params.style_tag,),
        )

    if normalized_request.asset_kind == "building":
        params = _validate_building_params(normalized_request.params, warnings_list)
        effective_detail_level = _effective_detail_level(normalized_request.runtime_profile, params.detail_level)
        mesh, audit = _build_building_mesh(params, detail_level=effective_detail_level)
        metrics = _building_quality_metrics(mesh, params, normalized_request.runtime_profile, audit)
        if normalized_request.runtime_profile == "production" and effective_detail_level < 3 and not metrics.meets_min_faces:
            warnings_list.append("production building detail preset was upshifted once to satisfy quality gates")
            effective_detail_level = 3
            mesh, audit = _build_building_mesh(params, detail_level=effective_detail_level)
            metrics = _building_quality_metrics(mesh, params, normalized_request.runtime_profile, audit)
        _quality_gate("building", metrics)
        snapshot = asdict(params)
        snapshot["effective_detail_level"] = int(effective_detail_level)
        return ParametricAssetResult(
            asset_kind="building",
            runtime_profile=normalized_request.runtime_profile,
            resolved_device_backend=resolved_device_backend,
            mesh=mesh,
            bbox_size_xyz=_bbox_size(mesh),
            bbox_bounds=(
                tuple(float(v) for v in mesh.bounds[0]),
                tuple(float(v) for v in mesh.bounds[1]),
            ),
            parameter_snapshot=snapshot,
            quality_metrics=metrics,
            warnings=tuple(warnings_list),
            material_family=params.material_family,
            style_tags=(params.style_tag,),
        )

    if normalized_request.asset_kind == "tree":
        params = _validate_tree_params(normalized_request.params, warnings_list)
        effective_detail_level = _effective_detail_level(normalized_request.runtime_profile, params.detail_level)
        tree_scene, flat_mesh, audit = _build_tree_mesh(params, detail_level=effective_detail_level)
        metrics = _tree_quality_metrics(flat_mesh, params, normalized_request.runtime_profile, audit)
        if normalized_request.runtime_profile == "production" and effective_detail_level < 3 and not metrics.meets_min_faces:
            warnings_list.append("production tree detail preset was upshifted once to satisfy quality gates")
            effective_detail_level = 3
            tree_scene, flat_mesh, audit = _build_tree_mesh(params, detail_level=effective_detail_level)
            metrics = _tree_quality_metrics(flat_mesh, params, normalized_request.runtime_profile, audit)
        _quality_gate("tree", metrics)
        snapshot = asdict(params)
        snapshot["effective_detail_level"] = int(effective_detail_level)
        scene_bounds = np.asarray(tree_scene.bounds, dtype=np.float64)
        scene_span = scene_bounds[1] - scene_bounds[0]
        return ParametricAssetResult(
            asset_kind="tree",
            runtime_profile=normalized_request.runtime_profile,
            resolved_device_backend=resolved_device_backend,
            mesh=tree_scene,
            bbox_size_xyz=(float(scene_span[0]), float(scene_span[1]), float(scene_span[2])),
            bbox_bounds=(
                tuple(float(v) for v in scene_bounds[0]),
                tuple(float(v) for v in scene_bounds[1]),
            ),
            parameter_snapshot=snapshot,
            quality_metrics=metrics,
            warnings=tuple(warnings_list),
            style_tags=(params.style_tag,),
        )

    # Helper to avoid repeating boilerplate for simple primitive assets
    def _dispatch_primitive_asset(kind: str, validate_params, build_mesh, quality_metrics, material_family: str):
        params = validate_params(normalized_request.params, warnings_list)
        effective_detail_level = _effective_detail_level(normalized_request.runtime_profile, params.detail_level)
        mesh, audit = build_mesh(params, detail_level=effective_detail_level)
        metrics = quality_metrics(mesh, params, normalized_request.runtime_profile, audit)
        if normalized_request.runtime_profile == "production" and effective_detail_level < 3 and not metrics.meets_min_faces:
            warnings_list.append(f"production {kind} detail preset was upshifted once to satisfy quality gates")
            effective_detail_level = 3
            mesh, audit = build_mesh(params, detail_level=effective_detail_level)
            metrics = quality_metrics(mesh, params, normalized_request.runtime_profile, audit)
        _quality_gate(kind, metrics)
        snapshot = asdict(params)
        snapshot["effective_detail_level"] = int(effective_detail_level)
        return ParametricAssetResult(
            asset_kind=kind,
            runtime_profile=normalized_request.runtime_profile,
            resolved_device_backend=resolved_device_backend,
            mesh=mesh,
            bbox_size_xyz=_bbox_size(mesh),
            bbox_bounds=(
                tuple(float(v) for v in mesh.bounds[0]),
                tuple(float(v) for v in mesh.bounds[1]),
            ),
            parameter_snapshot=snapshot,
            quality_metrics=metrics,
            warnings=tuple(warnings_list),
            material_family=material_family,
            style_tags=(params.style_tag,),
        )

    if normalized_request.asset_kind == "amphitheater":
        return _dispatch_primitive_asset(
            "amphitheater",
            _validate_amphitheater_params,
            _build_amphitheater_mesh,
            _amphitheater_quality_metrics,
            "concrete",
        )
    if normalized_request.asset_kind == "playground":
        return _dispatch_primitive_asset(
            "playground",
            _validate_playground_params,
            _build_playground_mesh,
            _playground_quality_metrics,
            "metal_wood",
        )
    if normalized_request.asset_kind == "outdoor_seating":
        return _dispatch_primitive_asset(
            "outdoor_seating",
            _validate_outdoor_seating_params,
            _build_outdoor_seating_mesh,
            _outdoor_seating_quality_metrics,
            "metal_wood",
        )
    if normalized_request.asset_kind == "kiosk":
        return _dispatch_primitive_asset(
            "kiosk",
            _validate_kiosk_params,
            _build_kiosk_mesh,
            _kiosk_quality_metrics,
            "metal",
        )
    if normalized_request.asset_kind == "sculpture":
        return _dispatch_primitive_asset(
            "sculpture",
            _validate_sculpture_params,
            _build_sculpture_mesh,
            _sculpture_quality_metrics,
            "metal",
        )

    params = _validate_lamp_params(normalized_request.params, warnings_list)
    effective_detail_level = _effective_detail_level(normalized_request.runtime_profile, params.detail_level)
    mesh, audit = _build_lamp_mesh(params, detail_level=effective_detail_level)
    metrics = _lamp_quality_metrics(mesh, params, normalized_request.runtime_profile, audit)
    if normalized_request.runtime_profile == "production" and effective_detail_level < 3 and not metrics.meets_min_faces:
        warnings_list.append("production lamp detail preset was upshifted once to satisfy quality gates")
        effective_detail_level = 3
        mesh, audit = _build_lamp_mesh(params, detail_level=effective_detail_level)
        metrics = _lamp_quality_metrics(mesh, params, normalized_request.runtime_profile, audit)
    _quality_gate("lamp", metrics)
    snapshot = asdict(params)
    snapshot["effective_detail_level"] = int(effective_detail_level)
    return ParametricAssetResult(
        asset_kind="lamp",
        runtime_profile=normalized_request.runtime_profile,
        resolved_device_backend=resolved_device_backend,
        mesh=mesh,
        bbox_size_xyz=_bbox_size(mesh),
        bbox_bounds=(
            tuple(float(v) for v in mesh.bounds[0]),
            tuple(float(v) for v in mesh.bounds[1]),
        ),
        parameter_snapshot=snapshot,
        quality_metrics=metrics,
        warnings=tuple(warnings_list),
        material_family=params.material_family,
        style_tags=(params.style_tag,),
    )
