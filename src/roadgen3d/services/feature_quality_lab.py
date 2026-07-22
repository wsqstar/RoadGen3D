"""Small, repeatable visual experiments for one generated street feature.

The feature lab intentionally sits between the full design-matrix benchmark and
ad-hoc screenshot review.  A run locks the scene context and seed, varies only
an explicit feature allowlist, captures a fixed engineering tri-view, and asks
a vision model for a bounded parameter patch rather than arbitrary mesh/code
changes.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from ..evaluation_views import encode_render_views_for_evaluation
from ..json_safe import make_json_safe
from .design_types import sanitize_compose_config_patch


FEATURE_QUALITY_PROTOCOL_VERSION = "roadgen3d_feature_quality_v1"
TRI_VIEW_IDS = ("feature_top", "feature_longitudinal", "feature_cross_section")


@dataclass(frozen=True)
class FeatureTarget:
    target_id: str
    label: str
    allowed_fields: tuple[str, ...]
    acceptance_checks: tuple[str, ...]


FEATURE_TARGETS: Dict[str, FeatureTarget] = {
    "curb_ramp": FeatureTarget(
        target_id="curb_ramp",
        label="Road-to-sidewalk curb ramp",
        allowed_fields=("curb_ramp_enabled", "curb_ramp_side", "curb_ramp_position_ratio"),
        acceptance_checks=(
            "Ramp is independent from the bus stop and bus bay.",
            "Ramp spans 1.5 m along the road and 1.0 m in horizontal run.",
            "Surface rises continuously from road level to sidewalk level.",
            "No visible gap, overlap, inverted face, or floating edge.",
        ),
    ),
    "bus_stop": FeatureTarget(
        target_id="bus_stop",
        label="Bus stop and optional bus bay",
        allowed_fields=("bus_stop_enabled", "bus_stop_placement", "furniture_style"),
        acceptance_checks=(
            "Stop placement is legible from top and street-facing views.",
            "Curbside and bay variants do not silently add a curb ramp.",
            "Shelter, boarding area, curb, and carriageway do not intersect incorrectly.",
        ),
    ),
    "building": FeatureTarget(
        target_id="building",
        label="Surrounding building generation",
        allowed_fields=(
            "building_density",
            "building_max_per_100m",
            "building_representation",
            "surrounding_building_mode",
            "infill_policy",
            "building_height_mode",
        ),
        acceptance_checks=(
            "Building footprints remain outside the carriageway and clear path.",
            "Scale, height rhythm, frontage, and representation are visually coherent.",
            "The source footprint geometry remains traceable in the output.",
        ),
    ),
    "surface_material": FeatureTarget(
        target_id="surface_material",
        label="Road, curb, and sidewalk material system",
        allowed_fields=("style_preset", "scene_texture_mode", "furniture_style"),
        acceptance_checks=(
            "Road, curb, ramp, and sidewalk remain visually distinguishable.",
            "Texture scale and seams are plausible in all three views.",
            "Material changes do not alter geometry or feature placement.",
        ),
    ),
}


class FeatureExperimentError(ValueError):
    """Raised when a feature experiment violates its isolation contract."""


def build_feature_experiment(
    *,
    experiment_id: str,
    target_id: str,
    brief: str,
    fixed_patch: Mapping[str, Any] | None,
    variants: Sequence[Mapping[str, Any]],
    seed: int = 42,
    graph_template_id: str = "hkust_gz_gate",
) -> Dict[str, Any]:
    """Validate and normalize a deterministic one-feature experiment manifest."""

    target = FEATURE_TARGETS.get(str(target_id or "").strip())
    if target is None:
        raise FeatureExperimentError(
            f"target_id must be one of: {', '.join(sorted(FEATURE_TARGETS))}"
        )
    clean_id = str(experiment_id or "").strip()
    clean_brief = str(brief or "").strip()
    if not clean_id or not clean_brief:
        raise FeatureExperimentError("experiment_id and brief are required")
    if not variants:
        raise FeatureExperimentError("at least one variant is required")

    fixed = sanitize_compose_config_patch(fixed_patch)
    overlap = sorted(set(fixed) & set(target.allowed_fields))
    if overlap:
        raise FeatureExperimentError(
            "feature fields belong in variants, not fixed_patch: " + ", ".join(overlap)
        )
    fixed["seed"] = int(seed)

    normalized_variants: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_variant in enumerate(variants):
        variant_id = str(raw_variant.get("variant_id") or f"variant_{index + 1}").strip()
        if not variant_id or variant_id in seen_ids:
            raise FeatureExperimentError(f"duplicate or empty variant_id: {variant_id!r}")
        seen_ids.add(variant_id)
        raw_patch = dict(raw_variant.get("patch", {}) or {})
        forbidden = sorted(set(raw_patch) - set(target.allowed_fields))
        if forbidden:
            raise FeatureExperimentError(
                f"variant {variant_id} changes fields outside {target.target_id}: {', '.join(forbidden)}"
            )
        clean_patch = sanitize_compose_config_patch(raw_patch)
        if set(clean_patch) != set(raw_patch):
            invalid = sorted(set(raw_patch) - set(clean_patch))
            raise FeatureExperimentError(
                f"variant {variant_id} contains invalid values: {', '.join(invalid)}"
            )
        normalized_variants.append({
            "variant_id": variant_id,
            "label": str(raw_variant.get("label") or variant_id).strip(),
            "patch": clean_patch,
        })

    return {
        "protocol_version": FEATURE_QUALITY_PROTOCOL_VERSION,
        "experiment_id": clean_id,
        "target": {
            "target_id": target.target_id,
            "label": target.label,
            "brief": clean_brief,
            "allowed_fields": list(target.allowed_fields),
            "acceptance_checks": list(target.acceptance_checks),
        },
        "controls": {
            "seed": int(seed),
            "graph_template_id": str(graph_template_id or "hkust_gz_gate").strip(),
            "fixed_patch": fixed,
            "capture_profile": "feature_tri_view",
            "required_view_ids": list(TRI_VIEW_IDS),
            "one_feature_per_experiment": True,
        },
        "variants": normalized_variants,
    }


def feature_tri_views_from_layout(layout_path: str | Path) -> List[Dict[str, Any]]:
    """Load exactly the fixed top/longitudinal/cross-section views from a layout."""

    layout = Path(layout_path).expanduser().resolve()
    payload = json.loads(layout.read_text(encoding="utf-8"))
    summary = dict(payload.get("summary", {}) or {})
    candidates = list(summary.get("render_views_3d", []) or [])
    by_id = {str(item.get("view_id") or item.get("name") or ""): item for item in candidates}
    missing = [view_id for view_id in TRI_VIEW_IDS if view_id not in by_id]
    if missing:
        raise FeatureExperimentError(
            "layout is missing feature tri-view captures: " + ", ".join(missing)
        )
    ordered = [by_id[view_id] for view_id in TRI_VIEW_IDS]
    return encode_render_views_for_evaluation(
        ordered,
        limit=len(TRI_VIEW_IDS),
        label_prefix="Feature tri-view",
        base_dir=layout.parent,
    )


def build_feature_review_messages(
    *,
    experiment: Mapping[str, Any],
    variant: Mapping[str, Any],
    rendered_views: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Build a target-specific multimodal review request with a strict JSON schema."""

    target = dict(experiment.get("target", {}) or {})
    view_by_id = {str(item.get("view_id") or ""): item for item in rendered_views}
    missing = [view_id for view_id in TRI_VIEW_IDS if view_id not in view_by_id]
    if missing:
        raise FeatureExperimentError("visual review requires all three views: " + ", ".join(missing))
    allowed_fields = list(target.get("allowed_fields", []) or [])
    prompt = {
        "task": "Review one generated 3D street feature, not the whole street.",
        "feature": target,
        "variant": dict(variant),
        "comparison_rules": [
            "Use only visible evidence from the named views.",
            "Separate geometry correctness from appearance preference.",
            "Do not propose mesh edits, code, or fields outside allowed_fields.",
            "If evidence is insufficient, mark the relevant score confidence low.",
        ],
        "required_output": {
            "scores_0_100": {
                "text_alignment": "number",
                "geometry_fidelity": "number",
                "placement_validity": "number",
                "material_coherence": "number",
                "visual_quality": "number",
            },
            "defects": [{"view_id": "string", "severity": "low|medium|high", "evidence": "string"}],
            "passed_checks": ["string"],
            "failed_checks": ["string"],
            "proposed_patch": {field: "value" for field in allowed_fields},
            "reasoning": "short string",
            "confidence": "low|medium|high",
        },
    }
    content: List[Dict[str, Any]] = [{"type": "text", "text": json.dumps(prompt, ensure_ascii=False)}]
    for view_id in TRI_VIEW_IDS:
        view = view_by_id[view_id]
        content.append({"type": "text", "text": f"VIEW {view_id}: {view.get('label', view_id)}"})
        content.append({"type": "image_url", "image_url": {"url": str(view.get("image_data_url") or "")}})
    return [
        {
            "role": "system",
            "content": "You are a strict 3D urban-feature QA reviewer. Return JSON only.",
        },
        {"role": "user", "content": content},
    ]


