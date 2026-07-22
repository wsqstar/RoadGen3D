"""Asynchronous single-feature generation and visual-review runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from typing import Any, Dict, Mapping
from uuid import uuid4

from ..json_safe import make_json_safe
from .design_types import DesignDraft, sanitize_compose_config_patch, sanitize_scene_context
from .feature_quality_lab import (
    FEATURE_TARGETS,
    TRI_VIEW_IDS,
    build_feature_experiment,
    build_feature_review_messages,
    feature_tri_views_from_layout,
    normalize_feature_review,
    write_feature_contact_sheet,
)


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FEATURE_RUN_DIR = (ROOT / "artifacts" / "feature_quality_runs").resolve()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class FeatureVariantRun:
    variant_id: str
    label: str
    patch: Dict[str, Any]
    status: str = "pending"
    progress: int = 0
    layout_path: str = ""
    scene_glb_path: str = ""
    views: list[Dict[str, Any]] = field(default_factory=list)
    review: Dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    error: str = ""


@dataclass
class FeatureRunState:
    run_id: str
    experiment: Dict[str, Any]
    scene_context: Dict[str, Any]
    generation_options: Dict[str, Any]
    output_dir: Path
    visual_review: bool = True
    status: str = "queued"
    stage: str = "queued"
    progress: int = 0
    variants: list[FeatureVariantRun] = field(default_factory=list)
    accepted_variant_id: str = ""
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    error: str = ""


class FeatureQualityRunService:
    """Generate 3–6 isolated variants and preserve their fixed tri-view reviews."""

    def __init__(self, *, design_service: Any, output_root: str | Path | None = None) -> None:
        self.design_service = design_service
        self.output_root = Path(output_root or DEFAULT_FEATURE_RUN_DIR).expanduser().resolve()
        self._runs: Dict[str, FeatureRunState] = {}
        self._queue: Queue[str] = Queue()
        self._lock = Lock()
        self._worker: Thread | None = None

    def submit_run(
        self,
        *,
        target_id: str,
        brief: str,
        variant_count: int,
        base_patch: Mapping[str, Any] | None = None,
        graph_template_id: str = "hkust_gz_gate",
        scene_context: Mapping[str, Any] | None = None,
        generation_options: Mapping[str, Any] | None = None,
        visual_review: bool = True,
    ) -> Dict[str, Any]:
        count = max(3, min(int(variant_count or 3), 6))
        target = FEATURE_TARGETS.get(str(target_id or "").strip())
        if target is None:
            raise ValueError(f"target_id must be one of: {', '.join(sorted(FEATURE_TARGETS))}")
        clean_base = sanitize_compose_config_patch(base_patch)
        fixed_patch = {key: value for key, value in clean_base.items() if key not in target.allowed_fields}
        seed = int(clean_base.get("seed", 42) or 42)
        variants = _variant_candidates(target.target_id, clean_base, count)
        run_id = uuid4().hex
        experiment = build_feature_experiment(
            experiment_id=f"{target.target_id}-{run_id[:8]}",
            target_id=target.target_id,
            brief=brief,
            fixed_patch=fixed_patch,
            variants=variants,
            seed=seed,
            graph_template_id=graph_template_id,
        )
        state = FeatureRunState(
            run_id=run_id,
            experiment=experiment,
            scene_context={"layout_mode": "graph_template", "graph_template_id": graph_template_id, **dict(scene_context or {})},
            generation_options=dict(generation_options or {}),
            output_dir=self.output_root / run_id,
            visual_review=bool(visual_review),
            variants=[
                FeatureVariantRun(
                    variant_id=str(item["variant_id"]),
                    label=str(item["label"]),
                    patch=dict(item["patch"]),
                )
                for item in experiment["variants"]
            ],
        )
        with self._lock:
            self._runs[run_id] = state
        self._ensure_worker()
        self._queue.put(run_id)
        return self._payload(state)

    def get_run(self, run_id: str) -> Dict[str, Any] | None:
        with self._lock:
            state = self._runs.get(str(run_id or "").strip())
            return self._payload(state) if state is not None else None

    def accept_variant(self, run_id: str, variant_id: str) -> Dict[str, Any]:
        with self._lock:
            state = self._runs.get(str(run_id or "").strip())
            if state is None:
                raise KeyError(run_id)
            variant = next((item for item in state.variants if item.variant_id == variant_id), None)
            if variant is None:
                raise KeyError(variant_id)
            if variant.status != "succeeded":
                raise ValueError("only a succeeded variant can be accepted")
            state.accepted_variant_id = variant.variant_id
            self._write_manifest(state)
            fixed = dict(state.experiment.get("controls", {}).get("fixed_patch", {}) or {})
            return {
                "run_id": state.run_id,
                "accepted_variant_id": variant.variant_id,
                "patch": {**fixed, **variant.patch},
                "layout_path": variant.layout_path,
                "scene_glb_path": variant.scene_glb_path,
            }

    def artifact_path(self, run_id: str, variant_id: str, view_id: str) -> Path | None:
        with self._lock:
            state = self._runs.get(str(run_id or "").strip())
            if state is None:
                return None
            variant = next((item for item in state.variants if item.variant_id == variant_id), None)
            if variant is None:
                return None
            view = next((item for item in variant.views if str(item.get("view_id")) == view_id), None)
            path_text = str((view or {}).get("path") or "")
            if not path_text:
                return None
            path = Path(path_text).expanduser().resolve()
            try:
                path.relative_to(state.output_dir.resolve())
            except ValueError:
                return None
            return path if path.exists() and path.is_file() else None

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = Thread(target=self._worker_loop, name="roadgen3d-feature-quality-worker", daemon=True)
        self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            run_id = self._queue.get()
            with self._lock:
                state = self._runs.get(run_id)
                if state is None:
                    continue
                state.status = "running"
                state.stage = "generating"
                state.started_at = _now()
            try:
                self._execute(state)
            except Exception as exc:
                with self._lock:
                    state.status = "failed"
                    state.stage = "failed"
                    state.error = str(exc)
                    state.finished_at = _now()
                    self._write_manifest(state)

    def _execute(self, state: FeatureRunState) -> None:
        state.output_dir.mkdir(parents=True, exist_ok=True)
        fixed = dict(state.experiment.get("controls", {}).get("fixed_patch", {}) or {})
        total = len(state.variants)
        for index, variant in enumerate(state.variants):
            with self._lock:
                variant.status = "running"
                variant.progress = 5
                state.progress = int(index / total * 100)
            variant_dir = state.output_dir / variant.variant_id
            variant_dir.mkdir(parents=True, exist_ok=True)
            patch = {**fixed, **variant.patch}
            generation_options = dict(state.generation_options)
            # The Viewer parameter spec describes the current scene and would
            # recompile over the per-variant patch.  Feature runs keep its
            # asset/runtime options but use the isolated canonical patch as the
            # sole parameter source for this batch.
            generation_options.pop("street_design_parameter_spec", None)
            generation_options.pop("parameter_sources_by_field", None)
            try:
                draft = DesignDraft(
                    normalized_scene_query=str(state.experiment["target"]["brief"]),
                    compose_config_patch=patch,
                    citations_by_field={},
                    design_summary=f"Feature quality variant {variant.variant_id}",
                    parameter_sources_by_field={key: "feature_quality_lab" for key in patch},
                )
                result = self.design_service.generate_scene(
                    draft,
                    scene_context=sanitize_scene_context(state.scene_context),
                    generation_options={
                        **generation_options,
                        "out_dir": str(variant_dir),
                        "preset_id": "skip_llm",
                        "random_seed": int(fixed.get("seed", 42)),
                        "design_variant_id": variant.variant_id,
                        "design_variant_name": variant.label,
                        "capture_3d_views": True,
                        "capture_profile": "feature_tri_view",
                        "capture_failure_policy": "warn",
                        "retain_glb_policy": "always",
                        "render_presentation_artifacts": False,
                    },
                )
                variant.layout_path = str(result.get("scene_layout_path") or "")
                variant.scene_glb_path = str(result.get("scene_glb_path") or "")
                encoded_views = feature_tri_views_from_layout(variant.layout_path)
                raw_layout = json.loads(Path(variant.layout_path).read_text(encoding="utf-8"))
                raw_views = list(dict(raw_layout.get("summary", {}) or {}).get("render_views_3d", []) or [])
                raw_by_id = {str(item.get("view_id") or ""): item for item in raw_views}
                variant.views = [dict(raw_by_id[view_id]) for view_id in TRI_VIEW_IDS]
                variant.progress = 75
                if state.visual_review:
                    messages = build_feature_review_messages(
                        experiment=state.experiment,
                        variant={"variant_id": variant.variant_id, "label": variant.label, "patch": variant.patch},
                        rendered_views=encoded_views,
                    )
                    try:
                        client = self.design_service._get_llm_client()
                        raw_review = client.chat_json(messages, temperature=0.0, capability="vision")
                        variant.review = normalize_feature_review(
                            raw_review,
                            target_id=str(state.experiment["target"]["target_id"]),
                        )
                        scores = [float(value) for value in variant.review.get("scores_0_100", {}).values() if value is not None]
                        variant.score = round(sum(scores) / len(scores), 2) if scores else None
                    except Exception as exc:
                        variant.review = {"status": "unavailable", "reason": str(exc), "proposed_patch": {}}
                variant.status = "succeeded"
                variant.progress = 100
            except Exception as exc:
                variant.status = "failed"
                variant.error = str(exc)
                variant.progress = 100
            with self._lock:
                state.progress = int((index + 1) / total * 100)
                self._write_manifest(state)

        with self._lock:
            succeeded = [item for item in state.variants if item.status == "succeeded"]
            state.status = "succeeded" if succeeded else "failed"
            state.stage = "review_ready" if succeeded else "failed"
            state.finished_at = _now()
            if not succeeded:
                state.error = "all feature variants failed"
            self._write_manifest(state)
            write_feature_contact_sheet(self._payload(state), output_path=state.output_dir / "contact_sheet.html")

    def _write_manifest(self, state: FeatureRunState) -> None:
        state.output_dir.mkdir(parents=True, exist_ok=True)
        (state.output_dir / "manifest.json").write_text(
            json.dumps(make_json_safe(self._payload(state)), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _payload(state: FeatureRunState) -> Dict[str, Any]:
        return dict(make_json_safe({
            "run_id": state.run_id,
            "experiment_id": state.experiment.get("experiment_id"),
            "target": state.experiment.get("target"),
            "controls": state.experiment.get("controls"),
            "status": state.status,
            "stage": state.stage,
            "progress": state.progress,
            "accepted_variant_id": state.accepted_variant_id,
            "created_at": state.created_at,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "error": state.error,
            "artifact_dir": str(state.output_dir),
            "contact_sheet_path": str(state.output_dir / "contact_sheet.html"),
            "variants": [vars(item) for item in state.variants],
        }))


def _variant_candidates(target_id: str, base: Mapping[str, Any], count: int) -> list[Dict[str, Any]]:
    if target_id == "curb_ramp":
        positions = (0.25, 0.5, 0.75)
        candidates = [
            {
                "variant_id": f"ramp-{side}-{int(position * 100)}",
                "label": f"{side.title()} · {int(position * 100)}%",
                "patch": {"curb_ramp_enabled": True, "curb_ramp_side": side, "curb_ramp_position_ratio": position},
            }
            for position in positions for side in ("right", "left")
        ]
    elif target_id == "bus_stop":
        candidates = [
            {
                "variant_id": f"stop-{placement}-{style}",
                "label": f"{placement.title()} · {style.replace('_', ' ').title()}",
                "patch": {"bus_stop_enabled": True, "bus_stop_placement": placement, "furniture_style": style},
            }
            for placement in ("curbside", "bay")
            for style in ("civic_clean", "lush_natural", "transit_modern")
        ]
    elif target_id == "building":
        candidates = [
            {"variant_id": "building-massing-low", "label": "Massing · low", "patch": {"building_representation": "transparent_massing", "building_density": 0.3, "infill_policy": "large_gap_only"}},
            {"variant_id": "building-massing-mid", "label": "Massing · medium", "patch": {"building_representation": "transparent_massing", "building_density": 0.55, "infill_policy": "balanced"}},
            {"variant_id": "building-assets-mid", "label": "Assets · medium", "patch": {"building_representation": "asset", "building_density": 0.55, "infill_policy": "balanced"}},
            {"variant_id": "building-assets-high", "label": "Assets · high", "patch": {"building_representation": "asset", "building_density": 0.8, "infill_policy": "aggressive"}},
            {"variant_id": "building-footprints", "label": "Footprint based", "patch": {"building_representation": "asset", "surrounding_building_mode": "footprint_based", "building_density": 0.55}},
            {"variant_id": "building-class-height", "label": "Class heights", "patch": {"building_representation": "asset", "building_height_mode": "class_only", "building_density": 0.55}},
        ]
    else:
        candidates = [
            {"variant_id": "material-civic", "label": "Civic clean", "patch": {"style_preset": "civic_clean_v1", "scene_texture_mode": "topdown_tiles_v1", "furniture_style": "civic_clean"}},
            {"variant_id": "material-lush", "label": "Lush walkable", "patch": {"style_preset": "lush_walkable_v1", "scene_texture_mode": "topdown_tiles_v1", "furniture_style": "lush_natural"}},
            {"variant_id": "material-transit", "label": "Transit modern", "patch": {"style_preset": "transit_modern_v1", "scene_texture_mode": "topdown_tiles_v1", "furniture_style": "transit_modern"}},
            {"variant_id": "material-solid", "label": "Solid-color baseline", "patch": {"style_preset": "civic_clean_v1", "scene_texture_mode": "solid_color_legacy", "furniture_style": "civic_clean"}},
            {"variant_id": "material-lush-solid", "label": "Lush solid", "patch": {"style_preset": "lush_walkable_v1", "scene_texture_mode": "solid_color_legacy", "furniture_style": "lush_natural"}},
            {"variant_id": "material-transit-solid", "label": "Transit solid", "patch": {"style_preset": "transit_modern_v1", "scene_texture_mode": "solid_color_legacy", "furniture_style": "transit_modern"}},
        ]
    # Put the current design first when it is a valid, target-only patch.
    current = {key: value for key, value in base.items() if key in FEATURE_TARGETS[target_id].allowed_fields}
    if current:
        candidates.insert(0, {"variant_id": "current", "label": "Current parameters", "patch": current})
    unique: list[Dict[str, Any]] = []
    fingerprints: set[str] = set()
    for item in candidates:
        fingerprint = json.dumps(item["patch"], sort_keys=True, ensure_ascii=True)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        unique.append(item)
    while len(unique) < count:
        source = dict(unique[len(unique) % len(unique)])
        source["variant_id"] = f"{source['variant_id']}-{len(unique) + 1}"
        source["label"] = f"{source['label']} · repeat {len(unique) + 1}"
        unique.append(source)
    return unique[:count]


__all__ = ["FeatureQualityRunService"]
