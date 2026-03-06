#!/usr/bin/env python3
"""Gradio UI for RoadGen3D milestone pipelines."""

from __future__ import annotations

import argparse
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
from roadgen3d.pipeline import M1Pipeline
from roadgen3d.street_layout import compose_street_scene
from roadgen3d.types import StreetComposeConfig
from scripts.m1_01_seed_assets import seed_assets
from scripts.m2_11_encode_shapee_latents import encode_latents as encode_shapee_latents
from scripts.m4_01_collect_policy_data import collect_policy_data
from scripts.m4_02_train_layout_policy import train_from_jsonl
from scripts.m4_10_eval_engineering import run_eval as run_m4_eval


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
            constraint_weight=float(m5_constraint_weight),
            constraint_veto_threshold=float(m5_constraint_veto),
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
            policy_temperature=float(policy_temperature),
        )

        layout_path = Path(result.outputs["scene_layout"])
        layout_json_text = layout_path.read_text(encoding="utf-8")
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
            f"- scene_layout: {result.outputs.get('scene_layout', '')}"
        )
        if result.outputs.get("policy_fallback_reason"):
            summary += f"\n- policy_fallback_reason: {result.outputs['policy_fallback_reason']}"
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
) -> Tuple[str, List[List[str]], str, str | None, List[str], str, str, str | None, List[str]]:
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
    )
    best_log = (
        "Best model run done.\n"
        f"- policy_mode: learned\n"
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
    default_m4_queries = str((ROOT / "data/eval/queries_m4.txt").resolve())
    default_policy_ckpt = str((ROOT / "artifacts/m4/layout_policy.pt").resolve())

    with gr.Blocks(title="RoadGen3D M5 Gradio") as demo:
        gr.Markdown("# RoadGen3D M5 UI")

        with gr.Tabs():
            # ── Tab A: 准备与索引 ──
            with gr.Tab("A: 准备与索引"):
                gr.Markdown(
                    """
                    **为什么先准备索引与 latent（RAG 入口）**
                    - 输入：`real_assets_manifest.jsonl`（含 asset_id / text_desc / latent_path）+ CLIP 模型
                    - 输出：`index_ip.faiss` / `id_map.json` / `real_assets_for_pipeline.jsonl`
                    - 步骤 1 生成 CLIP embedding 并构建 FAISS inner-product 索引；步骤 2 为每个 mesh 编码 Shape-E latent
                    - 后续步骤 3/4/5/6 均依赖此索引与 latent，若数据变动需重新执行
                    """
                )
                with gr.Row():
                    prepare_btn = gr.Button("1) Prepare Assets + Index", variant="primary")
                    encode_btn = gr.Button("2) Prepare Real Latents", variant="primary")
                with gr.Row():
                    dataset_profile = gr.Dropdown(label="Dataset Profile", choices=["mock", "real"], value="real")
                    model_name = gr.Textbox(label="Model Name", value="openai/clip-vit-base-patch32")
                    local_files_only = gr.Checkbox(label="Local Files Only", value=True)
                    device = gr.Dropdown(label="Device", choices=["cpu", "mps", "cuda"], value="cpu")
                with gr.Row():
                    real_manifest = gr.Textbox(label="Real Manifest Path", value=default_real_manifest)
                    artifacts_dir = gr.Textbox(label="Artifacts Dir", value=default_artifacts)
                    model_dir = gr.Textbox(label="CLIP Model Dir", value=default_model_dir)
                with gr.Accordion("Encode Parameters", open=False):
                    with gr.Row():
                        encode_mode = gr.Dropdown(label="Encode Mode", choices=["mesh_ref", "auto", "shapee"], value="mesh_ref")
                        shapee_model_dir = gr.Textbox(label="Shape-E Model Dir", value=default_shapee_model_dir)
                        shapee_local_only = gr.Checkbox(label="Shape-E Local Only", value=True)
                    with gr.Row():
                        real_mesh_root = gr.Textbox(label="Real Mesh Root", value=default_real_mesh_root)
                        real_latents_dir = gr.Textbox(label="Real Latents Dir", value=default_real_latents_dir)
                        render_cache_dir = gr.Textbox(label="Shape-E Render Cache Dir", value=default_render_cache_dir)
                    with gr.Row():
                        encode_skip_existing = gr.Checkbox(label="Encode: Skip Existing", value=False)
                        encode_no_placeholder_fallback = gr.Checkbox(label="Encode: No Placeholder Fallback", value=True)
                        encode_no_mesh_reference_fallback = gr.Checkbox(label="Encode: No Mesh-Reference Fallback", value=False)
                        encode_verbose = gr.Checkbox(label="Encode: Verbose", value=False)
                with gr.Accordion("Mock Dataset Parameters (dataset_profile=mock)", open=False):
                    with gr.Row():
                        data_dir = gr.Textbox(label="Data Dir (mock)", value=default_data)
                        num_assets = gr.Slider(label="Num Assets (mock)", minimum=1, maximum=256, step=1, value=8)
                    with gr.Row():
                        seed = gr.Number(label="Seed", value=42, precision=0)
                        latent_dim = gr.Number(label="Latent Dim", value=256, precision=0)
                prepare_log = gr.Textbox(label="Prepare Log", lines=8)
                encode_log = gr.Textbox(label="Encode Log", lines=8)
                assets_preview = gr.Dataframe(
                    headers=["asset_id", "description", "latent_path"],
                    datatype=["str", "str", "str"],
                    row_count=(0, "dynamic"),
                    col_count=(3, "fixed"),
                    label="Assets Preview",
                )

            # ── Tab B: 推理与街道 ──
            with gr.Tab("B: 推理与街道"):
                gr.Markdown(
                    """
                    **单资产链路：text → retrieve → latent/mesh_ref → voxel → mesh**
                    - 步骤 3 根据 query 在 FAISS 索引中检索 top-k 资产，通过 Shape-E decoder 生成 voxel 并导出 GLB/PLY
                    - 步骤 4 街道链路：多类别资产检索 + AABB 碰撞编排 + 完整街道场景导出
                    - voxel 路径用于诊断；街道主路径直接组合 mesh reference，无需 voxel 中间步骤
                    - 可选 learned policy（来自 Tab C 训练）或 rule-based 启发式策略
                    """
                )
                with gr.Row():
                    run_btn = gr.Button("3) Run Query Pipeline", variant="primary")
                    street_btn = gr.Button("4) Run Street Compose", variant="primary")
                with gr.Row():
                    query = gr.Textbox(label="Query", value="a wooden park bench")
                    topk = gr.Slider(label="Top K", minimum=1, maximum=10, step=1, value=1)
                    decoder_choice = gr.Dropdown(label="Decoder", choices=["placeholder", "shapee"], value="shapee")
                    shapee_strict = gr.Checkbox(label="Shape-E Strict (no fallback)", value=True)
                with gr.Accordion("Voxel & Export", open=False):
                    with gr.Row():
                        resolution = gr.Slider(label="Resolution", minimum=16, maximum=128, step=16, value=64)
                        threshold = gr.Slider(label="Threshold", minimum=0.05, maximum=0.95, step=0.05, value=0.5)
                        voxel_size = gr.Number(label="Voxel Size", value=0.1)
                    with gr.Row():
                        export_method = gr.Dropdown(label="Export Method", choices=["marching_cubes", "cubes"], value="marching_cubes")
                        export_format = gr.Dropdown(label="Export Format", choices=["both", "glb", "ply"], value="both")
                with gr.Accordion("Street Parameters", open=False):
                    with gr.Row():
                        street_length_m = gr.Number(label="Street Length (m)", value=80.0)
                        street_road_width_m = gr.Number(label="Road Width (m)", value=8.0)
                        street_sidewalk_width_m = gr.Number(label="Sidewalk Width (m)", value=2.5)
                        street_lane_count = gr.Slider(label="Lane Count", minimum=1, maximum=4, step=1, value=2)
                    with gr.Row():
                        street_density = gr.Slider(label="Street Density", minimum=0.2, maximum=2.0, step=0.1, value=1.0)
                        street_seed = gr.Number(label="Street Seed", value=42, precision=0)
                        street_topk_per_category = gr.Slider(label="TopK Per Category", minimum=1, maximum=50, step=1, value=20)
                        street_max_trials_per_slot = gr.Slider(label="Max Trials Per Slot", minimum=1, maximum=100, step=1, value=30)
                    with gr.Row():
                        street_placement_policy = gr.Dropdown(label="Street Placement Policy", choices=["rule", "learned"], value="learned")
                        policy_ckpt = gr.Textbox(label="Policy CKPT Path", value=default_policy_ckpt)
                        policy_temperature = gr.Number(label="Policy Temperature", value=0.12)
                gr.Markdown("#### Single Asset Results")
                run_summary = gr.Textbox(label="Run Summary", lines=9)
                hits_table = gr.Dataframe(
                    headers=["asset_id", "score"],
                    datatype=["str", "str"],
                    row_count=(0, "dynamic"),
                    col_count=(2, "fixed"),
                    label="Top-K Retrieval Hits",
                )
                result_json = gr.Code(label="Pipeline Result JSON", language="json")
                model_view = gr.Model3D(label="3D Preview (GLB)")
                mesh_files = gr.Files(label="Mesh Downloads (GLB/PLY)")
                gr.Markdown("#### Street Compose Results")
                street_summary = gr.Textbox(label="Street Compose Summary", lines=8)
                street_instances = gr.Dataframe(
                    headers=["instance_id", "asset_id", "category", "score", "x", "z", "yaw_deg", "source"],
                    datatype=["str", "str", "str", "str", "str", "str", "str", "str"],
                    row_count=(0, "dynamic"),
                    col_count=(8, "fixed"),
                    label="Street Instances",
                )
                street_layout_json = gr.Code(label="Street Layout JSON", language="json")
                street_model_view = gr.Model3D(label="Street Scene Preview (GLB)")
                street_files = gr.Files(label="Street Scene Downloads")

            # ── Tab C: M4 训练评测 ──
            with gr.Tab("C: M4 训练评测"):
                gr.Markdown(
                    """
                    **M4 可学习布局器：输入 / 流程 / 输出**
                    - 输入：
                      `real manifest + FAISS index + queries + seed 范围 + 当前 checkpoint(可选)`
                    - 中间流程：
                      `rule-based policy` 先在多 seed 场景上蒸馏 `slot→candidate` 样本（含 AABB 通过/碰撞），
                      再训练 `learned policy` 候选打分函数；可 `resume` 续训，也可 `recollect` 重采样蒸馏数据。
                    - 训练目标：
                      使 learned policy 的放置决策质量优于或接近 rule 启发式（在相同约束下）。
                    - 评测流程：
                      learned vs rule 在相同 seed 对比，核心指标为
                      `instance_count / dropped_slots`（并输出完整工程指标报告）。
                    - 输出：
                      `layout_policy.pt`、训练日志与 loss 曲线、`eval_report.json`、可视化街道结果（GLB/JSON）。
                    """
                )
                with gr.Row():
                    train_btn = gr.Button("5) Train Layout Policy (M4)", variant="primary")
                    run_best_model_btn = gr.Button("6) Run Best Model (Street)", variant="primary")
                with gr.Row():
                    m4_artifacts_dir = gr.Textbox(label="M4 Artifacts Dir", value=default_m4_artifacts_dir)
                    m4_queries = gr.Textbox(label="M4 Queries File", value=default_m4_queries)
                with gr.Row():
                    m4_collect_seed_start = gr.Number(label="Collect Seed Start", value=0, precision=0)
                    m4_collect_seed_end = gr.Number(label="Collect Seed End", value=49, precision=0)
                    m4_eval_seed_start = gr.Number(label="Eval Seed Start", value=0, precision=0)
                    m4_eval_seed_end = gr.Number(label="Eval Seed End", value=4, precision=0)
                with gr.Row():
                    m4_recollect_data = gr.Checkbox(label="Recollect Distilled Data", value=True)
                    m4_resume_training = gr.Checkbox(label="Resume From Existing CKPT", value=True)
                    m4_run_eval_after_train = gr.Checkbox(label="Run Eval After Train", value=True)
                with gr.Accordion("Training Hyperparameters", open=False):
                    with gr.Row():
                        m4_train_epochs = gr.Number(label="Train Epochs", value=20, precision=0)
                        m4_train_batch_size = gr.Number(label="Train Batch Size", value=256, precision=0)
                        m4_train_lr = gr.Number(label="Train LR", value=1e-3)
                    with gr.Row():
                        m4_train_weight_decay = gr.Number(label="Train Weight Decay", value=1e-4)
                        m4_train_entropy_weight = gr.Number(label="Train Entropy Weight", value=0.01)
                        m4_train_patience = gr.Number(label="Train Patience", value=3, precision=0)
                m4_progress = gr.Slider(
                    label="M4 Progress (%) [Distill + Train + Eval]",
                    minimum=0.0,
                    maximum=100.0,
                    value=0.0,
                    step=0.1,
                    interactive=False,
                )
                m4_loss_curve = gr.Plot(label="M4 Train/Val Loss Curve")
                m4_train_log = gr.Textbox(label="M4 Train Log", lines=8)
                m4_train_json = gr.Code(label="M4 Train Summary JSON", language="json")
                m4_eval_json = gr.Code(label="M4 Eval Report JSON", language="json")
                run_best_log = gr.Textbox(label="Run Best Model Log", lines=8)
                with gr.Accordion("Run Best Layout JSON", open=False):
                    run_best_layout_json = gr.Code(label="Run Best Layout JSON", language="json")
                run_best_model_view = gr.Model3D(label="Run Best Street Preview (GLB)")
                run_best_files = gr.Files(label="Run Best Downloads")

            # ── Tab D: M5 OSM 约束 ──
            with gr.Tab("D: M5 OSM 约束"):
                gr.Markdown(
                    """
                    **M5: OSM 道路放置区 + POI 规则引擎 + 合规评估**
                    - Layout Mode `osm`：从 OpenStreetMap 获取真实道路几何，在人行道区域内采样家具放置位置
                    - Layout Mode `template`：使用 M3/M4 模板化道路（默认，向后兼容）
                    - Constraint Mode `soft`：根据 POI（入口/消防栓/公交站）距离计算惩罚分数，结合检索分数进行 best-of-K 选择
                    - Constraint Mode `off`：关闭约束评分，仅使用检索分数排序
                    - AOI BBox：感兴趣区域的经纬度边界框（仅 `osm` 模式需要），格式为 `(min_lon, min_lat, max_lon, max_lat)`
                    - 合规评估：统计违规率、可行性分数、逐规则违规次数
                    """
                )
                gr.Markdown("#### OSM & Constraint Parameters")
                with gr.Row():
                    m5_layout_mode = gr.Dropdown(label="Layout Mode", choices=["template", "osm"], value="template")
                    m5_constraint_mode = gr.Dropdown(label="Constraint Mode", choices=["off", "soft"], value="soft")
                with gr.Row():
                    m5_constraint_weight = gr.Slider(label="Constraint Weight (λ)", minimum=0.0, maximum=1.0, step=0.05, value=0.45)
                    m5_constraint_veto = gr.Slider(label="Veto Threshold", minimum=0.0, maximum=1.0, step=0.05, value=0.95)
                gr.Markdown("#### AOI Bounding Box (WGS-84)")
                with gr.Row():
                    m5_bbox_min_lon = gr.Number(label="Min Lon", value=0.0)
                    m5_bbox_min_lat = gr.Number(label="Min Lat", value=0.0)
                    m5_bbox_max_lon = gr.Number(label="Max Lon", value=0.0)
                    m5_bbox_max_lat = gr.Number(label="Max Lat", value=0.0)
                gr.Markdown(
                    """
                    > **提示**：在 Tab B 点击 `4) Run Street Compose` 或 Tab C 点击 `6) Run Best Model` 时，
                    > 上述 M5 参数会自动传入街道生成流程。`osm` 模式需要先填写有效的 AOI BBox。
                    """
                )

        # ── Event Bindings (unchanged) ──
        prepare_btn.click(
            fn=prepare_assets_and_index,
            inputs=[
                dataset_profile,
                data_dir,
                artifacts_dir,
                real_manifest,
                num_assets,
                seed,
                latent_dim,
                model_name,
                model_dir,
                local_files_only,
                device,
            ],
            outputs=[prepare_log, assets_preview],
        )
        encode_btn.click(
            fn=_encode_start_log,
            inputs=[dataset_profile, encode_mode],
            outputs=[encode_log],
            queue=False,
        ).then(
            fn=encode_real_latents,
            inputs=[
                dataset_profile,
                real_manifest,
                real_mesh_root,
                real_latents_dir,
                shapee_model_dir,
                render_cache_dir,
                encode_mode,
                device,
                shapee_local_only,
                encode_skip_existing,
                encode_no_placeholder_fallback,
                encode_no_mesh_reference_fallback,
                encode_verbose,
            ],
            outputs=[encode_log],
        )
        run_btn.click(
            fn=run_query_pipeline,
            inputs=[
                dataset_profile,
                query,
                topk,
                data_dir,
                artifacts_dir,
                real_manifest,
                model_name,
                model_dir,
                local_files_only,
                device,
                decoder_choice,
                shapee_model_dir,
                shapee_strict,
                resolution,
                threshold,
                voxel_size,
                export_method,
                export_format,
            ],
            outputs=[run_summary, hits_table, result_json, model_view, mesh_files],
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
            ],
            outputs=[
                street_summary,
                street_instances,
                street_layout_json,
                street_model_view,
                street_files,
            ],
        )
        train_btn.click(
            fn=run_m4_train_policy,
            inputs=[
                dataset_profile,
                real_manifest,
                artifacts_dir,
                m4_artifacts_dir,
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
            ],
            outputs=[m4_train_log, m4_train_json, m4_eval_json, policy_ckpt, m4_progress, m4_loss_curve],
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
