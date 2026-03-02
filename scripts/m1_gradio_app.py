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
from typing import Dict, Iterator, List, Tuple

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
) -> Iterator[Tuple[str, str, str, str]]:
    started_at = datetime.now()
    profile = dataset_profile.strip().lower()
    if profile != "real":
        yield "M4 training requires dataset_profile='real'.", "{}", "{}", policy_ckpt_text
        return

    log_lines = [
        "M4 training started...",
        f"- started_at: {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- recollect_data: {bool(m4_recollect_data)}",
        f"- resume_training: {bool(m4_resume_training)}",
        f"- run_eval_after_train: {bool(m4_run_eval_after_train)}",
    ]
    train_json = "{}"
    eval_json = "{}"
    ckpt_out = policy_ckpt_text
    epoch_curve: List[Dict[str, float]] = []

    def _snapshot() -> Tuple[str, str, str, str]:
        return ("\n".join(log_lines), train_json, eval_json, ckpt_out)

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
                events.put(("log", f"- collecting distilled data -> {data_path}"))
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
                )
                events.put(("log", f"- collected_rows: {len(collected_rows)}"))
            else:
                events.put(("log", f"- reuse existing distilled data: {data_path}"))

            events.put(("log", "- training policy model..."))
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
                events.put(("log", "- running engineering eval (learned vs rule)..."))
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

    while not done_event.is_set() or not events.empty():
        try:
            event, payload = events.get(timeout=0.2)
        except queue.Empty:
            continue

        if event == "log":
            log_lines.append(str(payload))
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
            log_lines.append("- eval done.")
        elif event == "error":
            log_lines.append(str(payload))
        elif event == "done":
            duration_sec = time.time() - started_at.timestamp()
            log_lines.append(f"- duration_sec: {duration_sec:.2f}")

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

    with gr.Blocks(title="RoadGen3D M4 Gradio") as demo:
        learned_policy_state = gr.State("learned")
        gr.Markdown(
            """
            # RoadGen3D M4 UI
            - Top buttons first: `1/2/3/4/5/6`，覆盖准备、检索、街道组合与可学习策略训练。
            - Default profile: `real`, decoder: `shapee (strict)`, street policy: `learned`
            """
        )

        with gr.Row():
            decoder_choice = gr.Dropdown(
                label="Decoder",
                choices=["placeholder", "shapee"],
                value="shapee",
            )
            dataset_profile = gr.Dropdown(
                label="Dataset Profile",
                choices=["mock", "real"],
                value="real",
            )
            query = gr.Textbox(label="Query", value="a wooden park bench")
            topk = gr.Slider(label="Top K", minimum=1, maximum=10, step=1, value=1)

        # Put action buttons at the top to avoid missing critical operations.
        with gr.Row():
            prepare_btn = gr.Button("1) Prepare Assets + Index", variant="primary")
            encode_btn = gr.Button("2) Prepare Real Latents", variant="primary")
            run_btn = gr.Button("3) Run Query Pipeline", variant="primary")
            street_btn = gr.Button("4) Run Street Compose", variant="primary")
            train_btn = gr.Button("5) Train Layout Policy (M4)", variant="primary")
            train_and_street_btn = gr.Button("6) Train + Run Street", variant="primary")

        with gr.Row():
            real_manifest = gr.Textbox(label="Real Manifest Path", value=default_real_manifest)
            artifacts_dir = gr.Textbox(label="Artifacts Dir", value=default_artifacts)
            model_dir = gr.Textbox(label="CLIP Model Dir", value=default_model_dir)
            shapee_model_dir = gr.Textbox(label="Shape-E Model Dir", value=default_shapee_model_dir)

        with gr.Accordion("Advanced Parameters", open=False):
            with gr.Row():
                encode_mode = gr.Dropdown(
                    label="Encode Mode",
                    choices=["mesh_ref", "auto", "shapee"],
                    value="mesh_ref",
                )
                shapee_strict = gr.Checkbox(label="Shape-E Strict (no fallback)", value=True)
                local_files_only = gr.Checkbox(label="Local Files Only", value=True)
                device = gr.Dropdown(label="Device", choices=["cpu", "mps", "cuda"], value="cpu")
                shapee_local_only = gr.Checkbox(label="Shape-E Local Only", value=True)
            with gr.Row():
                real_mesh_root = gr.Textbox(label="Real Mesh Root", value=default_real_mesh_root)
                real_latents_dir = gr.Textbox(label="Real Latents Dir", value=default_real_latents_dir)
                render_cache_dir = gr.Textbox(label="Shape-E Render Cache Dir", value=default_render_cache_dir)
            with gr.Row():
                encode_skip_existing = gr.Checkbox(label="Encode: Skip Existing", value=False)
                encode_no_placeholder_fallback = gr.Checkbox(
                    label="Encode: No Placeholder Fallback",
                    value=True,
                )
                encode_no_mesh_reference_fallback = gr.Checkbox(
                    label="Encode: No Mesh-Reference Fallback",
                    value=False,
                )
                encode_verbose = gr.Checkbox(label="Encode: Verbose", value=False)
            with gr.Row():
                resolution = gr.Slider(label="Resolution", minimum=16, maximum=128, step=16, value=64)
                threshold = gr.Slider(label="Threshold", minimum=0.05, maximum=0.95, step=0.05, value=0.5)
                voxel_size = gr.Number(label="Voxel Size", value=0.1)
            with gr.Row():
                export_method = gr.Dropdown(
                    label="Export Method",
                    choices=["marching_cubes", "cubes"],
                    value="marching_cubes",
                )
                export_format = gr.Dropdown(
                    label="Export Format",
                    choices=["both", "glb", "ply"],
                    value="both",
                )
                model_name = gr.Textbox(label="Model Name", value="openai/clip-vit-base-patch32")
            with gr.Row():
                street_placement_policy = gr.Dropdown(
                    label="Street Placement Policy",
                    choices=["rule", "learned"],
                    value="learned",
                )
                policy_ckpt = gr.Textbox(label="Policy CKPT Path", value=default_policy_ckpt)
                policy_temperature = gr.Number(label="Policy Temperature", value=0.12)
            with gr.Row():
                street_length_m = gr.Number(label="Street Length (m)", value=80.0)
                street_road_width_m = gr.Number(label="Road Width (m)", value=8.0)
                street_sidewalk_width_m = gr.Number(label="Sidewalk Width (m)", value=2.5)
                street_lane_count = gr.Slider(label="Lane Count", minimum=1, maximum=4, step=1, value=2)
            with gr.Row():
                street_density = gr.Slider(label="Street Density", minimum=0.2, maximum=2.0, step=0.1, value=1.0)
                street_seed = gr.Number(label="Street Seed", value=42, precision=0)
                street_topk_per_category = gr.Slider(
                    label="TopK Per Category",
                    minimum=1,
                    maximum=50,
                    step=1,
                    value=20,
                )
                street_max_trials_per_slot = gr.Slider(
                    label="Max Trials Per Slot",
                    minimum=1,
                    maximum=100,
                    step=1,
                    value=30,
                )

        with gr.Accordion("Mock Dataset Parameters (Only for dataset_profile=mock)", open=False):
            with gr.Row():
                data_dir = gr.Textbox(label="Data Dir (mock)", value=default_data)
                num_assets = gr.Slider(label="Num Assets (mock)", minimum=1, maximum=256, step=1, value=8)
            with gr.Row():
                seed = gr.Number(label="Seed", value=42, precision=0)
                latent_dim = gr.Number(label="Latent Dim", value=256, precision=0)

        with gr.Accordion("M4 Training & Evaluation", open=False):
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
            with gr.Row():
                m4_train_epochs = gr.Number(label="Train Epochs", value=20, precision=0)
                m4_train_batch_size = gr.Number(label="Train Batch Size", value=256, precision=0)
                m4_train_lr = gr.Number(label="Train LR", value=1e-3)
            with gr.Row():
                m4_train_weight_decay = gr.Number(label="Train Weight Decay", value=1e-4)
                m4_train_entropy_weight = gr.Number(label="Train Entropy Weight", value=0.01)
                m4_train_patience = gr.Number(label="Train Patience", value=3, precision=0)

        prepare_log = gr.Textbox(label="Prepare Log", lines=8)
        encode_log = gr.Textbox(label="Encode Log", lines=8)
        m4_train_log = gr.Textbox(label="M4 Train Log", lines=8)
        m4_train_json = gr.Code(label="M4 Train Summary JSON", language="json")
        m4_eval_json = gr.Code(label="M4 Eval Report JSON", language="json")
        assets_preview = gr.Dataframe(
            headers=["asset_id", "description", "latent_path"],
            datatype=["str", "str", "str"],
            row_count=(0, "dynamic"),
            col_count=(3, "fixed"),
            label="Assets Preview",
        )

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
                learned_policy_state,
                policy_ckpt,
                policy_temperature,
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
            outputs=[m4_train_log, m4_train_json, m4_eval_json, policy_ckpt],
        )
        train_and_street_btn.click(
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
            outputs=[m4_train_log, m4_train_json, m4_eval_json, policy_ckpt],
        ).then(
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
            ],
            outputs=[
                street_summary,
                street_instances,
                street_layout_json,
                street_model_view,
                street_files,
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