def normalize_feature_review(
    review: Mapping[str, Any],
    *,
    target_id: str,
) -> Dict[str, Any]:
    """Clamp scores and remove every proposed change outside the target allowlist."""

    target = FEATURE_TARGETS.get(str(target_id or "").strip())
    if target is None:
        raise FeatureExperimentError(f"unknown feature target: {target_id}")
    raw_scores = dict(review.get("scores_0_100", {}) or {})
    score_keys = (
        "text_alignment",
        "geometry_fidelity",
        "placement_validity",
        "material_coherence",
        "visual_quality",
    )
    scores: Dict[str, float | None] = {}
    for key in score_keys:
        try:
            scores[key] = round(max(0.0, min(100.0, float(raw_scores[key]))), 2)
        except (KeyError, TypeError, ValueError):
            scores[key] = None

    raw_patch = dict(review.get("proposed_patch", {}) or {})
    prohibited = sorted(set(raw_patch) - set(target.allowed_fields))
    allowed_raw = {key: value for key, value in raw_patch.items() if key in target.allowed_fields}
    clean_patch = sanitize_compose_config_patch(allowed_raw)
    rejected_invalid = sorted(set(allowed_raw) - set(clean_patch))
    return {
        "scores_0_100": scores,
        "defects": [dict(item) for item in list(review.get("defects", []) or []) if isinstance(item, Mapping)],
        "passed_checks": [str(item) for item in list(review.get("passed_checks", []) or [])],
        "failed_checks": [str(item) for item in list(review.get("failed_checks", []) or [])],
        "proposed_patch": clean_patch,
        "rejected_patch_fields": prohibited + rejected_invalid,
        "reasoning": str(review.get("reasoning") or "").strip(),
        "confidence": str(review.get("confidence") or "low").strip().lower(),
    }


