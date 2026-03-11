#!/usr/bin/env python3
"""Generate 120 procedural GLB street-furniture assets from m3_asset_task_list.csv.

Reads the CSV task list, generates parametric meshes using trimesh primitives,
fits each to its target bounding box, and writes the JSONL manifest.

Usage:
    .venv/bin/python scripts/m3_02_generate_procedural_assets.py \
        --csv docs/m3_asset_task_list.csv \
        --mesh-out-dir data/real/meshes \
        --manifest-out data/real/real_assets_manifest.jsonl \
        --seed 42 --clean
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import trimesh
except ImportError as exc:
    raise RuntimeError("trimesh is required. Install via: pip install -r requirements-m2.txt") from exc

from roadgen3d.parametric_assets import GenerationRequest, ParametricAssetResult, generate_parametric_asset

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AssetSpec:
    task_id: str
    category: str
    asset_id: str
    style_tag: str
    text_desc: str
    target_h: float
    target_w: float
    target_d: float
    poly_budget_k: int
    license: str
    source: str


# ---------------------------------------------------------------------------
# Style color palette: (primary_rgba, accent_rgba)
# ---------------------------------------------------------------------------

STYLE_COLORS: Dict[str, Tuple[Tuple[int, ...], Tuple[int, ...]]] = {
    "modern":       ((128, 128, 128, 255), (64, 64, 64, 255)),
    "classic":      ((101, 67, 33, 255),   (34, 100, 34, 255)),
    "industrial":   ((169, 169, 169, 255), (105, 105, 105, 255)),
    "minimalist":   ((240, 240, 240, 255), (200, 200, 200, 255)),
    "ornate":       ((212, 175, 55, 255),  (0, 80, 0, 255)),
    "retro":        ((255, 99, 71, 255),   (240, 230, 140, 255)),
    "modular":      ((70, 130, 180, 255),  (60, 179, 113, 255)),
    "eco":          ((107, 142, 35, 255),  (139, 90, 43, 255)),
    "brutalist":    ((112, 128, 144, 255), (90, 90, 90, 255)),
    "nordic":       ((222, 184, 135, 255), (245, 245, 220, 255)),
    "japan_scandi": ((245, 222, 179, 255), (188, 143, 143, 255)),
    "victorian":    ((28, 28, 28, 255),    (72, 61, 139, 255)),
    "contemporary": ((192, 192, 192, 255), (160, 160, 160, 255)),
    "tactical":     ((85, 107, 47, 255),   (255, 215, 0, 255)),
    "art_deco":     ((255, 215, 0, 255),   (20, 20, 20, 255)),
}

STYLE_ORDER = list(STYLE_COLORS.keys())

MIN_FACES_BY_CATEGORY: Dict[str, int] = {
    "bench": 300,
    "lamp": 500,
    "trash": 300,
    "tree": 1500,
    "bus_stop": 800,
    "mailbox": 250,
    "hydrant": 350,
    "bollard": 180,
}

MAX_GENERATION_ATTEMPTS = 10
ANISOTROPIC_SCALE_WARN_THRESHOLD = 6.0


def _color(mesh: trimesh.Trimesh, rgba: Tuple[int, ...]) -> trimesh.Trimesh:
    mesh.visual.face_colors = list(rgba)
    return mesh


def _concat(parts: List[trimesh.Trimesh]) -> trimesh.Trimesh:
    return trimesh.util.concatenate(parts)


# ---------------------------------------------------------------------------
# Dimension fitting
# ---------------------------------------------------------------------------

def fit_to_target_box(
    mesh: trimesh.Trimesh,
    target_h: float,
    target_w: float,
    target_d: float,
    *,
    label: str = "",
) -> trimesh.Trimesh:
    """Anisotropic scale so bounding box matches target exactly, then ground at Y=0."""
    bounds = mesh.bounds
    span = bounds[1] - bounds[0]
    sx = target_w / max(float(span[0]), 1e-6)
    sy = target_h / max(float(span[1]), 1e-6)
    sz = target_d / max(float(span[2]), 1e-6)
    if max(abs(sx), abs(sy), abs(sz)) > ANISOTROPIC_SCALE_WARN_THRESHOLD:
        name = label if label else "mesh"
        print(
            f"  WARN: {name} has large anisotropic scale factors "
            f"(sx={sx:.2f}, sy={sy:.2f}, sz={sz:.2f})"
        )
    mesh.apply_transform(np.diag([sx, sy, sz, 1.0]))
    mesh.apply_translation([0.0, -float(mesh.bounds[0][1]), 0.0])
    return mesh


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return float(min(max(float(value), float(minimum)), float(maximum)))


def _contains_any(text: str, keywords: Tuple[str, ...]) -> bool:
    lowered = str(text).strip().lower()
    return any(keyword in lowered for keyword in keywords)


def _bench_style_defaults(style_tag: str, text_desc: str) -> Dict[str, object]:
    style = str(style_tag).strip().lower()
    text = str(text_desc).strip().lower()
    params: Dict[str, object] = {"leg_type": "dual_frame", "armrest_enabled": False, "slat_count": 5, "material_family": "metal_wood"}
    if style in {"minimalist", "brutalist"}:
        params["leg_type"] = "pedestal"
    elif style in {"classic", "victorian"}:
        params["leg_type"] = "four_leg"

    if style in {"classic", "ornate", "victorian", "retro"} or _contains_any(text, ("armrest", "curved arm")):
        params["armrest_enabled"] = True

    if style in {"minimalist", "brutalist"} or _contains_any(text, ("concrete", "slab")):
        params["material_family"] = "concrete"
    elif style in {"eco", "nordic", "japan_scandi"}:
        params["material_family"] = "wood"
    elif style in {"classic", "retro", "victorian", "ornate"}:
        params["material_family"] = "metal_wood"
    elif style in {"industrial", "modern", "modular"}:
        params["material_family"] = "metal_wood"

    if style in {"classic", "eco", "nordic", "japan_scandi"}:
        params["slat_count"] = 6
    elif style in {"brutalist", "minimalist"}:
        params["slat_count"] = 3
    return params


def _lamp_style_defaults(style_tag: str, text_desc: str) -> Dict[str, object]:
    style = str(style_tag).strip().lower()
    text = str(text_desc).strip().lower()
    params: Dict[str, object] = {
        "luminaire_type": "flat_led",
        "single_or_double_arm": "single",
        "light_direction": "roadside",
        "material_family": "metal",
        "arm_length_m": 0.80,
    }
    if _contains_any(text, ("globe", "sphere", "lantern")):
        params["luminaire_type"] = "globe"
    elif _contains_any(text, ("box", "rectangular", "floodlight")):
        params["luminaire_type"] = "box"
    elif _contains_any(text, ("cone", "conical", "downlight", "bowl", "shade")):
        params["luminaire_type"] = "cone"

    if style in {"ornate", "victorian"} or _contains_any(text, ("multi-arm", "three curved branches", "four ornamental branches")):
        params["single_or_double_arm"] = "double"

    if _contains_any(text, ("downlight", "bowl", "cone")):
        params["light_direction"] = "downward"
    elif _contains_any(text, ("bidirectional", "double-sided")):
        params["light_direction"] = "bidirectional"

    if style == "brutalist":
        params["material_family"] = "cast_iron"
    elif style in {"classic", "victorian", "ornate"}:
        params["material_family"] = "cast_iron"
    elif style in {"industrial", "modern", "modular", "retro", "minimalist"}:
        params["material_family"] = "painted_steel"

    arm_lengths = {
        "modern": 0.80,
        "classic": 0.90,
        "industrial": 0.95,
        "minimalist": 0.55,
        "ornate": 1.20,
        "retro": 1.10,
        "modular": 0.90,
        "eco": 0.85,
        "brutalist": 0.70,
        "nordic": 0.70,
        "japan_scandi": 0.70,
        "victorian": 1.20,
    }
    params["arm_length_m"] = arm_lengths.get(style, 0.80)
    if _contains_any(text, ("multi-arm", "three curved branches", "four ornamental branches")):
        params["arm_length_m"] = 1.20
    elif _contains_any(text, ("downlight", "bowl", "shade", "cone")):
        params["arm_length_m"] = 0.75
    elif _contains_any(text, ("floodlight", "rectangular")):
        params["arm_length_m"] = 0.95
    return params


def _parametric_request_from_asset_spec(
    spec: AssetSpec,
    runtime_profile: str,
    device: str,
) -> GenerationRequest:
    profile = str(runtime_profile).strip().lower()
    if profile not in {"preview", "production"}:
        raise ValueError("parametric_runtime_profile must be 'preview' or 'production'")
    style_tag = str(spec.style_tag).strip().lower() or "modern"
    detail_level = 3 if profile == "production" else 1

    if spec.category == "bench":
        params: Dict[str, object] = {
            "width_m": float(spec.target_w),
            "depth_m": _clamp(float(spec.target_d), 0.40, 0.75),
            "seat_height_m": 0.45,
            "backrest_height_m": _clamp(float(spec.target_h) - 0.45, 0.20, 0.55),
            "backrest_angle_deg": 12.0,
            "style_tag": style_tag,
            "detail_level": detail_level,
        }
        params.update(_bench_style_defaults(style_tag, spec.text_desc))
        return GenerationRequest(
            asset_kind="bench",
            runtime_profile=profile,
            device_backend=str(device).strip().lower() or "auto",
            seed=42,
            params=params,
        )

    if spec.category == "lamp":
        base_diameter = _clamp(max(float(spec.target_w), float(spec.target_d)), 0.25, 0.60)
        params = {
            "pole_height_m": float(spec.target_h),
            "base_diameter_m": base_diameter,
            "pole_radius_m": _clamp(base_diameter * 0.17, 0.04, 0.12),
            "arm_length_m": 0.80,
            "style_tag": style_tag,
            "detail_level": detail_level,
        }
        params.update(_lamp_style_defaults(style_tag, spec.text_desc))
        return GenerationRequest(
            asset_kind="lamp",
            runtime_profile=profile,
            device_backend=str(device).strip().lower() or "auto",
            seed=42,
            params=params,
        )

    raise ValueError(f"Unsupported parametric category: {spec.category}")


# ---------------------------------------------------------------------------
# BENCH generators (15 variants)
# ---------------------------------------------------------------------------

def _bench_variant(
    idx: int,
    style: str,
    primary: tuple,
    accent: tuple,
    complexity_level: int = 0,
) -> trimesh.Trimesh:
    parts: List[trimesh.Trimesh] = []

    # Common dimensions (will be scaled to target later)
    W, D = 1.80, 0.55
    seat_thick = 0.06
    seat_h = 0.45

    if idx == 0:  # modern: slab seat + thin legs
        seat = _color(trimesh.creation.box(extents=(W, seat_thick, D)), primary)
        seat.apply_translation([0, seat_h, 0])
        back = _color(trimesh.creation.box(extents=(W, 0.40, 0.04)), accent)
        back.apply_translation([0, seat_h + 0.23, -D / 2 + 0.02])
        parts = [seat, back]
        for x in [-0.7, 0.7]:
            for z in [-0.15, 0.15]:
                leg = _color(trimesh.creation.cylinder(radius=0.025, height=seat_h, sections=12), accent)
                leg.apply_translation([x, seat_h / 2, z])
                parts.append(leg)

    elif idx == 1:  # classic: slatted seat + thick legs
        for i in range(5):
            slat = _color(trimesh.creation.box(extents=(W, 0.03, 0.08)), primary)
            slat.apply_translation([0, seat_h, -D / 2 + 0.06 + i * 0.11])
            parts.append(slat)
        for i in range(4):
            bslat = _color(trimesh.creation.box(extents=(W, 0.08, 0.03)), accent)
            bslat.apply_translation([0, seat_h + 0.08 + i * 0.10, -D / 2 + 0.015])
            parts.append(bslat)
        for x in [-0.8, 0.8]:
            leg = _color(trimesh.creation.box(extents=(0.06, seat_h, 0.06)), accent)
            leg.apply_translation([x, seat_h / 2, 0])
            parts.append(leg)

    elif idx == 2:  # industrial: perforated look + angle frame
        seat = _color(trimesh.creation.box(extents=(W, 0.04, D)), primary)
        seat.apply_translation([0, seat_h, 0])
        frame_h = _color(trimesh.creation.box(extents=(W + 0.08, 0.06, 0.06)), accent)
        frame_h.apply_translation([0, seat_h - 0.03, D / 2])
        frame_h2 = _color(trimesh.creation.box(extents=(W + 0.08, 0.06, 0.06)), accent)
        frame_h2.apply_translation([0, seat_h - 0.03, -D / 2])
        parts = [seat, frame_h, frame_h2]
        for x in [-0.85, 0.85]:
            for z in [-D / 2, D / 2]:
                leg = _color(trimesh.creation.box(extents=(0.06, seat_h, 0.06)), accent)
                leg.apply_translation([x, seat_h / 2, z])
                parts.append(leg)

    elif idx == 3:  # minimalist: no back, pedestal
        seat = _color(trimesh.creation.box(extents=(W, 0.05, D)), primary)
        seat.apply_translation([0, seat_h, 0])
        ped = _color(trimesh.creation.box(extents=(0.30, seat_h, 0.30)), accent)
        ped.apply_translation([0, seat_h / 2, 0])
        parts = [seat, ped]

    elif idx == 4:  # ornate: scroll-like, sphere decorations
        seat = _color(trimesh.creation.box(extents=(W, seat_thick, D)), primary)
        seat.apply_translation([0, seat_h, 0])
        back = _color(trimesh.creation.box(extents=(W, 0.45, 0.03)), accent)
        back.apply_translation([0, seat_h + 0.25, -D / 2 + 0.015])
        parts = [seat, back]
        for x in [-0.8, 0.0, 0.8]:
            ball = _color(trimesh.creation.icosphere(subdivisions=2, radius=0.04), primary)
            ball.apply_translation([x, seat_h + 0.50, -D / 2 + 0.015])
            parts.append(ball)
        for x in [-0.8, 0.8]:
            leg = _color(trimesh.creation.cylinder(radius=0.04, height=seat_h, sections=16), accent)
            leg.apply_translation([x, seat_h / 2, 0])
            parts.append(leg)

    elif idx == 5:  # retro: thick round legs
        seat = _color(trimesh.creation.box(extents=(W, 0.06, D)), primary)
        seat.apply_translation([0, seat_h, 0])
        back = _color(trimesh.creation.box(extents=(W, 0.30, 0.05)), accent)
        back.apply_translation([0, seat_h + 0.18, -D / 2 + 0.025])
        parts = [seat, back]
        for x in [-0.7, 0.0, 0.7]:
            leg = _color(trimesh.creation.cylinder(radius=0.06, height=seat_h, sections=16), primary)
            leg.apply_translation([x, seat_h / 2, 0])
            parts.append(leg)

    elif idx == 6:  # modular: 3 colored segments
        colors = [primary, accent, (70, 180, 70, 255)]
        seg_w = W / 3 - 0.02
        for si, col in enumerate(colors):
            seg = _color(trimesh.creation.box(extents=(seg_w, seat_thick, D)), col)
            seg.apply_translation([-W / 3 + si * W / 3, seat_h, 0])
            parts.append(seg)
        for x in [-0.85, -0.28, 0.28, 0.85]:
            leg = _color(trimesh.creation.cylinder(radius=0.02, height=seat_h, sections=12), accent)
            leg.apply_translation([x, seat_h / 2, 0])
            parts.append(leg)

    elif idx == 7:  # eco: timber look
        seat = _color(trimesh.creation.box(extents=(W, 0.08, D)), primary)
        seat.apply_translation([0, seat_h, 0])
        back = _color(trimesh.creation.box(extents=(W, 0.35, 0.06)), accent)
        back.apply_translation([0, seat_h + 0.22, -D / 2 + 0.03])
        parts = [seat, back]
        for x in [-0.7, 0.7]:
            leg = _color(trimesh.creation.box(extents=(0.10, seat_h, 0.10)), accent)
            leg.apply_translation([x, seat_h / 2, 0])
            parts.append(leg)

    elif idx == 8:  # brutalist: monolith
        seat = _color(trimesh.creation.box(extents=(W, 0.12, D)), primary)
        seat.apply_translation([0, seat_h, 0])
        sup = _color(trimesh.creation.box(extents=(W * 0.9, seat_h, 0.15)), primary)
        sup.apply_translation([0, seat_h / 2, 0])
        parts = [seat, sup]

    elif idx == 9:  # nordic: slender tapered
        seat = _color(trimesh.creation.box(extents=(W, 0.04, D * 0.9)), primary)
        seat.apply_translation([0, seat_h, 0])
        back = _color(trimesh.creation.box(extents=(W, 0.25, 0.03)), accent)
        back.apply_translation([0, seat_h + 0.15, -D / 2 * 0.9 + 0.015])
        parts = [seat, back]
        for x in [-0.75, 0.75]:
            leg = _color(trimesh.creation.cylinder(radius=0.02, height=seat_h, sections=12), accent)
            leg.apply_translation([x, seat_h / 2, -0.10])
            parts.append(leg)
            leg2 = _color(trimesh.creation.cylinder(radius=0.02, height=seat_h, sections=12), accent)
            leg2.apply_translation([x, seat_h / 2, 0.10])
            parts.append(leg2)

    elif idx == 10:  # japan_scandi: low, minimal
        low_h = 0.35
        seat = _color(trimesh.creation.box(extents=(W, 0.05, D)), primary)
        seat.apply_translation([0, low_h, 0])
        parts = [seat]
        for x in [-0.8, 0.8]:
            for z in [-0.20, 0.20]:
                leg = _color(trimesh.creation.box(extents=(0.04, low_h, 0.04)), accent)
                leg.apply_translation([x, low_h / 2, z])
                parts.append(leg)

    elif idx == 11:  # victorian: claw feet, floral back
        seat = _color(trimesh.creation.box(extents=(W, seat_thick, D)), accent)
        seat.apply_translation([0, seat_h, 0])
        back = _color(trimesh.creation.box(extents=(W, 0.50, 0.03)), primary)
        back.apply_translation([0, seat_h + 0.28, -D / 2 + 0.015])
        parts = [seat, back]
        for x in [-0.8, 0.8]:
            leg = _color(trimesh.creation.cylinder(radius=0.05, height=seat_h, sections=16), primary)
            leg.apply_translation([x, seat_h / 2, 0])
            claw = _color(trimesh.creation.icosphere(subdivisions=1, radius=0.06), primary)
            claw.apply_translation([x, 0.0, 0])
            parts.extend([leg, claw])

    elif idx == 12:  # contemporary: angled back
        seat = _color(trimesh.creation.box(extents=(W, seat_thick, D)), primary)
        seat.apply_translation([0, seat_h, 0])
        back = _color(trimesh.creation.box(extents=(W, 0.50, 0.04)), accent)
        back.apply_translation([0, seat_h + 0.20, -D / 2 + 0.02])
        rot = trimesh.transformations.rotation_matrix(math.radians(10), [1, 0, 0], [0, seat_h, -D / 2])
        back.apply_transform(rot)
        parts = [seat, back]
        for x in [-0.7, 0.7]:
            leg = _color(trimesh.creation.box(extents=(0.04, seat_h, D)), accent)
            leg.apply_translation([x, seat_h / 2, 0])
            parts.append(leg)

    elif idx == 13:  # tactical: center divider
        seat = _color(trimesh.creation.box(extents=(W, seat_thick, D)), primary)
        seat.apply_translation([0, seat_h, 0])
        back = _color(trimesh.creation.box(extents=(W, 0.40, 0.04)), accent)
        back.apply_translation([0, seat_h + 0.23, -D / 2 + 0.02])
        div = _color(trimesh.creation.box(extents=(0.04, 0.30, D)), accent)
        div.apply_translation([0, seat_h + 0.15, 0])
        parts = [seat, back, div]
        for x in [-0.8, 0.8]:
            leg = _color(trimesh.creation.box(extents=(0.06, seat_h, 0.06)), primary)
            leg.apply_translation([x, seat_h / 2, 0])
            parts.append(leg)

    else:  # idx==14, art_deco: stepped back
        seat = _color(trimesh.creation.box(extents=(W, seat_thick, D)), accent)
        seat.apply_translation([0, seat_h, 0])
        parts = [seat]
        for step_i in range(3):
            step_h = 0.12
            step = _color(trimesh.creation.box(extents=(W - step_i * 0.30, step_h, 0.03)), primary)
            step.apply_translation([0, seat_h + 0.05 + step_i * (step_h + 0.02), -D / 2 + 0.015])
            parts.append(step)
        for x in [-0.8, 0.8]:
            leg = _color(trimesh.creation.box(extents=(0.05, seat_h, 0.05)), accent)
            leg.apply_translation([x, seat_h / 2, 0])
            parts.append(leg)

    return _concat(parts)


# ---------------------------------------------------------------------------
# LAMP generators
# ---------------------------------------------------------------------------

def _lamp_detail_parts(
    idx: int,
    pole_h: float,
    pole_r: float,
    primary: tuple,
    accent: tuple,
    complexity_level: int,
) -> List[trimesh.Trimesh]:
    if complexity_level <= 0:
        return []
    parts: List[trimesh.Trimesh] = []
    detail_sections = 16 + complexity_level * 8

    # Base flange rings and segmented collar details.
    ring_count = 1 + complexity_level
    for ring_idx in range(ring_count):
        y = 0.10 + (pole_h * 0.75) * (ring_idx + 1) / (ring_count + 1)
        ring = _color(
            trimesh.creation.cylinder(
                radius=pole_r + 0.03 + 0.005 * ring_idx,
                height=0.025 + 0.005 * complexity_level,
                sections=detail_sections,
            ),
            accent if ring_idx % 2 else primary,
        )
        ring.apply_translation([0.0, y, 0.0])
        parts.append(ring)

    # Add side support arms and shades to break "single-pole" silhouette.
    arm_count = max(2, 2 + complexity_level)
    arm_len = 0.35 + 0.10 * complexity_level
    for arm_idx in range(arm_count):
        angle = (360.0 / arm_count) * arm_idx + idx * 3.0
        arm = _color(
            trimesh.creation.cylinder(
                radius=0.015 + 0.004 * complexity_level,
                height=arm_len,
                sections=detail_sections,
            ),
            accent,
        )
        arm.apply_translation([arm_len * 0.5, pole_h - 0.25, 0.0])
        arm.apply_transform(trimesh.transformations.rotation_matrix(math.radians(65.0), [0, 0, 1]))
        arm.apply_transform(trimesh.transformations.rotation_matrix(math.radians(angle), [0, 1, 0]))
        parts.append(arm)

        shade = _color(
            trimesh.creation.cone(
                radius=0.10 + 0.02 * complexity_level,
                height=0.14 + 0.03 * complexity_level,
                sections=detail_sections,
            ),
            primary if arm_idx % 2 else accent,
        )
        dx = (0.20 + 0.12 * complexity_level) * math.cos(math.radians(angle))
        dz = (0.20 + 0.12 * complexity_level) * math.sin(math.radians(angle))
        shade.apply_translation([dx, pole_h + 0.12, dz])
        parts.append(shade)
    return parts


def _lamp_variant(
    idx: int,
    style: str,
    primary: tuple,
    accent: tuple,
    complexity_level: int = 0,
) -> trimesh.Trimesh:
    parts: List[trimesh.Trimesh] = []
    pole_h = 4.2
    pole_r = 0.06

    if idx == 0:  # modern: slim + rect head
        pole = _color(trimesh.creation.cylinder(radius=pole_r, height=pole_h, sections=16), primary)
        pole.apply_translation([0, pole_h / 2, 0])
        head = _color(trimesh.creation.box(extents=(0.30, 0.08, 0.30)), accent)
        head.apply_translation([0, pole_h + 0.04, 0])
        base = _color(trimesh.creation.cylinder(radius=0.15, height=0.06, sections=16), accent)
        base.apply_translation([0, 0.03, 0])
        parts = [pole, head, base]

    elif idx == 1:  # classic: tapered + globe
        pole = _color(trimesh.creation.cylinder(radius=pole_r, height=pole_h, sections=16), primary)
        pole.apply_translation([0, pole_h / 2, 0])
        globe = _color(trimesh.creation.icosphere(subdivisions=2, radius=0.15), accent)
        globe.apply_translation([0, pole_h + 0.15, 0])
        base = _color(trimesh.creation.cylinder(radius=0.18, height=0.10, sections=16), primary)
        base.apply_translation([0, 0.05, 0])
        parts = [pole, globe, base]

    elif idx == 2:  # industrial: square pole + floodlight
        pole = _color(trimesh.creation.box(extents=(0.10, pole_h, 0.10)), primary)
        pole.apply_translation([0, pole_h / 2, 0])
        head = _color(trimesh.creation.box(extents=(0.35, 0.12, 0.25)), accent)
        head.apply_translation([0.10, pole_h + 0.06, 0])
        parts = [pole, head]

    elif idx == 3:  # minimalist: ultra-thin + disc
        pole = _color(trimesh.creation.cylinder(radius=0.03, height=pole_h, sections=12), primary)
        pole.apply_translation([0, pole_h / 2, 0])
        disc = _color(trimesh.creation.cylinder(radius=0.10, height=0.03, sections=16), accent)
        disc.apply_translation([0, pole_h + 0.015, 0])
        parts = [pole, disc]

    elif idx == 4:  # ornate: 3 arms
        pole = _color(trimesh.creation.cylinder(radius=pole_r, height=pole_h, sections=16), primary)
        pole.apply_translation([0, pole_h / 2, 0])
        parts = [pole]
        for angle in [0, 120, 240]:
            arm = _color(trimesh.creation.cylinder(radius=0.02, height=0.30, sections=12), accent)
            arm.apply_translation([0.15, pole_h - 0.10, 0])
            rot = trimesh.transformations.rotation_matrix(math.radians(angle), [0, 1, 0])
            arm.apply_transform(rot)
            globe = _color(trimesh.creation.icosphere(subdivisions=2, radius=0.08), primary)
            dx = 0.25 * math.cos(math.radians(angle))
            dz = 0.25 * math.sin(math.radians(angle))
            globe.apply_translation([dx, pole_h, dz])
            parts.extend([arm, globe])

    elif idx == 5:  # retro: gooseneck + bowl
        pole = _color(trimesh.creation.cylinder(radius=pole_r, height=pole_h, sections=16), primary)
        pole.apply_translation([0, pole_h / 2, 0])
        arm = _color(trimesh.creation.cylinder(radius=0.03, height=0.35, sections=12), accent)
        arm.apply_translation([0.17, pole_h - 0.05, 0])
        rot = trimesh.transformations.rotation_matrix(math.radians(70), [0, 0, 1])
        arm.apply_transform(rot)
        bowl = _color(trimesh.creation.cylinder(radius=0.18, height=0.06, sections=16), primary)
        bowl.apply_translation([0.25, pole_h + 0.05, 0])
        parts = [pole, arm, bowl]

    elif idx == 6:  # modular: segmented pole
        parts = []
        seg_h = pole_h / 3
        for si in range(3):
            seg = _color(trimesh.creation.cylinder(radius=pole_r, height=seg_h - 0.04, sections=16), primary if si % 2 == 0 else accent)
            seg.apply_translation([0, seg_h * si + (seg_h - 0.04) / 2 + 0.02, 0])
            ring = _color(trimesh.creation.cylinder(radius=pole_r + 0.02, height=0.04, sections=16), accent)
            ring.apply_translation([0, seg_h * (si + 1), 0])
            parts.extend([seg, ring])
        head = _color(trimesh.creation.box(extents=(0.20, 0.08, 0.20)), primary)
        head.apply_translation([0, pole_h + 0.04, 0])
        parts.append(head)

    elif idx == 7:  # eco: solar panel
        pole = _color(trimesh.creation.cylinder(radius=pole_r, height=pole_h, sections=16), accent)
        pole.apply_translation([0, pole_h / 2, 0])
        panel = _color(trimesh.creation.box(extents=(0.30, 0.02, 0.20)), primary)
        panel.apply_translation([0, pole_h + 0.10, 0])
        rot = trimesh.transformations.rotation_matrix(math.radians(25), [0, 0, 1])
        panel.apply_transform(rot)
        head = _color(trimesh.creation.cylinder(radius=0.08, height=0.06, sections=16), accent)
        head.apply_translation([0, pole_h + 0.03, 0])
        parts = [pole, panel, head]

    elif idx == 8:  # brutalist: thick square + embedded
        pole = _color(trimesh.creation.box(extents=(0.14, pole_h, 0.14)), primary)
        pole.apply_translation([0, pole_h / 2, 0])
        head = _color(trimesh.creation.box(extents=(0.20, 0.20, 0.14)), accent)
        head.apply_translation([0, pole_h + 0.10, 0])
        parts = [pole, head]

    elif idx == 9:  # nordic: slim + cone shade
        pole = _color(trimesh.creation.cylinder(radius=0.035, height=pole_h, sections=12), primary)
        pole.apply_translation([0, pole_h / 2, 0])
        shade = _color(trimesh.creation.cone(radius=0.14, height=0.12, sections=16), accent)
        shade.apply_translation([0, pole_h + 0.06, 0])
        parts = [pole, shade]

    elif idx == 10:  # japan_scandi: box shade
        pole = _color(trimesh.creation.cylinder(radius=0.04, height=pole_h, sections=12), accent)
        pole.apply_translation([0, pole_h / 2, 0])
        shade = _color(trimesh.creation.box(extents=(0.22, 0.25, 0.22)), primary)
        shade.apply_translation([0, pole_h + 0.125, 0])
        parts = [pole, shade]

    elif idx == 11:  # victorian: 4 arms + globes
        pole = _color(trimesh.creation.cylinder(radius=0.07, height=pole_h, sections=16), primary)
        pole.apply_translation([0, pole_h / 2, 0])
        parts = [pole]
        for angle in [0, 90, 180, 270]:
            dx = 0.20 * math.cos(math.radians(angle))
            dz = 0.20 * math.sin(math.radians(angle))
            arm = _color(trimesh.creation.cylinder(radius=0.02, height=0.25, sections=12), primary)
            arm.apply_translation([dx / 2, pole_h - 0.15, dz / 2])
            globe = _color(trimesh.creation.icosphere(subdivisions=2, radius=0.07), accent)
            globe.apply_translation([dx, pole_h, dz])
            parts.extend([arm, globe])

    elif idx == 12:  # contemporary: angled arm
        pole = _color(trimesh.creation.cylinder(radius=pole_r, height=pole_h, sections=16), primary)
        pole.apply_translation([0, pole_h / 2, 0])
        arm = _color(trimesh.creation.cylinder(radius=0.03, height=0.50, sections=12), accent)
        arm.apply_translation([0.20, pole_h + 0.05, 0])
        rot = trimesh.transformations.rotation_matrix(math.radians(60), [0, 0, 1])
        arm.apply_transform(rot)
        head = _color(trimesh.creation.box(extents=(0.30, 0.05, 0.12)), primary)
        head.apply_translation([0.30, pole_h + 0.15, 0])
        parts = [pole, arm, head]

    elif idx == 13:  # tactical: armored + yellow band
        pole = _color(trimesh.creation.cylinder(radius=0.08, height=pole_h, sections=16), primary)
        pole.apply_translation([0, pole_h / 2, 0])
        band = _color(trimesh.creation.cylinder(radius=0.09, height=0.10, sections=16), accent)
        band.apply_translation([0, pole_h * 0.7, 0])
        head = _color(trimesh.creation.cylinder(radius=0.12, height=0.15, sections=16), primary)
        head.apply_translation([0, pole_h + 0.075, 0])
        parts = [pole, band, head]

    else:  # art_deco: stepped base + fan crown
        parts = []
        for step_i in range(3):
            r = 0.20 - step_i * 0.05
            h = 0.15
            step = _color(trimesh.creation.cylinder(radius=r, height=h, sections=16), accent if step_i % 2 else primary)
            step.apply_translation([0, step_i * h + h / 2, 0])
            parts.append(step)
        pole = _color(trimesh.creation.cylinder(radius=pole_r, height=pole_h - 0.45, sections=16), primary)
        pole.apply_translation([0, 0.45 + (pole_h - 0.45) / 2, 0])
        crown = _color(trimesh.creation.cylinder(radius=0.18, height=0.10, sections=16), accent)
        crown.apply_translation([0, pole_h + 0.05, 0])
        parts.extend([pole, crown])

    parts.extend(
        _lamp_detail_parts(
            idx=idx,
            pole_h=pole_h,
            pole_r=pole_r,
            primary=primary,
            accent=accent,
            complexity_level=complexity_level,
        )
    )
    return _concat(parts)


# ---------------------------------------------------------------------------
# TRASH generators
# ---------------------------------------------------------------------------

def _trash_variant(
    idx: int,
    style: str,
    primary: tuple,
    accent: tuple,
    complexity_level: int = 0,
) -> trimesh.Trimesh:
    parts: List[trimesh.Trimesh] = []
    body_h = 0.85
    body_r = 0.28

    if idx == 0:  # modern: cylinder + dome lid
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=20), primary)
        body.apply_translation([0, body_h / 2, 0])
        lid = _color(trimesh.creation.cylinder(radius=body_r + 0.01, height=0.04, sections=20), accent)
        lid.apply_translation([0, body_h + 0.02, 0])
        parts = [body, lid]

    elif idx == 1:  # classic: square + dome
        body = _color(trimesh.creation.box(extents=(0.55, body_h, 0.55)), primary)
        body.apply_translation([0, body_h / 2, 0])
        lid = _color(trimesh.creation.icosphere(subdivisions=2, radius=0.30), accent)
        lid.apply_translation([0, body_h + 0.10, 0])
        parts = [body, lid]

    elif idx == 2:  # industrial: wire cage frame
        parts = []
        for dz in [-0.25, 0.25]:
            for dx in [-0.25, 0.25]:
                bar = _color(trimesh.creation.box(extents=(0.03, body_h, 0.03)), primary)
                bar.apply_translation([dx, body_h / 2, dz])
                parts.append(bar)
        for y in [0.0, body_h / 2, body_h]:
            ring = _color(trimesh.creation.box(extents=(0.53, 0.03, 0.53)), accent)
            ring.apply_translation([0, y, 0])
            parts.append(ring)
        lid = _color(trimesh.creation.box(extents=(0.55, 0.03, 0.55)), primary)
        lid.apply_translation([0, body_h + 0.015, 0])
        parts.append(lid)

    elif idx == 3:  # minimalist: slim cylinder, slot top
        body = _color(trimesh.creation.cylinder(radius=body_r * 0.85, height=body_h, sections=20), primary)
        body.apply_translation([0, body_h / 2, 0])
        lid = _color(trimesh.creation.cylinder(radius=body_r * 0.85, height=0.05, sections=20), accent)
        lid.apply_translation([0, body_h + 0.025, 0])
        parts = [body, lid]

    elif idx == 4:  # ornate: decorative rings
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=20), primary)
        body.apply_translation([0, body_h / 2, 0])
        for ry in [0.20, 0.50, body_h - 0.05]:
            ring = _color(trimesh.creation.cylinder(radius=body_r + 0.03, height=0.04, sections=20), accent)
            ring.apply_translation([0, ry, 0])
            parts.append(ring)
        lid = _color(trimesh.creation.icosphere(subdivisions=2, radius=body_r * 0.7), accent)
        lid.apply_translation([0, body_h + 0.08, 0])
        parts.insert(0, body)

    elif idx == 5:  # retro: dome top + stripe
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=20), primary)
        body.apply_translation([0, body_h / 2, 0])
        stripe = _color(trimesh.creation.cylinder(radius=body_r + 0.01, height=0.06, sections=20), accent)
        stripe.apply_translation([0, body_h * 0.6, 0])
        lid = _color(trimesh.creation.icosphere(subdivisions=2, radius=body_r * 0.8), primary)
        lid.apply_translation([0, body_h + 0.05, 0])
        parts = [body, stripe, lid]

    elif idx == 6:  # modular: dual compartment
        for i, col in enumerate([primary, accent]):
            comp = _color(trimesh.creation.box(extents=(0.25, body_h, 0.50)), col)
            comp.apply_translation([-0.14 + i * 0.28, body_h / 2, 0])
            lid = _color(trimesh.creation.box(extents=(0.25, 0.04, 0.50)), (70, 180, 70, 255) if i else accent)
            lid.apply_translation([-0.14 + i * 0.28, body_h + 0.02, 0])
            parts.extend([comp, lid])

    elif idx == 7:  # eco: wooden slat
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=20), primary)
        body.apply_translation([0, body_h / 2, 0])
        top = _color(trimesh.creation.cylinder(radius=body_r, height=0.04, sections=20), accent)
        top.apply_translation([0, body_h + 0.02, 0])
        parts = [body, top]

    elif idx == 8:  # brutalist: concrete cube
        body = _color(trimesh.creation.box(extents=(0.55, body_h, 0.55)), primary)
        body.apply_translation([0, body_h / 2, 0])
        parts = [body]

    elif idx == 9:  # nordic: barrel
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=20), primary)
        body.apply_translation([0, body_h / 2, 0])
        lid = _color(trimesh.creation.cylinder(radius=body_r * 0.9, height=0.05, sections=20), accent)
        lid.apply_translation([0, body_h + 0.025, 0])
        handle = _color(trimesh.creation.cylinder(radius=0.015, height=0.10, sections=8), accent)
        handle.apply_translation([0, body_h + 0.10, 0])
        parts = [body, lid, handle]

    elif idx == 10:  # japan_scandi: square wooden
        body = _color(trimesh.creation.box(extents=(0.50, body_h * 0.85, 0.50)), primary)
        body.apply_translation([0, body_h * 0.85 / 2, 0])
        lid = _color(trimesh.creation.box(extents=(0.52, 0.04, 0.52)), accent)
        lid.apply_translation([0, body_h * 0.85 + 0.02, 0])
        parts = [body, lid]

    elif idx == 11:  # victorian: cast iron + ball feet
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=20), primary)
        body.apply_translation([0, body_h / 2 + 0.06, 0])
        ring = _color(trimesh.creation.cylinder(radius=body_r + 0.03, height=0.04, sections=20), accent)
        ring.apply_translation([0, body_h * 0.6, 0])
        parts = [body, ring]
        for angle in [0, 120, 240]:
            foot = _color(trimesh.creation.icosphere(subdivisions=1, radius=0.04), primary)
            foot.apply_translation([body_r * 0.8 * math.cos(math.radians(angle)), 0.0,
                                    body_r * 0.8 * math.sin(math.radians(angle))])
            parts.append(foot)

    elif idx == 12:  # contemporary: tapered oval
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=20), primary)
        body.apply_translation([0, body_h / 2, 0])
        body.apply_transform(np.diag([1.0, 1.0, 0.7, 1.0]))
        lid = _color(trimesh.creation.cylinder(radius=body_r, height=0.04, sections=20), accent)
        lid.apply_translation([0, body_h + 0.02, 0])
        lid.apply_transform(np.diag([1.0, 1.0, 0.7, 1.0]))
        parts = [body, lid]

    elif idx == 13:  # tactical: heavy + warning stripes
        body = _color(trimesh.creation.cylinder(radius=body_r + 0.03, height=body_h, sections=20), primary)
        body.apply_translation([0, body_h / 2, 0])
        stripe1 = _color(trimesh.creation.cylinder(radius=body_r + 0.04, height=0.05, sections=20), accent)
        stripe1.apply_translation([0, body_h * 0.3, 0])
        stripe2 = _color(trimesh.creation.cylinder(radius=body_r + 0.04, height=0.05, sections=20), accent)
        stripe2.apply_translation([0, body_h * 0.6, 0])
        lid = _color(trimesh.creation.cylinder(radius=body_r + 0.03, height=0.06, sections=20), primary)
        lid.apply_translation([0, body_h + 0.03, 0])
        parts = [body, stripe1, stripe2, lid]

    else:  # art_deco: hexagonal stepped
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=6), accent)
        body.apply_translation([0, body_h / 2, 0])
        step = _color(trimesh.creation.cylinder(radius=body_r * 0.8, height=0.08, sections=6), primary)
        step.apply_translation([0, body_h + 0.04, 0])
        cap = _color(trimesh.creation.cylinder(radius=body_r * 0.5, height=0.06, sections=6), accent)
        cap.apply_translation([0, body_h + 0.11, 0])
        parts = [body, step, cap]

    return _concat(parts)


# ---------------------------------------------------------------------------
# TREE generators
# ---------------------------------------------------------------------------

def _tree_detail_parts(
    idx: int,
    trunk_h: float,
    trunk_r: float,
    canopy_base: float,
    primary: tuple,
    accent: tuple,
    complexity_level: int,
) -> List[trimesh.Trimesh]:
    if complexity_level <= 0:
        return []
    parts: List[trimesh.Trimesh] = []
    rng = np.random.default_rng(1000 + idx * 31 + complexity_level * 17)

    # Layered branch scaffold: trunk + 2-3 branch levels.
    branch_levels = min(3, 1 + complexity_level)
    branch_sections = 14 + 6 * complexity_level
    for level in range(branch_levels):
        base_h = trunk_h * (0.45 + 0.18 * level)
        branch_count = 4 + complexity_level + level
        branch_len = 0.45 + 0.12 * complexity_level - 0.05 * level
        branch_pitch = 40.0 + 8.0 * level
        for b_idx in range(branch_count):
            angle = (360.0 / branch_count) * b_idx + float(rng.uniform(-12.0, 12.0))
            branch = _color(
                trimesh.creation.cylinder(
                    radius=max(0.035, trunk_r * (0.55 - 0.10 * level)),
                    height=max(0.20, branch_len),
                    sections=branch_sections,
                ),
                accent,
            )
            branch.apply_translation([branch_len * 0.5, base_h, 0.0])
            branch.apply_transform(
                trimesh.transformations.rotation_matrix(math.radians(branch_pitch), [0, 0, 1])
            )
            branch.apply_transform(
                trimesh.transformations.rotation_matrix(math.radians(angle), [0, 1, 0])
            )
            parts.append(branch)

    # Multi-cluster foliage blobs to avoid single primitive canopies.
    blob_count = 6 + complexity_level * 3 + max(0, complexity_level - 1) * 2
    blob_subdiv = 2 + (1 if complexity_level >= 2 else 0) + (1 if complexity_level >= 3 else 0)
    for _ in range(blob_count):
        radius = float(rng.uniform(0.22, 0.50 + 0.08 * complexity_level))
        theta = float(rng.uniform(0.0, math.tau))
        radial = float(rng.uniform(0.1, 0.95))
        x = radial * math.cos(theta)
        z = radial * math.sin(theta)
        y = canopy_base + float(rng.uniform(0.45, 1.85 + 0.10 * complexity_level))
        blob = _color(
            trimesh.creation.icosphere(
                subdivisions=blob_subdiv,
                radius=radius,
            ),
            primary,
        )
        blob.apply_translation([x, y, z])
        parts.append(blob)
    return parts


def _tree_variant(
    idx: int,
    style: str,
    primary: tuple,
    accent: tuple,
    complexity_level: int = 0,
) -> trimesh.Trimesh:
    parts: List[trimesh.Trimesh] = []
    trunk_h = 3.0
    trunk_r = 0.18
    trunk_color = (101, 67, 33, 255)
    canopy_base = trunk_h

    if idx == 0:  # modern: sphere canopy
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r, height=trunk_h, sections=16), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        canopy = _color(trimesh.creation.icosphere(subdivisions=3, radius=1.10), primary)
        canopy.apply_translation([0, canopy_base + 1.10, 0])
        parts = [trunk, canopy]

    elif idx == 1:  # classic: big round crown
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r + 0.05, height=trunk_h, sections=16), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        canopy = _color(trimesh.creation.icosphere(subdivisions=3, radius=1.30), primary)
        canopy.apply_translation([0, canopy_base + 1.20, 0])
        parts = [trunk, canopy]

    elif idx == 2:  # industrial: compact ball
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r + 0.03, height=trunk_h + 0.3, sections=16), trunk_color)
        trunk.apply_translation([0, (trunk_h + 0.3) / 2, 0])
        canopy = _color(trimesh.creation.icosphere(subdivisions=2, radius=0.90), primary)
        canopy.apply_translation([0, trunk_h + 0.3 + 0.90, 0])
        parts = [trunk, canopy]

    elif idx == 3:  # minimalist: thin trunk + small sphere
        trunk = _color(trimesh.creation.cylinder(radius=0.08, height=trunk_h + 0.5, sections=12), trunk_color)
        trunk.apply_translation([0, (trunk_h + 0.5) / 2, 0])
        canopy = _color(trimesh.creation.icosphere(subdivisions=2, radius=0.80), primary)
        canopy.apply_translation([0, trunk_h + 0.5 + 0.80, 0])
        parts = [trunk, canopy]

    elif idx == 4:  # ornate: triple sphere cluster
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r, height=trunk_h, sections=16), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        parts = [trunk]
        for dx, dz, r in [(0, 0, 1.0), (-0.5, 0.3, 0.7), (0.4, -0.4, 0.7)]:
            blob = _color(trimesh.creation.icosphere(subdivisions=2, radius=r), primary)
            blob.apply_translation([dx, canopy_base + r + 0.2, dz])
            parts.append(blob)

    elif idx == 5:  # retro: flat disc canopy
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r, height=trunk_h, sections=16), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        canopy = _color(trimesh.creation.cylinder(radius=1.20, height=0.40, sections=20), primary)
        canopy.apply_translation([0, canopy_base + 0.20, 0])
        parts = [trunk, canopy]

    elif idx == 6:  # modular: stacked discs
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r, height=trunk_h, sections=16), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        parts = [trunk]
        for li in range(3):
            r = 1.10 - li * 0.25
            disc = _color(trimesh.creation.cylinder(radius=r, height=0.30, sections=20), primary if li % 2 == 0 else accent)
            disc.apply_translation([0, canopy_base + 0.15 + li * 0.50, 0])
            parts.append(disc)

    elif idx == 7:  # eco: multi-blob organic
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r, height=trunk_h, sections=16), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        parts = [trunk]
        for dx, dz, r in [(0, 0, 0.9), (-0.4, 0.4, 0.6), (0.5, 0.2, 0.55), (-0.2, -0.5, 0.5)]:
            blob = _color(trimesh.creation.icosphere(subdivisions=2, radius=r), primary)
            blob.apply_translation([dx, canopy_base + r + 0.1, dz])
            parts.append(blob)

    elif idx == 8:  # brutalist: box canopy
        trunk = _color(trimesh.creation.box(extents=(0.30, trunk_h, 0.30)), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        canopy = _color(trimesh.creation.box(extents=(2.00, 1.50, 2.00)), primary)
        canopy.apply_translation([0, canopy_base + 0.75, 0])
        parts = [trunk, canopy]

    elif idx == 9:  # nordic: cone (pine)
        trunk = _color(trimesh.creation.cylinder(radius=0.12, height=trunk_h * 0.6, sections=12), trunk_color)
        trunk.apply_translation([0, trunk_h * 0.3, 0])
        canopy = _color(trimesh.creation.cone(radius=1.00, height=3.00, sections=16), primary)
        canopy.apply_translation([0, trunk_h * 0.6 + 1.50, 0])
        parts = [trunk, canopy]

    elif idx == 10:  # japan_scandi: wide flat
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r, height=trunk_h, sections=16), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        canopy = _color(trimesh.creation.cylinder(radius=1.30, height=0.25, sections=20), (200, 130, 150, 255))
        canopy.apply_translation([0, canopy_base + 0.12, 0])
        parts = [trunk, canopy]

    elif idx == 11:  # victorian: tall oval
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r, height=trunk_h, sections=16), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        canopy = _color(trimesh.creation.icosphere(subdivisions=3, radius=1.00), primary)
        canopy.apply_translation([0, canopy_base + 1.30, 0])
        canopy.apply_transform(np.diag([0.8, 1.3, 0.8, 1.0]))
        parts = [trunk, canopy]

    elif idx == 12:  # contemporary: asymmetric
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r, height=trunk_h, sections=16), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        rot = trimesh.transformations.rotation_matrix(math.radians(8), [0, 0, 1])
        trunk.apply_transform(rot)
        canopy = _color(trimesh.creation.icosphere(subdivisions=2, radius=1.10), primary)
        canopy.apply_translation([0.3, canopy_base + 1.00, 0])
        parts = [trunk, canopy]

    elif idx == 13:  # tactical: rectangular hedge
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r, height=trunk_h, sections=16), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        canopy = _color(trimesh.creation.box(extents=(1.80, 1.60, 1.80)), primary)
        canopy.apply_translation([0, canopy_base + 0.80, 0])
        parts = [trunk, canopy]

    else:  # art_deco: tiered cones
        trunk = _color(trimesh.creation.cylinder(radius=trunk_r, height=trunk_h, sections=16), trunk_color)
        trunk.apply_translation([0, trunk_h / 2, 0])
        parts = [trunk]
        for ti in range(3):
            r = 1.10 - ti * 0.30
            h = 0.70 - ti * 0.10
            tier = _color(trimesh.creation.cone(radius=r, height=h, sections=16), primary if ti % 2 == 0 else accent)
            tier.apply_translation([0, canopy_base + ti * 0.60 + h / 2, 0])
            parts.append(tier)

    parts.extend(
        _tree_detail_parts(
            idx=idx,
            trunk_h=trunk_h,
            trunk_r=trunk_r,
            canopy_base=canopy_base,
            primary=primary,
            accent=accent,
            complexity_level=complexity_level,
        )
    )
    return _concat(parts)


# ---------------------------------------------------------------------------
# BUS STOP generators
# ---------------------------------------------------------------------------

def _bus_stop_variant(
    idx: int,
    style: str,
    primary: tuple,
    accent: tuple,
    complexity_level: int = 0,
) -> trimesh.Trimesh:
    parts: List[trimesh.Trimesh] = []
    W, H, D = 3.20, 2.60, 1.30

    def _posts(xs, zs, h, r, col):
        for x in xs:
            for z in zs:
                p = _color(trimesh.creation.cylinder(radius=r, height=h, sections=12), col)
                p.apply_translation([x, h / 2, z])
                parts.append(p)

    if idx == 0:  # modern: flat roof + glass
        _posts([-W / 2 + 0.10, W / 2 - 0.10], [-D / 2 + 0.10, D / 2 - 0.10], H, 0.04, accent)
        roof = _color(trimesh.creation.box(extents=(W, 0.06, D)), primary)
        roof.apply_translation([0, H + 0.03, 0])
        panel = _color(trimesh.creation.box(extents=(W, H * 0.8, 0.03)), (180, 200, 220, 128))
        panel.apply_translation([0, H * 0.4, -D / 2 + 0.015])
        parts.extend([roof, panel])

    elif idx == 1:  # classic: pitched roof + brick
        _posts([-W / 2 + 0.15, W / 2 - 0.15], [-D / 2 + 0.15], H, 0.08, accent)
        roof = _color(trimesh.creation.box(extents=(W + 0.20, 0.08, D + 0.20)), primary)
        roof.apply_translation([0, H + 0.04, 0])
        wall = _color(trimesh.creation.box(extents=(W, H * 0.9, 0.08)), accent)
        wall.apply_translation([0, H * 0.45, -D / 2 + 0.04])
        parts.extend([roof, wall])

    elif idx == 2:  # industrial: corrugated
        _posts([-W / 2 + 0.10, W / 2 - 0.10], [0], H, 0.06, primary)
        roof = _color(trimesh.creation.box(extents=(W + 0.10, 0.06, D + 0.10)), accent)
        roof.apply_translation([0, H + 0.03, 0])
        parts.append(roof)

    elif idx == 3:  # minimalist: floating roof, 2 poles
        for x in [-W / 2 + 0.10, W / 2 - 0.10]:
            p = _color(trimesh.creation.cylinder(radius=0.03, height=H, sections=12), primary)
            p.apply_translation([x, H / 2, 0])
            parts.append(p)
        roof = _color(trimesh.creation.box(extents=(W, 0.04, D * 0.8)), accent)
        roof.apply_translation([0, H + 0.02, 0])
        parts.append(roof)

    elif idx == 4:  # ornate: arched roof
        _posts([-W / 2 + 0.10, W / 2 - 0.10], [-D / 2 + 0.10, D / 2 - 0.10], H, 0.06, accent)
        roof = _color(trimesh.creation.cylinder(radius=D / 2, height=W, sections=16), primary)
        roof.apply_translation([0, H + D / 4, 0])
        rot = trimesh.transformations.rotation_matrix(math.radians(90), [0, 0, 1])
        roof.apply_transform(rot)
        parts.append(roof)

    elif idx == 5:  # retro: curved panel
        _posts([-W / 2 + 0.10, W / 2 - 0.10], [-D / 2 + 0.10], H, 0.05, primary)
        roof = _color(trimesh.creation.box(extents=(W, 0.06, D)), accent)
        roof.apply_translation([0, H + 0.03, 0])
        panel = _color(trimesh.creation.box(extents=(W, H * 0.7, 0.04)), primary)
        panel.apply_translation([0, H * 0.35, -D / 2 + 0.12])
        parts.extend([roof, panel])

    elif idx == 6:  # modular: bolt joints
        _posts([-W / 2 + 0.10, 0, W / 2 - 0.10], [-D / 2 + 0.10], H, 0.04, primary)
        roof = _color(trimesh.creation.box(extents=(W, 0.05, D)), accent)
        roof.apply_translation([0, H + 0.025, 0])
        for x in [-W / 2 + 0.10, 0, W / 2 - 0.10]:
            bolt = _color(trimesh.creation.cylinder(radius=0.06, height=0.04, sections=12), (200, 200, 0, 255))
            bolt.apply_translation([x, H, -D / 2 + 0.10])
            parts.append(bolt)
        parts.append(roof)

    elif idx == 7:  # eco: wood + green roof
        _posts([-W / 2 + 0.10, W / 2 - 0.10], [-D / 2 + 0.10, D / 2 - 0.10], H, 0.06, accent)
        roof = _color(trimesh.creation.box(extents=(W, 0.08, D)), (60, 140, 60, 255))
        roof.apply_translation([0, H + 0.04, 0])
        wall = _color(trimesh.creation.box(extents=(W, H * 0.6, 0.06)), accent)
        wall.apply_translation([0, H * 0.3, -D / 2 + 0.03])
        parts.extend([roof, wall])

    elif idx == 8:  # brutalist: concrete slab
        wall = _color(trimesh.creation.box(extents=(W, H, 0.15)), primary)
        wall.apply_translation([0, H / 2, -D / 2 + 0.075])
        roof = _color(trimesh.creation.box(extents=(W + 0.10, 0.12, D)), primary)
        roof.apply_translation([0, H + 0.06, 0])
        parts = [wall, roof]

    elif idx == 9:  # nordic: timber gable
        _posts([-W / 2 + 0.10, W / 2 - 0.10], [-D / 2 + 0.10, D / 2 - 0.10], H, 0.05, primary)
        roof = _color(trimesh.creation.box(extents=(W + 0.10, 0.06, D + 0.10)), accent)
        roof.apply_translation([0, H + 0.03, 0])
        gable = _color(trimesh.creation.box(extents=(W + 0.10, 0.20, 0.06)), primary)
        gable.apply_translation([0, H + 0.16, 0])
        parts.extend([roof, gable])

    elif idx == 10:  # japan_scandi: lattice screen
        _posts([-W / 2 + 0.10, W / 2 - 0.10], [-D / 2 + 0.10], H, 0.04, accent)
        roof = _color(trimesh.creation.box(extents=(W + 0.10, 0.05, D)), primary)
        roof.apply_translation([0, H + 0.025, 0])
        for gi in range(6):
            bar = _color(trimesh.creation.box(extents=(0.03, H * 0.7, 0.03)), accent)
            bar.apply_translation([-W / 2 + 0.30 + gi * 0.50, H * 0.35, -D / 2 + 0.10])
            parts.append(bar)
        parts.append(roof)

    elif idx == 11:  # victorian: spire roof
        _posts([-W / 2 + 0.10, W / 2 - 0.10], [-D / 2 + 0.10, D / 2 - 0.10], H, 0.07, primary)
        roof = _color(trimesh.creation.box(extents=(W, 0.06, D)), accent)
        roof.apply_translation([0, H + 0.03, 0])
        spire = _color(trimesh.creation.cone(radius=0.15, height=0.40, sections=12), primary)
        spire.apply_translation([0, H + 0.26, 0])
        parts.extend([roof, spire])

    elif idx == 12:  # contemporary: curved roof
        _posts([-W / 2 + 0.10, W / 2 - 0.10], [0], H, 0.04, primary)
        roof = _color(trimesh.creation.cylinder(radius=D * 0.6, height=W, sections=16), accent)
        roof.apply_translation([0, H + D * 0.15, 0])
        rot = trimesh.transformations.rotation_matrix(math.radians(90), [0, 0, 1])
        roof.apply_transform(rot)
        parts.append(roof)

    elif idx == 13:  # tactical: heavy panels
        wall = _color(trimesh.creation.box(extents=(W, H, 0.10)), primary)
        wall.apply_translation([0, H / 2, -D / 2 + 0.05])
        side_l = _color(trimesh.creation.box(extents=(0.10, H, D)), accent)
        side_l.apply_translation([-W / 2 + 0.05, H / 2, 0])
        roof = _color(trimesh.creation.box(extents=(W + 0.10, 0.10, D + 0.10)), primary)
        roof.apply_translation([0, H + 0.05, 0])
        parts = [wall, side_l, roof]

    else:  # art_deco: stepped roof
        _posts([-W / 2 + 0.10, W / 2 - 0.10], [-D / 2 + 0.10, D / 2 - 0.10], H, 0.06, accent)
        for si in range(3):
            w = W - si * 0.40
            step = _color(trimesh.creation.box(extents=(w, 0.06, D)), primary if si % 2 == 0 else accent)
            step.apply_translation([0, H + 0.03 + si * 0.08, 0])
            parts.append(step)

    return _concat(parts)


# ---------------------------------------------------------------------------
# MAILBOX generators
# ---------------------------------------------------------------------------

def _mailbox_variant(
    idx: int,
    style: str,
    primary: tuple,
    accent: tuple,
    complexity_level: int = 0,
) -> trimesh.Trimesh:
    parts: List[trimesh.Trimesh] = []
    post_h = 0.80
    box_h = 0.45

    if idx == 0:  # modern: rect box on post
        post = _color(trimesh.creation.cylinder(radius=0.04, height=post_h, sections=12), accent)
        post.apply_translation([0, post_h / 2, 0])
        body = _color(trimesh.creation.box(extents=(0.40, box_h, 0.35)), primary)
        body.apply_translation([0, post_h + box_h / 2, 0])
        parts = [post, body]

    elif idx == 1:  # classic: British pillar box
        body = _color(trimesh.creation.cylinder(radius=0.22, height=1.10, sections=20), (200, 30, 30, 255))
        body.apply_translation([0, 0.55, 0])
        dome = _color(trimesh.creation.icosphere(subdivisions=2, radius=0.22), (200, 30, 30, 255))
        dome.apply_translation([0, 1.15, 0])
        parts = [body, dome]

    elif idx == 2:  # industrial: riveted box
        post = _color(trimesh.creation.box(extents=(0.08, post_h, 0.08)), primary)
        post.apply_translation([0, post_h / 2, 0])
        body = _color(trimesh.creation.box(extents=(0.45, box_h, 0.35)), accent)
        body.apply_translation([0, post_h + box_h / 2, 0])
        parts = [post, body]

    elif idx == 3:  # minimalist: slim column
        body = _color(trimesh.creation.cylinder(radius=0.15, height=1.20, sections=16), primary)
        body.apply_translation([0, 0.60, 0])
        parts = [body]

    elif idx == 4:  # ornate: eagle finial
        post = _color(trimesh.creation.cylinder(radius=0.05, height=post_h, sections=12), accent)
        post.apply_translation([0, post_h / 2, 0])
        body = _color(trimesh.creation.box(extents=(0.40, box_h, 0.35)), primary)
        body.apply_translation([0, post_h + box_h / 2, 0])
        finial = _color(trimesh.creation.cone(radius=0.08, height=0.12, sections=12), accent)
        finial.apply_translation([0, post_h + box_h + 0.06, 0])
        parts = [post, body, finial]

    elif idx == 5:  # retro: bulbous
        body = _color(trimesh.creation.cylinder(radius=0.25, height=0.80, sections=20), primary)
        body.apply_translation([0, 0.40, 0])
        dome = _color(trimesh.creation.icosphere(subdivisions=2, radius=0.25), accent)
        dome.apply_translation([0, 0.85, 0])
        parts = [body, dome]

    elif idx == 6:  # modular: stacked compartments
        post = _color(trimesh.creation.cylinder(radius=0.04, height=0.40, sections=12), accent)
        post.apply_translation([0, 0.20, 0])
        parts = [post]
        for ci in range(3):
            comp = _color(trimesh.creation.box(extents=(0.40, 0.25, 0.30)), primary if ci % 2 == 0 else accent)
            comp.apply_translation([0, 0.40 + 0.125 + ci * 0.27, 0])
            parts.append(comp)

    elif idx == 7:  # eco: wooden
        post = _color(trimesh.creation.cylinder(radius=0.05, height=post_h, sections=12), accent)
        post.apply_translation([0, post_h / 2, 0])
        body = _color(trimesh.creation.box(extents=(0.38, box_h, 0.32)), primary)
        body.apply_translation([0, post_h + box_h / 2, 0])
        parts = [post, body]

    elif idx == 8:  # brutalist: concrete block
        body = _color(trimesh.creation.box(extents=(0.45, 1.10, 0.40)), primary)
        body.apply_translation([0, 0.55, 0])
        parts = [body]

    elif idx == 9:  # nordic: house-shaped
        post = _color(trimesh.creation.cylinder(radius=0.04, height=post_h, sections=12), accent)
        post.apply_translation([0, post_h / 2, 0])
        body = _color(trimesh.creation.box(extents=(0.35, box_h * 0.8, 0.30)), primary)
        body.apply_translation([0, post_h + box_h * 0.4, 0])
        peak = _color(trimesh.creation.cone(radius=0.22, height=0.15, sections=4), accent)
        peak.apply_translation([0, post_h + box_h * 0.8 + 0.075, 0])
        parts = [post, body, peak]

    elif idx == 10:  # japan_scandi: simple box
        post = _color(trimesh.creation.cylinder(radius=0.04, height=post_h, sections=12), accent)
        post.apply_translation([0, post_h / 2, 0])
        body = _color(trimesh.creation.box(extents=(0.35, box_h * 0.7, 0.30)), primary)
        body.apply_translation([0, post_h + box_h * 0.35, 0])
        parts = [post, body]

    elif idx == 11:  # victorian: fluted column + crown
        body = _color(trimesh.creation.cylinder(radius=0.18, height=1.10, sections=16), primary)
        body.apply_translation([0, 0.55, 0])
        crown = _color(trimesh.creation.cone(radius=0.22, height=0.15, sections=16), accent)
        crown.apply_translation([0, 1.18, 0])
        parts = [body, crown]

    elif idx == 12:  # contemporary: wedge
        post = _color(trimesh.creation.cylinder(radius=0.04, height=post_h, sections=12), accent)
        post.apply_translation([0, post_h / 2, 0])
        body = _color(trimesh.creation.box(extents=(0.40, box_h, 0.35)), primary)
        body.apply_translation([0, post_h + box_h / 2, 0])
        rot = trimesh.transformations.rotation_matrix(math.radians(8), [0, 0, 1])
        body.apply_transform(rot)
        parts = [post, body]

    elif idx == 13:  # tactical: armored
        post = _color(trimesh.creation.cylinder(radius=0.06, height=post_h, sections=12), primary)
        post.apply_translation([0, post_h / 2, 0])
        body = _color(trimesh.creation.box(extents=(0.45, box_h, 0.38)), accent)
        body.apply_translation([0, post_h + box_h / 2, 0])
        parts = [post, body]

    else:  # art_deco: fan top
        post = _color(trimesh.creation.cylinder(radius=0.05, height=post_h, sections=12), accent)
        post.apply_translation([0, post_h / 2, 0])
        body = _color(trimesh.creation.box(extents=(0.40, box_h, 0.35)), accent)
        body.apply_translation([0, post_h + box_h / 2, 0])
        fan = _color(trimesh.creation.cone(radius=0.25, height=0.12, sections=8), primary)
        fan.apply_translation([0, post_h + box_h + 0.06, 0])
        parts = [post, body, fan]

    return _concat(parts)


# ---------------------------------------------------------------------------
# HYDRANT generators
# ---------------------------------------------------------------------------

def _hydrant_variant(
    idx: int,
    style: str,
    primary: tuple,
    accent: tuple,
    complexity_level: int = 0,
) -> trimesh.Trimesh:
    parts: List[trimesh.Trimesh] = []
    body_h = 0.65
    body_r = 0.12

    def _valves(n, h_frac, r, col):
        for i in range(n):
            angle = i * (360 / n)
            v = _color(trimesh.creation.cylinder(radius=r, height=0.12, sections=12), col)
            v.apply_translation([0, 0, 0])
            rot = trimesh.transformations.rotation_matrix(math.radians(90), [0, 0, 1])
            v.apply_transform(rot)
            dx = (body_r + 0.06) * math.cos(math.radians(angle))
            dz = (body_r + 0.06) * math.sin(math.radians(angle))
            v.apply_translation([dx, body_h * h_frac, dz])
            parts.append(v)

    if idx == 0:  # modern: smooth + 2 valves
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=16), primary)
        body.apply_translation([0, body_h / 2, 0])
        cap = _color(trimesh.creation.cylinder(radius=body_r + 0.02, height=0.04, sections=16), accent)
        cap.apply_translation([0, body_h + 0.02, 0])
        parts = [body, cap]
        _valves(2, 0.55, 0.04, accent)

    elif idx == 1:  # classic: barrel + dome + 3 valves
        body = _color(trimesh.creation.cylinder(radius=body_r + 0.02, height=body_h, sections=16), (200, 30, 30, 255))
        body.apply_translation([0, body_h / 2, 0])
        dome = _color(trimesh.creation.icosphere(subdivisions=2, radius=body_r + 0.02), (220, 200, 0, 255))
        dome.apply_translation([0, body_h + 0.04, 0])
        parts = [body, dome]
        _valves(3, 0.50, 0.045, (200, 30, 30, 255))

    elif idx == 2:  # industrial: square body
        body = _color(trimesh.creation.box(extents=(0.28, body_h, 0.28)), primary)
        body.apply_translation([0, body_h / 2, 0])
        cap = _color(trimesh.creation.box(extents=(0.30, 0.04, 0.30)), accent)
        cap.apply_translation([0, body_h + 0.02, 0])
        parts = [body, cap]
        v = _color(trimesh.creation.box(extents=(0.10, 0.10, 0.10)), accent)
        v.apply_translation([0.20, body_h * 0.5, 0])
        parts.append(v)

    elif idx == 3:  # minimalist: slim pillar
        body = _color(trimesh.creation.cylinder(radius=body_r * 0.8, height=body_h, sections=16), primary)
        body.apply_translation([0, body_h / 2, 0])
        cap = _color(trimesh.creation.cylinder(radius=body_r * 0.8, height=0.03, sections=16), accent)
        cap.apply_translation([0, body_h + 0.015, 0])
        parts = [body, cap]

    elif idx == 4:  # ornate: embossed rings
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=16), primary)
        body.apply_translation([0, body_h / 2, 0])
        for ry in [0.15, 0.35, 0.55]:
            ring = _color(trimesh.creation.cylinder(radius=body_r + 0.03, height=0.03, sections=16), accent)
            ring.apply_translation([0, ry, 0])
            parts.append(ring)
        cap = _color(trimesh.creation.icosphere(subdivisions=2, radius=body_r), accent)
        cap.apply_translation([0, body_h + 0.04, 0])
        parts.insert(0, body)

    elif idx == 5:  # retro: squat wide
        body = _color(trimesh.creation.cylinder(radius=body_r + 0.04, height=body_h * 0.8, sections=16), primary)
        body.apply_translation([0, body_h * 0.4, 0])
        cap = _color(trimesh.creation.icosphere(subdivisions=2, radius=body_r + 0.04), accent)
        cap.apply_translation([0, body_h * 0.8 + 0.04, 0])
        parts = [body, cap]
        _valves(2, 0.45, 0.05, primary)

    elif idx == 6:  # modular: sectioned
        parts = []
        seg_h = body_h / 3
        for si in range(3):
            seg = _color(trimesh.creation.cylinder(radius=body_r, height=seg_h - 0.02, sections=16), primary if si % 2 == 0 else accent)
            seg.apply_translation([0, si * seg_h + (seg_h - 0.02) / 2 + 0.01, 0])
            parts.append(seg)
        cap = _color(trimesh.creation.cylinder(radius=body_r + 0.01, height=0.04, sections=16), accent)
        cap.apply_translation([0, body_h + 0.02, 0])
        parts.append(cap)

    elif idx == 7:  # eco: green band
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=16), primary)
        body.apply_translation([0, body_h / 2, 0])
        band = _color(trimesh.creation.cylinder(radius=body_r + 0.02, height=0.06, sections=16), (40, 160, 40, 255))
        band.apply_translation([0, body_h * 0.6, 0])
        cap = _color(trimesh.creation.cylinder(radius=body_r, height=0.04, sections=16), accent)
        cap.apply_translation([0, body_h + 0.02, 0])
        parts = [body, band, cap]

    elif idx == 8:  # brutalist: blocky square
        body = _color(trimesh.creation.box(extents=(0.25, body_h, 0.25)), primary)
        body.apply_translation([0, body_h / 2, 0])
        cap = _color(trimesh.creation.box(extents=(0.27, 0.04, 0.27)), primary)
        cap.apply_translation([0, body_h + 0.02, 0])
        parts = [body, cap]

    elif idx == 9:  # nordic: slender tapered
        body = _color(trimesh.creation.cylinder(radius=body_r * 0.85, height=body_h, sections=16), primary)
        body.apply_translation([0, body_h / 2, 0])
        cap = _color(trimesh.creation.cylinder(radius=body_r * 0.7, height=0.04, sections=16), accent)
        cap.apply_translation([0, body_h + 0.02, 0])
        parts = [body, cap]

    elif idx == 10:  # japan_scandi: low compact
        body = _color(trimesh.creation.cylinder(radius=body_r + 0.02, height=body_h * 0.8, sections=16), primary)
        body.apply_translation([0, body_h * 0.4, 0])
        cap = _color(trimesh.creation.icosphere(subdivisions=2, radius=body_r), accent)
        cap.apply_translation([0, body_h * 0.8 + 0.04, 0])
        parts = [body, cap]

    elif idx == 11:  # victorian: fluted dome
        body = _color(trimesh.creation.cylinder(radius=body_r, height=body_h, sections=16), primary)
        body.apply_translation([0, body_h / 2, 0])
        dome = _color(trimesh.creation.icosphere(subdivisions=2, radius=body_r + 0.01), accent)
        dome.apply_translation([0, body_h + 0.05, 0])
        ring = _color(trimesh.creation.cylinder(radius=body_r + 0.04, height=0.03, sections=16), primary)
        ring.apply_translation([0, body_h * 0.3, 0])
        parts = [body, dome, ring]
        _valves(2, 0.50, 0.04, primary)

    elif idx == 12:  # contemporary: conical
        body = _color(trimesh.creation.cone(radius=body_r + 0.02, height=body_h, sections=16), primary)
        body.apply_translation([0, body_h / 2, 0])
        cap = _color(trimesh.creation.cylinder(radius=body_r * 0.6, height=0.05, sections=16), accent)
        cap.apply_translation([0, body_h + 0.025, 0])
        parts = [body, cap]

    elif idx == 13:  # tactical: armored + reflective
        body = _color(trimesh.creation.cylinder(radius=body_r + 0.03, height=body_h, sections=16), primary)
        body.apply_translation([0, body_h / 2, 0])
        band1 = _color(trimesh.creation.cylinder(radius=body_r + 0.05, height=0.04, sections=16), accent)
        band1.apply_translation([0, body_h * 0.3, 0])
        band2 = _color(trimesh.creation.cylinder(radius=body_r + 0.05, height=0.04, sections=16), accent)
        band2.apply_translation([0, body_h * 0.6, 0])
        cap = _color(trimesh.creation.cylinder(radius=body_r + 0.03, height=0.05, sections=16), primary)
        cap.apply_translation([0, body_h + 0.025, 0])
        parts = [body, band1, band2, cap]

    else:  # art_deco: tiered rings
        parts = []
        for ri in range(4):
            r = body_r + 0.04 - ri * 0.01
            h = body_h / 4
            tier = _color(trimesh.creation.cylinder(radius=r, height=h - 0.02, sections=16), primary if ri % 2 == 0 else accent)
            tier.apply_translation([0, ri * h + (h - 0.02) / 2 + 0.01, 0])
            parts.append(tier)
        cap = _color(trimesh.creation.cone(radius=body_r, height=0.08, sections=8), accent)
        cap.apply_translation([0, body_h + 0.04, 0])
        parts.append(cap)

    return _concat(parts)


# ---------------------------------------------------------------------------
# BOLLARD generators
# ---------------------------------------------------------------------------

def _bollard_variant(
    idx: int,
    style: str,
    primary: tuple,
    accent: tuple,
    complexity_level: int = 0,
) -> trimesh.Trimesh:
    parts: List[trimesh.Trimesh] = []
    h = 0.90
    r = 0.08

    if idx == 0:  # modern: cylinder + dome
        body = _color(trimesh.creation.cylinder(radius=r, height=h, sections=16), primary)
        body.apply_translation([0, h / 2, 0])
        cap = _color(trimesh.creation.icosphere(subdivisions=2, radius=r), accent)
        cap.apply_translation([0, h, 0])
        base = _color(trimesh.creation.cylinder(radius=r + 0.03, height=0.04, sections=16), accent)
        base.apply_translation([0, 0.02, 0])
        parts = [body, cap, base]

    elif idx == 1:  # classic: mushroom cap + ring
        body = _color(trimesh.creation.cylinder(radius=r, height=h, sections=16), primary)
        body.apply_translation([0, h / 2, 0])
        cap = _color(trimesh.creation.cylinder(radius=r + 0.04, height=0.05, sections=16), accent)
        cap.apply_translation([0, h + 0.025, 0])
        ring = _color(trimesh.creation.cylinder(radius=r + 0.02, height=0.03, sections=16), accent)
        ring.apply_translation([0, h * 0.75, 0])
        parts = [body, cap, ring]

    elif idx == 2:  # industrial: square
        body = _color(trimesh.creation.box(extents=(0.16, h, 0.16)), primary)
        body.apply_translation([0, h / 2, 0])
        cap = _color(trimesh.creation.box(extents=(0.18, 0.03, 0.18)), accent)
        cap.apply_translation([0, h + 0.015, 0])
        parts = [body, cap]

    elif idx == 3:  # minimalist: slim flat top
        body = _color(trimesh.creation.cylinder(radius=r * 0.75, height=h, sections=12), primary)
        body.apply_translation([0, h / 2, 0])
        parts = [body]

    elif idx == 4:  # ornate: spiral acorn
        body = _color(trimesh.creation.cylinder(radius=r, height=h, sections=16), primary)
        body.apply_translation([0, h / 2, 0])
        acorn = _color(trimesh.creation.icosphere(subdivisions=2, radius=r + 0.02), accent)
        acorn.apply_translation([0, h + 0.05, 0])
        ring1 = _color(trimesh.creation.cylinder(radius=r + 0.03, height=0.03, sections=16), accent)
        ring1.apply_translation([0, h * 0.3, 0])
        ring2 = _color(trimesh.creation.cylinder(radius=r + 0.03, height=0.03, sections=16), accent)
        ring2.apply_translation([0, h * 0.6, 0])
        parts = [body, acorn, ring1, ring2]

    elif idx == 5:  # retro: cannon shape (wider middle)
        body = _color(trimesh.creation.cylinder(radius=r, height=h, sections=16), primary)
        body.apply_translation([0, h / 2, 0])
        belly = _color(trimesh.creation.cylinder(radius=r + 0.04, height=h * 0.4, sections=16), accent)
        belly.apply_translation([0, h * 0.45, 0])
        cap = _color(trimesh.creation.icosphere(subdivisions=2, radius=r), primary)
        cap.apply_translation([0, h, 0])
        parts = [body, belly, cap]

    elif idx == 6:  # modular: banded stripes
        parts = []
        seg_h = h / 5
        for si in range(5):
            col = accent if si % 2 else primary
            seg = _color(trimesh.creation.cylinder(radius=r, height=seg_h - 0.01, sections=16), col)
            seg.apply_translation([0, si * seg_h + (seg_h - 0.01) / 2, 0])
            parts.append(seg)
        cap = _color(trimesh.creation.cylinder(radius=r + 0.01, height=0.03, sections=16), accent)
        cap.apply_translation([0, h + 0.015, 0])
        parts.append(cap)

    elif idx == 7:  # eco: timber
        body = _color(trimesh.creation.cylinder(radius=r + 0.02, height=h, sections=12), primary)
        body.apply_translation([0, h / 2, 0])
        cap = _color(trimesh.creation.icosphere(subdivisions=1, radius=r + 0.02), accent)
        cap.apply_translation([0, h, 0])
        parts = [body, cap]

    elif idx == 8:  # brutalist: concrete square
        body = _color(trimesh.creation.box(extents=(0.18, h, 0.18)), primary)
        body.apply_translation([0, h / 2, 0])
        parts = [body]

    elif idx == 9:  # nordic: tapered + dome
        body = _color(trimesh.creation.cylinder(radius=r * 0.9, height=h, sections=12), primary)
        body.apply_translation([0, h / 2, 0])
        cap = _color(trimesh.creation.icosphere(subdivisions=2, radius=r * 0.7), accent)
        cap.apply_translation([0, h, 0])
        parts = [body, cap]

    elif idx == 10:  # japan_scandi: pebble rounded
        body = _color(trimesh.creation.icosphere(subdivisions=3, radius=0.10), primary)
        body.apply_translation([0, 0.45, 0])
        body.apply_transform(np.diag([1.0, h / 0.20, 1.0, 1.0]))
        parts = [body]

    elif idx == 11:  # victorian: urn fluted
        body = _color(trimesh.creation.cylinder(radius=r, height=h, sections=16), primary)
        body.apply_translation([0, h / 2, 0])
        belly = _color(trimesh.creation.cylinder(radius=r + 0.03, height=0.15, sections=16), accent)
        belly.apply_translation([0, h * 0.35, 0])
        cap = _color(trimesh.creation.cone(radius=r + 0.02, height=0.10, sections=12), accent)
        cap.apply_translation([0, h + 0.05, 0])
        parts = [body, belly, cap]

    elif idx == 12:  # contemporary: tapered + LED ring
        body = _color(trimesh.creation.cylinder(radius=r, height=h, sections=16), primary)
        body.apply_translation([0, h / 2, 0])
        ring = _color(trimesh.creation.cylinder(radius=r + 0.03, height=0.02, sections=16), (100, 200, 255, 255))
        ring.apply_translation([0, h * 0.85, 0])
        parts = [body, ring]

    elif idx == 13:  # tactical: crash-rated thick
        body = _color(trimesh.creation.cylinder(radius=r + 0.04, height=h, sections=16), primary)
        body.apply_translation([0, h / 2, 0])
        band = _color(trimesh.creation.cylinder(radius=r + 0.06, height=0.06, sections=16), (220, 30, 30, 255))
        band.apply_translation([0, h * 0.7, 0])
        cap = _color(trimesh.creation.cylinder(radius=r + 0.04, height=0.04, sections=16), accent)
        cap.apply_translation([0, h + 0.02, 0])
        parts = [body, band, cap]

    else:  # art_deco: octagonal faceted
        body = _color(trimesh.creation.cylinder(radius=r + 0.01, height=h, sections=8), accent)
        body.apply_translation([0, h / 2, 0])
        cap = _color(trimesh.creation.cone(radius=r + 0.01, height=0.10, sections=8), primary)
        cap.apply_translation([0, h + 0.05, 0])
        ring = _color(trimesh.creation.cylinder(radius=r + 0.04, height=0.03, sections=8), primary)
        ring.apply_translation([0, h * 0.5, 0])
        parts = [body, cap, ring]

    return _concat(parts)


# ---------------------------------------------------------------------------
# Generator dispatch
# ---------------------------------------------------------------------------

GENERATORS = {
    "bench": _bench_variant,
    "lamp": _lamp_variant,
    "trash": _trash_variant,
    "tree": _tree_variant,
    "bus_stop": _bus_stop_variant,
    "mailbox": _mailbox_variant,
    "hydrant": _hydrant_variant,
    "bollard": _bollard_variant,
}


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def parse_csv(csv_path: Path) -> List[AssetSpec]:
    specs: List[AssetSpec] = []
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            specs.append(AssetSpec(
                task_id=row["task_id"].strip(),
                category=row["category"].strip(),
                asset_id=row["asset_id"].strip(),
                style_tag=row["style_tag"].strip(),
                text_desc=row["text_desc"].strip(),
                target_h=float(row["target_height_m"]),
                target_w=float(row["target_width_m"]),
                target_d=float(row["target_depth_m"]),
                poly_budget_k=int(row["poly_budget_k"]),
                license=row["license"].strip(),
                source=row["source"].strip(),
            ))
    return specs


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate_all(
    specs: List[AssetSpec],
    mesh_out_dir: Path,
    manifest_out: Path,
    project_root: Path,
    seed: int = 42,
    bench_lamp_backend: str = "parametric",
    parametric_runtime_profile: str = "production",
    device: str = "auto",
) -> None:
    mesh_out_dir.mkdir(parents=True, exist_ok=True)
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    latents_dir = project_root / "data" / "real" / "latents"
    latents_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed)
    _ = rng  # reserved for future stochastic choices

    rows: List[Dict[str, str]] = []
    summary: Dict[str, List[int]] = {}
    quality_counts = {
        "below_min_attempts": 0,
        "over_budget_attempts": 0,
        "retry_count": 0,
        "below_min_count": 0,
    }
    backend_mode = str(bench_lamp_backend).strip().lower()
    if backend_mode not in {"parametric", "legacy"}:
        raise ValueError("bench_lamp_backend must be 'parametric' or 'legacy'")
    runtime_profile = str(parametric_runtime_profile).strip().lower()
    if runtime_profile not in {"preview", "production"}:
        raise ValueError("parametric_runtime_profile must be 'preview' or 'production'")
    backend_counts: Dict[str, int] = {"parametric": 0, "legacy": 0}
    parametric_categories: Dict[str, int] = {}

    for spec in specs:
        gen_fn = GENERATORS.get(spec.category)
        if gen_fn is None:
            raise ValueError(f"No generator for category: {spec.category}")

        style_idx = STYLE_ORDER.index(spec.style_tag) if spec.style_tag in STYLE_ORDER else 0
        primary, accent = STYLE_COLORS.get(spec.style_tag, STYLE_COLORS["modern"])
        budget = spec.poly_budget_k * 1000
        min_faces = MIN_FACES_BY_CATEGORY.get(spec.category, 0)

        mesh: trimesh.Trimesh | None = None
        n_faces = -1
        parametric_result: ParametricAssetResult | None = None
        used_backend = "legacy"
        if spec.category in {"bench", "lamp"} and backend_mode == "parametric":
            request = _parametric_request_from_asset_spec(spec, runtime_profile=runtime_profile, device=device)
            request = GenerationRequest(
                asset_kind=request.asset_kind,
                runtime_profile=request.runtime_profile,
                device_backend=request.device_backend,
                seed=int(seed),
                quality_profile=request.quality_profile,
                physics_profile=request.physics_profile,
                design_profile=request.design_profile,
                precision=request.precision,
                allow_fallback=request.allow_fallback,
                params=dict(request.params),
            )
            parametric_result = generate_parametric_asset(request)
            mesh = parametric_result.mesh.copy()
            n_faces = int(len(mesh.faces))
            if n_faces < min_faces or n_faces > budget:
                raise RuntimeError(
                    f"Failed to satisfy face constraints for {spec.asset_id}: "
                    f"min_faces={min_faces}, budget={budget}. "
                    f"Generated faces={n_faces}. Try using --bench-lamp-backend legacy or adjust budgets."
                )
            used_backend = "parametric"
        else:
            for attempt in range(MAX_GENERATION_ATTEMPTS):
                complexity_level = min(3, attempt // 2)
                raw_mesh = gen_fn(
                    style_idx,
                    spec.style_tag,
                    primary,
                    accent,
                    complexity_level=complexity_level,
                )
                candidate = fit_to_target_box(
                    raw_mesh,
                    spec.target_h,
                    spec.target_w,
                    spec.target_d,
                    label=f"{spec.asset_id}#a{attempt}",
                )
                face_count = int(len(candidate.faces))
                if face_count < min_faces and complexity_level > 0:
                    refined_mesh = raw_mesh.copy()
                    for refine_idx in range(complexity_level):
                        refined_mesh = refined_mesh.subdivide()
                        refined_candidate = fit_to_target_box(
                            refined_mesh.copy(),
                            spec.target_h,
                            spec.target_w,
                            spec.target_d,
                            label=f"{spec.asset_id}#a{attempt}.r{refine_idx + 1}",
                        )
                        refined_face_count = int(len(refined_candidate.faces))
                        if refined_face_count > budget:
                            break
                        candidate = refined_candidate
                        face_count = refined_face_count
                        if face_count >= min_faces:
                            break
                if face_count < min_faces:
                    quality_counts["below_min_attempts"] += 1
                    quality_counts["retry_count"] += 1
                    print(
                        f"  RETRY: {spec.asset_id} faces={face_count:,} < min={min_faces:,} "
                        f"(attempt {attempt + 1}/{MAX_GENERATION_ATTEMPTS}, complexity={complexity_level})"
                    )
                    continue
                if face_count > budget:
                    quality_counts["over_budget_attempts"] += 1
                    quality_counts["retry_count"] += 1
                    print(
                        f"  RETRY: {spec.asset_id} faces={face_count:,} > budget={budget:,} "
                        f"(attempt {attempt + 1}/{MAX_GENERATION_ATTEMPTS}, complexity={complexity_level})"
                    )
                    continue
                mesh = candidate
                n_faces = face_count
                break

        if mesh is None:
            raise RuntimeError(
                f"Failed to satisfy face constraints for {spec.asset_id}: "
                f"min_faces={min_faces}, budget={budget}. "
                f"Try increasing poly budget or generator detail controls."
            )

        if n_faces < min_faces:
            quality_counts["below_min_count"] += 1

        glb_path = (mesh_out_dir / f"{spec.asset_id}.glb").resolve()
        mesh.export(str(glb_path))

        # split allocation: 12 train, 1 val, 2 test per category
        cat_count = summary.get(spec.category, [])
        cat_idx = len(cat_count)
        if cat_idx < 12:
            split = "train"
        elif cat_idx < 13:
            split = "val"
        else:
            split = "test"

        row = {
            "asset_id": spec.asset_id,
            "category": spec.category,
            "text_desc": spec.text_desc,
            "mesh_path": str(glb_path),
            "latent_path": str((latents_dir / f"{spec.asset_id}.pt").resolve()),
            "license": spec.license,
            "source": "parametric_generated" if used_backend == "parametric" else "procedural_generated",
            "split": split,
        }
        if used_backend == "parametric" and parametric_result is not None:
            row.update(
                {
                    "generator_type": parametric_result.generator_type,
                    "runtime_profile": runtime_profile,
                    "style_tags": list(parametric_result.style_tags),
                    "material_family": parametric_result.material_family,
                    "parameter_snapshot": dict(parametric_result.parameter_snapshot),
                    "quality_metrics": parametric_result.quality_metrics.to_dict(),
                    "frontage_width_m": float(parametric_result.bbox_size_xyz[0]),
                    "depth_m": float(parametric_result.bbox_size_xyz[2]),
                    "asset_role": "street_furniture",
                }
            )
        rows.append(row)

        summary.setdefault(spec.category, []).append(n_faces)
        backend_counts[used_backend] = backend_counts.get(used_backend, 0) + 1
        if used_backend == "parametric":
            parametric_categories[spec.category] = parametric_categories.get(spec.category, 0) + 1
        print(f"  [{spec.asset_id}] faces={n_faces:,}  style={spec.style_tag}  backend={used_backend}")

    if quality_counts["below_min_count"] > 0:
        raise RuntimeError(
            f"Generation quality gate failed: below_min_count={quality_counts['below_min_count']} "
            "(all generated assets must satisfy category minimum faces)."
        )

    # Write manifest
    with manifest_out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")

    # Print summary
    print("\n=== Generation Summary ===")
    print(f"{'Category':<12} {'Count':>5} {'Avg Faces':>10} {'Min':>8} {'Max':>8}")
    print("-" * 48)
    total = 0
    for cat in sorted(summary.keys()):
        faces = summary[cat]
        total += len(faces)
        print(f"{cat:<12} {len(faces):>5} {int(np.mean(faces)):>10,} {min(faces):>8,} {max(faces):>8,}")
    print("-" * 48)
    print(f"{'TOTAL':<12} {total:>5}")
    print(
        "Quality gates: "
        f"below_min_count={quality_counts['below_min_count']}, "
        f"below_min_attempts={quality_counts['below_min_attempts']}, "
        f"over_budget_attempts={quality_counts['over_budget_attempts']}, "
        f"retry_count={quality_counts['retry_count']}"
    )
    print(
        "Backend usage: "
        f"parametric={backend_counts.get('parametric', 0)}, "
        f"legacy={backend_counts.get('legacy', 0)}"
    )
    print(f"Parametric categories: {json.dumps(parametric_categories, ensure_ascii=True, sort_keys=True)}")
    print(f"\nManifest: {manifest_out}")
    print(f"Meshes:   {mesh_out_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate 120 procedural GLB street assets.")
    p.add_argument("--csv", type=Path, default=Path("docs/m3_asset_task_list.csv"), help="Input CSV task list")
    p.add_argument("--mesh-out-dir", type=Path, default=Path("data/real/meshes"), help="Output mesh directory")
    p.add_argument("--manifest-out", type=Path, default=Path("data/real/real_assets_manifest.jsonl"), help="Output manifest")
    p.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    p.add_argument("--clean", action="store_true", help="Delete old meshes and latents before generating")
    p.add_argument("--bench-lamp-backend", choices=["parametric", "legacy"], default="parametric")
    p.add_argument("--parametric-runtime-profile", choices=["preview", "production"], default="production")
    p.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent

    # Print git hash for traceability
    try:
        git_hash = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                            cwd=str(project_root), text=True).strip()
        print(f"Git HEAD: {git_hash}")
    except Exception:
        print("Git HEAD: unknown")

    print(f"Seed: {args.seed}")

    if args.clean:
        mesh_dir = (project_root / args.mesh_out_dir).resolve()
        latents_dir = (project_root / "data" / "real" / "latents").resolve()
        for p in mesh_dir.glob("*.glb"):
            if p.name != ".gitkeep":
                p.unlink()
                print(f"  Removed: {p.name}")
        for p in latents_dir.glob("*.pt"):
            if p.name != ".gitkeep":
                p.unlink()
                print(f"  Removed: {p.name}")

    csv_path = (project_root / args.csv).resolve()
    mesh_out = (project_root / args.mesh_out_dir).resolve()
    manifest_out = (project_root / args.manifest_out).resolve()

    specs = parse_csv(csv_path)
    print(f"Parsed {len(specs)} asset specs from {csv_path}")

    generate_all(
        specs=specs,
        mesh_out_dir=mesh_out,
        manifest_out=manifest_out,
        project_root=project_root,
        seed=args.seed,
        bench_lamp_backend=args.bench_lamp_backend,
        parametric_runtime_profile=args.parametric_runtime_profile,
        device=args.device,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
