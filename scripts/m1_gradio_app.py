#!/usr/bin/env python3
"""Gradio UI for RoadGen3D milestone pipelines."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
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
from roadgen3d.osm_ingest import fetch_osm_data
from roadgen3d.pipeline import M1Pipeline
from roadgen3d.program_generator import ProgramTrainConfig
from roadgen3d.street_layout import compose_street_scene
from roadgen3d.types import PrepareWorkspaceResult, StepResult, StreetComposeConfig, WorkspaceReadiness
from scripts.m1_01_seed_assets import seed_assets
from scripts.m2_11_encode_shapee_latents import encode_latents as encode_shapee_latents
from scripts.m4_01_collect_policy_data import collect_policy_data
from scripts.m4_02_train_layout_policy import train_from_jsonl
from scripts.m4_10_eval_engineering import run_eval as run_m4_eval
from scripts.m6_01_collect_program_data import collect_program_data
from scripts.m6_02_train_program_generator import train_from_jsonl as train_program_from_jsonl


def _to_path(path_text: str) -> Path:
    return Path(path_text.strip()).expanduser().resolve()


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
                latents_ok = all(Path(row["latent_path"]).exists() for row in rows)
                details["manifest_asset_count"] = len(rows)
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
            f"- recommended_next_action: {final_readiness.recommended_next_action}",
        ]
    )
    return PrepareWorkspaceResult(summary=summary, readiness=final_readiness, steps=tuple(steps))


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
    bands = program.get("bands", []) or []
    summary = {
        "road_type": program.get("road_type", ""),
        "cross_section_type": program.get("cross_section_type", ""),
        "lane_count": program.get("lane_count", 0),
        "band_widths": {band.get("name", ""): band.get("width_m", 0.0) for band in bands},
        "furniture_requirements": program.get("furniture_requirements", {}),
        "design_goals": program.get("design_goals", []),
    }
    return json.dumps(summary, indent=2, ensure_ascii=True)


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
) -> Tuple[str, str, List[List[str]], List[List[str]]]:
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
        )
    except Exception as exc:
        detail = traceback.format_exc(limit=3)
        return f"Prepare workspace failed: {exc}\n{detail}", "{}", [], []


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
    dataset_profile: str,
    query: str,
    real_manifest_text: str,
    artifacts_dir_text: str,
    model_name: str,
    model_dir_text: str,
    local_files_only: bool,
    device: str,
    street_length_m: float,
    street_road_width_m: float,
    street_sidewalk_width_m: float,
    street_lane_count: int,
    street_density: float,
    street_seed: int,
    street_topk_per_category: int,
    street_max_trials_per_slot: int,
    export_format: str,
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
            aoi_bbox=(float(m5_bbox_min_lon), float(m5_bbox_min_lat), float(m5_bbox_max_lon), float(m5_bbox_max_lat)) if str(m5_layout_mode).strip() == "osm" else None,
            osm_cache_dir=str(osm_cache_dir_text).strip() or "artifacts/m5/osm_cache",
            constraint_weight=float(m5_constraint_weight),
            constraint_veto_threshold=float(m5_constraint_veto),
            design_rule_profile=str(design_rule_profile).strip(),
            city_context=str(city_context).strip(),
            target_street_type=str(target_street_type).strip(),
            program_generator=str(program_generator).strip(),
            layout_solver=str(layout_solver).strip(),
            allow_solver_fallback=bool(allow_solver_fallback),
            segment_length_m=float(segment_length_m),
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
            f"- scene_layout: {result.outputs.get('scene_layout', '')}"
        )
        if result.outputs.get("policy_fallback_reason"):
            summary += f"\n- policy_fallback_reason: {result.outputs['policy_fallback_reason']}"
        if result.outputs.get("program_fallback_reason"):
            summary += f"\n- program_fallback_reason: {result.outputs['program_fallback_reason']}"
        if result.outputs.get("solver_fallback_reason"):
            summary += f"\n- solver_fallback_reason: {result.outputs['solver_fallback_reason']}"
        model_path = result.outputs.get("scene_glb") or None
        files: List[str] = []
        if result.outputs.get("scene_glb"):
            files.append(result.outputs["scene_glb"])
        if result.outputs.get("scene_ply"):
            files.append(result.outputs["scene_ply"])
        if result.outputs.get("scene_layout"):
            files.append(result.outputs["scene_layout"])
        return summary, instance_rows, layout_json_text, model_path, files
    except ModelLoadError as exc:
        return f"Model load error: {exc}", [], "", None, []
    except Exception as exc:
        detail = traceback.format_exc(limit=3)
        return f"Street compose failed: {exc}\n{detail}", [], "", None, []


def run_best_model_street(
    dataset_profile: str,
    query: str,
    real_manifest_text: str,
    artifacts_dir_text: str,
    model_name: str,
    model_dir_text: str,
    local_files_only: bool,
    device: str,
    street_length_m: float,
    street_road_width_m: float,
    street_sidewalk_width_m: float,
    street_lane_count: int,
    street_density: float,
    street_seed: int,
    street_topk_per_category: int,
    street_max_trials_per_slot: int,
    export_format: str,
    policy_ckpt_text: str,
    policy_temperature: float,
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
                ],
                seed_start=0,
                seed_end=19,
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


def _toggle_osm_visibility(layout_mode: str) -> Dict[str, Any]:
    return gr.update(visible=str(layout_mode).strip().lower() == "osm")


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

    with gr.Blocks(title="RoadGen3D StreetGen") as demo:
        gr.Markdown("# RoadGen3D 神经符号街道生成")
        gr.Markdown("默认工作流：`准备 -> 生成 -> 研究`")

        with gr.Tabs():
            with gr.Tab("1) 准备"):
                gr.Markdown("一键准备工作区：校验 manifest、补齐 latent、构建索引，并在需要时预热 OSM cache。")
                with gr.Row():
                    dataset_profile = gr.Dropdown(label="数据源", choices=["real", "mock"], value="real")
                    model_name = gr.Textbox(label="CLIP Model", value="openai/clip-vit-base-patch32")
                    local_files_only = gr.Checkbox(label="Local Files Only", value=True)
                    device = gr.Dropdown(label="Device", choices=["cpu", "mps", "cuda"], value="cpu")
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
                        prepare_layout_mode = gr.Dropdown(label="Prepare Layout Mode", choices=["template", "osm"], value="template")
                        osm_cache_dir = gr.Textbox(label="OSM Cache Dir", value=default_osm_cache_dir)
                        force_reindex = gr.Checkbox(label="Force Reindex", value=False)
                        force_reencode = gr.Checkbox(label="Force Reencode", value=False)
                        force_osm_refresh = gr.Checkbox(label="Force OSM Refresh", value=False)
                    with gr.Row(visible=False) as prepare_bbox_row:
                        prepare_bbox_min_lon = gr.Number(label="AOI Min Lon", value=0.0)
                        prepare_bbox_min_lat = gr.Number(label="AOI Min Lat", value=0.0)
                        prepare_bbox_max_lon = gr.Number(label="AOI Max Lon", value=0.0)
                        prepare_bbox_max_lat = gr.Number(label="AOI Max Lat", value=0.0)

            with gr.Tab("2) 生成街道"):
                gr.Markdown("默认入口：先生成 `StreetProgram`，再做约束求解与资产实现。")
                query = gr.Textbox(label="Query", value="pedestrian-friendly boulevard with transit access")
                with gr.Row():
                    m5_layout_mode = gr.Dropdown(label="Layout Mode", choices=["template", "osm"], value="template")
                    design_rule_profile = gr.Dropdown(
                        label="Design Rule Profile",
                        choices=["balanced_complete_street_v1", "pedestrian_priority_v1", "transit_priority_v1"],
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
                with gr.Row(visible=False) as street_bbox_row:
                    m5_bbox_min_lon = gr.Number(label="AOI Min Lon", value=0.0)
                    m5_bbox_min_lat = gr.Number(label="AOI Min Lat", value=0.0)
                    m5_bbox_max_lon = gr.Number(label="AOI Max Lon", value=0.0)
                    m5_bbox_max_lat = gr.Number(label="AOI Max Lat", value=0.0)
                street_btn = gr.Button("Run Street", variant="primary")
                with gr.Row():
                    street_model_view = gr.Model3D(label="Street Preview (GLB)")
                    street_summary = gr.Textbox(label="Scene Summary", lines=10)
                with gr.Row():
                    street_program_summary = gr.Code(label="StreetProgram Summary", language="json")
                    street_solver_summary = gr.Code(label="Solver Edits / Conflicts", language="json")
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
                with gr.Accordion("Scene Details", open=False):
                    street_instances = gr.Dataframe(
                        headers=["instance_id", "asset_id", "category", "score", "x", "z", "yaw_deg", "source"],
                        datatype=["str", "str", "str", "str", "str", "str", "str", "str"],
                        row_count=(0, "dynamic"),
                        col_count=(8, "fixed"),
                        label="Street Instances",
                    )
                    street_layout_json = gr.Code(label="Street Layout JSON", language="json")
                    street_files = gr.Files(label="Scene Downloads")

            with gr.Tab("3) 研究与训练"):
                gr.Markdown("研究工具：用于改进 `learned_v1` / `learned policy`，不是默认运行入口。")
                gr.Markdown("`Run Best Model` 使用当前“生成街道”页的查询与街道设置。")
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
                        program_train_epochs = gr.Number(label="Program Epochs", value=20, precision=0)
                        program_train_batch_size = gr.Number(label="Program Batch Size", value=128, precision=0)
                        program_train_lr = gr.Number(label="Program LR", value=1e-3)
                        program_train_weight_decay = gr.Number(label="Program Weight Decay", value=1e-4)
                        program_train_patience = gr.Number(label="Program Patience", value=3, precision=0)
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
                    run_best_layout_json = gr.Code(label="Run Best Layout JSON", language="json")
                    run_best_model_view = gr.Model3D(label="Run Best Street Preview (GLB)")
                    run_best_files = gr.Files(label="Run Best Downloads")

        prepare_layout_mode.change(
            fn=_toggle_osm_visibility,
            inputs=[prepare_layout_mode],
            outputs=[prepare_bbox_row],
        )
        m5_layout_mode.change(
            fn=_toggle_osm_visibility,
            inputs=[m5_layout_mode],
            outputs=[street_bbox_row],
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
            outputs=[prepare_summary, readiness_json, readiness_cards, prepare_steps],
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
            ],
            outputs=[
                street_summary,
                street_instances,
                street_layout_json,
                street_model_view,
                street_files,
            ],
        ).then(
            fn=_extract_program_summary,
            inputs=[street_layout_json],
            outputs=[street_program_summary],
        ).then(
            fn=_extract_solver_summary,
            inputs=[street_layout_json],
            outputs=[street_solver_summary],
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
            fn=_extract_program_summary,
            inputs=[run_best_layout_json],
            outputs=[run_best_program_summary],
        ).then(
            fn=_extract_solver_summary,
            inputs=[run_best_layout_json],
            outputs=[run_best_solver_summary],
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
