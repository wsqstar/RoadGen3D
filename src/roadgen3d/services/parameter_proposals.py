"""Side-effect-free LLM proposals for the versioned street parameter contract."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Mapping, Sequence

from .street_design_parameters import (
    compile_street_design_parameter_spec,
    validate_street_design_parameter_spec,
)


class ParameterProposalError(ValueError):
    """Raised when an LLM proposal tries to mutate facts or escape the schema."""


_ALLOWED_SKELETON_FIELDS = frozenset({
    "laneCount",
    "laneWidthM",
    "sidewalkWidthM",
    "furnishingWidthM",
    "curbWidthM",
    "junctionCornerPolicy",
    "junctionCornerRadiusM",
})
_ALLOWED_FURNITURE_FIELDS = frozenset({"globalDensity", "categories"})
_ALLOWED_CATEGORY_FIELDS = frozenset({
    "enabled",
    "targetCountPer100M",
    "preferredSpacingM",
    "minimumSpacingM",
    "roadSetbackM",
    "allowedZones",
})


def _deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate_patch_shape(raw_patch: Mapping[str, Any]) -> Dict[str, Any]:
    patch = copy.deepcopy(dict(raw_patch))
    unknown_top = sorted(set(patch) - {"skeleton", "furniture", "buildings", "seed"})
    if unknown_top:
        raise ParameterProposalError(
            "AI parameter proposals cannot modify source geometry or unknown fields: "
            + ", ".join(unknown_top)
        )
    if "skeleton" in patch:
        skeleton = dict(patch["skeleton"] or {})
        unknown = sorted(set(skeleton) - _ALLOWED_SKELETON_FIELDS)
        if unknown:
            raise ParameterProposalError("Unsupported skeleton proposal fields: " + ", ".join(unknown))
        patch["skeleton"] = skeleton
    if "furniture" in patch:
        furniture = dict(patch["furniture"] or {})
        unknown = sorted(set(furniture) - _ALLOWED_FURNITURE_FIELDS)
        if unknown:
            raise ParameterProposalError("Unsupported furniture proposal fields: " + ", ".join(unknown))
        if "categories" in furniture:
            categories: Dict[str, Any] = {}
            for category, raw in dict(furniture.get("categories") or {}).items():
                config = dict(raw or {})
                invalid = sorted(set(config) - _ALLOWED_CATEGORY_FIELDS)
                if invalid:
                    raise ParameterProposalError(
                        f"Unsupported proposal fields for furniture category {category}: " + ", ".join(invalid)
                    )
                categories[str(category)] = config
            furniture["categories"] = categories
        patch["furniture"] = furniture
    if "buildings" in patch:
        buildings = dict(patch["buildings"] or {})
        if set(buildings) - {"representation"}:
            raise ParameterProposalError("AI may only suggest the building representation; footprints stay locked.")
        patch["buildings"] = buildings
    return patch


def _flatten(value: Mapping[str, Any], prefix: str = "") -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping):
            result.update(_flatten(item, path))
        else:
            result[path] = item
    return result


def build_parameter_proposal_messages(
    *,
    current_spec: Mapping[str, Any],
    design_goals: Sequence[str],
    structured_weaknesses: Mapping[str, Any],
    scene_summary: Mapping[str, Any],
) -> list[dict[str, str]]:
    system = (
        "You are a street-design parameter assistant. Return JSON only. Suggest a minimal patch to the supplied "
        "StreetDesignParameterSpec. Never change source, geometry, centerlines, junction topology, building footprints, "
        "GeoJSON, GLB, asset IDs, or scene edit commands. Do not retrieve external knowledge. Allowed changes are road "
        "cross-section numbers, junction corner policy/radius, furniture density/count/spacing/setback/zones, building "
        "representation, and seed. Return {patch, changed_fields, warnings}; each changed_fields item has field, reason, "
        "confidence."
    )
    user = json.dumps(
        {
            "design_goals": list(design_goals),
            "current_parameters": current_spec,
            "structured_metric_weaknesses": structured_weaknesses,
            "read_only_scene_summary": scene_summary,
        },
        ensure_ascii=False,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def create_parameter_proposal(
    *,
    llm_client: Any,
    current_spec: Mapping[str, Any],
    design_goals: Sequence[str],
    structured_weaknesses: Mapping[str, Any] | None = None,
    scene_summary: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    base = validate_street_design_parameter_spec(current_spec)
    base_compiled = compile_street_design_parameter_spec(base)
    response = llm_client.chat_json(
        build_parameter_proposal_messages(
            current_spec=base,
            design_goals=design_goals,
            structured_weaknesses=dict(structured_weaknesses or {}),
            scene_summary=dict(scene_summary or {}),
        )
    )
    if not isinstance(response, Mapping):
        raise ParameterProposalError("AI parameter proposal must be a JSON object.")
    patch = _validate_patch_shape(dict(response.get("patch") or {}))
    proposed = validate_street_design_parameter_spec(_deep_merge(base, patch))
    proposed_compiled = compile_street_design_parameter_spec(proposed)
    before = _flatten(base)
    after = _flatten(proposed)
    raw_changes = {
        str(item.get("field")): dict(item)
        for item in list(response.get("changed_fields") or [])
        if isinstance(item, Mapping) and item.get("field")
    }
    changed_fields = []
    for field in sorted(path for path in after if before.get(path) != after.get(path)):
        metadata = raw_changes.get(field, {})
        try:
            confidence = max(0.0, min(1.0, float(metadata.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        changed_fields.append({
            "field": field,
            "before": before.get(field),
            "after": after.get(field),
            "reason": str(metadata.get("reason") or "AI parameter suggestion"),
            "confidence": confidence,
        })
    proposal_key = json.dumps(
        {"base": base_compiled.fingerprint, "proposed": proposed_compiled.fingerprint},
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "proposalId": "proposal_" + hashlib.sha256(proposal_key.encode("utf-8")).hexdigest()[:16],
        "baseFingerprint": base_compiled.fingerprint,
        "proposedFingerprint": proposed_compiled.fingerprint,
        "patch": patch,
        "proposedSpec": proposed,
        "changedFields": changed_fields,
        "warnings": [str(item) for item in list(response.get("warnings") or [])],
        "generationMode": "parametric",
        "knowledgeSource": "none",
        "sideEffectFree": True,
    }
