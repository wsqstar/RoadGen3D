#!/usr/bin/env python3
"""Render scenario-design structure previews without street furniture."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services.design_runtime import generate_scene_from_draft  # noqa: E402
from roadgen3d.services.design_types import DesignDraft, sanitize_compose_config_patch  # noqa: E402
from roadgen3d.services.scenario_designs import ScenarioDesignService  # noqa: E402


DEFAULT_OUTPUT_ROOT = ROOT / "artifacts" / "scenario_design_options" / "hkust_gz_gate_from_current_layout"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render scenario-design structure previews without street furniture.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--graph-template-id", default="hkust_gz_gate")
    parser.add_argument("--scenario-id", action="append", default=[])
    parser.add_argument("--base-seed", type=int, default=20260517)
    parser.add_argument("--keep-old", action="store_true", help="Do not swap generated previews into the catalog target folders.")
    return parser.parse_args()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _replace_path_prefix(value: Any, old_root: Path, new_root: Path) -> Any:
    if isinstance(value, str):
        old_text = str(old_root)
        if value.startswith(old_text):
            return str(new_root) + value[len(old_text):]
        return value
    if isinstance(value, list):
        return [_replace_path_prefix(item, old_root, new_root) for item in value]
    if isinstance(value, dict):
        return {key: _replace_path_prefix(item, old_root, new_root) for key, item in value.items()}
    return value


def _structure_only_patch(patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(patch)
    merged.update({
        "street_furniture_profile": "none",
        "street_furniture_profile_source": "manual",
        "street_furniture_profile_confidence": 1.0,
        "street_furniture_profile_reasons": ("scenario_structure_preview",),
        "amenity_coverage_mode": "off",
        "curated_street_assets_profile": "disabled",
        "minimum_category_presence": (),
        "optional_category_presence": (),
        "max_bus_stops_per_scene": 0,
        "allow_demo_bus_stop_when_osm_absent": False,
    })
    return sanitize_compose_config_patch(merged)


def _materialize_root_layout(*, generated_layout_path: Path, render_root: Path, final_root: Path) -> Path:
    payload = json.loads(generated_layout_path.read_text(encoding="utf-8"))
    payload = _replace_path_prefix(payload, render_root.resolve(), final_root.resolve())

    nested_glb = Path(str(payload.get("outputs", {}).get("scene_glb", "") or "")).expanduser()
    render_glb = Path(str(_replace_path_prefix(str(nested_glb), final_root.resolve(), render_root.resolve()))).resolve()
    root_glb = render_root / "scene.glb"
    if render_glb.exists():
        shutil.copyfile(render_glb, root_glb)
        payload.setdefault("outputs", {})["scene_glb"] = str(final_root / "scene.glb")

    payload.setdefault("outputs", {})["scene_layout"] = str(final_root / "scene_layout.json")
    summary = dict(payload.get("summary") or {})
    summary.update({
        "scenario_structure_preview": True,
        "preset_id": "structure_preview",
        "street_furniture_profile": "none",
        "structure_preview_rendered_at": _timestamp(),
    })
    payload["summary"] = summary

    root_layout = render_root / "scene_layout.json"
    root_layout.write_text(json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False), encoding="utf-8")
    return root_layout


def _swap_preview_dir(render_root: Path, final_root: Path) -> None:
    backup_root = final_root.with_name(f"{final_root.name}.backup-{_timestamp()}")
    if final_root.exists():
        final_root.rename(backup_root)
    render_root.rename(final_root)


def main() -> int:
    args = _parse_args()
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    service = ScenarioDesignService(design_service=None)
    catalog = service.list_scenarios()
    requested = {str(item).strip() for item in args.scenario_id if str(item).strip()}
    scenarios = [
        item
        for item in catalog.get("items", [])
        if item.get("enabled") and (not requested or str(item.get("scenario_id")) in requested)
    ]
    if not scenarios:
        print("No enabled scenarios matched.", file=sys.stderr)
        return 1

    for index, scenario in enumerate(scenarios):
        scenario_id = str(scenario["scenario_id"])
        title = str(scenario.get("title_zh") or scenario_id)
        final_root = output_root / scenario_id
        render_root = output_root / f".{scenario_id}.structure-preview-{_timestamp()}"
        render_root.mkdir(parents=True, exist_ok=True)
        print(f"[{index + 1}/{len(scenarios)}] Rendering {scenario_id} · {title}")

        inputs = service.generation_inputs_for_scenario(
            scenario_id,
            graph_template_id=args.graph_template_id,
            validate=True,
        )
        compose_patch = _structure_only_patch(dict(inputs.get("compose_config_patch") or {}))
        compose_patch["query"] = str(scenario.get("query") or scenario_id)
        compose_patch["seed"] = int(args.base_seed) + index

        draft = DesignDraft(
            normalized_scene_query=str(scenario.get("query") or scenario_id),
            compose_config_patch=compose_patch,
            citations_by_field={},
            design_summary=str(scenario.get("intent_zh") or scenario.get("title_zh") or scenario_id),
            risk_notes=("Structure preview render; street furniture disabled.",),
            parameter_sources_by_field={
                "scenario_id": "scenario_design_catalog",
                "street_furniture_profile": "structure_preview_override",
            },
            template_patch=dict(inputs["template_patch"]) if isinstance(inputs.get("template_patch"), dict) else None,
        )
        scene_context: dict[str, Any]
        if inputs.get("reference_annotation_path"):
            scene_context = {
                "layout_mode": "reference_annotation",
                "reference_annotation_path": str(inputs["reference_annotation_path"]),
                "scenario_id": scenario_id,
                "scenario_title": title,
                "scenario_design_variant": dict(scenario),
            }
        else:
            scene_context = {
                "layout_mode": "graph_template",
                "graph_template_id": args.graph_template_id,
                "template_patch": dict(inputs["template_patch"]) if isinstance(inputs.get("template_patch"), dict) else None,
                "scenario_id": scenario_id,
                "scenario_title": title,
                "scenario_design_variant": dict(scenario),
            }

        result = generate_scene_from_draft(
            draft,
            scene_context=scene_context,
            generation_options={
                "out_dir": str(render_root),
                "artifacts_dir": str(render_root),
                "preset_id": "skip_llm",
                "random_seed": int(args.base_seed) + index,
                "design_variant_id": "scenario_structure_preview",
                "design_variant_name": f"{title} · Structure Preview",
                "build_production_artifacts": True,
                "render_presentation_artifacts": False,
                "capture_3d_views": False,
                "export_format": "glb",
                "retain_glb_policy": "always",
            },
        )
        generated_layout_path = Path(result.scene_layout_path).expanduser().resolve()
        root_layout = _materialize_root_layout(
            generated_layout_path=generated_layout_path,
            render_root=render_root,
            final_root=final_root,
        )
        payload = json.loads(root_layout.read_text(encoding="utf-8"))
        street_furniture_count = sum(
            1
            for placement in payload.get("placements", []) or []
            if str(placement.get("placement_group", "")).strip().lower() == "street_furniture"
        )
        if street_furniture_count:
            raise RuntimeError(f"{scenario_id} still has {street_furniture_count} street-furniture placements.")
        if not args.keep_old:
            _swap_preview_dir(render_root, final_root)
            print(f"  -> wrote {final_root / 'scene_layout.json'}")
        else:
            print(f"  -> kept generated preview at {render_root}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