def write_feature_contact_sheet(
    run_payload: Mapping[str, Any],
    *,
    output_path: str | Path,
) -> str:
    """Write a dependency-free text + tri-view HTML comparison board."""

    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    cards = []
    for item in list(run_payload.get("variants", []) or []):
        views = {str(view.get("view_id") or ""): view for view in list(item.get("views", []) or [])}
        images = []
        for view_id in TRI_VIEW_IDS:
            view = views.get(view_id, {})
            path = str(view.get("path") or view.get("image_path") or "")
            if path:
                try:
                    path = str(Path(path).expanduser().resolve().relative_to(out.parent))
                except ValueError:
                    path = Path(path).expanduser().resolve().as_uri()
            images.append(
                f'<figure><img src="{html.escape(path)}" alt="{view_id}"><figcaption>{view_id}</figcaption></figure>'
            )
        cards.append(
            '<article><h2>' + html.escape(str(item.get("label") or item.get("variant_id") or "variant"))
            + '</h2><pre>' + html.escape(json.dumps(item.get("patch", {}), ensure_ascii=False, indent=2))
            + '</pre><div class="views">' + "".join(images) + '</div><pre>'
            + html.escape(json.dumps(item.get("review", {}), ensure_ascii=False, indent=2)) + '</pre></article>'
        )
    document = f"""<!doctype html><meta charset=\"utf-8\"><title>Feature quality lab</title>
<style>body{{font:14px system-ui;margin:24px;background:#f5f5f2;color:#202124}}article{{background:white;padding:18px;margin:18px 0;border-radius:12px}}.views{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}img{{width:100%;background:#ddd}}figure{{margin:0}}pre{{white-space:pre-wrap}}figcaption{{font-weight:600;margin-top:5px}}</style>
<h1>{html.escape(str(run_payload.get('experiment_id') or 'Feature quality lab'))}</h1>
<p>Generated {html.escape(datetime.now(timezone.utc).isoformat(timespec='seconds'))}. Fixed text + engineering tri-view + bounded review.</p>
{''.join(cards)}"""
    out.write_text(document, encoding="utf-8")
    return str(out)


def write_experiment_manifest(experiment: Mapping[str, Any], output_path: str | Path) -> str:
    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(make_json_safe(experiment), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out)
