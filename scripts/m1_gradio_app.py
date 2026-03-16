#!/usr/bin/env python3
"""Gradio UI for RoadGen3D milestone pipelines."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import random
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

# Mitigate duplicate OpenMP runtime conflicts (common with torch/faiss on macOS).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import gradio as gr
except Exception as exc:  # pragma: no cover - runtime guard
    raise SystemExit(
        "gradio is not installed. Run: "
        ".venv/bin/python -m pip install gradio>=5,<6"
    ) from exc

from roadgen3d.decoder import PlaceholderVoxelDecoder
from roadgen3d.decoder_shapee import ShapeEDecoder
from roadgen3d.embedder import ClipTextEmbedder, ModelLoadError
from roadgen3d.index_store import FaissIndexStore
from roadgen3d.layout_policy import PolicyTrainConfig
from roadgen3d.latent_store import LatentStore, load_asset_records
from roadgen3d.parametric_assets import generate_parametric_asset
from roadgen3d.china_cities import get_city_by_name, get_city_choices
from roadgen3d.osm_ingest import fetch_osm_data, parse_osm_features, project_to_local
from roadgen3d.placement_zones import (
    EFFECTIVE_POI_EVALUATOR_VERSION,
    evaluate_projected_road_context,
)
from roadgen3d.poi_taxonomy import (
    core_poi_count,
    nonempty_poi_points,
    normalize_poi_counts,
    poi_breakdown_string,
    poi_weighted_score,
    qualifies_poi_counts,
)
from roadgen3d.road_discovery import discover_poi_roads, write_discovered_roads_jsonl
from roadgen3d.pipeline import M1Pipeline
from roadgen3d.program_generator import ProgramTrainConfig
from roadgen3d.scene_graph_viz import (
    SCENE_GRAPH_NODE_TYPES,
    plot_scene_graph,
    scene_graph_control_state,
)
from roadgen3d.street_layout import compose_street_scene
from roadgen3d.spatial_features import SpatialContext
from roadgen3d.spatial_viz import (
    plot_distance_heatmap,
    plot_distance_histograms,
    plot_poi_exclusion_overview,
    plot_scene_with_markers,
    plot_zoning_grid_preview,
)
from roadgen3d.types import PrepareWorkspaceResult, StepResult, StreetComposeConfig, WorkspaceReadiness
from scripts.m1_01_seed_assets import seed_assets
from scripts.m2_11_encode_shapee_latents import encode_latents as encode_shapee_latents
from scripts.m4_01_collect_policy_data import collect_policy_data
from scripts.m4_02_train_layout_policy import train_from_jsonl
from scripts.m4_10_eval_engineering import run_eval as run_m4_eval
from scripts.m6_01_collect_program_data import collect_program_data
from scripts.m6_02_train_program_generator import train_from_jsonl as train_program_from_jsonl

TIMELINE_KEYBOARD_JS = """
() => {
    const TILT_STEP = 10;
    const ORBIT_STEP = 15;
    const ZOOM_RATIO = 0.85;
    const MIN_PHI = 10;
    const MAX_PHI = 170;
    const DEG = Math.PI / 180;
    let keydownHandler = null;

    function getModelViewer() {
        const el = document.getElementById('street-model-view');
        if (!el) return null;
        let mv = el.querySelector('model-viewer');
        if (mv) return mv;
        for (const child of el.querySelectorAll('*')) {
            if (child.shadowRoot) {
                mv = child.shadowRoot.querySelector('model-viewer');
                if (mv) return mv;
            }
        }
        return null;
    }

    function orbitCamera(dThetaDeg, dPhiDeg, zoomFactor) {
        const mv = getModelViewer();
        if (!mv) return;
        try {
            const orbit = mv.getCameraOrbit();
            let theta = orbit.theta / DEG;
            let phi   = orbit.phi   / DEG;
            let r     = orbit.radius;
            theta += dThetaDeg;
            phi = Math.max(MIN_PHI, Math.min(MAX_PHI, phi + dPhiDeg));
            if (zoomFactor !== 1) r = Math.max(0.05, r * zoomFactor);
            mv.cameraOrbit = `${theta}deg ${phi}deg ${r}m`;
        } catch (_) {}
    }

    function resetCamera() {
        const mv = getModelViewer();
        if (!mv) return;
        mv.cameraOrbit = '0deg 75deg auto';
        mv.cameraTarget = 'auto auto auto';
        mv.fieldOfView = 'auto';
    }

    function triggerSlider(value) {
        const container = document.getElementById('production-step-slider');
        if (!container) return;
        const input = container.querySelector('input[type="range"]');
        if (!input) return;
        const max = parseFloat(input.max);
        if (max <= 0) return;
        const v = Math.max(parseFloat(input.min), Math.min(max, value));
        const nativeSetter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype, 'value'
        ).set;
        nativeSetter.call(input, v);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function handleKeydown(e) {
        const tag = document.activeElement?.tagName;
        if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
        if (document.activeElement?.type === 'range') return;
        const timeline = document.getElementById('production-timeline');
        if (!timeline) return;
        if (document.activeElement !== document.body && !timeline.contains(document.activeElement)) return;

        if (e.shiftKey && e.key === 'ArrowLeft') {
            const btn = document.querySelector('#prev-step-btn button');
            if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
            return;
        }
        if (e.shiftKey && e.key === 'ArrowRight') {
            const btn = document.querySelector('#next-step-btn button');
            if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
            return;
        }
        if (e.key === 'ArrowLeft') {
            e.preventDefault(); orbitCamera(-ORBIT_STEP, 0, 1); return;
        }
        if (e.key === 'ArrowRight') {
            e.preventDefault(); orbitCamera(ORBIT_STEP, 0, 1); return;
        }
        if (e.key === 'ArrowUp') {
            e.preventDefault(); orbitCamera(0, -TILT_STEP, 1); return;
        }
        if (e.key === 'ArrowDown') {
            e.preventDefault(); orbitCamera(0, TILT_STEP, 1); return;
        }
        if (e.key === '+' || e.key === '=') {
            e.preventDefault(); orbitCamera(0, 0, ZOOM_RATIO); return;
        }
        if (e.key === '-' || e.key === 'x' || e.key === 'X') {
            e.preventDefault(); orbitCamera(0, 0, 1 / ZOOM_RATIO); return;
        }
        if (e.key === 'r' || e.key === 'R') {
            e.preventDefault(); resetCamera(); return;
        }
        if (e.key === 'Home') {
            e.preventDefault(); triggerSlider(0); return;
        }
        if (e.key === 'End') {
            e.preventDefault();
            const container = document.getElementById('production-step-slider');
            const input = container?.querySelector('input[type="range"]');
            if (input) triggerSlider(parseFloat(input.max));
            return;
        }
    }

    function initKeyboardShortcuts() {
        if (keydownHandler) {
            document.removeEventListener('keydown', keydownHandler);
        }
        keydownHandler = handleKeydown;
        document.addEventListener('keydown', keydownHandler);
        
        const tl = document.getElementById('production-timeline');
        if (tl && !tl.hasAttribute('tabindex')) tl.setAttribute('tabindex', '0');
    }

    // Wait for Gradio DOM to be ready
    function waitForGradio() {
        const timeline = document.getElementById('production-timeline');
        if (timeline) {
            initKeyboardShortcuts();
            console.log('[RoadGen3D] Keyboard shortcuts initialized');
        } else {
            setTimeout(waitForGradio, 200);
        }
    }

    // Use MutationObserver to detect when Production Timeline appears
    const observer = new MutationObserver((mutations, obs) => {
        const timeline = document.getElementById('production-timeline');
        if (timeline) {
            initKeyboardShortcuts();
            obs.disconnect();
            console.log('[RoadGen3D] Keyboard shortcuts initialized via MutationObserver');
        }
    });

    observer.observe(document.body, { childList: true, subtree: true });

    // Also try immediately in case DOM is already ready
    if (document.readyState === 'complete') {
        setTimeout(waitForGradio, 100);
    } else {
        document.addEventListener('load', () => setTimeout(waitForGradio, 100));
    }
}
"""

_PARAMETRIC_STYLE_CHOICES = [
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
]


def _to_path(path_text: str) -> Path:
    return Path(path_text.strip()).expanduser().resolve()


def _spatial_context_poi_points(spatial_ctx: Dict[str, Any]) -> Dict[str, Tuple[Tuple[float, float], ...]]:
    mapping = spatial_ctx.get("poi_points_by_type_xz", {}) or {}
    normalized = {
        poi_type: tuple((float(point[0]), float(point[1])) for point in points)
        for poi_type, points in nonempty_poi_points(mapping).items()
    }
    if not normalized:
        normalized = {
            poi_type: tuple((float(point[0]), float(point[1])) for point in points)
            for poi_type, points in {
                "entrance": spatial_ctx.get("entrance_points_xz", []) or [],
                "bus_stop": spatial_ctx.get("bus_stop_points_xz", []) or [],
                "fire_hydrant": spatial_ctx.get("fire_points_xz", []) or [],
            }.items()
            if points
        }
    return normalized


def _spatial_context_poi_counts(spatial_ctx: Dict[str, Any]) -> Dict[str, int]:
    return normalize_poi_counts({
        poi_type: len(points)
        for poi_type, points in _spatial_context_poi_points(spatial_ctx).items()
    })


def _load_real_manifest_rows(manifest_path: Path) -> List[Dict[str, str]]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"Real manifest not found: {manifest_path}")
    rows: List[Dict[str, str]] = []
    for idx, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        for key in ("asset_id", "text_desc", "latent_path"):
            if key not in payload:
                raise ValueError(f"Missing key '{key}' in real manifest line {idx}: {manifest_path}")
        rows.append(
            {
                "asset_id": str(payload["asset_id"]),
                "description": str(payload["text_desc"]),
                "latent_path": str(
                    (
                        Path(str(payload["latent_path"])).expanduser()
                        if Path(str(payload["latent_path"])).expanduser().is_absolute()
                        else (manifest_path.parent / str(payload["latent_path"])).resolve()
                    )
                ),
            }
        )
    if not rows:
        raise ValueError(
            "Real manifest is empty. Add at least one JSONL row, then rebuild the real index."
    )
    return rows


def _normalize_tag_values(value: object) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw = [item.strip().lower() for item in value.split(",")]
    else:
        raw = [str(item).strip().lower() for item in value]
    return tuple(sorted({item for item in raw if item}))


def _load_asset_library_rows(manifest_path: Path) -> List[Dict[str, object]]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"Real manifest not found: {manifest_path}")
    rows: List[Dict[str, object]] = []
    for idx, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        asset_id = str(payload.get("asset_id", "")).strip()
        text_desc = str(payload.get("text_desc", "")).strip()
        if not asset_id or not text_desc:
            raise ValueError(f"Invalid asset row in manifest line {idx}: {manifest_path}")
        category = str(payload.get("category", "")).strip().lower()
        asset_role = str(payload.get("asset_role", "building" if category == "building" else "street_furniture")).strip().lower()
        theme_tags = _normalize_tag_values(payload.get("theme_tags"))
        rows.append(
            {
                "asset_id": asset_id,
                "category": category,
                "asset_role": asset_role,
                "text_desc": text_desc,
                "theme_tags": theme_tags,
                "frontage_width_m": float(payload.get("frontage_width_m", 0.0) or 0.0),
                "depth_m": float(payload.get("depth_m", 0.0) or 0.0),
                "height_class": str(payload.get("height_class", "")).strip().lower(),
                "source": str(payload.get("source", "")).strip(),
                "generator_type": str(payload.get("generator_type", "")).strip(),
            }
        )
    return rows


def browse_asset_library(
    real_manifest_text: str,
    search_text: str = "",
) -> Tuple[List[List[str]], str]:
    try:
        manifest_path = _to_path(real_manifest_text)
        rows = _load_asset_library_rows(manifest_path)
        query = str(search_text).strip().lower()
        if query:
            rows = [
                row
                for row in rows
                if query in str(row.get("asset_id", "")).lower()
                or query in str(row.get("category", "")).lower()
                or query in str(row.get("asset_role", "")).lower()
                or query in str(row.get("text_desc", "")).lower()
                or query in ",".join(row.get("theme_tags", ()) or ()).lower()
            ]
        role_counts: Dict[str, int] = {}
        theme_counts: Dict[str, int] = {}
        for row in rows:
            role = str(row.get("asset_role", "")).strip().lower()
            role_counts[role] = role_counts.get(role, 0) + 1
            for tag in row.get("theme_tags", ()) or ():
                theme_counts[str(tag)] = theme_counts.get(str(tag), 0) + 1
        table = [
            [
                str(row.get("asset_id", "")),
                str(row.get("category", "")),
                str(row.get("asset_role", "")),
                ",".join(row.get("theme_tags", ()) or ()),
                f"{float(row.get('frontage_width_m', 0.0) or 0.0):.1f}" if float(row.get("frontage_width_m", 0.0) or 0.0) > 0.0 else "",
                f"{float(row.get('depth_m', 0.0) or 0.0):.1f}" if float(row.get("depth_m", 0.0) or 0.0) > 0.0 else "",
                str(row.get("height_class", "")),
                str(row.get("source", "")),
                str(row.get("text_desc", "")),
            ]
            for row in rows
        ]
        stats = {
            "asset_count": len(rows),
            "role_counts": role_counts,
            "theme_counts": dict(sorted(theme_counts.items())),
            "building_asset_count": int(role_counts.get("building", 0)),
        }
        return table, json.dumps(stats, indent=2, ensure_ascii=True)
    except Exception as exc:
        return [], json.dumps({"error": str(exc)}, indent=2, ensure_ascii=True)


def _toggle_parametric_controls(asset_kind: str):
    is_bench = str(asset_kind).strip().lower() == "bench"
    return gr.update(visible=is_bench), gr.update(visible=not is_bench)


def _write_preview_placeholder_latent(latent_path: Path, mesh_path: Path) -> str:
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import torch
    except ImportError:
        latent_path.write_text(json.dumps({"mesh_path": str(mesh_path)}, ensure_ascii=True), encoding="utf-8")
        return "torch unavailable; wrote JSON placeholder latent"
    torch.save({"mesh_path": str(mesh_path)}, latent_path)
    return ""


def _load_manifest_asset_ids(manifest_path: Path) -> set[str]:
    if not manifest_path.exists():
        return set()
    asset_ids: set[str] = set()
    for idx, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        asset_id = str(payload.get("asset_id", "")).strip()
        if not asset_id:
            raise ValueError(f"Invalid asset row in manifest line {idx}: {manifest_path}")
        asset_ids.add(asset_id)
    return asset_ids


def _parametric_identity(result_meta: Dict[str, Any], asset_id_text: str, text_desc_text: str) -> Tuple[str, str]:
    style_tags = result_meta.get("style_tags", []) or []
    style_tag = str(style_tags[0]).strip() if style_tags else "default"
    asset_kind = str(result_meta.get("asset_kind", "")).strip()
    runtime_profile = str(result_meta.get("runtime_profile", "")).strip()
    material_family = str(result_meta.get("material_family", "")).strip()

    def _slug(value: str) -> str:
        cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value).strip())
        compact = "_".join(part for part in cleaned.split("_") if part)
        return compact or "default"

    def _auto_asset_id() -> str:
        signature_payload = {
            "asset_kind": str(result_meta.get("asset_kind", "")).strip().lower(),
            "runtime_profile": str(result_meta.get("runtime_profile", "")).strip().lower(),
            "material_family": str(result_meta.get("material_family", "")).strip().lower(),
            "style_tags": [str(tag).strip().lower() for tag in result_meta.get("style_tags", []) or []],
            "parameter_snapshot": dict(result_meta.get("parameter_snapshot", {}) or {}),
        }
        signature = hashlib.sha1(
            json.dumps(signature_payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:8]
        tokens = [
            _slug(asset_kind),
            _slug(style_tag),
            _slug(material_family) if material_family else "",
            _slug(runtime_profile),
            signature,
        ]
        return "_".join(token for token in tokens if token)

    asset_id = str(asset_id_text).strip() or _auto_asset_id()
    text_desc = str(text_desc_text).strip() or f"parametric {style_tag} {material_family} {asset_kind}".strip()
    return asset_id, text_desc


def _parametric_manifest_row(
    *,
    asset_id: str,
    text_desc: str,
    mesh_path: Path,
    latent_path: Path,
    result_meta: Dict[str, Any],
) -> Dict[str, Any]:
    bbox = result_meta.get("bbox", {}).get("size_xyz", [0.0, 0.0, 0.0])
    return {
        "asset_id": asset_id,
        "category": str(result_meta.get("asset_kind", "")).strip(),
        "text_desc": text_desc,
        "mesh_path": str(mesh_path),
        "latent_path": str(latent_path),
        "source": "parametric_generated",
        "generator_type": str(result_meta.get("generator_type", "parametric_v1")).strip(),
        "runtime_profile": str(result_meta.get("runtime_profile", "")).strip(),
        "style_tags": list(result_meta.get("style_tags", []) or []),
        "material_family": str(result_meta.get("material_family", "")).strip(),
        "parameter_snapshot": dict(result_meta.get("parameter_snapshot", {}) or {}),
        "quality_metrics": dict(result_meta.get("quality_metrics", {}) or {}),
        "frontage_width_m": float(bbox[0]) if len(bbox) >= 1 else 0.0,
        "depth_m": float(bbox[2]) if len(bbox) >= 3 else 0.0,
        "asset_role": "street_furniture",
    }


def preview_parametric_asset(
    asset_kind: str,
    runtime_profile: str,
    device_backend: str,
    preview_out_dir_text: str,
    asset_id_text: str,
    text_desc_text: str,
    bench_width_m: float,
    bench_depth_m: float,
    bench_seat_height_m: float,
    bench_backrest_height_m: float,
    bench_backrest_angle_deg: float,
    bench_leg_type: str,
    bench_armrest_enabled: bool,
    bench_slat_count: int,
    bench_material_family: str,
    bench_style_tag: str,
    bench_detail_level: int,
    lamp_pole_height_m: float,
    lamp_pole_radius_m: float,
    lamp_base_diameter_m: float,
    lamp_arm_length_m: float,
    lamp_luminaire_type: str,
    lamp_single_or_double_arm: str,
    lamp_light_direction: str,
    lamp_material_family: str,
    lamp_style_tag: str,
    lamp_detail_level: int,
) -> Tuple[str, str, str | None, List[str], Dict[str, Any] | None]:
    try:
        preview_out_dir = _to_path(preview_out_dir_text)
        preview_out_dir.mkdir(parents=True, exist_ok=True)
        kind = str(asset_kind).strip().lower()
        if kind == "bench":
            params = {
                "width_m": float(bench_width_m),
                "depth_m": float(bench_depth_m),
                "seat_height_m": float(bench_seat_height_m),
                "backrest_height_m": float(bench_backrest_height_m),
                "backrest_angle_deg": float(bench_backrest_angle_deg),
                "leg_type": str(bench_leg_type).strip(),
                "armrest_enabled": bool(bench_armrest_enabled),
                "slat_count": int(bench_slat_count),
                "material_family": str(bench_material_family).strip(),
                "style_tag": str(bench_style_tag).strip(),
                "detail_level": int(bench_detail_level),
            }
        else:
            params = {
                "pole_height_m": float(lamp_pole_height_m),
                "pole_radius_m": float(lamp_pole_radius_m),
                "base_diameter_m": float(lamp_base_diameter_m),
                "arm_length_m": float(lamp_arm_length_m),
                "luminaire_type": str(lamp_luminaire_type).strip(),
                "single_or_double_arm": str(lamp_single_or_double_arm).strip(),
                "light_direction": str(lamp_light_direction).strip(),
                "material_family": str(lamp_material_family).strip(),
                "style_tag": str(lamp_style_tag).strip(),
                "detail_level": int(lamp_detail_level),
            }
        result = generate_parametric_asset(
            {
                "asset_kind": kind,
                "runtime_profile": str(runtime_profile).strip().lower(),
                "device_backend": str(device_backend).strip().lower() or "auto",
                "params": params,
            }
        )
        result_meta = result.to_metadata()
        asset_id, text_desc = _parametric_identity(result_meta, asset_id_text, text_desc_text)
        mesh_path = preview_out_dir / f"{asset_id}.glb"
        result_json_path = preview_out_dir / f"{asset_id}.result.json"
        latent_path = preview_out_dir / f"{asset_id}.pt"
        result.mesh.export(str(mesh_path))
        latent_warning = _write_preview_placeholder_latent(latent_path, mesh_path)
        if latent_warning:
            warnings_list = list(result_meta.get("warnings", []) or [])
            warnings_list.append(latent_warning)
            result_meta["warnings"] = warnings_list
        result_meta["asset_id"] = asset_id
        result_meta["text_desc"] = text_desc
        result_meta["mesh_path"] = str(mesh_path)
        result_meta["latent_path"] = str(latent_path)
        result_json_path.write_text(json.dumps(result_meta, indent=2, ensure_ascii=True), encoding="utf-8")
        manifest_row = _parametric_manifest_row(
            asset_id=asset_id,
            text_desc=text_desc,
            mesh_path=mesh_path,
            latent_path=latent_path,
            result_meta=result_meta,
        )
        preview_state = {
            "asset_id": asset_id,
            "text_desc": text_desc,
            "category": manifest_row["category"],
            "mesh_path": str(mesh_path),
            "result_json_path": str(result_json_path),
            "latent_path": str(latent_path),
            "manifest_row": manifest_row,
        }
        status = (
            "Parametric preview ready.\n"
            f"- asset_id: {asset_id}\n"
            f"- category: {manifest_row['category']}\n"
            f"- runtime_profile: {result.runtime_profile}\n"
            f"- device_backend: {result.resolved_device_backend}\n"
            f"- face_count: {result.quality_metrics.face_count}\n"
            f"- result_json: {result_json_path}"
        )
        return status, json.dumps(result_meta, indent=2, ensure_ascii=True), str(mesh_path), [str(mesh_path), str(result_json_path), str(latent_path)], preview_state
    except Exception as exc:
        detail = traceback.format_exc(limit=3)
        return f"Parametric preview failed: {exc}\n{detail}", json.dumps({"error": str(exc)}, indent=2, ensure_ascii=True), None, [], None


def append_parametric_asset_to_manifest(
    preview_state: Dict[str, Any] | None,
    real_manifest_text: str,
) -> Tuple[str, List[List[str]], str]:
    try:
        if not preview_state:
            raise ValueError("No preview asset available. Generate Preview before appending to manifest.")
        manifest_row = dict(preview_state.get("manifest_row", {}) or {})
        asset_id = str(preview_state.get("asset_id", "")).strip()
        if not asset_id or not manifest_row:
            raise ValueError("Preview state is incomplete. Regenerate the preview before appending.")
        manifest_path = _to_path(real_manifest_text)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        existing_ids = _load_manifest_asset_ids(manifest_path)
        if asset_id in existing_ids:
            raise ValueError(f"Asset '{asset_id}' already exists in manifest: {manifest_path}")
        with manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest_row, ensure_ascii=True) + "\n")
        table, stats_json = browse_asset_library(str(manifest_path), "")
        status = (
            "Parametric asset appended to manifest.\n"
            f"- asset_id: {asset_id}\n"
            f"- manifest: {manifest_path}"
        )
        return status, table, stats_json
    except Exception as exc:
        table, stats_json = browse_asset_library(real_manifest_text, "")
        return f"Append to manifest failed: {exc}", table, stats_json


def _write_assets_jsonl(rows: List[Dict[str, str]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    return out_path


def _build_index_from_assets(
    assets_path: Path,
    artifacts_dir: Path,
    model_name: str,
    model_dir: Path | None,
    local_files_only: bool,
    device: str,
) -> Tuple[List[str], np.ndarray, ClipTextEmbedder]:
    records = load_asset_records(assets_path)
    if not records:
        raise ValueError(
            f"No assets found in {assets_path}. Provide at least one asset record before building index."
        )
    descriptions = [record.description for record in records]
    asset_ids = [record.asset_id for record in records]

    embedder = ClipTextEmbedder(
        model_name=model_name,
        model_dir=model_dir,
        local_files_only=bool(local_files_only),
        device=device,
    )
    embeddings = embedder.encode_texts(descriptions)

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    np.save(artifacts_dir / "asset_text_embeds.npy", embeddings)
    (artifacts_dir / "asset_ids.json").write_text(
        json.dumps(asset_ids, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    meta = {
        "num_assets": len(asset_ids),
        "embedding_dim": int(embeddings.shape[1]),
        "model_source": embedder.model_source,
        "projection_dim": int(embedder.projection_dim),
        "local_files_only": bool(local_files_only),
    }
    (artifacts_dir / "embed_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8")

    index_store = FaissIndexStore.build(embeddings=embeddings, asset_ids=asset_ids)
    index_store.save(index_path=artifacts_dir / "index_ip.faiss", id_map_path=artifacts_dir / "id_map.json")
    return asset_ids, embeddings, embedder


def _bbox_hash(bbox: Tuple[float, float, float, float]) -> str:
    key = f"{bbox[0]:.6f},{bbox[1]:.6f},{bbox[2]:.6f},{bbox[3]:.6f}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def inspect_workspace_readiness(
    dataset_profile: str,
    data_dir_text: str,
    artifacts_dir_text: str,
    real_manifest_text: str,
    model_dir_text: str,
    real_latents_dir_text: str,
    layout_mode: str = "template",
    aoi_bbox: Tuple[float, float, float, float] | None = None,
    osm_cache_dir_text: str = "",
) -> WorkspaceReadiness:
    profile = str(dataset_profile).strip().lower()
    artifacts_dir = _to_path(artifacts_dir_text)
    model_dir = _to_path(model_dir_text) if str(model_dir_text).strip() else None
    latents_dir = _to_path(real_latents_dir_text) if str(real_latents_dir_text).strip() else None
    missing: List[str] = []
    details: Dict[str, Any] = {
        "profile": profile,
        "artifacts_dir": str(artifacts_dir),
        "model_dir": str(model_dir) if model_dir is not None else "",
    }

    manifest_ok = False
    latents_ok = False
    if profile == "mock":
        data_dir = _to_path(data_dir_text)
        manifest_ok = bool((data_dir / "assets.jsonl").exists()) or data_dir.exists()
        latents_ok = True
        details["assets_path"] = str((data_dir / "assets.jsonl").resolve())
    else:
        manifest_path = _to_path(real_manifest_text)
        manifest_ok = manifest_path.exists()
        details["manifest_path"] = str(manifest_path)
        if manifest_ok:
            try:
                rows = _load_real_manifest_rows(manifest_path)
                asset_library_rows = _load_asset_library_rows(manifest_path)
                latents_ok = all(Path(row["latent_path"]).exists() for row in rows)
                details["manifest_asset_count"] = len(rows)
                role_counts: Dict[str, int] = {}
                theme_counts: Dict[str, int] = {}
                for row in asset_library_rows:
                    role = str(row.get("asset_role", "")).strip().lower()
                    role_counts[role] = role_counts.get(role, 0) + 1
                    for theme_tag in row.get("theme_tags", ()) or ():
                        theme_counts[str(theme_tag)] = theme_counts.get(str(theme_tag), 0) + 1
                details["asset_role_counts"] = role_counts
                details["theme_tag_counts"] = dict(sorted(theme_counts.items()))
                details["building_asset_count"] = int(role_counts.get("building", 0))
                if not latents_ok:
                    missing.append("latents")
            except Exception as exc:
                manifest_ok = False
                details["manifest_error"] = str(exc)
        else:
            missing.append("manifest")
        if latents_dir is not None:
            details["latents_dir"] = str(latents_dir)

    index_ok = bool((artifacts_dir / "index_ip.faiss").exists() and (artifacts_dir / "id_map.json").exists())
    if not index_ok:
        missing.append("index")

    osm_cache_ok = True
    if str(layout_mode).strip().lower() == "osm":
        if aoi_bbox is None:
            osm_cache_ok = False
            missing.append("osm_cache")
        else:
            cache_dir = _to_path(osm_cache_dir_text) if str(osm_cache_dir_text).strip() else (artifacts_dir / "osm_cache").resolve()
            cache_path = cache_dir / f"overpass_{_bbox_hash(aoi_bbox)}.json"
            osm_cache_ok = cache_path.exists()
            details["osm_cache_path"] = str(cache_path)
            if not osm_cache_ok:
                missing.append("osm_cache")

    if model_dir is not None and not model_dir.exists():
        missing.append("model_dir")
        details["model_dir_error"] = "missing"

    if profile == "mock":
        recommended = "Prepare Workspace to seed assets and rebuild index."
    elif not manifest_ok:
        recommended = "Fix the real manifest path and rerun Prepare Workspace."
    elif not latents_ok:
        recommended = "Prepare Workspace to encode missing latents."
    elif not index_ok:
        recommended = "Prepare Workspace to build the FAISS index."
    elif not osm_cache_ok:
        recommended = "Prepare Workspace to prefetch OSM cache for the AOI."
    else:
        recommended = "Workspace is ready. Go to Generate Street."

    return WorkspaceReadiness(
        manifest_ok=bool(manifest_ok),
        latents_ok=bool(latents_ok),
        index_ok=bool(index_ok),
        osm_cache_ok=bool(osm_cache_ok),
        missing_items=tuple(sorted(set(missing))),
        recommended_next_action=recommended,
        details=details,
    )


def prepare_manifest_assets(
    dataset_profile: str,
    data_dir_text: str,
    artifacts_dir_text: str,
    real_manifest_text: str,
    num_assets: int,
    seed: int,
    latent_dim: int,
) -> StepResult:
    profile = str(dataset_profile).strip().lower()
    data_dir = _to_path(data_dir_text)
    artifacts_dir = _to_path(artifacts_dir_text)
    if profile == "mock":
        rows = seed_assets(out_dir=data_dir, num_assets=int(num_assets), seed=int(seed), latent_dim=int(latent_dim))
        return StepResult(
            step="prepare_manifest_assets",
            status="completed",
            message=f"Seeded {len(rows)} mock assets.",
            outputs={"assets_path": str((data_dir / 'assets.jsonl').resolve()), "asset_count": len(rows)},
        )

    manifest_path = _to_path(real_manifest_text)
    rows = _load_real_manifest_rows(manifest_path)
    assets_path = _write_assets_jsonl(rows, artifacts_dir / "real_assets_for_pipeline.jsonl")
    return StepResult(
        step="prepare_manifest_assets",
        status="completed",
        message=f"Normalized {len(rows)} real assets for pipeline indexing.",
        outputs={"assets_path": str(assets_path), "asset_count": len(rows)},
    )


def prepare_latents_if_needed(
    dataset_profile: str,
    real_manifest_text: str,
    real_mesh_root_text: str,
    real_latents_dir_text: str,
    shapee_model_dir_text: str,
    render_cache_dir_text: str,
    encode_mode: str,
    device: str,
    shapee_local_only: bool,
    force_reencode: bool,
) -> StepResult:
    profile = str(dataset_profile).strip().lower()
    if profile != "real":
        return StepResult(step="prepare_latents_if_needed", status="skipped", message="Mock profile does not require latent encoding.")

    manifest_path = _to_path(real_manifest_text)
    rows = _load_real_manifest_rows(manifest_path)
    all_exist = all(Path(row["latent_path"]).exists() for row in rows)
    if all_exist and not bool(force_reencode):
        return StepResult(
            step="prepare_latents_if_needed",
            status="skipped",
            message="All latents already exist; skipped encoding.",
            outputs={"manifest_path": str(manifest_path), "asset_count": len(rows)},
        )

    log = encode_real_latents(
        dataset_profile=dataset_profile,
        real_manifest_text=real_manifest_text,
        real_mesh_root_text=real_mesh_root_text,
        real_latents_dir_text=real_latents_dir_text,
        shapee_model_dir_text=shapee_model_dir_text,
        render_cache_dir_text=render_cache_dir_text,
        encode_mode=encode_mode,
        device=device,
        shapee_local_only=shapee_local_only,
        skip_existing=not bool(force_reencode),
        no_placeholder_fallback=False,
        no_mesh_reference_fallback=False,
        verbose=False,
    )
    return StepResult(
        step="prepare_latents_if_needed",
        status="completed" if "failed" not in log.lower() else "error",
        message=log,
        outputs={"manifest_path": str(manifest_path)},
    )


def prepare_index_if_needed(
    dataset_profile: str,
    data_dir_text: str,
    artifacts_dir_text: str,
    real_manifest_text: str,
    num_assets: int,
    seed: int,
    latent_dim: int,
    model_name: str,
    model_dir_text: str,
    local_files_only: bool,
    device: str,
    force_reindex: bool,
) -> StepResult:
    artifacts_dir = _to_path(artifacts_dir_text)
    index_ok = (artifacts_dir / "index_ip.faiss").exists() and (artifacts_dir / "id_map.json").exists()
    if index_ok and not bool(force_reindex):
        return StepResult(
            step="prepare_index_if_needed",
            status="skipped",
            message="FAISS index already exists; skipped rebuild.",
            outputs={"index_path": str((artifacts_dir / 'index_ip.faiss').resolve())},
        )
    log, preview = prepare_assets_and_index(
        dataset_profile=dataset_profile,
        data_dir_text=data_dir_text,
        artifacts_dir_text=artifacts_dir_text,
        real_manifest_text=real_manifest_text,
        num_assets=num_assets,
        seed=seed,
        latent_dim=latent_dim,
        model_name=model_name,
        model_dir_text=model_dir_text,
        local_files_only=local_files_only,
        device=device,
    )
    return StepResult(
        step="prepare_index_if_needed",
        status="completed" if "failed" not in log.lower() and "error" not in log.lower() else "error",
        message=log,
        outputs={"preview_count": len(preview), "index_path": str((artifacts_dir / 'index_ip.faiss').resolve())},
    )


def prepare_osm_cache_if_needed(
    layout_mode: str,
    artifacts_dir_text: str,
    osm_cache_dir_text: str,
    force_osm_refresh: bool,
    aoi_bbox: Tuple[float, float, float, float] | None,
) -> StepResult:
    if str(layout_mode).strip().lower() != "osm":
        return StepResult(step="prepare_osm_cache_if_needed", status="skipped", message="Template mode does not require OSM cache.")
    if aoi_bbox is None:
        return StepResult(step="prepare_osm_cache_if_needed", status="skipped", message="OSM mode selected without AOI bbox.")
    artifacts_dir = _to_path(artifacts_dir_text)
    cache_dir = _to_path(osm_cache_dir_text) if str(osm_cache_dir_text).strip() else (artifacts_dir / "osm_cache").resolve()
    fetch_osm_data(bbox=aoi_bbox, cache_dir=cache_dir, force_refetch=bool(force_osm_refresh))
    return StepResult(
        step="prepare_osm_cache_if_needed",
        status="completed",
        message=f"OSM cache ready for bbox={aoi_bbox}.",
        outputs={"cache_dir": str(cache_dir), "bbox": list(aoi_bbox)},
    )


def discover_poi_roads_if_needed(
    layout_mode: str,
    artifacts_dir_text: str,
    osm_cache_dir_text: str,
    aoi_bbox: Tuple[float, float, float, float] | None,
    force_rediscover: bool = False,
) -> StepResult:
    """Step 5: discover POI-rich roads around the selected city."""
    if str(layout_mode).strip().lower() != "osm":
        return StepResult(step="discover_poi_roads", status="skipped", message="Template mode — road discovery not needed.")
    if aoi_bbox is None:
        return StepResult(step="discover_poi_roads", status="skipped", message="No AOI bbox provided.")

    artifacts_dir = _to_path(artifacts_dir_text)
    cache_dir = _to_path(osm_cache_dir_text) if str(osm_cache_dir_text).strip() else (artifacts_dir / "osm_cache").resolve()
    discovered_path = artifacts_dir.parent / "m5" / "discovered_poi_roads.jsonl"
    metadata_path = _discovered_metadata_path(discovered_path)

    if discovered_path.exists() and not force_rediscover and _discovered_cache_matches(discovered_path, aoi_bbox):
        import json as _json
        n_roads = sum(1 for line in discovered_path.read_text(encoding="utf-8").splitlines() if line.strip())
        return StepResult(
            step="discover_poi_roads",
            status="completed",
            message=f"Using cached discovery: {n_roads} POI-rich roads in {discovered_path.name}.",
            outputs={"discovered_roads_path": str(discovered_path), "road_count": n_roads},
        )

    # Build a minimal CityRecord-like object from the bbox
    class _AdhocCity:
        def __init__(self, bbox):
            self.name_en = "adhoc"
            self.name_zh = "adhoc"
            self.province = ""
            self.bbox = bbox

    city = _AdhocCity(aoi_bbox)
    roads = discover_poi_roads(city, cache_dir)
    write_discovered_roads_jsonl(roads, discovered_path)
    _write_discovered_roads_metadata(metadata_path, aoi_bbox)
    return StepResult(
        step="discover_poi_roads",
        status="completed",
        message=f"Discovered {len(roads)} POI-rich roads (>= 100m, poi_score >= 2.0, core_poi_count >= 1).",
        outputs={"discovered_roads_path": str(discovered_path), "road_count": len(roads)},
    )


def _load_discovered_road_records(discovered_path: Path) -> List[Dict[str, Any]]:
    if not discovered_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in discovered_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _discovered_metadata_path(discovered_path: Path) -> Path:
    return discovered_path.with_suffix(".meta.json")


def _write_discovered_roads_metadata(
    metadata_path: Path,
    aoi_bbox: Tuple[float, float, float, float],
    *,
    min_poi_count: int = 2,
    min_road_length_m: float = 100.0,
    min_poi_score: float = 2.0,
    min_core_poi_count: int = 1,
) -> None:
    metadata = {
        "aoi_bbox": [float(value) for value in aoi_bbox],
        "min_poi_count": int(min_poi_count),
        "min_road_length_m": float(min_road_length_m),
        "min_poi_score": float(min_poi_score),
        "min_core_poi_count": int(min_core_poi_count),
        "poi_evaluator_version": EFFECTIVE_POI_EVALUATOR_VERSION,
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=True), encoding="utf-8")


def _load_discovered_roads_metadata(metadata_path: Path) -> Dict[str, Any]:
    if not metadata_path.exists():
        return {}
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def _discovered_cache_matches(
    discovered_path: Path,
    aoi_bbox: Tuple[float, float, float, float] | None,
    *,
    min_poi_count: int = 2,
    min_road_length_m: float = 100.0,
    min_poi_score: float = 2.0,
    min_core_poi_count: int = 1,
) -> bool:
    if aoi_bbox is None or not discovered_path.exists():
        return False
    metadata = _load_discovered_roads_metadata(_discovered_metadata_path(discovered_path))
    if not metadata:
        return False
    return (
        tuple(float(value) for value in metadata.get("aoi_bbox", ())) == tuple(float(value) for value in aoi_bbox)
        and int(metadata.get("min_poi_count", -1)) == int(min_poi_count)
        and float(metadata.get("min_road_length_m", -1.0)) == float(min_road_length_m)
        and float(metadata.get("min_poi_score", -1.0)) == float(min_poi_score)
        and int(metadata.get("min_core_poi_count", -1)) == int(min_core_poi_count)
        and str(metadata.get("poi_evaluator_version", "")) == EFFECTIVE_POI_EVALUATOR_VERSION
    )


def _probe_discovered_road_context_metrics(
    row: Dict[str, Any],
    *,
    osm_cache_dir: Path,
    road_width_m: float,
    sidewalk_width_m: float,
    lane_count: int,
    road_selection: str,
) -> Dict[str, Any]:
    candidate_bbox = tuple(float(value) for value in row["bbox"])
    probe_config = StreetComposeConfig(
        query="probe",
        length_m=80.0,
        road_width_m=float(road_width_m),
        sidewalk_width_m=float(sidewalk_width_m),
        lane_count=int(lane_count),
        density=1.0,
        seed=0,
        topk_per_category=1,
        max_trials_per_slot=1,
        layout_mode="osm",
        constraint_mode="off",
        aoi_bbox=candidate_bbox,
        osm_cache_dir=str(osm_cache_dir),
        road_selection=str(road_selection),
        selected_road_osm_id=int(row["osm_id"]),
    )
    raw = fetch_osm_data(bbox=candidate_bbox, cache_dir=Path(osm_cache_dir))
    features = parse_osm_features(raw)
    projected = project_to_local(features, candidate_bbox)
    _filtered, placement_ctx, poi_counts = evaluate_projected_road_context(projected, probe_config)
    return {
        "poi_counts": poi_counts,
        "poi_fit_feasible": bool(getattr(placement_ctx, "poi_fit_feasible", True)),
        "poi_fit_report": dict(getattr(placement_ctx, "poi_fit_report", {}) or {}),
        "required_left_width_m": float(getattr(placement_ctx, "required_left_width_m", 0.0) or 0.0),
        "required_right_width_m": float(getattr(placement_ctx, "required_right_width_m", 0.0) or 0.0),
        "row_width_m": float(getattr(placement_ctx, "row_width_m", 0.0) or 0.0),
    }


def _probe_discovered_road_effective_poi_counts(
    row: Dict[str, Any],
    *,
    osm_cache_dir: Path,
    road_width_m: float,
    sidewalk_width_m: float,
    lane_count: int,
    road_selection: str,
) -> Dict[str, int]:
    """Backward-compatible wrapper returning only effective POI counts."""

    return dict(
        _probe_discovered_road_context_metrics(
            row,
            osm_cache_dir=osm_cache_dir,
            road_width_m=road_width_m,
            sidewalk_width_m=sidewalk_width_m,
            lane_count=lane_count,
            road_selection=road_selection,
        ).get("poi_counts", {})
    )


_DEFAULT_EFFECTIVE_POI_COUNTS_PROBE = _probe_discovered_road_effective_poi_counts


def _select_auto_discovered_road(
    *,
    artifacts_dir: Path,
    osm_cache_dir: Path,
    aoi_bbox: Tuple[float, float, float, float] | None,
    seed: int,
    road_width_m: float,
    sidewalk_width_m: float,
    lane_count: int,
    road_selection: str,
) -> Tuple[Dict[str, Any], bool, Dict[str, Any]]:
    """Return one POI-rich road chosen deterministically from discovery results."""
    discovered_path = artifacts_dir.parent / "m5" / "discovered_poi_roads.jsonl"
    metadata_path = _discovered_metadata_path(discovered_path)
    if not _discovered_cache_matches(discovered_path, aoi_bbox):
        cached_rows = []
    else:
        cached_rows = [
            row for row in _load_discovered_road_records(discovered_path)
            if qualifies_poi_counts(row.get("poi_types", {}))
        ]
    auto_discovered = False

    if not cached_rows:
        if aoi_bbox is None:
            raise RuntimeError("OSM mode requires an AOI bbox to auto-discover POI-rich roads.")

        class _AdhocCity:
            def __init__(self, bbox):
                self.name_en = "adhoc"
                self.name_zh = "adhoc"
                self.province = ""
                self.bbox = bbox

        roads = discover_poi_roads(_AdhocCity(aoi_bbox), osm_cache_dir)
        auto_discovered = True
        write_discovered_roads_jsonl(roads, discovered_path)
        _write_discovered_roads_metadata(metadata_path, aoi_bbox)
        cached_rows = [
            row for row in _load_discovered_road_records(discovered_path)
            if qualifies_poi_counts(row.get("poi_types", {}))
        ]

    if not cached_rows:
        raise RuntimeError(
            "No POI-rich roads found for the current area "
            "(requires weighted POI score >= 2.0 and at least 1 core POI)."
        )

    ordered_rows = sorted(
        cached_rows,
        key=lambda row: (
            int(row.get("osm_id", 0)),
            float(row.get("road_length_m", 0.0)),
            tuple(float(v) for v in row.get("bbox", ())),
        ),
    )
    rng = random.Random(int(seed))
    rng.shuffle(ordered_rows)
    for row in ordered_rows:
        effective_counts = _probe_discovered_road_effective_poi_counts(
            row,
            osm_cache_dir=osm_cache_dir,
            road_width_m=float(road_width_m),
            sidewalk_width_m=float(sidewalk_width_m),
            lane_count=int(lane_count),
            road_selection=str(road_selection),
        )
        if _probe_discovered_road_effective_poi_counts is _DEFAULT_EFFECTIVE_POI_COUNTS_PROBE:
            probe_metrics = _probe_discovered_road_context_metrics(
                row,
                osm_cache_dir=osm_cache_dir,
                road_width_m=float(road_width_m),
                sidewalk_width_m=float(sidewalk_width_m),
                lane_count=int(lane_count),
                road_selection=str(road_selection),
            )
            probe_metrics["poi_counts"] = effective_counts
        else:
            probe_metrics = {
                "poi_counts": effective_counts,
                "poi_fit_feasible": True,
                "poi_fit_report": {},
                "required_left_width_m": 0.0,
                "required_right_width_m": 0.0,
                "row_width_m": 0.0,
            }
        if qualifies_poi_counts(effective_counts) and bool(probe_metrics.get("poi_fit_feasible", True)):
            return row, auto_discovered, probe_metrics

    raise RuntimeError(
        "No discovered POI-rich roads remain valid after compose filtering "
        "(requires weighted POI score >= 2.0, at least 1 core POI, and a feasible POI-driven cross-section)."
    )


def prepare_workspace(
    dataset_profile: str,
    data_dir_text: str,
    artifacts_dir_text: str,
    real_manifest_text: str,
    real_mesh_root_text: str,
    real_latents_dir_text: str,
    num_assets: int,
    seed: int,
    latent_dim: int,
    model_name: str,
    model_dir_text: str,
    local_files_only: bool,
    device: str,
    shapee_model_dir_text: str,
    render_cache_dir_text: str,
    encode_mode: str,
    shapee_local_only: bool,
    layout_mode: str = "template",
    osm_cache_dir_text: str = "",
    force_reindex: bool = False,
    force_reencode: bool = False,
    force_osm_refresh: bool = False,
    aoi_bbox: Tuple[float, float, float, float] | None = None,
) -> PrepareWorkspaceResult:
    steps: List[StepResult] = []
    initial = inspect_workspace_readiness(
        dataset_profile=dataset_profile,
        data_dir_text=data_dir_text,
        artifacts_dir_text=artifacts_dir_text,
        real_manifest_text=real_manifest_text,
        model_dir_text=model_dir_text,
        real_latents_dir_text=real_latents_dir_text,
        layout_mode=layout_mode,
        aoi_bbox=aoi_bbox,
        osm_cache_dir_text=osm_cache_dir_text,
    )
    steps.append(
        StepResult(
            step="inspect_workspace_readiness",
            status="completed",
            message=initial.recommended_next_action,
            outputs=initial.to_dict(),
        )
    )
    manifest_step = prepare_manifest_assets(
        dataset_profile=dataset_profile,
        data_dir_text=data_dir_text,
        artifacts_dir_text=artifacts_dir_text,
        real_manifest_text=real_manifest_text,
        num_assets=num_assets,
        seed=seed,
        latent_dim=latent_dim,
    )
    steps.append(manifest_step)
    latent_step = prepare_latents_if_needed(
        dataset_profile=dataset_profile,
        real_manifest_text=real_manifest_text,
        real_mesh_root_text=real_mesh_root_text,
        real_latents_dir_text=real_latents_dir_text,
        shapee_model_dir_text=shapee_model_dir_text,
        render_cache_dir_text=render_cache_dir_text,
        encode_mode=encode_mode,
        device=device,
        shapee_local_only=shapee_local_only,
        force_reencode=force_reencode,
    )
    steps.append(latent_step)
    index_step = prepare_index_if_needed(
        dataset_profile=dataset_profile,
        data_dir_text=data_dir_text,
        artifacts_dir_text=artifacts_dir_text,
        real_manifest_text=real_manifest_text,
        num_assets=num_assets,
        seed=seed,
        latent_dim=latent_dim,
        model_name=model_name,
        model_dir_text=model_dir_text,
        local_files_only=local_files_only,
        device=device,
        force_reindex=force_reindex,
    )
    steps.append(index_step)
    osm_step = prepare_osm_cache_if_needed(
        layout_mode=layout_mode,
        artifacts_dir_text=artifacts_dir_text,
        osm_cache_dir_text=osm_cache_dir_text,
        force_osm_refresh=force_osm_refresh,
        aoi_bbox=aoi_bbox,
    )
    steps.append(osm_step)
    discovery_step = discover_poi_roads_if_needed(
        layout_mode=layout_mode,
        artifacts_dir_text=artifacts_dir_text,
        osm_cache_dir_text=osm_cache_dir_text,
        aoi_bbox=aoi_bbox,
    )
    steps.append(discovery_step)
    discovered_rows: List[Tuple[str, ...]] = []
    if discovery_step.outputs:
        _discovered_path = discovery_step.outputs.get("discovered_roads_path")
        if _discovered_path and Path(_discovered_path).exists():
            import json as _json
            for line in Path(_discovered_path).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = _json.loads(line)
                discovered_rows.append((
                    str(rec.get("osm_id", "")),
                    str(rec.get("highway_type", "")),
                    f"{rec.get('road_length_m', 0):.0f}",
                    str(rec.get("poi_count", 0)),
                    f"{float(rec.get('poi_score', poi_weighted_score(rec.get('poi_types', {})))):.1f}",
                    str(int(rec.get("core_poi_count", core_poi_count(rec.get("poi_types", {}))))),
                    str(rec.get("poi_breakdown", poi_breakdown_string(rec.get("poi_types", {})))),
                    f"({rec['bbox'][0]:.4f}, {rec['bbox'][1]:.4f}, {rec['bbox'][2]:.4f}, {rec['bbox'][3]:.4f})",
                ))
    discovery_count = len(discovered_rows)
    final_readiness = inspect_workspace_readiness(
        dataset_profile=dataset_profile,
        data_dir_text=data_dir_text,
        artifacts_dir_text=artifacts_dir_text,
        real_manifest_text=real_manifest_text,
        model_dir_text=model_dir_text,
        real_latents_dir_text=real_latents_dir_text,
        layout_mode=layout_mode,
        aoi_bbox=aoi_bbox,
        osm_cache_dir_text=osm_cache_dir_text,
    )
    summary = "\n".join(
        [
            "Prepare Workspace done.",
            f"- manifest_ok: {final_readiness.manifest_ok}",
            f"- latents_ok: {final_readiness.latents_ok}",
            f"- index_ok: {final_readiness.index_ok}",
            f"- osm_cache_ok: {final_readiness.osm_cache_ok}",
            f"- building_asset_count: {int(final_readiness.details.get('building_asset_count', 0) or 0)}",
            f"- discovered_roads: {discovery_count}",
            f"- recommended_next_action: {final_readiness.recommended_next_action}",
        ]
    )
    return PrepareWorkspaceResult(
        summary=summary,
        readiness=final_readiness,
        steps=tuple(steps),
        discovered_roads_rows=tuple(discovered_rows),
    )


def _readiness_cards(readiness: WorkspaceReadiness) -> List[List[str]]:
    return [
        ["manifest", "ok" if readiness.manifest_ok else "missing"],
        ["latents", "ok" if readiness.latents_ok else "missing"],
        ["index", "ok" if readiness.index_ok else "missing"],
        ["osm_cache", "ok" if readiness.osm_cache_ok else "missing"],
    ]


def _steps_table(result: PrepareWorkspaceResult) -> List[List[str]]:
    return [
        [step.step, step.status, step.message]
        for step in result.steps
    ]


def _extract_program_summary(layout_json_text: str) -> str:
    if not layout_json_text.strip():
        return "{}"
    payload = json.loads(layout_json_text)
    program = payload.get("street_program", {}) or {}
    runtime_summary = payload.get("summary", {}) or {}
    spatial_ctx = runtime_summary.get("spatial_context", {}) or {}
    bands = program.get("bands", []) or []
    poi_counts_all = _spatial_context_poi_counts(spatial_ctx)
    poi_counts = {poi_type: count for poi_type, count in poi_counts_all.items() if int(count) > 0}
    observed_poi_counts = program.get("observed_poi_counts") or runtime_summary.get("observed_poi_counts") or poi_counts
    summary = {
        "road_type": program.get("road_type", ""),
        "cross_section_type": program.get("cross_section_type", ""),
        "lane_count": program.get("lane_count", 0),
        "band_widths": {band.get("name", ""): band.get("width_m", 0.0) for band in bands},
        "furniture_requirements": program.get("furniture_requirements", {}),
        "control_points": program.get("control_points", []),
        "design_goals": program.get("design_goals", []),
        "selected_road_osm_id": runtime_summary.get("selected_road_osm_id"),
        "selected_road_discovered_poi_count": runtime_summary.get("selected_road_discovered_poi_count"),
        "selected_road_discovered_poi_score": runtime_summary.get("selected_road_discovered_poi_score"),
        "selected_road_discovered_core_poi_count": runtime_summary.get("selected_road_discovered_core_poi_count"),
        "selected_road_effective_poi_count": runtime_summary.get("selected_road_effective_poi_count", sum(poi_counts_all.values())),
        "selected_road_effective_poi_score": runtime_summary.get("selected_road_effective_poi_score", poi_weighted_score(poi_counts_all)),
        "selected_road_core_poi_count": runtime_summary.get("selected_road_core_poi_count", core_poi_count(poi_counts_all)),
        "poi_fit_feasible": runtime_summary.get("poi_fit_feasible", program.get("poi_fit_feasible", True)),
        "poi_fit_report": runtime_summary.get("poi_fit_report", program.get("poi_fit_report", {})),
        "selected_road_required_left_width_m": runtime_summary.get("selected_road_required_left_width_m"),
        "selected_road_required_right_width_m": runtime_summary.get("selected_road_required_right_width_m"),
        "selected_road_final_row_width_m": runtime_summary.get("selected_road_final_row_width_m", program.get("row_width_m")),
        "carriageway_width_m": runtime_summary.get("carriageway_width_m", program.get("road_width_m")),
        "left_clear_path_width_m": runtime_summary.get("left_clear_path_width_m", program.get("left_clear_path_width_m")),
        "right_clear_path_width_m": runtime_summary.get("right_clear_path_width_m", program.get("right_clear_path_width_m")),
        "left_furnishing_width_m": runtime_summary.get("left_furnishing_width_m", program.get("left_furnishing_width_m")),
        "right_furnishing_width_m": runtime_summary.get("right_furnishing_width_m", program.get("right_furnishing_width_m")),
        "row_width_m": runtime_summary.get("row_width_m", program.get("row_width_m")),
        "width_expanded": runtime_summary.get("width_expanded", program.get("width_expanded", False)),
        "width_reallocation_reason": runtime_summary.get("width_reallocation_reason", program.get("width_reallocation_reason", "")),
        "style_preset": runtime_summary.get("style_preset", program.get("context_conditions", {}).get("style_preset")),
        "beauty_mode": runtime_summary.get("beauty_mode", "presentation_v1"),
        "presentation_score": runtime_summary.get("presentation_score"),
        "style_coherence": runtime_summary.get("style_coherence"),
        "visual_clutter": runtime_summary.get("visual_clutter"),
        "spacing_rhythm": runtime_summary.get("spacing_rhythm"),
        "focal_readability": runtime_summary.get("focal_readability"),
        "theme_segments": runtime_summary.get("theme_segments", program.get("theme_segments", [])),
        "building_strategy_summary": program.get("building_strategy_summary", runtime_summary.get("building_summary", {})),
        "poi_counts": poi_counts,
        "observed_poi_counts": observed_poi_counts,
        "total_poi_points": sum(poi_counts_all.values()),
        "exclusion_zone_count": len(runtime_summary.get("poi_exclusion_zones", []) or []),
    }
    return json.dumps(summary, indent=2, ensure_ascii=True)


def _extract_theme_summary(layout_json_text: str) -> str:
    if not layout_json_text.strip():
        return "{}"
    payload = json.loads(layout_json_text)
    summary = payload.get("summary", {}) or {}
    diagnostics = summary.get("theme_diagnostics", {}) or {}
    result = {
        "theme_segments": summary.get("theme_segments", []),
        "theme_inference_mode": diagnostics.get("theme_inference_mode", ""),
        "theme_vocab_name": diagnostics.get("theme_vocab_name", ""),
        "zone_programs": diagnostics.get("zone_programs", []),
    }
    return json.dumps(result, indent=2, ensure_ascii=True)


def _extract_building_summary(layout_json_text: str) -> str:
    if not layout_json_text.strip():
        return "{}"
    payload = json.loads(layout_json_text)
    summary = payload.get("summary", {}) or {}
    result = {
        "building_generation_mode": summary.get("building_generation_mode", "footprint_based"),
        "building_summary": summary.get("building_summary", {}),
        "land_use_summary": summary.get("land_use_summary", {}),
        "lot_generation_summary": summary.get("lot_generation_summary", {}),
        "building_retrieval_coverage": summary.get("building_retrieval_coverage", {}),
        "building_footprint_count": len(payload.get("building_footprints", []) or []),
        "generated_lot_count": len(payload.get("generated_lots", []) or []),
        "building_placement_count": len(payload.get("building_placements", []) or []),
    }
    return json.dumps(result, indent=2, ensure_ascii=True)


def _extract_solver_summary(layout_json_text: str) -> str:
    if not layout_json_text.strip():
        return "{}"
    payload = json.loads(layout_json_text)
    summary = payload.get("summary", {}) or {}
    solver = payload.get("solver", {}) or {}
    result = {
        "layout_solver_used": summary.get("layout_solver_used", ""),
        "rule_satisfaction_rate": summary.get("rule_satisfaction_rate", 0.0),
        "topology_validity": summary.get("topology_validity", 0.0),
        "cross_section_feasibility": summary.get("cross_section_feasibility", 0.0),
        "editability": summary.get("editability", 0.0),
        "conflict_explainability": summary.get("conflict_explainability", 0.0),
        "fallback_reason": summary.get("solver_fallback_reason", ""),
        "edits": solver.get("edits", []),
        "conflicts": solver.get("conflicts", []),
    }
    return json.dumps(result, indent=2, ensure_ascii=True)


def _extract_presentation_views(layout_json_text: str):
    if not layout_json_text.strip():
        return [], "{}"
    try:
        payload = json.loads(layout_json_text)
        summary = payload.get("summary", {}) or {}
        render_views = summary.get("render_views", []) or []
        gallery_items = []
        for item in render_views:
            path = str(item.get("path", "")).strip()
            if not path:
                continue
            title = str(item.get("title", "")).strip() or str(item.get("name", "view")).strip() or "view"
            gallery_items.append((path, title))
        report = {
            "style_preset": summary.get("style_preset", ""),
            "beauty_mode": summary.get("beauty_mode", ""),
            "render_preset": summary.get("render_preset", ""),
            "presentation_score": summary.get("presentation_score", 0.0),
            "style_coherence": summary.get("style_coherence", 0.0),
            "visual_clutter": summary.get("visual_clutter", 0.0),
            "spacing_rhythm": summary.get("spacing_rhythm", 0.0),
            "focal_readability": summary.get("focal_readability", 0.0),
            "composition_report": summary.get("composition_report", {}),
            "render_views": [
                {
                    "name": item.get("name", ""),
                    "title": item.get("title", ""),
                    "path": item.get("path", ""),
                }
                for item in render_views
            ],
        }
        return gallery_items, json.dumps(report, indent=2, ensure_ascii=True)
    except Exception:
        return [], "{}"


def _format_production_step_summary(step: Dict[str, Any]) -> str:
    counts = step.get("counts", {}) or {}
    lines = [
        f"{int(step.get('index', 0)) + 1}. {str(step.get('title', '')).strip() or str(step.get('step_id', 'step'))}",
        f"- step_id: {str(step.get('step_id', '')).strip()}",
        f"- visible_instances: {int(counts.get('visible_instance_count', 0) or 0)}",
        f"- delta_instances: {len(step.get('delta_instance_ids', []) or [])}",
        f"- buildings: {int(counts.get('building_count', 0) or 0)}",
        f"- anchor_furniture: {int(counts.get('furniture_anchor_count', 0) or 0)}",
        f"- required_furniture: {int(counts.get('furniture_required_count', 0) or 0)}",
        f"- optional_furniture: {int(counts.get('furniture_optional_count', 0) or 0)}",
        f"- poi_points: {int(counts.get('poi_point_count', 0) or 0)}",
        f"- zoning_cells: {int(counts.get('zoning_cell_count', 0) or 0)}",
    ]
    companion_path = str(step.get("companion_path", "")).strip()
    if companion_path:
        lines.append(f"- companion: {companion_path}")
    return "\n".join(lines)


def _production_step_download_paths(step: Dict[str, Any]) -> List[str]:
    files: List[str] = []
    glb_path = str(step.get("glb_path", "")).strip()
    companion_path = str(step.get("companion_path", "")).strip()
    if glb_path:
        files.append(glb_path)
    if companion_path:
        files.append(companion_path)
    return files


def _select_production_step(production_steps: List[Dict[str, Any]] | None, step_index: float | int):
    steps = [dict(step) for step in list(production_steps or []) if isinstance(step, dict)]
    if not steps:
        return gr.update(label="Production Step"), "No production steps available.", None, None, []
    idx = max(0, min(int(step_index), len(steps) - 1))
    step = steps[idx]
    title = str(step.get("title", "")).strip() or str(step.get("step_id", "step"))
    model_path = str(step.get("glb_path", "")).strip() or None
    companion_path = str(step.get("companion_path", "")).strip() or None
    return (
        gr.update(label=f"Production Step: {title}"),
        _format_production_step_summary(step),
        model_path,
        companion_path,
        _production_step_download_paths(step),
    )


def _compute_nav_button_states(steps: list, idx: int):
    if not steps:
        return gr.update(interactive=False), gr.update(interactive=False)
    return gr.update(interactive=idx > 0), gr.update(interactive=idx < len(steps) - 1)


def _update_nav_button_states(production_steps: List[Dict[str, Any]] | None, step_index: float | int):
    steps = [dict(s) for s in list(production_steps or []) if isinstance(s, dict)]
    idx = max(0, min(int(step_index), len(steps) - 1)) if steps else 0
    return _compute_nav_button_states(steps, idx)


def _navigate_production_step(production_steps: List[Dict[str, Any]] | None, current_step: float | int, direction: int):
    steps = [dict(s) for s in list(production_steps or []) if isinstance(s, dict)]
    if not steps:
        return (gr.update(label="Production Step"), "No production steps available.", None, None, [],
                gr.update(interactive=False), gr.update(interactive=False))
    new_idx = max(0, min(int(current_step) + direction, len(steps) - 1))
    _slider_label, summary, model, companion, files = _select_production_step(steps, new_idx)
    title = str(steps[new_idx].get("title", "")).strip() or str(steps[new_idx].get("step_id", "step"))
    prev_update, next_update = _compute_nav_button_states(steps, new_idx)
    return gr.update(value=new_idx, label=f"Production Step: {title}"), summary, model, companion, files, prev_update, next_update


def _load_production_steps(layout_json_text: str):
    if not layout_json_text or not layout_json_text.strip():
        return [], gr.update(minimum=0, maximum=0, value=0, step=1, interactive=False, label="Production Step"), "No production steps available.", None, None, [], gr.update(interactive=False), gr.update(interactive=False)
    try:
        payload = json.loads(layout_json_text)
        steps = [dict(step) for step in payload.get("production_steps", []) or [] if isinstance(step, dict)]
        if not steps:
            outputs = payload.get("outputs", {}) or {}
            scene_glb = str(outputs.get("scene_glb", "")).strip() or None
            fallback_files = [path for path in (scene_glb, str(outputs.get("scene_layout", "")).strip()) if path]
            return (
                [],
                gr.update(minimum=0, maximum=0, value=0, step=1, interactive=False, label="Production Step"),
                "No production steps available.",
                scene_glb,
                None,
                fallback_files,
                gr.update(interactive=False),
                gr.update(interactive=False),
            )
        slider_label, summary, model_path, companion_path, files = _select_production_step(steps, 0)
        first_title = str(steps[0].get("title", "")).strip() or str(steps[0].get("step_id", "step"))
        return (
            steps,
            gr.update(
                minimum=0,
                maximum=max(len(steps) - 1, 0),
                value=0,
                step=1,
                interactive=bool(len(steps) > 1),
                label=f"Production Step: {first_title}",
            ),
            summary,
            model_path,
            companion_path,
            files,
            gr.update(interactive=False),
            gr.update(interactive=bool(len(steps) > 1)),
        )
    except Exception:
        return [], gr.update(minimum=0, maximum=0, value=0, step=1, interactive=False, label="Production Step"), "No production steps available.", None, None, [], gr.update(interactive=False), gr.update(interactive=False)


def run_prepare_workspace(
    dataset_profile: str,
    data_dir_text: str,
    artifacts_dir_text: str,
    real_manifest_text: str,
    real_mesh_root_text: str,
    real_latents_dir_text: str,
    num_assets: int,
    seed: int,
    latent_dim: int,
    model_name: str,
    model_dir_text: str,
    local_files_only: bool,
    device: str,
    shapee_model_dir_text: str,
    render_cache_dir_text: str,
    encode_mode: str,
    shapee_local_only: bool,
    layout_mode: str,
    osm_cache_dir_text: str,
    force_reindex: bool,
    force_reencode: bool,
    force_osm_refresh: bool,
    m5_bbox_min_lon: float,
    m5_bbox_min_lat: float,
    m5_bbox_max_lon: float,
    m5_bbox_max_lat: float,
) -> Tuple[str, str, List[List[str]], List[List[str]], List[List[str]]]:
    try:
        bbox = None
        if str(layout_mode).strip().lower() == "osm":
            bbox = (
                float(m5_bbox_min_lon),
                float(m5_bbox_min_lat),
                float(m5_bbox_max_lon),
                float(m5_bbox_max_lat),
            )
        result = prepare_workspace(
            dataset_profile=dataset_profile,
            data_dir_text=data_dir_text,
            artifacts_dir_text=artifacts_dir_text,
            real_manifest_text=real_manifest_text,
            real_mesh_root_text=real_mesh_root_text,
            real_latents_dir_text=real_latents_dir_text,
            num_assets=int(num_assets),
            seed=int(seed),
            latent_dim=int(latent_dim),
            model_name=model_name,
            model_dir_text=model_dir_text,
            local_files_only=local_files_only,
            device=device,
            shapee_model_dir_text=shapee_model_dir_text,
            render_cache_dir_text=render_cache_dir_text,
            encode_mode=encode_mode,
            shapee_local_only=shapee_local_only,
            layout_mode=layout_mode,
            osm_cache_dir_text=osm_cache_dir_text,
            force_reindex=force_reindex,
            force_reencode=force_reencode,
            force_osm_refresh=force_osm_refresh,
            aoi_bbox=bbox,
        )
        return (
            result.summary,
            json.dumps(result.readiness.to_dict(), indent=2, ensure_ascii=True),
            _readiness_cards(result.readiness),
            _steps_table(result),
            [list(row) for row in result.discovered_roads_rows],
        )
    except Exception as exc:
        detail = traceback.format_exc(limit=3)
        return f"Prepare workspace failed: {exc}\n{detail}", "{}", [], [], []


def prepare_assets_and_index(
    dataset_profile: str,
    data_dir_text: str,
    artifacts_dir_text: str,
    real_manifest_text: str,
    num_assets: int,
    seed: int,
    latent_dim: int,
    model_name: str,
    model_dir_text: str,
    local_files_only: bool,
    device: str,
) -> Tuple[str, List[List[str]]]:
    try:
        profile = dataset_profile.strip().lower()
        if profile not in {"mock", "real"}:
            return "dataset_profile must be mock or real", []

        data_dir = _to_path(data_dir_text)
        artifacts_dir = _to_path(artifacts_dir_text)
        model_dir = _to_path(model_dir_text) if model_dir_text.strip() else None
        if model_dir is not None and not model_dir.exists():
            return f"Model directory does not exist: {model_dir}", []

        if profile == "mock":
            rows = seed_assets(
                out_dir=data_dir,
                num_assets=int(num_assets),
                seed=int(seed),
                latent_dim=int(latent_dim),
            )
            assets_path = data_dir / "assets.jsonl"
            preview = [[row["asset_id"], row["description"], row["latent_path"]] for row in rows]
        else:
            manifest_path = _to_path(real_manifest_text)
            rows = _load_real_manifest_rows(manifest_path)
            assets_path = _write_assets_jsonl(rows, artifacts_dir / "real_assets_for_pipeline.jsonl")
            preview = [[row["asset_id"], row["description"], row["latent_path"]] for row in rows[:200]]

        _build_index_from_assets(
            assets_path=assets_path,
            artifacts_dir=artifacts_dir,
            model_name=model_name,
            model_dir=model_dir,
            local_files_only=local_files_only,
            device=device,
        )

        log = (
            "Prepared assets and FAISS index.\n"
            f"- profile: {profile}\n"
            f"- assets: {assets_path}\n"
            f"- embeddings: {artifacts_dir / 'asset_text_embeds.npy'}\n"
            f"- index: {artifacts_dir / 'index_ip.faiss'}\n"
            f"- count: {len(preview)}"
        )
        return log, preview
    except ModelLoadError as exc:
        return f"Model load error: {exc}", []
    except Exception as exc:
        detail = traceback.format_exc(limit=3)
        return f"Prepare failed: {exc}\n{detail}", []


def encode_real_latents(
    dataset_profile: str,
    real_manifest_text: str,
    real_mesh_root_text: str,
    real_latents_dir_text: str,
    shapee_model_dir_text: str,
    render_cache_dir_text: str,
    encode_mode: str,
    device: str,
    shapee_local_only: bool,
    skip_existing: bool,
    no_placeholder_fallback: bool,
    no_mesh_reference_fallback: bool,
    verbose: bool,
) -> str:
    started_at = datetime.now()
    try:
        profile = dataset_profile.strip().lower()
        if profile != "real":
            return "Encode skipped: Dataset Profile is not 'real'."

        manifest_path = _to_path(real_manifest_text)
        mesh_root = _to_path(real_mesh_root_text) if real_mesh_root_text.strip() else None
        latents_dir = _to_path(real_latents_dir_text)
        shapee_model_dir = _to_path(shapee_model_dir_text) if shapee_model_dir_text.strip() else None
        render_cache_dir = _to_path(render_cache_dir_text) if render_cache_dir_text.strip() else None

        stats = encode_shapee_latents(
            manifest_path=manifest_path,
            output_manifest=manifest_path,
            latents_dir=latents_dir,
            allow_placeholder_fallback=not bool(no_placeholder_fallback),
            dry_run=False,
            skip_existing=bool(skip_existing),
            device=device,
            shapee_model_dir=shapee_model_dir,
            shapee_local_only=bool(shapee_local_only),
            render_cache_dir=render_cache_dir,
            verbose=bool(verbose),
            mesh_root=mesh_root,
            allow_mesh_reference_fallback=not bool(no_mesh_reference_fallback),
            encode_mode=encode_mode,
        )
        duration_sec = time.time() - started_at.timestamp()
        return (
            "Real latent preparation done.\n"
            f"- started_at: {started_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"- duration_sec: {duration_sec:.2f}\n"
            f"- manifest: {manifest_path}\n"
            f"- encode_mode: {encode_mode}\n"
            f"- mesh_root: {mesh_root}\n"
            f"- latents_dir: {latents_dir}\n"
            f"- shapee_encoded: {stats['shapee_encoded']}\n"
            f"- mesh_reference_written: {stats['mesh_reference_written']}\n"
            f"- placeholder_written: {stats['placeholder_written']}\n"
            f"- skipped_existing: {stats['skipped_existing']}\n"
            f"- total_assets: {stats['total_assets']}"
        )
    except Exception as exc:
        duration_sec = time.time() - started_at.timestamp()
        detail = traceback.format_exc(limit=3)
        return (
            "Encode failed.\n"
            f"- started_at: {started_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"- duration_sec: {duration_sec:.2f}\n"
            f"- error: {exc}\n{detail}"
        )


def _encode_start_log(dataset_profile: str, encode_mode: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        "Real latent preparation started...\n"
        f"- started_at: {now}\n"
        f"- dataset_profile: {dataset_profile}\n"
        f"- encode_mode: {encode_mode}\n"
        "- status: running"
    )


def _build_decoder(
    decoder_choice: str,
    resolution: int,
    threshold: float,
    device: str,
    shapee_model_dir_text: str,
    shapee_strict: bool,
):
    placeholder = PlaceholderVoxelDecoder(resolution=resolution, threshold=threshold)
    if decoder_choice == "placeholder":
        return placeholder

    shapee_model_dir = _to_path(shapee_model_dir_text) if shapee_model_dir_text.strip() else None
    return ShapeEDecoder(
        resolution=resolution,
        threshold=threshold,
        device=device,
        model_dir=shapee_model_dir,
        strict=bool(shapee_strict),
        fallback_decoder=None if shapee_strict else placeholder,
        skip_voxel=True,  # Skip voxel conversion, use mesh directly
    )


def _resolve_assets_path(dataset_profile: str, data_dir: Path, artifacts_dir: Path, real_manifest_path: Path) -> Path:
    profile = dataset_profile.strip().lower()
    if profile == "mock":
        return data_dir / "assets.jsonl"

    cached_assets = artifacts_dir / "real_assets_for_pipeline.jsonl"
    if cached_assets.exists():
        try:
            if load_asset_records(cached_assets):
                return cached_assets
        except Exception:
            pass

    rows = _load_real_manifest_rows(real_manifest_path)
    return _write_assets_jsonl(rows, cached_assets)


def run_query_pipeline(
    dataset_profile: str,
    query: str,
    topk: int,
    data_dir_text: str,
    artifacts_dir_text: str,
    real_manifest_text: str,
    model_name: str,
    model_dir_text: str,
    local_files_only: bool,
    device: str,
    decoder_choice: str,
    shapee_model_dir_text: str,
    shapee_strict: bool,
    resolution: int,
    threshold: float,
    voxel_size: float,
    export_method: str,
    export_format: str,
) -> Tuple[str, List[List[str]], str, str | None, List[str]]:
    try:
        if not query.strip():
            return "Query cannot be empty.", [], "", None, []

        data_dir = _to_path(data_dir_text)
        artifacts_dir = _to_path(artifacts_dir_text)
        real_manifest_path = _to_path(real_manifest_text)
        model_dir = _to_path(model_dir_text) if model_dir_text.strip() else None
        if model_dir is not None and not model_dir.exists():
            return f"Model directory does not exist: {model_dir}", [], "", None, []

        assets_path = _resolve_assets_path(
            dataset_profile=dataset_profile,
            data_dir=data_dir,
            artifacts_dir=artifacts_dir,
            real_manifest_path=real_manifest_path,
        )

        embedder = ClipTextEmbedder(
            model_name=model_name,
            model_dir=model_dir,
            local_files_only=bool(local_files_only),
            device=device,
        )
        index_store = FaissIndexStore.load(
            index_path=artifacts_dir / "index_ip.faiss",
            id_map_path=artifacts_dir / "id_map.json",
        )
        latent_store = LatentStore(assets_jsonl_path=assets_path)
        decoder = _build_decoder(
            decoder_choice=decoder_choice,
            resolution=int(resolution),
            threshold=float(threshold),
            device=device,
            shapee_model_dir_text=shapee_model_dir_text,
            shapee_strict=bool(shapee_strict),
        )

        pipeline = M1Pipeline(
            embedder=embedder,
            index_store=index_store,
            latent_store=latent_store,
            decoder=decoder,
        )

        result, hits = pipeline.run(
            query=query,
            topk=int(topk),
            output_dir=artifacts_dir,
            voxel_size=float(voxel_size),
            export_method=export_method,
            export_format=export_format,
        )
        result_path = artifacts_dir / "pipeline_result.json"
        pipeline.save_result_json(result=result, hits=hits, out_path=result_path)

        summary = (
            "Pipeline done.\n"
            f"- profile: {dataset_profile}\n"
            f"- decoder: {result.outputs.get('decoder_used', decoder_choice)}\n"
            f"- top1: {result.top_hit.asset_id}\n"
            f"- score: {result.top_hit.score:.4f}\n"
            f"- occupied_voxels: {result.occupied_voxels}\n"
            f"- voxel_shape: {result.voxel_shape}\n"
            f"- result_json: {result_path}"
        )
        shapee_error = result.outputs.get("shapee_error", "")
        if shapee_error:
            summary += f"\n- shapee_error: {shapee_error}"
        hits_table = [[hit.asset_id, f"{hit.score:.6f}"] for hit in hits]
        result_json = json.dumps(result.to_dict(), indent=2, ensure_ascii=True)
        model_path = result.outputs.get("mesh_glb") or None
        files: List[str] = []
        if result.outputs.get("mesh_glb"):
            files.append(result.outputs["mesh_glb"])
        if result.outputs.get("mesh_ply"):
            files.append(result.outputs["mesh_ply"])
        return summary, hits_table, result_json, model_path, files
    except ModelLoadError as exc:
        return f"Model load error: {exc}", [], "", None, []
    except Exception as exc:
        detail = traceback.format_exc(limit=3)
        return f"Pipeline failed: {exc}\n{detail}", [], "", None, []


def run_street_compose(
    dataset_profile: str = "real",
    query: str = "",
    real_manifest_text: str = "",
    artifacts_dir_text: str = "",
    model_name: str = "openai/clip-vit-base-patch32",
    model_dir_text: str = "",
    local_files_only: bool = True,
    device: str = "auto",
    street_length_m: float = 80.0,
    street_road_width_m: float = 8.0,
    street_sidewalk_width_m: float = 2.5,
    street_lane_count: int = 2,
    street_density: float = 1.0,
    street_seed: int = 42,
    street_topk_per_category: int = 20,
    street_max_trials_per_slot: int = 30,
    export_format: str = "both",
    street_placement_policy: str = "rule",
    policy_ckpt_text: str = "",
    policy_temperature: float = 0.12,
    m5_layout_mode: str = "template",
    m5_constraint_mode: str = "soft",
    m5_constraint_weight: float = 0.45,
    m5_constraint_veto: float = 0.95,
    m5_bbox_min_lon: float = 0.0,
    m5_bbox_min_lat: float = 0.0,
    m5_bbox_max_lon: float = 0.0,
    m5_bbox_max_lat: float = 0.0,
    design_rule_profile: str = "balanced_complete_street_v1",
    program_generator: str = "heuristic_v1",
    layout_solver: str = "banded",
    program_ckpt_text: str = "",
    osm_cache_dir_text: str = "",
    city_context: str = "generic_city",
    target_street_type: str = "mixed_use",
    allow_solver_fallback: bool = True,
    segment_length_m: float = 12.0,
    road_selection: str = "primary_road",
    style_preset: str = "civic_clean_v1",
    beauty_mode: str = "presentation_v1",
    render_preset: str = "jury_default_v1",
    asset_curation_mode: str = "scene_ready_first",
    enable_surrounding_buildings: bool = True,
    building_search_topk: int = 5,
    theme_inference_mode: str = "deterministic_auto",
    theme_vocab_name: str = "fixed_v1",
    surrounding_building_mode: str = "footprint_based",
) -> Tuple[str, List[List[str]], str, str | None, List[str]]:
    try:
        profile = dataset_profile.strip().lower()
        if profile != "real":
            return "Street compose requires dataset_profile='real'.", [], "", None, []
        if not query.strip():
            return "Query cannot be empty.", [], "", None, []

        manifest_path = _to_path(real_manifest_text)
        artifacts_dir = _to_path(artifacts_dir_text)
        model_dir = _to_path(model_dir_text) if model_dir_text.strip() else None
        if model_dir is not None and not model_dir.exists():
            return f"Model directory does not exist: {model_dir}", [], "", None, []

        requested_bbox = (
            float(m5_bbox_min_lon),
            float(m5_bbox_min_lat),
            float(m5_bbox_max_lon),
            float(m5_bbox_max_lat),
        ) if str(m5_layout_mode).strip() == "osm" else None
        osm_cache_dir = _to_path(osm_cache_dir_text) if str(osm_cache_dir_text).strip() else (ROOT / "artifacts" / "m5" / "osm_cache").resolve()
        effective_bbox = requested_bbox
        effective_osm_id = None
        selected_discovered_poi_count = 0
        selected_discovered_poi_score = 0.0
        selected_discovered_core_poi_count = 0
        road_source = ""
        selected_required_left_width_m = 0.0
        selected_required_right_width_m = 0.0
        selected_final_row_width_m = 0.0
        if str(m5_layout_mode).strip() == "osm":
            selected_road, auto_discovered, probe_metrics = _select_auto_discovered_road(
                artifacts_dir=artifacts_dir,
                osm_cache_dir=osm_cache_dir,
                aoi_bbox=requested_bbox,
                seed=int(street_seed),
                road_width_m=float(street_road_width_m),
                sidewalk_width_m=float(street_sidewalk_width_m),
                lane_count=int(street_lane_count),
                road_selection=str(road_selection).strip(),
            )
            effective_bbox = tuple(float(v) for v in selected_road["bbox"])
            effective_osm_id = int(selected_road["osm_id"])
            selected_discovered_poi_count = int(selected_road.get("poi_count", 0))
            selected_discovered_poi_score = float(selected_road.get("poi_score", poi_weighted_score(selected_road.get("poi_types", {}))))
            selected_discovered_core_poi_count = int(selected_road.get("core_poi_count", core_poi_count(selected_road.get("poi_types", {}))))
            selected_required_left_width_m = float(probe_metrics.get("required_left_width_m", 0.0) or 0.0)
            selected_required_right_width_m = float(probe_metrics.get("required_right_width_m", 0.0) or 0.0)
            selected_final_row_width_m = float(probe_metrics.get("row_width_m", 0.0) or 0.0)
            if auto_discovered:
                road_source = "auto_discovered"

        config = StreetComposeConfig(
            query=query,
            length_m=float(street_length_m),
            road_width_m=float(street_road_width_m),
            sidewalk_width_m=float(street_sidewalk_width_m),
            lane_count=int(street_lane_count),
            density=float(street_density),
            seed=int(street_seed),
            topk_per_category=int(street_topk_per_category),
            max_trials_per_slot=int(street_max_trials_per_slot),
            layout_mode=str(m5_layout_mode).strip(),
            constraint_mode=str(m5_constraint_mode).strip(),
            aoi_bbox=effective_bbox,
            osm_cache_dir=str(osm_cache_dir),
            constraint_weight=float(m5_constraint_weight),
            constraint_veto_threshold=float(m5_constraint_veto),
            design_rule_profile=str(design_rule_profile).strip(),
            city_context=str(city_context).strip(),
            target_street_type=str(target_street_type).strip(),
            program_generator=str(program_generator).strip(),
            layout_solver=str(layout_solver).strip(),
            allow_solver_fallback=bool(allow_solver_fallback),
            segment_length_m=float(segment_length_m),
            road_selection=str(road_selection).strip(),
            selected_road_osm_id=effective_osm_id,
            selected_road_discovered_poi_count=selected_discovered_poi_count or None,
            selected_road_discovered_poi_score=selected_discovered_poi_score or None,
            selected_road_discovered_core_poi_count=selected_discovered_core_poi_count or None,
            style_preset=str(style_preset).strip(),
            beauty_mode=str(beauty_mode).strip(),
            render_preset=str(render_preset).strip(),
            asset_curation_mode=str(asset_curation_mode).strip(),
            enable_surrounding_buildings=bool(enable_surrounding_buildings),
            surrounding_building_mode=str(surrounding_building_mode).strip(),
            building_search_topk=int(building_search_topk),
            theme_inference_mode=str(theme_inference_mode).strip(),
            theme_vocab_name=str(theme_vocab_name).strip(),
        )
        result = compose_street_scene(
            config=config,
            manifest_path=manifest_path,
            artifacts_dir=artifacts_dir,
            model_name=model_name,
            model_dir=model_dir,
            local_files_only=bool(local_files_only),
            device=device,
            export_format=export_format,
            out_dir=artifacts_dir,
            placement_policy=street_placement_policy,
            policy_ckpt=_to_path(policy_ckpt_text) if policy_ckpt_text.strip() else None,
            program_ckpt=_to_path(program_ckpt_text) if program_ckpt_text.strip() else None,
            policy_temperature=float(policy_temperature),
        )

        layout_path = Path(result.outputs["scene_layout"])
        layout_json_text = layout_path.read_text(encoding="utf-8")
        layout_payload = json.loads(layout_json_text)
        layout_summary = layout_payload.get("summary", {})
        manifest_generator_types: Dict[str, str] = {}
        if manifest_path.exists():
            try:
                manifest_generator_types = {
                    str(row.get("asset_id", "")): str(row.get("generator_type", ""))
                    for row in _load_asset_library_rows(manifest_path)
                }
            except Exception:
                manifest_generator_types = {}
        instance_rows = [
            [
                placement.instance_id,
                placement.asset_id,
                placement.category,
                f"{placement.score:.6f}",
                f"{placement.position_xyz[0]:.3f}",
                f"{placement.position_xyz[2]:.3f}",
                f"{placement.yaw_deg:.2f}",
                placement.selection_source,
                manifest_generator_types.get(
                    placement.asset_id,
                    "procedural_fallback" if placement.selection_source == "procedural_fallback" else "",
                ),
            ]
            for placement in result.placements
        ]
        summary = (
            "Street compose done.\n"
            f"- query: {result.query}\n"
            f"- instance_count: {result.instance_count}\n"
            f"- dropped_slots: {result.dropped_slots}\n"
            f"- policy_used: {result.outputs.get('policy_used', street_placement_policy)}\n"
            f"- program_generator_used: {result.outputs.get('program_generator_used', program_generator)}\n"
            f"- layout_solver_used: {result.outputs.get('layout_solver_used', layout_solver)}\n"
            f"- cross_section_type: {layout_summary.get('cross_section_type', '')}\n"
            f"- style_preset: {layout_summary.get('style_preset', style_preset)}\n"
            f"- asset_curation_mode: {layout_summary.get('asset_curation_mode', asset_curation_mode)}\n"
            f"- parametric_instance_count: {int(layout_summary.get('parametric_instance_count', 0) or 0)}\n"
            f"- production_step_count: {int(layout_summary.get('production_step_count', 0) or 0)}\n"
            f"- presentation_score: {float(layout_summary.get('presentation_score', 0.0) or 0.0):.3f}\n"
            f"- scene_layout: {result.outputs.get('scene_layout', '')}"
        )
        # Show selected road and POI info
        if effective_osm_id is not None:
            summary += f"\n- selected_road_osm_id: {effective_osm_id}"
            summary += f"\n- selected_road_discovered_poi_count: {selected_discovered_poi_count}"
            summary += f"\n- selected_road_discovered_poi_score: {selected_discovered_poi_score:.1f}"
            summary += f"\n- selected_road_discovered_core_poi_count: {selected_discovered_core_poi_count}"
            summary += (
                f"\n- selected_road_effective_poi_count: "
                f"{int(layout_summary.get('selected_road_effective_poi_count', 0) or 0)}"
            )
            summary += (
                f"\n- selected_road_effective_poi_score: "
                f"{float(layout_summary.get('selected_road_effective_poi_score', 0.0) or 0.0):.1f}"
            )
            summary += (
                f"\n- selected_road_core_poi_count: "
                f"{int(layout_summary.get('selected_road_core_poi_count', 0) or 0)}"
            )
            summary += (
                f"\n- selected_road_required_left_width_m: "
                f"{float(layout_summary.get('selected_road_required_left_width_m', selected_required_left_width_m) or 0.0):.2f}"
            )
            summary += (
                f"\n- selected_road_required_right_width_m: "
                f"{float(layout_summary.get('selected_road_required_right_width_m', selected_required_right_width_m) or 0.0):.2f}"
            )
            summary += (
                f"\n- selected_road_final_row_width_m: "
                f"{float(layout_summary.get('selected_road_final_row_width_m', selected_final_row_width_m) or 0.0):.2f}"
            )
            summary += (
                f"\n- poi_fit_feasible: "
                f"{bool(layout_summary.get('poi_fit_feasible', True))}"
            )
        if effective_bbox is not None:
            summary += f"\n- road_bbox: ({effective_bbox[0]:.4f}, {effective_bbox[1]:.4f}, {effective_bbox[2]:.4f}, {effective_bbox[3]:.4f})"
        if road_source:
            summary += f"\n- road_source: {road_source}"
        poi_zones = layout_summary.get("poi_exclusion_zones", [])
        spatial_ctx = layout_summary.get("spatial_context", {})
        poi_counts = _spatial_context_poi_counts(spatial_ctx)
        n_poi_total = sum(poi_counts.values())
        if n_poi_total > 0:
            summary += f"\n- poi_count: {n_poi_total} ({poi_breakdown_string(poi_counts)})"
            summary += f"\n- exclusion_zones: {len(poi_zones)}"
        poi_conflicts = layout_summary.get("poi_conflict_assets", [])
        if poi_conflicts:
            summary += f"\n- poi_conflicts: {len(poi_conflicts)}"
        if result.outputs.get("policy_fallback_reason"):
            summary += f"\n- policy_fallback_reason: {result.outputs['policy_fallback_reason']}"
        if result.outputs.get("program_fallback_reason"):
            summary += f"\n- program_fallback_reason: {result.outputs['program_fallback_reason']}"
        if result.outputs.get("solver_fallback_reason"):
            summary += f"\n- solver_fallback_reason: {result.outputs['solver_fallback_reason']}"
        summary += f"\n- theme_segment_count: {len(layout_summary.get('theme_segments', []) or [])}"
        summary += f"\n- surrounding_buildings_enabled: {bool(enable_surrounding_buildings)}"
        summary += f"\n- building_generation_mode: {layout_summary.get('building_generation_mode', surrounding_building_mode)}"
        summary += (
            f"\n- building_footprint_count: "
            f"{int((layout_summary.get('building_retrieval_coverage', {}) or {}).get('footprint_count', 0) or 0)}"
        )
        summary += (
            f"\n- generated_lot_count: "
            f"{int((layout_summary.get('lot_generation_summary', {}) or {}).get('lot_count', 0) or 0)}"
        )
        summary += (
            f"\n- building_placed_count: "
            f"{int((layout_summary.get('building_retrieval_coverage', {}) or {}).get('placed_count', 0) or 0)}"
        )
        summary += (
            f"\n- zoning_cell_count: "
            f"{int((layout_summary.get('zoning_preview_summary', {}) or {}).get('cell_count', 0) or 0)}"
        )
        summary += (
            f"\n- asset_library_scene_instances: "
            f"{int(layout_summary.get('asset_library_scene_instances', 0) or 0)}"
        )
        model_path = result.outputs.get("scene_glb") or None
        files: List[str] = []
        if result.outputs.get("scene_glb"):
            files.append(result.outputs["scene_glb"])
        if result.outputs.get("scene_ply"):
            files.append(result.outputs["scene_ply"])
        if result.outputs.get("scene_layout"):
            files.append(result.outputs["scene_layout"])
        for item in layout_summary.get("render_views", []) or []:
            path = str(item.get("path", "")).strip()
            if path:
                files.append(path)
        return summary, instance_rows, layout_json_text, model_path, files
    except ModelLoadError as exc:
        return f"Model load error: {exc}", [], "", None, []
    except Exception as exc:
        detail = traceback.format_exc(limit=3)
        return f"Street compose failed: {exc}\n{detail}", [], "", None, []


def _render_spatial_overview(layout_json_text: str) -> Any:
    """Render scene spatial overview from the layout JSON output."""
    try:
        if not layout_json_text or not layout_json_text.strip():
            return None
        payload = json.loads(layout_json_text)
        summary = payload.get("summary", {})
        placements_raw = payload.get("placements", [])
        length_m = float(summary.get("length_m", 80.0))
        road_width_m = float(summary.get("road_width_m", 8.0))
        sidewalk_width_m = float(summary.get("sidewalk_width_m", 2.5))

        # Build a minimal config-like object for visualization
        class _Cfg:
            pass
        cfg = _Cfg()
        cfg.road_width_m = road_width_m
        cfg.length_m = length_m
        cfg.sidewalk_width_m = sidewalk_width_m

        sc_raw = summary.get("spatial_context", {})
        spatial_ctx = SpatialContext(
            junction_points_xz=tuple(tuple(p) for p in sc_raw.get("junction_points_xz", [])),
            entrance_points_xz=tuple(tuple(p) for p in sc_raw.get("entrance_points_xz", [])),
            road_half_width_m=float(sc_raw.get("road_half_width_m", road_width_m / 2)),
            length_m=float(sc_raw.get("length_m", length_m)),
            bus_stop_points_xz=tuple(tuple(p) for p in sc_raw.get("bus_stop_points_xz", [])),
            fire_points_xz=tuple(tuple(p) for p in sc_raw.get("fire_points_xz", [])),
            poi_points_by_type_xz={
                poi_type: tuple(tuple(p) for p in points)
                for poi_type, points in (sc_raw.get("poi_points_by_type_xz", {}) or {}).items()
            },
        )

        # Build lightweight placement objects
        class _P:
            def __init__(self, pos, cat):
                self.position_xyz = pos
                self.category = cat
        placements = [
            _P(p.get("position_xyz", [0, 0, 0]), p.get("category", ""))
            for p in placements_raw
        ]
        return plot_scene_with_markers(
            spatial_ctx, placements, cfg,
            osm_geometry=summary.get("osm_geometry"),
            poi_exclusion_zones=summary.get("poi_exclusion_zones"),
            poi_conflicts=summary.get("poi_conflict_assets"),
        )
    except Exception:
        return None


def _render_zoning_preview(layout_json_text: str) -> Any:
    """Render theme/building zoning grid from the layout JSON output."""
    try:
        if not layout_json_text or not layout_json_text.strip():
            return None
        payload = json.loads(layout_json_text)
        summary = payload.get("summary", {}) or {}
        return plot_zoning_grid_preview(
            payload.get("zoning_grid", []) or [],
            building_footprints=payload.get("building_footprints", []) or [],
            generated_lots=payload.get("generated_lots", []) or [],
            building_placements=payload.get("building_placements", []) or [],
            osm_geometry=summary.get("osm_geometry"),
        )
    except Exception:
        return None


def _render_distance_heatmap(layout_json_text: str, heatmap_type: str) -> Any:
    """Render a distance heatmap from the layout JSON output."""
    try:
        if not layout_json_text or not layout_json_text.strip():
            return None, None
        payload = json.loads(layout_json_text)
        summary = payload.get("summary", {})
        placements_raw = payload.get("placements", [])
        length_m = float(summary.get("length_m", 80.0))
        road_width_m = float(summary.get("road_width_m", 8.0))
        sidewalk_width_m = float(summary.get("sidewalk_width_m", 2.5))

        class _Cfg:
            pass
        cfg = _Cfg()
        cfg.road_width_m = road_width_m
        cfg.length_m = length_m
        cfg.sidewalk_width_m = sidewalk_width_m

        sc_raw = summary.get("spatial_context", {})
        spatial_ctx = SpatialContext(
            junction_points_xz=tuple(tuple(p) for p in sc_raw.get("junction_points_xz", [])),
            entrance_points_xz=tuple(tuple(p) for p in sc_raw.get("entrance_points_xz", [])),
            road_half_width_m=float(sc_raw.get("road_half_width_m", road_width_m / 2)),
            length_m=float(sc_raw.get("length_m", length_m)),
        )

        class _P:
            def __init__(self, pos, cat):
                self.position_xyz = pos
                self.category = cat
        placements = [
            _P(p.get("position_xyz", [0, 0, 0]), p.get("category", ""))
            for p in placements_raw
        ]
        hmap = plot_distance_heatmap(spatial_ctx, placements, str(heatmap_type), cfg)
        hist = plot_distance_histograms(placements, spatial_ctx)
        return hmap, hist
    except Exception:
        return None, None


def _extract_poi_summary(layout_json_text: str):
    """Extract POI exclusion zone and conflict tables from layout JSON."""
    try:
        if not layout_json_text or not layout_json_text.strip():
            return [], [], "{}"
        payload = json.loads(layout_json_text)
        summary = payload.get("summary", {})

        # POI summary table: [Type, X, Z, Radius(m), Rule]
        zones = summary.get("poi_exclusion_zones", [])
        spatial_ctx = summary.get("spatial_context", {})
        seen: set = set()
        poi_rows: list = []
        for z in zones:
            key = (z["poi_type"], round(z["position_xz"][0], 3), round(z["position_xz"][1], 3))
            if key in seen:
                continue
            seen.add(key)
            poi_rows.append([
                z["poi_type"],
                f'{z["position_xz"][0]:.2f}',
                f'{z["position_xz"][1]:.2f}',
                f'{z["radius_m"]:.2f}',
                z["rule_name"],
            ])
        if not poi_rows:
            fallback_sources = _spatial_context_poi_points(spatial_ctx)
            for poi_type, points in fallback_sources.items():
                for pt in points:
                    if len(pt) < 2:
                        continue
                    poi_rows.append([
                        poi_type,
                        f"{float(pt[0]):.2f}",
                        f"{float(pt[1]):.2f}",
                        "",
                        "poi_point",
                    ])

        # Conflict table: [Instance, Category, X, Z, Violated Rules, Penalty]
        conflicts = summary.get("poi_conflict_assets", [])
        conflict_rows = [
            [
                c.get("instance_id", ""),
                c.get("category", ""),
                f'{c["position_xz"][0]:.2f}',
                f'{c["position_xz"][1]:.2f}',
                ", ".join(c.get("violated_rules", [])),
                f'{c.get("constraint_penalty", 0.0):.4f}',
            ]
            for c in conflicts
        ]

        # Stats JSON
        type_counts: dict = {}
        for row in poi_rows:
            type_counts[row[0]] = type_counts.get(row[0], 0) + 1
        instance_count = summary.get("instance_count", 0)
        stats = {
            "poi_counts": type_counts,
            "total_poi_points": len(poi_rows),
            "conflict_count": len(conflict_rows),
            "compliance_rate": round(1.0 - len(conflict_rows) / max(instance_count, 1), 4),
        }
        return poi_rows, conflict_rows, json.dumps(stats, indent=2)
    except Exception:
        return [], [], "{}"


def _render_poi_overview(layout_json_text: str):
    """Render dedicated POI exclusion zone overview plot."""
    try:
        if not layout_json_text or not layout_json_text.strip():
            return None
        payload = json.loads(layout_json_text)
        summary = payload.get("summary", {})
        placements_raw = payload.get("placements", [])
        length_m = float(summary.get("length_m", 80.0))
        road_width_m = float(summary.get("road_width_m", 8.0))
        sidewalk_width_m = float(summary.get("sidewalk_width_m", 2.5))

        class _Cfg:
            pass
        cfg = _Cfg()
        cfg.road_width_m = road_width_m
        cfg.length_m = length_m
        cfg.sidewalk_width_m = sidewalk_width_m

        sc_raw = summary.get("spatial_context", {})
        spatial_ctx = SpatialContext(
            junction_points_xz=tuple(tuple(p) for p in sc_raw.get("junction_points_xz", [])),
            entrance_points_xz=tuple(tuple(p) for p in sc_raw.get("entrance_points_xz", [])),
            road_half_width_m=float(sc_raw.get("road_half_width_m", road_width_m / 2)),
            length_m=float(sc_raw.get("length_m", length_m)),
            bus_stop_points_xz=tuple(tuple(p) for p in sc_raw.get("bus_stop_points_xz", [])),
            fire_points_xz=tuple(tuple(p) for p in sc_raw.get("fire_points_xz", [])),
            poi_points_by_type_xz={
                poi_type: tuple(tuple(p) for p in points)
                for poi_type, points in (sc_raw.get("poi_points_by_type_xz", {}) or {}).items()
            },
        )

        class _P:
            def __init__(self, pos, cat):
                self.position_xyz = pos
                self.category = cat
        placements = [
            _P(p.get("position_xyz", [0, 0, 0]), p.get("category", ""))
            for p in placements_raw
        ]

        zones = summary.get("poi_exclusion_zones", [])
        conflicts = summary.get("poi_conflict_assets", [])
        has_poi_points = bool(spatial_ctx.poi_points_by_type_xz)
        if not zones and not has_poi_points:
            return None
        return plot_poi_exclusion_overview(
            spatial_ctx, placements, cfg,
            poi_exclusion_zones=zones,
            poi_conflicts=conflicts,
            osm_geometry=summary.get("osm_geometry"),
        )
    except Exception:
        return None


def _init_scene_graph_controls(layout_json_text: str):
    """Initialize Scene Graph controls and render the default Plotly view."""
    empty_poi = gr.update(choices=[], value=[])
    empty_categories = gr.update(choices=[], value=[])
    empty_edges = gr.update(choices=[], value=[])
    empty_heatmap_category = gr.update(choices=[], value=None)
    if not layout_json_text or not layout_json_text.strip():
        return (
            None,
            gr.update(choices=list(SCENE_GRAPH_NODE_TYPES), value=list(SCENE_GRAPH_NODE_TYPES)),
            empty_poi,
            empty_categories,
            empty_edges,
            empty_heatmap_category,
            gr.update(choices=["combined", "attraction", "repulsion"], value="combined"),
            gr.update(value=True),
            gr.update(value=0.55),
        )
    try:
        payload = json.loads(layout_json_text)
        state = scene_graph_control_state(payload)
        figure = plot_scene_graph(
            payload,
            node_layers=state["node_layers"],
            poi_types=state["poi_types"],
            categories=state["categories"],
            edge_types=state["edge_types"],
            heatmap_category=state["heatmap_category"],
            heatmap_layer=state["heatmap_layer"],
            show_heatmap=bool(state["show_heatmap"]),
            heatmap_opacity=float(state["heatmap_opacity"]),
        )
        return (
            figure,
            gr.update(choices=list(state["available_node_layers"]), value=list(state["node_layers"])),
            gr.update(choices=list(state["available_poi_types"]), value=list(state["poi_types"])),
            gr.update(choices=list(state["available_categories"]), value=list(state["categories"])),
            gr.update(choices=list(state["available_edge_types"]), value=list(state["edge_types"])),
            gr.update(
                choices=list(state["available_categories"]),
                value=state["heatmap_category"] if state["heatmap_category"] else None,
            ),
            gr.update(choices=["combined", "attraction", "repulsion"], value=state["heatmap_layer"]),
            gr.update(value=bool(state["show_heatmap"])),
            gr.update(value=float(state["heatmap_opacity"])),
        )
    except Exception:
        return (
            None,
            gr.update(choices=list(SCENE_GRAPH_NODE_TYPES), value=list(SCENE_GRAPH_NODE_TYPES)),
            empty_poi,
            empty_categories,
            empty_edges,
            empty_heatmap_category,
            gr.update(choices=["combined", "attraction", "repulsion"], value="combined"),
            gr.update(value=True),
            gr.update(value=0.55),
        )


def _render_scene_graph_from_controls(
    layout_json_text: str,
    graph_node_layers: List[str],
    graph_poi_types: List[str],
    graph_categories: List[str],
    graph_edge_types: List[str],
    heatmap_category: str,
    heatmap_layer: str,
    show_heatmap: bool,
    heatmap_opacity: float,
):
    """Render the Scene Graph Plotly figure from the active filter controls."""
    try:
        if not layout_json_text or not layout_json_text.strip():
            return None
        payload = json.loads(layout_json_text)
        return plot_scene_graph(
            payload,
            node_layers=list(graph_node_layers or []),
            poi_types=list(graph_poi_types or []),
            categories=list(graph_categories or []),
            edge_types=list(graph_edge_types or []),
            heatmap_category=str(heatmap_category or ""),
            heatmap_layer=str(heatmap_layer or "combined"),
            show_heatmap=bool(show_heatmap),
            heatmap_opacity=float(heatmap_opacity),
        )
    except Exception:
        return None


def run_best_model_street(
    dataset_profile: str = "real",
    query: str = "",
    real_manifest_text: str = "",
    artifacts_dir_text: str = "",
    model_name: str = "openai/clip-vit-base-patch32",
    model_dir_text: str = "",
    local_files_only: bool = True,
    device: str = "auto",
    street_length_m: float = 80.0,
    street_road_width_m: float = 8.0,
    street_sidewalk_width_m: float = 2.5,
    street_lane_count: int = 2,
    street_density: float = 1.0,
    street_seed: int = 42,
    street_topk_per_category: int = 20,
    street_max_trials_per_slot: int = 30,
    export_format: str = "both",
    policy_ckpt_text: str = "",
    policy_temperature: float = 0.12,
    m5_layout_mode: str = "template",
    m5_constraint_mode: str = "soft",
    m5_constraint_weight: float = 0.45,
    m5_constraint_veto: float = 0.95,
    m5_bbox_min_lon: float = 0.0,
    m5_bbox_min_lat: float = 0.0,
    m5_bbox_max_lon: float = 0.0,
    m5_bbox_max_lat: float = 0.0,
    design_rule_profile: str = "balanced_complete_street_v1",
    program_generator: str = "heuristic_v1",
    layout_solver: str = "banded",
    program_ckpt_text: str = "",
    osm_cache_dir_text: str = "",
    city_context: str = "generic_city",
    target_street_type: str = "mixed_use",
    allow_solver_fallback: bool = True,
    segment_length_m: float = 12.0,
    research_target: str = "layout_policy",
    road_selection: str = "primary_road",
    style_preset: str = "civic_clean_v1",
    beauty_mode: str = "presentation_v1",
    render_preset: str = "jury_default_v1",
    asset_curation_mode: str = "scene_ready_first",
    enable_surrounding_buildings: bool = True,
    building_search_topk: int = 5,
    theme_inference_mode: str = "deterministic_auto",
    theme_vocab_name: str = "fixed_v1",
    surrounding_building_mode: str = "footprint_based",
) -> Tuple[str, List[List[str]], str, str | None, List[str], str, str, str | None, List[str]]:
    if str(research_target).strip().lower() == "program_generator":
        program_generator = "learned_v1"
    summary, rows, layout_json, model_path, files = run_street_compose(
        dataset_profile=dataset_profile,
        query=query,
        real_manifest_text=real_manifest_text,
        artifacts_dir_text=artifacts_dir_text,
        model_name=model_name,
        model_dir_text=model_dir_text,
        local_files_only=local_files_only,
        device=device,
        street_length_m=street_length_m,
        street_road_width_m=street_road_width_m,
        street_sidewalk_width_m=street_sidewalk_width_m,
        street_lane_count=street_lane_count,
        street_density=street_density,
        street_seed=street_seed,
        street_topk_per_category=street_topk_per_category,
        street_max_trials_per_slot=street_max_trials_per_slot,
        export_format=export_format,
        street_placement_policy="learned",
        policy_ckpt_text=policy_ckpt_text,
        policy_temperature=policy_temperature,
        m5_layout_mode=m5_layout_mode,
        m5_constraint_mode=m5_constraint_mode,
        m5_constraint_weight=m5_constraint_weight,
        m5_constraint_veto=m5_constraint_veto,
        m5_bbox_min_lon=m5_bbox_min_lon,
        m5_bbox_min_lat=m5_bbox_min_lat,
        m5_bbox_max_lon=m5_bbox_max_lon,
        m5_bbox_max_lat=m5_bbox_max_lat,
        design_rule_profile=design_rule_profile,
        program_generator=program_generator,
        layout_solver=layout_solver,
        program_ckpt_text=program_ckpt_text,
        osm_cache_dir_text=osm_cache_dir_text,
        city_context=city_context,
        target_street_type=target_street_type,
        allow_solver_fallback=allow_solver_fallback,
        segment_length_m=segment_length_m,
        road_selection=road_selection,
        style_preset=style_preset,
        beauty_mode=beauty_mode,
        render_preset=render_preset,
        asset_curation_mode=asset_curation_mode,
        enable_surrounding_buildings=enable_surrounding_buildings,
        surrounding_building_mode=surrounding_building_mode,
        building_search_topk=building_search_topk,
        theme_inference_mode=theme_inference_mode,
        theme_vocab_name=theme_vocab_name,
    )
    best_log = (
        "Best model run done.\n"
        f"- policy_mode: learned\n"
        f"- program_generator: {program_generator}\n"
        f"- policy_ckpt: {policy_ckpt_text}\n"
        f"{summary}"
    )
    return (
        summary,
        rows,
        layout_json,
        model_path,
        files,
        best_log,
        layout_json,
        model_path,
        files,
    )


def run_m4_train_policy(
    dataset_profile: str,
    real_manifest_text: str,
    artifacts_dir_text: str,
    m4_artifacts_dir_text: str,
    m4_queries_text: str,
    model_name: str,
    model_dir_text: str,
    local_files_only: bool,
    device: str,
    street_length_m: float,
    street_road_width_m: float,
    street_sidewalk_width_m: float,
    street_lane_count: int,
    street_density: float,
    street_topk_per_category: int,
    street_max_trials_per_slot: int,
    m4_collect_seed_start: int,
    m4_collect_seed_end: int,
    m4_recollect_data: bool,
    m4_resume_training: bool,
    m4_train_epochs: int,
    m4_train_batch_size: int,
    m4_train_lr: float,
    m4_train_weight_decay: float,
    m4_train_entropy_weight: float,
    m4_train_patience: int,
    m4_run_eval_after_train: bool,
    m4_eval_seed_start: int,
    m4_eval_seed_end: int,
    export_format: str,
    policy_temperature: float,
    policy_ckpt_text: str,
) -> Iterator[Tuple[str, str, str, str, float, Any]]:
    started_at = datetime.now()
    profile = dataset_profile.strip().lower()
    if profile != "real":
        yield "M4 training requires dataset_profile='real'.", "{}", "{}", policy_ckpt_text, 0.0, []
        return

    log_lines = [
        "M4 training started...",
        f"- started_at: {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- recollect_data: {bool(m4_recollect_data)}",
        f"- resume_training: {bool(m4_resume_training)}",
        f"- run_eval_after_train: {bool(m4_run_eval_after_train)}",
        "- note: collect progress = distillation data collection, not model training loss.",
    ]
    train_json = "{}"
    eval_json = "{}"
    ckpt_out = policy_ckpt_text
    progress_percent = 0.0
    epoch_curve: List[Dict[str, float]] = []
    total_epochs = max(int(m4_train_epochs), 1)

    def _to_curve_plot(curve: List[Dict[str, float]]) -> Any:
        try:
            import plotly.graph_objects as go
        except Exception:
            # Keep fallback behavior if plotly is unavailable.
            if not curve:
                return None
            try:
                import matplotlib.pyplot as plt
            except Exception:
                return None
            epochs = [float(item.get("epoch", 0.0)) for item in curve]
            train_vals = [float(item.get("train_loss", 0.0)) for item in curve]
            val_vals = [float(item.get("val_loss", 0.0)) for item in curve]
            fig, ax = plt.subplots(figsize=(6.2, 3.2))
            ax.plot(epochs, train_vals, marker="o", linewidth=1.8, label="train_loss")
            ax.plot(epochs, val_vals, marker="s", linewidth=1.8, label="val_loss")
            ax.set_xlabel("epoch")
            ax.set_ylabel("loss")
            ax.grid(alpha=0.25)
            ax.legend(loc="best")
            fig.tight_layout()
            return fig

        fig = go.Figure()
        if curve:
            epochs = [float(item.get("epoch", 0.0)) for item in curve]
            train_vals = [float(item.get("train_loss", 0.0)) for item in curve]
            val_vals = [float(item.get("val_loss", 0.0)) for item in curve]
            fig.add_trace(
                go.Scatter(
                    x=epochs,
                    y=train_vals,
                    mode="lines+markers",
                    name="train_loss",
                    line={"width": 2},
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=epochs,
                    y=val_vals,
                    mode="lines+markers",
                    name="val_loss",
                    line={"width": 2},
                )
            )
            title = "M4 Train/Val Loss Curve"
        else:
            title = "M4 Train/Val Loss Curve (waiting first epoch...)"

        fig.update_layout(
            title=title,
            xaxis_title="epoch",
            yaxis_title="loss",
            template="plotly_white",
            margin={"l": 36, "r": 16, "t": 48, "b": 36},
            height=320,
        )
        return fig

    def _snapshot() -> Tuple[str, str, str, str, float, Any]:
        return (
            "\n".join(log_lines),
            train_json,
            eval_json,
            ckpt_out,
            float(progress_percent),
            _to_curve_plot(epoch_curve),
        )

    yield _snapshot()

    events: queue.Queue[Tuple[str, object]] = queue.Queue()
    done_event = threading.Event()

    def _worker() -> None:
        try:
            manifest_path = _to_path(real_manifest_text)
            artifacts_dir = _to_path(artifacts_dir_text)
            m4_artifacts_dir = _to_path(m4_artifacts_dir_text)
            model_dir = _to_path(model_dir_text) if model_dir_text.strip() else None
            queries_path = _to_path(m4_queries_text) if m4_queries_text.strip() else None
            policy_ckpt = (
                _to_path(policy_ckpt_text)
                if policy_ckpt_text.strip()
                else (m4_artifacts_dir / "layout_policy.pt")
            )
            resume_ckpt = policy_ckpt if bool(m4_resume_training) and policy_ckpt.exists() else None

            data_path = m4_artifacts_dir / "policy_train.jsonl"
            if bool(m4_recollect_data) or not data_path.exists():
                events.put(("log", f"- [phase:distill] collecting distilled data -> {data_path}"))
                collected_rows = collect_policy_data(
                    manifest=manifest_path,
                    artifacts=artifacts_dir,
                    out=data_path,
                    queries_path=queries_path if (queries_path and queries_path.exists()) else None,
                    seed_start=int(m4_collect_seed_start),
                    seed_end=int(m4_collect_seed_end),
                    model_name=model_name,
                    model_dir=model_dir,
                    local_files_only=bool(local_files_only),
                    device=device,
                    length_m=float(street_length_m),
                    road_width_m=float(street_road_width_m),
                    sidewalk_width_m=float(street_sidewalk_width_m),
                    lane_count=int(street_lane_count),
                    density=float(street_density),
                    topk_per_category=int(street_topk_per_category),
                    max_trials_per_slot=int(street_max_trials_per_slot),
                    progress_callback=lambda payload: events.put(("collect_progress", payload)),
                )
                events.put(("log", f"- [phase:distill] collected_rows: {len(collected_rows)}"))
            else:
                events.put(("log", f"- [phase:distill] reuse existing distilled data: {data_path}"))

            events.put(("log", "- [phase:train] training policy model..."))
            train_summary = train_from_jsonl(
                data_path=data_path,
                out_dir=m4_artifacts_dir,
                config=PolicyTrainConfig(
                    epochs=int(m4_train_epochs),
                    batch_size=int(m4_train_batch_size),
                    lr=float(m4_train_lr),
                    weight_decay=float(m4_train_weight_decay),
                    entropy_weight=float(m4_train_entropy_weight),
                    patience=int(m4_train_patience),
                    device=device,
                ),
                resume_ckpt=resume_ckpt,
                progress_callback=lambda payload: events.put(("epoch", payload)),
            )
            events.put(("train_summary", train_summary))

            if bool(m4_run_eval_after_train):
                events.put(("eval_start", None))
                events.put(("log", "- [phase:eval] running engineering eval (learned vs rule)..."))
                eval_args = argparse.Namespace(
                    queries=queries_path if (queries_path and queries_path.exists()) else (ROOT / "data/eval/queries_m4.txt"),
                    manifest=manifest_path,
                    artifacts=artifacts_dir,
                    out_dir=m4_artifacts_dir,
                    model_name=model_name,
                    model_dir=model_dir,
                    local_files_only=bool(local_files_only),
                    device=device,
                    placement_policy="learned",
                    policy_ckpt=Path(str(train_summary["outputs"]["checkpoint"])),
                    policy_temperature=float(policy_temperature),
                    compare_rule=True,
                    seed_start=int(m4_eval_seed_start),
                    seed_end=int(m4_eval_seed_end),
                    length_m=float(street_length_m),
                    road_width_m=float(street_road_width_m),
                    sidewalk_width_m=float(street_sidewalk_width_m),
                    lane_count=int(street_lane_count),
                    density=float(street_density),
                    topk_per_category=int(street_topk_per_category),
                    max_trials_per_slot=int(street_max_trials_per_slot),
                    export_format=export_format,
                )
                eval_report = run_m4_eval(eval_args)
                events.put(("eval_report", eval_report))

            events.put(("done", None))
        except ModelLoadError as exc:
            events.put(("error", f"Model load error: {exc}"))
        except Exception as exc:
            detail = traceback.format_exc(limit=4)
            events.put(("error", f"M4 train failed.\n- error: {exc}\n{detail}"))
        finally:
            done_event.set()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    last_collect_log_step = -1

    while not done_event.is_set() or not events.empty():
        try:
            event, payload = events.get(timeout=0.2)
        except queue.Empty:
            continue

        if event == "log":
            log_lines.append(str(payload))
        elif event == "collect_progress":
            info = payload if isinstance(payload, dict) else {}
            ratio = float(info.get("ratio", 0.0))
            ratio = min(max(ratio, 0.0), 1.0)
            progress_percent = max(progress_percent, 5.0 + 40.0 * ratio)
            step = int(ratio * 20.0)  # 5% granularity
            if step > last_collect_log_step:
                processed = int(float(info.get("processed_slots", 0.0)))
                total = int(float(info.get("total_slots", 1.0)))
                log_lines.append(
                    f"- distill progress (not training): {processed}/{total} ({ratio * 100.0:.1f}%)"
                )
                last_collect_log_step = step
            train_json = json.dumps(
                {
                    "status": "distill_collecting",
                    "collect_ratio": ratio,
                    "processed_slots": int(float(info.get("processed_slots", 0.0))),
                    "total_slots": int(float(info.get("total_slots", 0.0))),
                },
                indent=2,
                ensure_ascii=True,
            )
        elif event == "epoch":
            info = payload if isinstance(payload, dict) else {}
            epoch = int(float(info.get("epoch", 0.0)))
            train_loss = float(info.get("train_loss", 0.0))
            val_loss = float(info.get("val_loss", 0.0))
            best_so_far = float(info.get("best_val_loss_so_far", val_loss))
            epoch_curve.append(
                {
                    "epoch": float(epoch),
                    "train_loss": train_loss,
                    "val_loss": val_loss,
                    "best_val_loss_so_far": best_so_far,
                }
            )
            log_lines.append(
                f"- epoch {epoch}: train_loss={train_loss:.6f}, val_loss={val_loss:.6f}, best={best_so_far:.6f}"
            )
            progress_percent = max(progress_percent, 45.0 + 45.0 * min(float(epoch) / float(total_epochs), 1.0))
            train_json = json.dumps(
                {
                    "status": "training",
                    "latest_epoch": epoch,
                    "latest_train_loss": train_loss,
                    "latest_val_loss": val_loss,
                    "curve_tail": epoch_curve[-20:],
                },
                indent=2,
                ensure_ascii=True,
            )
        elif event == "train_summary":
            summary = payload if isinstance(payload, dict) else {}
            ckpt_out = str(summary.get("outputs", {}).get("checkpoint", ckpt_out))
            train_json = json.dumps(summary, indent=2, ensure_ascii=True)
            log_lines.append(f"- training done: {ckpt_out}")
        elif event == "eval_report":
            report = payload if isinstance(payload, dict) else {}
            eval_json = json.dumps(report, indent=2, ensure_ascii=True)
            log_lines.append("- [phase:eval] done.")
            progress_percent = max(progress_percent, 99.0)
        elif event == "eval_start":
            progress_percent = max(progress_percent, 92.0)
        elif event == "error":
            log_lines.append(str(payload))
            progress_percent = max(progress_percent, 100.0)
        elif event == "done":
            duration_sec = time.time() - started_at.timestamp()
            log_lines.append(f"- duration_sec: {duration_sec:.2f}")
            progress_percent = 100.0

        yield _snapshot()


def run_m6_train_program(
    dataset_profile: str,
    real_manifest_text: str,
    m6_artifacts_dir_text: str,
    m4_queries_text: str,
    device: str,
    street_length_m: float,
    street_road_width_m: float,
    street_sidewalk_width_m: float,
    street_lane_count: int,
    street_density: float,
    street_topk_per_category: int,
    street_max_trials_per_slot: int,
    design_rule_profile: str,
    layout_mode: str,
    m5_bbox_min_lon: float,
    m5_bbox_min_lat: float,
    m5_bbox_max_lon: float,
    m5_bbox_max_lat: float,
    program_train_epochs: int,
    program_train_batch_size: int,
    program_train_lr: float,
    program_train_weight_decay: float,
    program_train_patience: int,
    program_ckpt_text: str,
    policy_ckpt_text: str = "",
) -> Iterator[Tuple[str, str, str, str, str, float, Any]]:
    started_at = datetime.now()
    profile = dataset_profile.strip().lower()
    if profile != "real":
        yield "Program training requires dataset_profile='real'.", "{}", "{}", policy_ckpt_text, program_ckpt_text, 0.0, None
        return

    def _curve_plot(curve: List[Dict[str, float]]) -> Any:
        try:
            import plotly.graph_objects as go
        except Exception:
            if not curve:
                return None
            try:
                import matplotlib.pyplot as plt
            except Exception:
                return None
            epochs = [float(item.get("epoch", 0.0)) for item in curve]
            train_vals = [float(item.get("train_loss", 0.0)) for item in curve]
            val_vals = [float(item.get("val_loss", 0.0)) for item in curve]
            fig, ax = plt.subplots(figsize=(6.2, 3.2))
            ax.plot(epochs, train_vals, marker="o", linewidth=1.8, label="train_loss")
            ax.plot(epochs, val_vals, marker="s", linewidth=1.8, label="val_loss")
            ax.set_xlabel("epoch")
            ax.set_ylabel("loss")
            ax.set_title("M6 Train/Val Loss Curve")
            ax.grid(alpha=0.25)
            ax.legend(loc="best")
            fig.tight_layout()
            return fig

        fig = go.Figure()
        if curve:
            fig.add_trace(go.Scatter(x=[c["epoch"] for c in curve], y=[c["train_loss"] for c in curve], mode="lines+markers", name="train_loss", line={"width": 2}))
            fig.add_trace(go.Scatter(x=[c["epoch"] for c in curve], y=[c["val_loss"] for c in curve], mode="lines+markers", name="val_loss", line={"width": 2}))
            title = "M6 Train/Val Loss Curve"
        else:
            title = "M6 Train/Val Loss Curve (waiting first epoch...)"
        fig.update_layout(title=title, xaxis_title="epoch", yaxis_title="loss", template="plotly_white", height=320, margin={"l": 36, "r": 16, "t": 48, "b": 36})
        return fig

    log_lines = [
        "M6 program training started...",
        f"- started_at: {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    train_json = "{}"
    eval_json = "{}"
    policy_ckpt_out = policy_ckpt_text
    program_ckpt_out = program_ckpt_text
    progress_percent = 0.0
    curve: List[Dict[str, float]] = []

    def _snapshot() -> Tuple[str, str, str, str, str, float, Any]:
        return (
            "\n".join(log_lines),
            train_json,
            eval_json,
            policy_ckpt_out,
            program_ckpt_out,
            float(progress_percent),
            _curve_plot(curve),
        )

    yield _snapshot()

    total_epochs = max(int(program_train_epochs), 1)
    events: queue.Queue[Tuple[str, object]] = queue.Queue()
    done_event = threading.Event()
    last_collect_log_step = -1

    def _worker() -> None:
        try:
            m6_artifacts_dir = _to_path(m6_artifacts_dir_text)
            queries_path = _to_path(m4_queries_text) if m4_queries_text.strip() else (ROOT / "data/eval/queries_m4.txt")
            program_ckpt = _to_path(program_ckpt_text) if program_ckpt_text.strip() else (m6_artifacts_dir / "program_generator.pt")
            bbox = None
            if str(layout_mode).strip().lower() == "osm":
                bbox = (float(m5_bbox_min_lon), float(m5_bbox_min_lat), float(m5_bbox_max_lon), float(m5_bbox_max_lat))
            collect_args = argparse.Namespace(
                manifest=_to_path(real_manifest_text),
                out=m6_artifacts_dir / "program_train.jsonl",
                queries=queries_path,
                layout_modes=[str(layout_mode).strip().lower()],
                constraint_profiles=[
                    "balanced_complete_street_v1",
                    "pedestrian_priority_v1",
                    "transit_priority_v1",
                    "noise_aware_v1",
                ],
                seed_start=0,
                seed_end=29,
                length_m=float(street_length_m),
                road_width_m=float(street_road_width_m),
                sidewalk_width_m=float(street_sidewalk_width_m),
                lane_count=int(street_lane_count),
                density=float(street_density),
                topk_per_category=int(street_topk_per_category),
                max_trials_per_slot=int(street_max_trials_per_slot),
                layout_solver="milp_template_v1",
                osm_bboxes_jsonl=None,
                osm_cache_dir=_to_path(str((ROOT / "artifacts/m5/osm_cache").resolve())),
            )
            if bbox is not None:
                bbox_file = m6_artifacts_dir / "bbox.jsonl"
                bbox_file.parent.mkdir(parents=True, exist_ok=True)
                bbox_file.write_text(json.dumps({"bbox": list(bbox)}, ensure_ascii=True) + "\n", encoding="utf-8")
                collect_args.osm_bboxes_jsonl = bbox_file

            events.put(("log", "- [phase:distill] collecting program data..."))
            rows = collect_program_data(
                collect_args,
                progress_callback=lambda payload: events.put(("collect_progress", payload)),
            )
            events.put(("log", f"- [phase:distill] collected_rows: {len(rows)}"))

            events.put(("log", "- [phase:train] training program generator..."))
            train_summary = train_program_from_jsonl(
                data_path=m6_artifacts_dir / "program_train.jsonl",
                out_dir=m6_artifacts_dir,
                config=ProgramTrainConfig(
                    epochs=int(program_train_epochs),
                    batch_size=int(program_train_batch_size),
                    lr=float(program_train_lr),
                    weight_decay=float(program_train_weight_decay),
                    patience=int(program_train_patience),
                    device=device,
                ),
                resume_ckpt=program_ckpt if program_ckpt.exists() else None,
                progress_callback=lambda payload: events.put(("epoch", payload)),
            )
            events.put(("train_summary", train_summary))
            events.put(("done", None))
        except Exception as exc:
            detail = traceback.format_exc(limit=4)
            events.put(("error", f"M6 train failed.\n- error: {exc}\n{detail}"))
        finally:
            done_event.set()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    while not done_event.is_set() or not events.empty():
        try:
            event, payload = events.get(timeout=0.2)
        except queue.Empty:
            continue

        if event == "log":
            log_lines.append(str(payload))

        elif event == "collect_progress":
            info = payload if isinstance(payload, dict) else {}
            ratio = min(max(float(info.get("ratio", 0.0)), 0.0), 1.0)
            progress_percent = max(progress_percent, 35.0 * ratio)
            step = int(ratio * 20.0)
            if step > last_collect_log_step:
                processed = int(float(info.get("processed_slots", 0)))
                total = int(float(info.get("total_slots", 1)))
                log_lines.append(
                    f"- distill progress (not training): {processed}/{total} ({ratio * 100.0:.1f}%)"
                )
                last_collect_log_step = step
            train_json = json.dumps(
                {
                    "status": "distill_collecting",
                    "collect_ratio": ratio,
                    "processed_slots": int(float(info.get("processed_slots", 0))),
                    "total_slots": int(float(info.get("total_slots", 0))),
                },
                indent=2,
                ensure_ascii=True,
            )

        elif event == "epoch":
            info = payload if isinstance(payload, dict) else {}
            epoch = int(float(info.get("epoch", 0)))
            train_loss = float(info.get("train_loss", 0.0))
            val_loss = float(info.get("val_loss", 0.0))
            best_so_far = float(info.get("best_val_loss_so_far", val_loss))
            curve.append(
                {
                    "epoch": float(epoch),
                    "train_loss": train_loss,
                    "val_loss": val_loss,
                    "best_val_loss_so_far": best_so_far,
                }
            )
            log_lines.append(
                f"- epoch {epoch}: train_loss={train_loss:.6f}, val_loss={val_loss:.6f}, best={best_so_far:.6f}"
            )
            progress_percent = max(progress_percent, 35.0 + 55.0 * min(float(epoch) / float(total_epochs), 1.0))
            train_json = json.dumps(
                {
                    "status": "training",
                    "latest_epoch": epoch,
                    "latest_train_loss": train_loss,
                    "latest_val_loss": val_loss,
                    "curve_tail": curve[-20:],
                },
                indent=2,
                ensure_ascii=True,
            )

        elif event == "train_summary":
            summary = payload if isinstance(payload, dict) else {}
            program_ckpt_out = str(summary.get("outputs", {}).get("checkpoint", program_ckpt_out))
            train_json = json.dumps(summary, indent=2, ensure_ascii=True)
            eval_json = json.dumps(
                {
                    "status": "trained",
                    "best_val_loss": summary.get("best_val_loss", 0.0),
                    "split": summary.get("split", {}),
                },
                indent=2,
                ensure_ascii=True,
            )
            log_lines.append(f"- checkpoint: {program_ckpt_out}")

        elif event == "error":
            log_lines.append(str(payload))
            progress_percent = 100.0

        elif event == "done":
            duration_sec = (datetime.now() - started_at).total_seconds()
            log_lines.append(f"- duration_sec: {duration_sec:.2f}")
            progress_percent = 100.0

        yield _snapshot()


def run_research_train(
    research_target: str,
    dataset_profile: str,
    real_manifest_text: str,
    artifacts_dir_text: str,
    m4_artifacts_dir_text: str,
    m6_artifacts_dir_text: str,
    m4_queries_text: str,
    model_name: str,
    model_dir_text: str,
    local_files_only: bool,
    device: str,
    street_length_m: float,
    street_road_width_m: float,
    street_sidewalk_width_m: float,
    street_lane_count: int,
    street_density: float,
    street_topk_per_category: int,
    street_max_trials_per_slot: int,
    design_rule_profile: str,
    layout_mode: str,
    m5_bbox_min_lon: float,
    m5_bbox_min_lat: float,
    m5_bbox_max_lon: float,
    m5_bbox_max_lat: float,
    m4_collect_seed_start: int,
    m4_collect_seed_end: int,
    m4_recollect_data: bool,
    m4_resume_training: bool,
    m4_train_epochs: int,
    m4_train_batch_size: int,
    m4_train_lr: float,
    m4_train_weight_decay: float,
    m4_train_entropy_weight: float,
    m4_train_patience: int,
    m4_run_eval_after_train: bool,
    m4_eval_seed_start: int,
    m4_eval_seed_end: int,
    export_format: str,
    policy_temperature: float,
    policy_ckpt_text: str,
    program_train_epochs: int,
    program_train_batch_size: int,
    program_train_lr: float,
    program_train_weight_decay: float,
    program_train_patience: int,
    program_ckpt_text: str,
) -> Iterator[Tuple[str, str, str, str, str, float, Any]]:
    if str(research_target).strip().lower() == "program_generator":
        yield from run_m6_train_program(
            dataset_profile=dataset_profile,
            real_manifest_text=real_manifest_text,
            m6_artifacts_dir_text=m6_artifacts_dir_text,
            m4_queries_text=m4_queries_text,
            device=device,
            street_length_m=street_length_m,
            street_road_width_m=street_road_width_m,
            street_sidewalk_width_m=street_sidewalk_width_m,
            street_lane_count=street_lane_count,
            street_density=street_density,
            street_topk_per_category=street_topk_per_category,
            street_max_trials_per_slot=street_max_trials_per_slot,
            design_rule_profile=design_rule_profile,
            layout_mode=layout_mode,
            m5_bbox_min_lon=m5_bbox_min_lon,
            m5_bbox_min_lat=m5_bbox_min_lat,
            m5_bbox_max_lon=m5_bbox_max_lon,
            m5_bbox_max_lat=m5_bbox_max_lat,
            program_train_epochs=program_train_epochs,
            program_train_batch_size=program_train_batch_size,
            program_train_lr=program_train_lr,
            program_train_weight_decay=program_train_weight_decay,
            program_train_patience=program_train_patience,
            program_ckpt_text=program_ckpt_text,
            policy_ckpt_text=policy_ckpt_text,
        )
        return

    generator = run_m4_train_policy(
        dataset_profile=dataset_profile,
        real_manifest_text=real_manifest_text,
        artifacts_dir_text=artifacts_dir_text,
        m4_artifacts_dir_text=m4_artifacts_dir_text,
        m4_queries_text=m4_queries_text,
        model_name=model_name,
        model_dir_text=model_dir_text,
        local_files_only=local_files_only,
        device=device,
        street_length_m=street_length_m,
        street_road_width_m=street_road_width_m,
        street_sidewalk_width_m=street_sidewalk_width_m,
        street_lane_count=street_lane_count,
        street_density=street_density,
        street_topk_per_category=street_topk_per_category,
        street_max_trials_per_slot=street_max_trials_per_slot,
        m4_collect_seed_start=m4_collect_seed_start,
        m4_collect_seed_end=m4_collect_seed_end,
        m4_recollect_data=m4_recollect_data,
        m4_resume_training=m4_resume_training,
        m4_train_epochs=m4_train_epochs,
        m4_train_batch_size=m4_train_batch_size,
        m4_train_lr=m4_train_lr,
        m4_train_weight_decay=m4_train_weight_decay,
        m4_train_entropy_weight=m4_train_entropy_weight,
        m4_train_patience=m4_train_patience,
        m4_run_eval_after_train=m4_run_eval_after_train,
        m4_eval_seed_start=m4_eval_seed_start,
        m4_eval_seed_end=m4_eval_seed_end,
        export_format=export_format,
        policy_temperature=policy_temperature,
        policy_ckpt_text=policy_ckpt_text,
    )
    for log_text, train_json, eval_json, policy_ckpt_value, progress, plot in generator:
        yield log_text, train_json, eval_json, policy_ckpt_value, program_ckpt_text, progress, plot


def _toggle_osm_visibility(layout_mode: str):
    vis = str(layout_mode).strip().lower() == "osm"
    return gr.update(visible=vis), gr.update(visible=vis)


def _on_city_selected(city_name_en: str):
    """Fill bbox fields from the selected city."""
    if not city_name_en:
        return gr.update(), gr.update(), gr.update(), gr.update()
    city = get_city_by_name(city_name_en)
    if city is None:
        return gr.update(), gr.update(), gr.update(), gr.update()
    return city.bbox[0], city.bbox[1], city.bbox[2], city.bbox[3]


def build_demo() -> gr.Blocks:
    default_data = str((ROOT / "data/m1").resolve())
    default_artifacts = str((ROOT / "artifacts/real").resolve())
    default_model_dir = str((ROOT / "models/clip-vit-base-patch32").resolve())
    default_shapee_model_dir = str((ROOT / "models/shapee").resolve())
    default_real_manifest = str((ROOT / "data/real/real_assets_manifest.jsonl").resolve())
    default_real_mesh_root = str((ROOT / "data/real/meshes").resolve())
    default_real_latents_dir = str((ROOT / "data/real/latents").resolve())
    default_render_cache_dir = str((ROOT / "artifacts/real/shapee_render_cache").resolve())
    default_m4_artifacts_dir = str((ROOT / "artifacts/m4").resolve())
    default_m6_artifacts_dir = str((ROOT / "artifacts/m6").resolve())
    default_m4_queries = str((ROOT / "data/eval/queries_m4.txt").resolve())
    default_policy_ckpt = str((ROOT / "artifacts/m4/layout_policy.pt").resolve())
    default_program_ckpt = str((ROOT / "artifacts/m6/program_generator.pt").resolve())
    default_osm_cache_dir = str((ROOT / "artifacts/m5/osm_cache").resolve())
    default_parametric_preview_dir = str((ROOT / "artifacts/real/parametric_preview").resolve())

    with gr.Blocks(title="RoadGen3D POI-Driven Street Workbench", js=TIMELINE_KEYBOARD_JS) as demo:
        gr.Markdown("# RoadGen3D POI驱动街道生成工作台")
        gr.Markdown("默认工作流：`准备 -> 生成 -> 研究`")

        with gr.Tabs():
            with gr.Tab("1) 准备"):
                gr.Markdown("一键准备工作区：校验 manifest、补齐 latent、构建索引，并在需要时预热 OSM cache。")
                gr.Markdown(
                    "\n".join(
                        [
                            "- 输入：资产 manifest、mesh/latent 路径、CLIP/Shape-E 模型目录、AOI bbox / 城市。",
                            "- 中间算法：readiness 检查、latent 编码、索引构建、OSM cache 预热、POI-rich roads discovery。",
                            "- 输出：workspace readiness、prepare steps、自动候选道路列表。",
                        ]
                    )
                )
                with gr.Row():
                    dataset_profile = gr.Dropdown(label="数据源", choices=["real", "mock"], value="real")
                    model_name = gr.Textbox(label="CLIP Model", value="openai/clip-vit-base-patch32")
                    local_files_only = gr.Checkbox(label="Local Files Only", value=True)
                    device = gr.Dropdown(label="Device", choices=["auto", "cpu", "mps", "cuda"], value="auto")
                with gr.Row():
                    real_manifest = gr.Textbox(label="Manifest", value=default_real_manifest)
                    artifacts_dir = gr.Textbox(label="Artifacts Dir", value=default_artifacts)
                    model_dir = gr.Textbox(label="Model Dir", value=default_model_dir)
                prepare_workspace_btn = gr.Button("Prepare Workspace", variant="primary")
                with gr.Row():
                    prepare_summary = gr.Textbox(label="Workspace Readiness Summary", lines=7)
                    readiness_cards = gr.Dataframe(
                        headers=["item", "status"],
                        datatype=["str", "str"],
                        row_count=(0, "dynamic"),
                        col_count=(2, "fixed"),
                        label="Readiness Cards",
                    )
                with gr.Row():
                    readiness_json = gr.Code(label="Readiness JSON", language="json")
                    prepare_steps = gr.Dataframe(
                        headers=["step", "status", "message"],
                        datatype=["str", "str", "str"],
                        row_count=(0, "dynamic"),
                        col_count=(3, "fixed"),
                        label="Prepare Steps",
                    )
                prepare_discover_table = gr.Dataframe(
                    headers=["OSM ID", "Highway", "Length(m)", "POIs", "POI Score", "Core POIs", "POI Breakdown", "BBox"],
                    datatype=["str", "str", "str", "str", "str", "str", "str", "str"],
                    row_count=(0, "dynamic"),
                    col_count=(8, "fixed"),
                    label="自动候选 POI-rich Roads（仅展示）",
                    interactive=False,
                )
                with gr.Accordion("Asset Library", open=False):
                    with gr.Row():
                        asset_library_search = gr.Textbox(label="Asset Search", value="")
                        asset_library_refresh_btn = gr.Button("Browse Assets")
                    with gr.Row():
                        asset_library_stats = gr.Code(label="Asset Library Stats", language="json")
                    asset_library_table = gr.Dataframe(
                        headers=["asset_id", "category", "asset_role", "theme_tags", "frontage_m", "depth_m", "height_class", "source", "text_desc"],
                        datatype=["str", "str", "str", "str", "str", "str", "str", "str", "str"],
                        row_count=(0, "dynamic"),
                        col_count=(9, "fixed"),
                        label="Asset Library Browser",
                    )
                with gr.Accordion("Parametric Asset Preview", open=False):
                    parametric_preview_state = gr.State(value=None)
                    with gr.Row():
                        parametric_asset_kind = gr.Dropdown(label="Asset Kind", choices=["bench", "lamp"], value="bench")
                        parametric_runtime_profile = gr.Dropdown(label="Runtime Profile", choices=["preview", "production"], value="preview")
                        parametric_device_backend = gr.Dropdown(label="Device Backend", choices=["auto", "cpu", "mps", "cuda"], value="auto")
                    with gr.Row():
                        parametric_preview_out_dir = gr.Textbox(label="Preview Out Dir", value=default_parametric_preview_dir)
                        parametric_asset_id = gr.Textbox(label="Asset ID", value="")
                        parametric_text_desc = gr.Textbox(label="Text Desc", value="")
                    with gr.Group(visible=True) as parametric_bench_group:
                        with gr.Row():
                            bench_width_m = gr.Number(label="Bench Width (m)", value=1.80)
                            bench_depth_m = gr.Number(label="Bench Depth (m)", value=0.55)
                            bench_seat_height_m = gr.Number(label="Bench Seat Height (m)", value=0.45)
                            bench_backrest_height_m = gr.Number(label="Bench Backrest Height (m)", value=0.35)
                        with gr.Row():
                            bench_backrest_angle_deg = gr.Number(label="Bench Backrest Angle (deg)", value=12.0)
                            bench_leg_type = gr.Dropdown(label="Bench Leg Type", choices=["dual_frame", "pedestal", "four_leg"], value="dual_frame")
                            bench_armrest_enabled = gr.Checkbox(label="Bench Armrest Enabled", value=False)
                            bench_slat_count = gr.Slider(label="Bench Slat Count", minimum=3, maximum=8, step=1, value=5)
                        with gr.Row():
                            bench_material_family = gr.Dropdown(label="Bench Material Family", choices=["metal", "wood", "metal_wood", "concrete"], value="metal_wood")
                            bench_style_tag = gr.Dropdown(label="Bench Style Tag", choices=_PARAMETRIC_STYLE_CHOICES, value="modern")
                            bench_detail_level = gr.Slider(label="Bench Detail Level", minimum=0, maximum=3, step=1, value=2)
                    with gr.Group(visible=False) as parametric_lamp_group:
                        with gr.Row():
                            lamp_pole_height_m = gr.Number(label="Lamp Pole Height (m)", value=5.00)
                            lamp_pole_radius_m = gr.Number(label="Lamp Pole Radius (m)", value=0.06)
                            lamp_base_diameter_m = gr.Number(label="Lamp Base Diameter (m)", value=0.35)
                            lamp_arm_length_m = gr.Number(label="Lamp Arm Length (m)", value=0.80)
                        with gr.Row():
                            lamp_luminaire_type = gr.Dropdown(label="Lamp Luminaire Type", choices=["flat_led", "globe", "box", "cone"], value="flat_led")
                            lamp_single_or_double_arm = gr.Dropdown(label="Lamp Single Or Double Arm", choices=["single", "double"], value="single")
                            lamp_light_direction = gr.Dropdown(label="Lamp Light Direction", choices=["roadside", "bidirectional", "downward"], value="roadside")
                        with gr.Row():
                            lamp_material_family = gr.Dropdown(label="Lamp Material Family", choices=["metal", "painted_steel", "cast_iron"], value="metal")
                            lamp_style_tag = gr.Dropdown(label="Lamp Style Tag", choices=_PARAMETRIC_STYLE_CHOICES, value="modern")
                            lamp_detail_level = gr.Slider(label="Lamp Detail Level", minimum=0, maximum=3, step=1, value=2)
                    with gr.Row():
                        parametric_preview_btn = gr.Button("Generate Preview", variant="primary")
                        parametric_append_btn = gr.Button("Append To Manifest")
                    with gr.Row():
                        parametric_model_view = gr.Model3D(label="Parametric Preview (GLB)")
                        parametric_status = gr.Textbox(label="Parametric Status", lines=7)
                    with gr.Row():
                        parametric_result_json = gr.Code(label="Parametric Result JSON", language="json")
                        parametric_downloads = gr.Files(label="Parametric Downloads")
                with gr.Accordion("Advanced", open=False):
                    with gr.Row():
                        data_dir = gr.Textbox(label="Mock Data Dir", value=default_data)
                        num_assets = gr.Slider(label="Mock Num Assets", minimum=1, maximum=256, step=1, value=8)
                        seed = gr.Number(label="Seed", value=42, precision=0)
                        latent_dim = gr.Number(label="Latent Dim", value=256, precision=0)
                    with gr.Row():
                        encode_mode = gr.Dropdown(label="Encode Mode", choices=["mesh_ref", "auto", "shapee"], value="mesh_ref")
                        shapee_model_dir = gr.Textbox(label="Shape-E Model Dir", value=default_shapee_model_dir)
                        shapee_local_only = gr.Checkbox(label="Shape-E Local Only", value=True)
                    with gr.Row():
                        real_mesh_root = gr.Textbox(label="Real Mesh Root", value=default_real_mesh_root)
                        real_latents_dir = gr.Textbox(label="Real Latents Dir", value=default_real_latents_dir)
                        render_cache_dir = gr.Textbox(label="Render Cache Dir", value=default_render_cache_dir)
                    with gr.Row():
                        prepare_layout_mode = gr.Dropdown(label="Prepare Layout Mode", choices=["template", "osm"], value="osm")
                        osm_cache_dir = gr.Textbox(label="OSM Cache Dir", value=default_osm_cache_dir)
                        force_reindex = gr.Checkbox(label="Force Reindex", value=False)
                        force_reencode = gr.Checkbox(label="Force Reencode", value=False)
                        force_osm_refresh = gr.Checkbox(label="Force OSM Refresh", value=False)
                    with gr.Row(visible=True) as prepare_city_row:
                        prepare_city_selector = gr.Dropdown(
                            label="中国城市 (City)",
                            choices=[("手动输入 Manual", "")] + get_city_choices(),
                            value="guangzhou",
                        )
                    with gr.Row(visible=True) as prepare_bbox_row:
                        prepare_bbox_min_lon = gr.Number(label="AOI Min Lon", value=113.2660)
                        prepare_bbox_min_lat = gr.Number(label="AOI Min Lat", value=23.1280)
                        prepare_bbox_max_lon = gr.Number(label="AOI Max Lon", value=113.2710)
                        prepare_bbox_max_lat = gr.Number(label="AOI Max Lat", value=23.1325)

            with gr.Tab("2) 生成街道"):
                gr.Markdown("默认入口：先生成 `StreetProgram`，再做约束求解与资产实现。")
                gr.Markdown(
                    "\n".join(
                        [
                            "- 输入：文本 query、道路宽度/车道数/密度、OSM AOI、设计规则、program generator、solver。",
                            "- 中间算法：自动选取 POI-rich road、POI-aware 横断面合成、StreetProgram 推理、约束求解、资产检索与摆放。",
                            "- 输出：GLB/PLY 场景、StreetProgram Summary、Solver Summary、POI/空间分析结果。",
                        ]
                    )
                )
                query = gr.Textbox(label="Query", value="pedestrian-friendly boulevard with stylized trees and transit access")
                with gr.Row():
                    m5_layout_mode = gr.Dropdown(label="Layout Mode", choices=["template", "osm"], value="osm")
                    design_rule_profile = gr.Dropdown(
                        label="Design Rule Profile",
                        choices=["balanced_complete_street_v1", "pedestrian_priority_v1", "transit_priority_v1", "noise_aware_v1"],
                        value="balanced_complete_street_v1",
                    )
                    program_generator = gr.Dropdown(
                        label="Program Generator",
                        choices=["learned_v1", "heuristic_v1"],
                        value="learned_v1",
                    )
                    layout_solver = gr.Dropdown(
                        label="Layout Solver",
                        choices=["milp_template_v1", "banded"],
                        value="milp_template_v1",
                    )
                    street_placement_policy = gr.Dropdown(
                        label="Policy",
                        choices=["rule", "learned"],
                        value="learned",
                    )
                    style_preset = gr.Dropdown(
                        label="Style Preset",
                        choices=["civic_clean_v1", "transit_modern_v1", "lush_walkable_v1"],
                        value="civic_clean_v1",
                    )
                with gr.Row(visible=True) as street_city_row:
                    street_city_selector = gr.Dropdown(
                        label="中国城市 (City)",
                        choices=[("手动输入 Manual", "")] + get_city_choices(),
                        value="guangzhou",
                    )
                with gr.Row(visible=True) as street_bbox_row:
                    m5_bbox_min_lon = gr.Number(label="AOI Min Lon", value=113.2660)
                    m5_bbox_min_lat = gr.Number(label="AOI Min Lat", value=23.1280)
                    m5_bbox_max_lon = gr.Number(label="AOI Max Lon", value=113.2710)
                    m5_bbox_max_lat = gr.Number(label="AOI Max Lat", value=23.1325)
                    road_selection = gr.Dropdown(
                        label="道路筛选 (Road Selection)",
                        choices=["primary_road", "longest", "all"],
                        value="primary_road",
                    )
                street_btn = gr.Button("Run Street", variant="primary")
                production_steps_state = gr.State(value=[])
                with gr.Accordion("Production Timeline", open=True, elem_id="production-timeline"):
                    with gr.Row():
                        prev_step_btn = gr.Button("◀ Prev", elem_id="prev-step-btn",
                                                   scale=1, variant="secondary", interactive=False)
                        production_step_slider = gr.Slider(
                            label="Production Step",
                            minimum=0,
                            maximum=0,
                            step=1,
                            value=0,
                            interactive=False,
                            scale=6,
                            elem_id="production-step-slider",
                        )
                        next_step_btn = gr.Button("Next ▶", elem_id="next-step-btn",
                                                   scale=1, variant="secondary", interactive=False)
                    gr.Markdown(
                        "**Shortcuts**: "
                        "← → Rotate Camera | "
                        "↑ ↓ Tilt Camera | "
                        "Shift+←/→ Switch Step | "
                        "+/=/z Zoom In | "
                        "-/x Zoom Out | "
                        "R Reset | "
                        "Home/End First/Last"
                    )
                    with gr.Row():
                        street_model_view = gr.Model3D(label="Production Step Preview (GLB)", elem_id="street-model-view")
                        production_companion_view = gr.Image(label="Production Companion View", type="filepath")
                    with gr.Row():
                        production_step_summary = gr.Textbox(label="Production Step Summary", lines=10)
                        street_summary = gr.Textbox(label="Scene Summary", lines=10)
                    production_step_downloads = gr.Files(label="Production Step Downloads")
                with gr.Row():
                    street_program_summary = gr.Code(label="StreetProgram Summary", language="json")
                    street_solver_summary = gr.Code(label="Solver Edits / Conflicts", language="json")
                with gr.Row():
                    theme_segments_preview = gr.Code(label="Theme Segments Preview", language="json")
                    building_summary_json = gr.Code(label="Building Summary", language="json")
                zoning_preview_plot = gr.Plot(label="Theme / Building Zoning Preview")
                with gr.Accordion("Advanced", open=False):
                    with gr.Row():
                        street_length_m = gr.Number(label="Street Length (m)", value=80.0)
                        street_road_width_m = gr.Number(label="Road Width (m)", value=8.0)
                        street_sidewalk_width_m = gr.Number(label="Sidewalk Width (m)", value=2.5)
                        street_lane_count = gr.Slider(label="Lane Count", minimum=1, maximum=4, step=1, value=2)
                        street_density = gr.Slider(label="Density", minimum=0.2, maximum=2.0, step=0.1, value=1.0)
                    with gr.Row():
                        street_seed = gr.Number(label="Seed", value=42, precision=0)
                        street_topk_per_category = gr.Slider(label="TopK Per Category", minimum=1, maximum=50, step=1, value=20)
                        street_max_trials_per_slot = gr.Slider(label="Max Trials Per Slot", minimum=1, maximum=100, step=1, value=30)
                        segment_length_m = gr.Number(label="Segment Length (m)", value=12.0)
                        allow_solver_fallback = gr.Checkbox(label="Allow Solver Fallback", value=True)
                    with gr.Row():
                        export_format = gr.Dropdown(label="Export Format", choices=["both", "glb", "ply"], value="both")
                        policy_ckpt = gr.Textbox(label="Policy CKPT", value=default_policy_ckpt)
                        program_ckpt = gr.Textbox(label="Program CKPT", value=default_program_ckpt)
                        policy_temperature = gr.Number(label="Policy Temperature", value=0.12)
                    with gr.Row():
                        m5_constraint_mode = gr.Dropdown(label="Constraint Mode", choices=["off", "soft"], value="soft")
                        m5_constraint_weight = gr.Slider(label="Constraint Weight", minimum=0.0, maximum=1.0, step=0.05, value=0.45)
                        m5_constraint_veto = gr.Slider(label="Veto Threshold", minimum=0.0, maximum=1.0, step=0.05, value=0.95)
                        street_osm_cache_dir = gr.Textbox(label="OSM Cache Dir", value=default_osm_cache_dir)
                    with gr.Row():
                        city_context = gr.Textbox(label="City Context", value="generic_city")
                        target_street_type = gr.Textbox(label="Target Street Type", value="mixed_use")
                    with gr.Row():
                        beauty_mode = gr.Dropdown(
                            label="Beauty Mode",
                            choices=["presentation_v1"],
                            value="presentation_v1",
                        )
                        render_preset = gr.Dropdown(
                            label="Render Preset",
                            choices=["jury_default_v1"],
                            value="jury_default_v1",
                        )
                        asset_curation_mode = gr.Dropdown(
                            label="Asset Curation",
                            choices=["scene_ready_first", "parametric_first", "curated_first", "legacy"],
                            value="scene_ready_first",
                        )
                    with gr.Row():
                        enable_surrounding_buildings = gr.Checkbox(label="Enable Surrounding Buildings", value=True)
                        surrounding_building_mode = gr.Dropdown(
                            label="Surrounding Building Mode",
                            choices=["footprint_based", "grid_growth"],
                            value="footprint_based",
                        )
                        building_search_topk = gr.Slider(label="Building Search TopK", minimum=1, maximum=20, step=1, value=5)
                        theme_inference_mode = gr.Dropdown(
                            label="Theme Inference",
                            choices=["deterministic_auto"],
                            value="deterministic_auto",
                        )
                        theme_vocab_name = gr.Dropdown(
                            label="Theme Vocab",
                            choices=["fixed_v1"],
                            value="fixed_v1",
                        )
                with gr.Accordion("Scene Details", open=False):
                    street_instances = gr.Dataframe(
                        headers=["instance_id", "asset_id", "category", "score", "x", "z", "yaw_deg", "source", "generator_type"],
                        datatype=["str", "str", "str", "str", "str", "str", "str", "str", "str"],
                        row_count=(0, "dynamic"),
                        col_count=(9, "fixed"),
                        label="Street Instances",
                    )
                    street_layout_json = gr.Code(label="Street Layout JSON", language="json")
                    street_files = gr.Files(label="Scene Downloads")
                with gr.Accordion("Presentation Views", open=True):
                    presentation_gallery = gr.Gallery(label="Presentation Views", columns=2, rows=2, height="auto")
                    presentation_report = gr.Code(label="Presentation Metrics", language="json")
                with gr.Accordion("Scene Graph", open=True):
                    scene_graph_plot = gr.Plot(label="Scene Graph")
                    with gr.Row():
                        graph_node_layers = gr.CheckboxGroup(
                            label="Node Layers",
                            choices=list(SCENE_GRAPH_NODE_TYPES),
                            value=list(SCENE_GRAPH_NODE_TYPES),
                        )
                        graph_poi_types = gr.CheckboxGroup(
                            label="POI Types",
                            choices=[],
                            value=[],
                        )
                    with gr.Row():
                        graph_categories = gr.CheckboxGroup(
                            label="Furniture Categories",
                            choices=[],
                            value=[],
                        )
                        graph_edge_types = gr.CheckboxGroup(
                            label="Edge Types",
                            choices=[],
                            value=[],
                        )
                    with gr.Row():
                        heatmap_category = gr.Dropdown(
                            label="Heatmap Category",
                            choices=[],
                            value=None,
                        )
                        heatmap_layer = gr.Dropdown(
                            label="Heatmap Layer",
                            choices=["combined", "attraction", "repulsion"],
                            value="combined",
                        )
                        show_scene_heatmap = gr.Checkbox(label="Show Heatmap", value=True)
                        scene_heatmap_opacity = gr.Slider(
                            label="Heatmap Opacity",
                            minimum=0.0,
                            maximum=1.0,
                            step=0.05,
                            value=0.55,
                        )
                with gr.Accordion("Spatial Distance Analysis", open=False):
                    scene_overview_plot = gr.Plot(label="Scene Overview (Junctions + Entrances)")
                    with gr.Row():
                        heatmap_type = gr.Dropdown(
                            choices=["road_edge", "junction", "entrance"],
                            value="road_edge",
                            label="Heatmap Type",
                        )
                        render_heatmap_btn = gr.Button("Render Heatmap")
                    distance_heatmap_plot = gr.Plot(label="Distance Heatmap")
                    distance_histogram_plot = gr.Plot(label="Distance Distribution")
                with gr.Accordion("POI Analysis", open=True):
                    poi_overview_plot = gr.Plot(label="POI Positions & Exclusion Zones")
                    with gr.Row():
                        poi_summary_table = gr.Dataframe(
                            headers=["Type", "X", "Z", "Radius(m)", "Rule"],
                            datatype=["str", "str", "str", "str", "str"],
                            row_count=(0, "dynamic"),
                            col_count=(5, "fixed"),
                            label="POI Points & Exclusion Radii",
                        )
                        poi_conflict_table = gr.Dataframe(
                            headers=["Instance", "Category", "X", "Z", "Violated Rules", "Penalty"],
                            datatype=["str", "str", "str", "str", "str", "str"],
                            row_count=(0, "dynamic"),
                            col_count=(6, "fixed"),
                            label="Assets in Violation Zones",
                        )
                    poi_stats_json = gr.Code(label="POI Statistics", language="json")

            with gr.Tab("3) 研究与训练"):
                gr.Markdown("研究工具：用于改进 `learned_v1` / `learned policy`，不是默认运行入口。")
                gr.Markdown("`Run Best Model` 使用当前“生成街道”页的查询与街道设置。")
                gr.Markdown(
                    "\n".join(
                        [
                            "- 输入：蒸馏查询集、训练超参数、policy/program checkpoint、生成页当前街道设置。",
                            "- 中间算法：数据蒸馏、layout policy 训练、program generator 训练、离线评估与 best-model 回放。",
                            "- 输出：训练日志、train/eval JSON、更新后的 checkpoint、best-model 生成结果。",
                        ]
                    )
                )
                with gr.Row():
                    train_btn = gr.Button("Train + Eval", variant="primary")
                    run_best_model_btn = gr.Button("Run Best Model")
                with gr.Row():
                    research_target = gr.Dropdown(
                        label="Research Target",
                        choices=["program_generator", "layout_policy"],
                        value="program_generator",
                    )
                    m4_artifacts_dir = gr.Textbox(label="M4 Artifacts Dir", value=default_m4_artifacts_dir)
                    m6_artifacts_dir = gr.Textbox(label="M6 Artifacts Dir", value=default_m6_artifacts_dir)
                    m4_queries = gr.Textbox(label="Queries File", value=default_m4_queries)
                with gr.Accordion("Program Distillation", open=False):
                    with gr.Row():
                        m4_collect_seed_start = gr.Number(label="Collect Seed Start", value=0, precision=0)
                        m4_collect_seed_end = gr.Number(label="Collect Seed End", value=49, precision=0)
                        m4_recollect_data = gr.Checkbox(label="Recollect Distilled Data", value=True)
                        m4_resume_training = gr.Checkbox(label="Resume From Existing CKPT", value=True)
                with gr.Accordion("Policy / Program Training", open=False):
                    with gr.Row():
                        m4_train_epochs = gr.Number(label="Policy Epochs", value=20, precision=0)
                        m4_train_batch_size = gr.Number(label="Policy Batch Size", value=256, precision=0)
                        m4_train_lr = gr.Number(label="Policy LR", value=1e-3)
                        m4_train_weight_decay = gr.Number(label="Policy Weight Decay", value=1e-4)
                        m4_train_entropy_weight = gr.Number(label="Policy Entropy Weight", value=0.01)
                        m4_train_patience = gr.Number(label="Policy Patience", value=3, precision=0)
                    with gr.Row():
                        program_train_epochs = gr.Number(label="Program Epochs", value=60, precision=0)
                        program_train_batch_size = gr.Number(label="Program Batch Size", value=32, precision=0)
                        program_train_lr = gr.Number(label="Program LR", value=5e-4)
                        program_train_weight_decay = gr.Number(label="Program Weight Decay", value=1e-4)
                        program_train_patience = gr.Number(label="Program Patience", value=5, precision=0)
                with gr.Accordion("Evaluation", open=False):
                    with gr.Row():
                        m4_run_eval_after_train = gr.Checkbox(label="Run Eval After Train", value=True)
                        m4_eval_seed_start = gr.Number(label="Eval Seed Start", value=0, precision=0)
                        m4_eval_seed_end = gr.Number(label="Eval Seed End", value=4, precision=0)
                research_progress = gr.Slider(
                    label="Research Progress (%)",
                    minimum=0.0,
                    maximum=100.0,
                    value=0.0,
                    step=0.1,
                    interactive=False,
                )
                research_curve = gr.Plot(label="Training Curve")
                research_log = gr.Textbox(label="Train + Eval Log", lines=10)
                with gr.Row():
                    research_train_json = gr.Code(label="Train Summary JSON", language="json")
                    research_eval_json = gr.Code(label="Eval Summary JSON", language="json")
                with gr.Accordion("Run Best Model Result", open=False):
                    run_best_log = gr.Textbox(label="Run Best Model Log", lines=8)
                    with gr.Row():
                        run_best_program_summary = gr.Code(label="Best StreetProgram Summary", language="json")
                        run_best_solver_summary = gr.Code(label="Best Solver Summary", language="json")
                    with gr.Row():
                        run_best_theme_summary = gr.Code(label="Best Theme Segments", language="json")
                        run_best_building_summary = gr.Code(label="Best Building Summary", language="json")
                    run_best_layout_json = gr.Code(label="Run Best Layout JSON", language="json")
                    run_best_model_view = gr.Model3D(label="Run Best Street Preview (GLB)")
                    run_best_files = gr.Files(label="Run Best Downloads")

        prepare_layout_mode.change(
            fn=_toggle_osm_visibility,
            inputs=[prepare_layout_mode],
            outputs=[prepare_city_row, prepare_bbox_row],
        )
        m5_layout_mode.change(
            fn=_toggle_osm_visibility,
            inputs=[m5_layout_mode],
            outputs=[street_city_row, street_bbox_row],
        )
        prepare_city_selector.change(
            fn=_on_city_selected,
            inputs=[prepare_city_selector],
            outputs=[prepare_bbox_min_lon, prepare_bbox_min_lat, prepare_bbox_max_lon, prepare_bbox_max_lat],
        )
        street_city_selector.change(
            fn=_on_city_selected,
            inputs=[street_city_selector],
            outputs=[m5_bbox_min_lon, m5_bbox_min_lat, m5_bbox_max_lon, m5_bbox_max_lat],
        )
        prepare_workspace_btn.click(
            fn=run_prepare_workspace,
            inputs=[
                dataset_profile,
                data_dir,
                artifacts_dir,
                real_manifest,
                real_mesh_root,
                real_latents_dir,
                num_assets,
                seed,
                latent_dim,
                model_name,
                model_dir,
                local_files_only,
                device,
                shapee_model_dir,
                render_cache_dir,
                encode_mode,
                shapee_local_only,
                prepare_layout_mode,
                osm_cache_dir,
                force_reindex,
                force_reencode,
                force_osm_refresh,
                prepare_bbox_min_lon,
                prepare_bbox_min_lat,
                prepare_bbox_max_lon,
                prepare_bbox_max_lat,
            ],
            outputs=[prepare_summary, readiness_json, readiness_cards, prepare_steps, prepare_discover_table],
        ).then(
            fn=browse_asset_library,
            inputs=[real_manifest, asset_library_search],
            outputs=[asset_library_table, asset_library_stats],
        )
        asset_library_refresh_btn.click(
            fn=browse_asset_library,
            inputs=[real_manifest, asset_library_search],
            outputs=[asset_library_table, asset_library_stats],
        )
        parametric_asset_kind.change(
            fn=_toggle_parametric_controls,
            inputs=[parametric_asset_kind],
            outputs=[parametric_bench_group, parametric_lamp_group],
        )
        parametric_preview_btn.click(
            fn=preview_parametric_asset,
            inputs=[
                parametric_asset_kind,
                parametric_runtime_profile,
                parametric_device_backend,
                parametric_preview_out_dir,
                parametric_asset_id,
                parametric_text_desc,
                bench_width_m,
                bench_depth_m,
                bench_seat_height_m,
                bench_backrest_height_m,
                bench_backrest_angle_deg,
                bench_leg_type,
                bench_armrest_enabled,
                bench_slat_count,
                bench_material_family,
                bench_style_tag,
                bench_detail_level,
                lamp_pole_height_m,
                lamp_pole_radius_m,
                lamp_base_diameter_m,
                lamp_arm_length_m,
                lamp_luminaire_type,
                lamp_single_or_double_arm,
                lamp_light_direction,
                lamp_material_family,
                lamp_style_tag,
                lamp_detail_level,
            ],
            outputs=[
                parametric_status,
                parametric_result_json,
                parametric_model_view,
                parametric_downloads,
                parametric_preview_state,
            ],
        )
        parametric_append_btn.click(
            fn=append_parametric_asset_to_manifest,
            inputs=[parametric_preview_state, real_manifest],
            outputs=[parametric_status, asset_library_table, asset_library_stats],
        )
        street_btn.click(
            fn=run_street_compose,
            inputs=[
                dataset_profile,
                query,
                real_manifest,
                artifacts_dir,
                model_name,
                model_dir,
                local_files_only,
                device,
                street_length_m,
                street_road_width_m,
                street_sidewalk_width_m,
                street_lane_count,
                street_density,
                street_seed,
                street_topk_per_category,
                street_max_trials_per_slot,
                export_format,
                street_placement_policy,
                policy_ckpt,
                policy_temperature,
                m5_layout_mode,
                m5_constraint_mode,
                m5_constraint_weight,
                m5_constraint_veto,
                m5_bbox_min_lon,
                m5_bbox_min_lat,
                m5_bbox_max_lon,
                m5_bbox_max_lat,
                design_rule_profile,
                program_generator,
                layout_solver,
                program_ckpt,
                street_osm_cache_dir,
                city_context,
                target_street_type,
                allow_solver_fallback,
                segment_length_m,
                road_selection,
                style_preset,
                beauty_mode,
                render_preset,
                asset_curation_mode,
                enable_surrounding_buildings,
                building_search_topk,
                theme_inference_mode,
                theme_vocab_name,
                surrounding_building_mode,
            ],
            outputs=[
                street_summary,
                street_instances,
                street_layout_json,
                street_model_view,
                street_files,
            ],
        ).then(
            fn=_load_production_steps,
            inputs=[street_layout_json],
            outputs=[
                production_steps_state,
                production_step_slider,
                production_step_summary,
                street_model_view,
                production_companion_view,
                production_step_downloads,
                prev_step_btn,
                next_step_btn,
            ],
        ).then(
            fn=_extract_program_summary,
            inputs=[street_layout_json],
            outputs=[street_program_summary],
        ).then(
            fn=_extract_solver_summary,
            inputs=[street_layout_json],
            outputs=[street_solver_summary],
        ).then(
            fn=_extract_theme_summary,
            inputs=[street_layout_json],
            outputs=[theme_segments_preview],
        ).then(
            fn=_extract_building_summary,
            inputs=[street_layout_json],
            outputs=[building_summary_json],
        ).then(
            fn=_render_zoning_preview,
            inputs=[street_layout_json],
            outputs=[zoning_preview_plot],
        ).then(
            fn=_render_spatial_overview,
            inputs=[street_layout_json],
            outputs=[scene_overview_plot],
        ).then(
            fn=_extract_poi_summary,
            inputs=[street_layout_json],
            outputs=[poi_summary_table, poi_conflict_table, poi_stats_json],
        ).then(
            fn=_render_poi_overview,
            inputs=[street_layout_json],
            outputs=[poi_overview_plot],
        ).then(
            fn=_extract_presentation_views,
            inputs=[street_layout_json],
            outputs=[presentation_gallery, presentation_report],
        ).then(
            fn=_init_scene_graph_controls,
            inputs=[street_layout_json],
            outputs=[
                scene_graph_plot,
                graph_node_layers,
                graph_poi_types,
                graph_categories,
                graph_edge_types,
                heatmap_category,
                heatmap_layer,
                show_scene_heatmap,
                scene_heatmap_opacity,
            ],
        )
        render_heatmap_btn.click(
            fn=_render_distance_heatmap,
            inputs=[street_layout_json, heatmap_type],
            outputs=[distance_heatmap_plot, distance_histogram_plot],
        )
        production_step_slider.change(
            fn=_select_production_step,
            inputs=[production_steps_state, production_step_slider],
            outputs=[
                production_step_slider,
                production_step_summary,
                street_model_view,
                production_companion_view,
                production_step_downloads,
            ],
        ).then(
            fn=_update_nav_button_states,
            inputs=[production_steps_state, production_step_slider],
            outputs=[prev_step_btn, next_step_btn],
        )
        prev_step_btn.click(
            fn=lambda steps, idx: _navigate_production_step(steps, idx, -1),
            inputs=[production_steps_state, production_step_slider],
            outputs=[
                production_step_slider,
                production_step_summary,
                street_model_view,
                production_companion_view,
                production_step_downloads,
                prev_step_btn,
                next_step_btn,
            ],
        )
        next_step_btn.click(
            fn=lambda steps, idx: _navigate_production_step(steps, idx, +1),
            inputs=[production_steps_state, production_step_slider],
            outputs=[
                production_step_slider,
                production_step_summary,
                street_model_view,
                production_companion_view,
                production_step_downloads,
                prev_step_btn,
                next_step_btn,
            ],
        )
        for control in (
            graph_node_layers,
            graph_poi_types,
            graph_categories,
            graph_edge_types,
            heatmap_category,
            heatmap_layer,
            show_scene_heatmap,
            scene_heatmap_opacity,
        ):
            control.change(
                fn=_render_scene_graph_from_controls,
                inputs=[
                    street_layout_json,
                    graph_node_layers,
                    graph_poi_types,
                    graph_categories,
                    graph_edge_types,
                    heatmap_category,
                    heatmap_layer,
                    show_scene_heatmap,
                    scene_heatmap_opacity,
                ],
                outputs=[scene_graph_plot],
            )
        train_btn.click(
            fn=run_research_train,
            inputs=[
                research_target,
                dataset_profile,
                real_manifest,
                artifacts_dir,
                m4_artifacts_dir,
                m6_artifacts_dir,
                m4_queries,
                model_name,
                model_dir,
                local_files_only,
                device,
                street_length_m,
                street_road_width_m,
                street_sidewalk_width_m,
                street_lane_count,
                street_density,
                street_topk_per_category,
                street_max_trials_per_slot,
                design_rule_profile,
                m5_layout_mode,
                m5_bbox_min_lon,
                m5_bbox_min_lat,
                m5_bbox_max_lon,
                m5_bbox_max_lat,
                m4_collect_seed_start,
                m4_collect_seed_end,
                m4_recollect_data,
                m4_resume_training,
                m4_train_epochs,
                m4_train_batch_size,
                m4_train_lr,
                m4_train_weight_decay,
                m4_train_entropy_weight,
                m4_train_patience,
                m4_run_eval_after_train,
                m4_eval_seed_start,
                m4_eval_seed_end,
                export_format,
                policy_temperature,
                policy_ckpt,
                program_train_epochs,
                program_train_batch_size,
                program_train_lr,
                program_train_weight_decay,
                program_train_patience,
                program_ckpt,
            ],
            outputs=[
                research_log,
                research_train_json,
                research_eval_json,
                policy_ckpt,
                program_ckpt,
                research_progress,
                research_curve,
            ],
        )
        run_best_model_btn.click(
            fn=run_best_model_street,
            inputs=[
                dataset_profile,
                query,
                real_manifest,
                artifacts_dir,
                model_name,
                model_dir,
                local_files_only,
                device,
                street_length_m,
                street_road_width_m,
                street_sidewalk_width_m,
                street_lane_count,
                street_density,
                street_seed,
                street_topk_per_category,
                street_max_trials_per_slot,
                export_format,
                policy_ckpt,
                policy_temperature,
                m5_layout_mode,
                m5_constraint_mode,
                m5_constraint_weight,
                m5_constraint_veto,
                m5_bbox_min_lon,
                m5_bbox_min_lat,
                m5_bbox_max_lon,
                m5_bbox_max_lat,
                design_rule_profile,
                program_generator,
                layout_solver,
                program_ckpt,
                street_osm_cache_dir,
                city_context,
                target_street_type,
                allow_solver_fallback,
                segment_length_m,
                research_target,
                road_selection,
                style_preset,
                beauty_mode,
                render_preset,
                asset_curation_mode,
                enable_surrounding_buildings,
                building_search_topk,
                theme_inference_mode,
                theme_vocab_name,
                surrounding_building_mode,
            ],
            outputs=[
                street_summary,
                street_instances,
                street_layout_json,
                street_model_view,
                street_files,
                run_best_log,
                run_best_layout_json,
                run_best_model_view,
                run_best_files,
            ],
        ).then(
            fn=_load_production_steps,
            inputs=[street_layout_json],
            outputs=[
                production_steps_state,
                production_step_slider,
                production_step_summary,
                street_model_view,
                production_companion_view,
                production_step_downloads,
                prev_step_btn,
                next_step_btn,
            ],
        ).then(
            fn=_extract_presentation_views,
            inputs=[street_layout_json],
            outputs=[presentation_gallery, presentation_report],
        ).then(
            fn=_extract_program_summary,
            inputs=[run_best_layout_json],
            outputs=[run_best_program_summary],
        ).then(
            fn=_extract_solver_summary,
            inputs=[run_best_layout_json],
            outputs=[run_best_solver_summary],
        ).then(
            fn=_extract_theme_summary,
            inputs=[run_best_layout_json],
            outputs=[run_best_theme_summary],
        ).then(
            fn=_extract_building_summary,
            inputs=[run_best_layout_json],
            outputs=[run_best_building_summary],
        )
    return demo


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch RoadGen3D milestone UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Server host.")
    parser.add_argument("--port", type=int, default=7860, help="Server port.")
    parser.add_argument("--share", action="store_true", help="Enable gradio share link.")
    parser.add_argument("--inbrowser", action="store_true", help="Open browser on launch.")
    parser.add_argument(
        "--keep-proxy-env",
        action="store_true",
        help="Do not clear proxy env vars when using localhost/127.0.0.1/0.0.0.0.",
    )
    return parser.parse_args()


def _configure_local_proxy_bypass(host: str, keep_proxy_env: bool) -> None:
    local_hosts = {"127.0.0.1", "localhost", "0.0.0.0"}
    if host not in local_hosts:
        return

    no_proxy_keys = ("NO_PROXY", "no_proxy")
    extra_values = ["127.0.0.1", "localhost", "::1"]
    for key in no_proxy_keys:
        current = os.environ.get(key, "")
        items = [item.strip() for item in current.split(",") if item.strip()]
        for value in extra_values:
            if value not in items:
                items.append(value)
        os.environ[key] = ",".join(items)

    if keep_proxy_env:
        return

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)


def main() -> int:
    args = parse_args()
    _configure_local_proxy_bypass(args.host, args.keep_proxy_env)
    demo = build_demo()
    demo.queue().launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        inbrowser=args.inbrowser,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
