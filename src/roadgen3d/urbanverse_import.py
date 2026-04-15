"""UrbanVerse subset import helpers for RoadGen3D."""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "urbanverse"
DEFAULT_CACHE_ROOT = ROOT / "artifacts" / "urbanverse_cache"
DEFAULT_REAL_OBJECT_MANIFEST_V2 = ROOT / "data" / "real" / "real_assets_manifest_v2.jsonl"
DEFAULT_REAL_GROUND_MANIFEST = ROOT / "data" / "materials" / "ground_material_manifest.jsonl"
DEFAULT_REAL_SKY_MANIFEST = ROOT / "data" / "materials" / "sky_manifest.jsonl"
DEFAULT_ARTIFACTS_DIR = ROOT / "artifacts" / "real"

OBJECTS_METADATA_REL_PATH = Path("metadata/objects.jsonl")
GROUND_METADATA_REL_PATH = Path("metadata/ground_materials.jsonl")
SKIES_METADATA_REL_PATH = Path("metadata/skies.jsonl")

SUPPORTED_OBJECT_CATEGORIES: Tuple[str, ...] = ("bench", "lamp", "trash", "mailbox", "tree")
REPORT_ONLY_CATEGORIES: Tuple[str, ...] = ("bus_stop", "bollard", "hydrant", "building")

_OBJECT_CATEGORY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "bench": ("bench", "public_bench", "park_bench"),
    "lamp": ("streetlight", "lamppost", "street_lamp", "pedestrian_light", "street_light", "lamp_post"),
    "trash": ("trash_can", "garbage_bin", "waste_bin", "litter_bin", "garbage_can", "trash_bin"),
    "mailbox": ("mailbox", "post_box", "postbox", "mail_slot"),
    "tree": ("tree", "street_tree", "deciduous_tree", "evergreen_tree"),
}
_OBJECT_RESCUE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "bench": ("bench", "park bench", "public bench", "street bench"),
    "lamp": ("streetlight", "street light", "lamppost", "street lamp", "pedestrian light", "lamp post"),
    "trash": ("trash can", "garbage bin", "waste bin", "litter bin", "garbage can", "trash bin"),
    "mailbox": ("mailbox", "postbox", "post box", "mail slot"),
    "tree": ("tree", "street tree", "deciduous", "evergreen", "pine", "oak", "maple", "spruce"),
}
_REPORT_ONLY_CATEGORY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "bus_stop": ("bus_stop", "bus_stop_sign", "bus_shelter", "bus_stop_shelter"),
    "bollard": ("bollard", "traffic_bollard", "safety_post"),
    "hydrant": ("hydrant", "fire_hydrant"),
    "building": ("building", "facade", "storefront", "house"),
}
_SURFACE_TYPE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "carriageway": ("road", "asphalt", "carriageway"),
    "sidewalk": ("sidewalk", "pedestrian"),
    "clear_path": ("clear_path",),
    "furnishing": ("plaza", "paving", "furnishing"),
    "transit_pad": ("transit_pad", "bus_pad"),
    "curb": ("curb",),
    "grass": ("grass", "lawn", "turf"),
    "building_buffer": ("buffer_strip",),
    "tree_pit": ("tree_pit", "soil"),
    "crossing": ("crosswalk",),
    "lane_mark": ("lane_mark", "line_paint"),
}
_TIME_OF_DAY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "day": ("morning", "noon", "day"),
    "evening": ("sunset", "dusk", "golden_hour", "golden hour", "evening"),
    "night": ("night",),
}


@dataclass(frozen=True)
class ImportArtifacts:
    object_manifest_path: Path
    ground_manifest_path: Path
    sky_manifest_path: Path
    unmapped_objects_path: Path
    skipped_rows_path: Path
    report_path: Path


def run_urbanverse_subset_import(
    *,
    input_root: Path,
    subset_name: str,
    output_root: Path | None = None,
    cache_root: Path | None = None,
    append_object_manifest: Path | None = None,
    append_ground_manifest: Path | None = None,
    append_sky_manifest: Path | None = None,
    rebuild_index: bool = False,
    artifacts_dir: Path | None = None,
    model_name: str = "openai/clip-vit-base-patch32",
    model_dir: Path | None = None,
    local_files_only: bool = False,
    device: str = "cpu",
) -> Dict[str, Any]:
    subset_key = _slugify(subset_name) or "default_subset"
    input_root = input_root.expanduser().resolve()
    output_root = Path(output_root or (DEFAULT_OUTPUT_ROOT / subset_key)).expanduser().resolve()
    cache_root = Path(cache_root or (DEFAULT_CACHE_ROOT / subset_key)).expanduser().resolve()
    artifacts_dir = Path(artifacts_dir or DEFAULT_ARTIFACTS_DIR).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    artifacts = ImportArtifacts(
        object_manifest_path=(output_root / "object_assets_manifest_v2.jsonl").resolve(),
        ground_manifest_path=(output_root / "ground_material_manifest.jsonl").resolve(),
        sky_manifest_path=(output_root / "sky_manifest.jsonl").resolve(),
        unmapped_objects_path=(output_root / "unmapped_objects.jsonl").resolve(),
        skipped_rows_path=(output_root / "skipped_rows.jsonl").resolve(),
        report_path=(output_root / "import_report.json").resolve(),
    )

    object_rows: List[Dict[str, Any]] = []
    ground_rows: List[Dict[str, Any]] = []
    sky_rows: List[Dict[str, Any]] = []
    unmapped_objects: List[Dict[str, Any]] = []
    skipped_rows: List[Dict[str, Any]] = []

    input_counts = {"objects": 0, "ground_materials": 0, "skies": 0}
    imported_counts = {"objects": 0, "ground_materials": 0, "skies": 0}
    copied_file_counts: Counter[str] = Counter()
    skipped_reason_counts: Counter[str] = Counter()
    unmapped_reason_counts: Counter[str] = Counter()
    stage_statuses: Dict[str, Dict[str, Any]] = {}
    report_only_category_counts: Counter[str] = Counter()

    object_metadata_path = (input_root / OBJECTS_METADATA_REL_PATH).resolve()
    object_payloads, object_status = _load_optional_jsonl(object_metadata_path)
    stage_statuses["objects"] = {"status": object_status, "input_manifest": str(object_metadata_path)}
    input_counts["objects"] = len(object_payloads)
    if object_status == "ok":
        for payload in object_payloads:
            mapped = _import_object_row(
                payload=payload,
                input_root=input_root,
                metadata_path=object_metadata_path,
                subset_key=subset_key,
                cache_root=cache_root,
                copied_file_counts=copied_file_counts,
            )
            kind = str(mapped["kind"])
            if kind == "imported":
                object_rows.append(dict(mapped["row"]))
            elif kind == "unmapped":
                reason = str(mapped["reason"])
                unmapped_reason_counts[reason] += 1
                unmapped_objects.append(dict(mapped["audit"]))
            else:
                reason = str(mapped["reason"])
                skipped_reason_counts[reason] += 1
                skipped_rows.append(dict(mapped["audit"]))
                report_category = str(mapped.get("report_only_category", "") or "").strip()
                if report_category:
                    report_only_category_counts[report_category] += 1
        object_rows = _clean_object_rows(object_rows, manifest_dir=artifacts.object_manifest_path.parent)
    imported_counts["objects"] = len(object_rows)

    ground_metadata_path = (input_root / GROUND_METADATA_REL_PATH).resolve()
    ground_payloads, ground_status = _load_optional_jsonl(ground_metadata_path)
    stage_statuses["ground_materials"] = {"status": ground_status, "input_manifest": str(ground_metadata_path)}
    input_counts["ground_materials"] = len(ground_payloads)
    if ground_status == "ok":
        for payload in ground_payloads:
            mapped = _import_ground_material_row(
                payload=payload,
                input_root=input_root,
                metadata_path=ground_metadata_path,
                subset_key=subset_key,
                cache_root=cache_root,
                copied_file_counts=copied_file_counts,
            )
            if str(mapped["kind"]) == "imported":
                ground_rows.append(dict(mapped["row"]))
            else:
                reason = str(mapped["reason"])
                skipped_reason_counts[reason] += 1
                skipped_rows.append(dict(mapped["audit"]))
    imported_counts["ground_materials"] = len(ground_rows)

    sky_metadata_path = (input_root / SKIES_METADATA_REL_PATH).resolve()
    sky_payloads, sky_status = _load_optional_jsonl(sky_metadata_path)
    stage_statuses["skies"] = {"status": sky_status, "input_manifest": str(sky_metadata_path)}
    input_counts["skies"] = len(sky_payloads)
    if sky_status == "ok":
        for payload in sky_payloads:
            mapped = _import_sky_row(
                payload=payload,
                input_root=input_root,
                metadata_path=sky_metadata_path,
                subset_key=subset_key,
                cache_root=cache_root,
                copied_file_counts=copied_file_counts,
            )
            if str(mapped["kind"]) == "imported":
                sky_rows.append(dict(mapped["row"]))
            else:
                reason = str(mapped["reason"])
                skipped_reason_counts[reason] += 1
                skipped_rows.append(dict(mapped["audit"]))
    imported_counts["skies"] = len(sky_rows)

    _write_jsonl(artifacts.object_manifest_path, object_rows)
    _write_jsonl(artifacts.ground_manifest_path, ground_rows)
    _write_jsonl(artifacts.sky_manifest_path, sky_rows)
    _write_jsonl(artifacts.unmapped_objects_path, unmapped_objects)
    _write_jsonl(artifacts.skipped_rows_path, skipped_rows)

    appended_counts = {"objects": 0, "ground_materials": 0, "skies": 0}
    append_targets = {
        "object_manifest": str(append_object_manifest.resolve()) if append_object_manifest else "",
        "ground_manifest": str(append_ground_manifest.resolve()) if append_ground_manifest else "",
        "sky_manifest": str(append_sky_manifest.resolve()) if append_sky_manifest else "",
    }
    if append_object_manifest is not None:
        appended_counts["objects"] = _append_jsonl_rows(
            append_object_manifest.expanduser().resolve(),
            object_rows,
            key_field="asset_id",
        )
    if append_ground_manifest is not None:
        appended_counts["ground_materials"] = _append_jsonl_rows(
            append_ground_manifest.expanduser().resolve(),
            ground_rows,
            key_field="material_id",
        )
    if append_sky_manifest is not None:
        appended_counts["skies"] = _append_jsonl_rows(
            append_sky_manifest.expanduser().resolve(),
            sky_rows,
            key_field="sky_id",
        )

    rebuild_index_performed = False
    index_summary: Dict[str, Any] = {}
    if rebuild_index and append_object_manifest is not None and object_rows:
        from scripts import asset_seed_production as production_seed

        index_summary = production_seed.rebuild_real_index(
            manifest_path=append_object_manifest.expanduser().resolve(),
            artifacts_dir=artifacts_dir,
            model_name=model_name,
            model_dir=model_dir,
            local_files_only=bool(local_files_only),
            device=str(device),
        )
        rebuild_index_performed = True

    report = {
        "subset_name": subset_key,
        "input_root": str(input_root),
        "output_root": str(output_root),
        "cache_root": str(cache_root),
        "stage_statuses": stage_statuses,
        "input_counts": input_counts,
        "imported_counts": imported_counts,
        "appended_counts": appended_counts,
        "skipped_counts": {
            "rows": int(sum(skipped_reason_counts.values())),
            "by_reason": dict(sorted(skipped_reason_counts.items())),
        },
        "unmapped_counts": {
            "objects": int(sum(unmapped_reason_counts.values())),
            "by_reason": dict(sorted(unmapped_reason_counts.items())),
        },
        "report_only_category_counts": dict(sorted(report_only_category_counts.items())),
        "copied_file_counts": {
            **dict(sorted(copied_file_counts.items())),
            "total": int(sum(copied_file_counts.values())),
        },
        "append_targets": append_targets,
        "rebuild_index": bool(rebuild_index_performed),
        "rebuild_index_requested": bool(rebuild_index),
        "index_summary": dict(index_summary),
        "outputs": {
            "object_manifest": str(artifacts.object_manifest_path),
            "ground_manifest": str(artifacts.ground_manifest_path),
            "sky_manifest": str(artifacts.sky_manifest_path),
            "unmapped_objects": str(artifacts.unmapped_objects_path),
            "skipped_rows": str(artifacts.skipped_rows_path),
            "report": str(artifacts.report_path),
        },
    }
    artifacts.report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    return report


def _load_optional_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], str]:
    if not path.exists():
        return [], "missing_input_manifest"
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(dict(json.loads(line)))
    return rows, "ok"


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(dict(row), ensure_ascii=True) for row in rows)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def _append_jsonl_rows(path: Path, rows: Sequence[Mapping[str, Any]], *, key_field: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: List[Dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing.append(dict(json.loads(line)))
    by_key: Dict[str, Dict[str, Any]] = {}
    for row in existing:
        key = _clean_text(row.get(key_field))
        if key:
            by_key[key] = dict(row)
    appended = 0
    for row in rows:
        key = _clean_text(row.get(key_field))
        if not key:
            continue
        if key not in by_key:
            appended += 1
        by_key[key] = dict(row)
    _write_jsonl(path, by_key.values())
    return int(appended)


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _slugify(value: object) -> str:
    text = _clean_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def _coerce_text_list(value: object) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,\n;]+", value) if part.strip()]
        return parts or [value.strip()] if value.strip() else []
    if isinstance(value, Mapping):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _normalized_token(value: object) -> str:
    text = _slugify(value)
    return text


def _resolve_source_path(path_value: object, *, input_root: Path, metadata_path: Path) -> Path | None:
    text = _clean_text(path_value)
    if not text:
        return None
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    metadata_based = (metadata_path.parent / candidate).resolve()
    if metadata_based.exists():
        return metadata_based
    return (input_root / candidate).resolve()


def _copy_file(
    source_path: Path | None,
    *,
    dest_dir: Path,
    dest_stem: str,
    copied_file_counts: MutableMapping[str, int],
    count_key: str,
) -> str:
    if source_path is None:
        return ""
    if not source_path.exists():
        return ""
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix or ""
    dest_path = (dest_dir / f"{dest_stem}{suffix}").resolve()
    shutil.copy2(source_path, dest_path)
    copied_file_counts[count_key] = int(copied_file_counts.get(count_key, 0)) + 1
    return str(dest_path)


def _write_placeholder_latent(latent_path: Path, *, mesh_path: Path) -> None:
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import torch
    except ImportError:
        latent_path.write_text(json.dumps({"mesh_path": str(mesh_path)}, ensure_ascii=True), encoding="utf-8")
        return
    torch.save({"mesh_path": str(mesh_path)}, latent_path)


def _extract_source_uid(payload: Mapping[str, Any]) -> str:
    for key in ("uid", "object_id", "asset_id"):
        text = _clean_text(payload.get(key))
        if text:
            return text
    return ""


def _extract_source_category(payload: Mapping[str, Any]) -> str:
    for key in ("category", "semantic_label", "class_name"):
        text = _clean_text(payload.get(key))
        if text:
            return text
    return ""


def _extract_text_description(payload: Mapping[str, Any]) -> Tuple[str, str]:
    description = _clean_text(payload.get("description"))
    if description:
        return description, "description"
    for key in ("name", "title", "caption"):
        text = _clean_text(payload.get(key))
        if text:
            return text, key
    return "", ""


def _build_text_blob(payload: Mapping[str, Any]) -> str:
    parts: List[str] = []
    for key in ("name", "title", "caption", "description", "category", "semantic_label", "class_name"):
        text = _clean_text(payload.get(key))
        if text:
            parts.append(text.lower())
    parts.extend(item.lower() for item in _coerce_text_list(payload.get("tags")))
    return " ".join(parts)


def _alias_lookup(value: object, mapping: Mapping[str, Sequence[str]]) -> str:
    token = _normalized_token(value)
    if not token:
        return ""
    for canonical, aliases in mapping.items():
        if token == canonical:
            return canonical
        if token in {_normalized_token(alias) for alias in aliases}:
            return canonical
    return ""


def _rescue_category(payload: Mapping[str, Any], mapping: Mapping[str, Sequence[str]]) -> str:
    text_blob = _build_text_blob(payload)
    if not text_blob:
        return ""
    for canonical, keywords in mapping.items():
        for keyword in keywords:
            if keyword.lower() in text_blob:
                return canonical
    return ""


def _extract_dimensions(payload: Mapping[str, Any]) -> Dict[str, float]:
    bbox = payload.get("bbox")
    dimensions = payload.get("dimensions")
    source = bbox if bbox is not None else dimensions
    if source is None:
        return {}
    if isinstance(source, Mapping):
        size_xyz = source.get("size_xyz")
        if isinstance(size_xyz, Sequence) and not isinstance(size_xyz, (str, bytes)):
            values = list(size_xyz)
            if len(values) >= 3:
                return {
                    "metric_width_m": float(values[0]),
                    "metric_height_m": float(values[1]),
                    "metric_depth_m": float(values[2]),
                }
        width = source.get("width_m", source.get("width"))
        depth = source.get("depth_m", source.get("depth"))
        height = source.get("height_m", source.get("height"))
        out: Dict[str, float] = {}
        if width not in (None, ""):
            out["metric_width_m"] = float(width)
        if depth not in (None, ""):
            out["metric_depth_m"] = float(depth)
        if height not in (None, ""):
            out["metric_height_m"] = float(height)
        return out
    if isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
        values = list(source)
        if len(values) >= 3:
            return {
                "metric_width_m": float(values[0]),
                "metric_height_m": float(values[1]),
                "metric_depth_m": float(values[2]),
            }
    return {}


def _extract_face_count(payload: Mapping[str, Any]) -> int | None:
    for key in ("mesh_face_count", "face_count", "faceCount"):
        value = payload.get(key)
        if value not in (None, ""):
            return int(value)
    metrics = payload.get("quality_metrics")
    if isinstance(metrics, Mapping):
        for key in ("face_count", "faceCount"):
            value = metrics.get(key)
            if value not in (None, ""):
                return int(value)
    return None


def _import_object_row(
    *,
    payload: Mapping[str, Any],
    input_root: Path,
    metadata_path: Path,
    subset_key: str,
    cache_root: Path,
    copied_file_counts: MutableMapping[str, int],
) -> Dict[str, Any]:
    source_uid = _extract_source_uid(payload)
    source_category = _extract_source_category(payload)
    if not source_uid:
        return {
            "kind": "skipped",
            "reason": "missing_source_uid",
            "audit": {"stage": "objects", "source_uid": "", "source_category": source_category, "reason": "missing_source_uid"},
        }
    description, description_source = _extract_text_description(payload)
    if not description:
        return {
            "kind": "unmapped",
            "reason": "missing_text_description",
            "audit": {
                "stage": "objects",
                "source_uid": source_uid,
                "source_category": source_category,
                "reason": "missing_text_description",
            },
        }

    canonical_category = _alias_lookup(source_category, _OBJECT_CATEGORY_ALIASES)
    if not canonical_category:
        canonical_category = _rescue_category(payload, _OBJECT_RESCUE_KEYWORDS)
    if not canonical_category:
        report_only_category = _alias_lookup(source_category, _REPORT_ONLY_CATEGORY_ALIASES)
        if not report_only_category:
            report_only_category = _rescue_category(payload, _REPORT_ONLY_CATEGORY_ALIASES)
        if report_only_category:
            return {
                "kind": "skipped",
                "reason": "unsupported_category_v1",
                "report_only_category": report_only_category,
                "audit": {
                    "stage": "objects",
                    "source_uid": source_uid,
                    "source_category": source_category,
                    "reason": "unsupported_category_v1",
                    "report_only_category": report_only_category,
                },
            }
        return {
            "kind": "unmapped",
            "reason": "no_supported_category_mapping",
            "audit": {
                "stage": "objects",
                "source_uid": source_uid,
                "source_category": source_category,
                "reason": "no_supported_category_mapping",
            },
        }

    mesh_source_path = _resolve_source_path(
        payload.get("mesh_path", payload.get("glb_path", payload.get("asset_path"))),
        input_root=input_root,
        metadata_path=metadata_path,
    )
    if mesh_source_path is None or not mesh_source_path.exists():
        return {
            "kind": "skipped",
            "reason": "missing_mesh_file",
            "audit": {
                "stage": "objects",
                "source_uid": source_uid,
                "source_category": source_category,
                "reason": "missing_mesh_file",
            },
        }

    safe_uid = _slugify(source_uid) or "unknown"
    asset_id = f"urbanverse_{canonical_category}_{safe_uid}"
    asset_dir = (cache_root / "objects" / asset_id).resolve()
    asset_dir.mkdir(parents=True, exist_ok=True)
    cached_mesh_path = ""
    tree_validation: Dict[str, Any] | None = None
    if canonical_category == "tree":
        from scripts.asset_ingest import _load_mesh_as_single_mesh, normalize_grounded_mesh, validate_tree_upright

        mesh = _load_mesh_as_single_mesh(mesh_source_path)
        normalized = normalize_grounded_mesh(mesh)
        is_upright, diagnostics = validate_tree_upright(normalized)
        if not is_upright:
            return {
                "kind": "skipped",
                "reason": "tree_validation_failed",
                "audit": {
                    "stage": "objects",
                    "source_uid": source_uid,
                    "source_category": source_category,
                    "reason": "tree_validation_failed",
                    "details": dict(diagnostics),
                },
            }
        tree_validation = dict(diagnostics)
        tree_validation["validation_mode"] = "trunk_axis"
        cached_mesh_file = (asset_dir / "mesh.glb").resolve()
        normalized.export(cached_mesh_file)
        cached_mesh_path = str(cached_mesh_file)
        copied_file_counts["object_meshes"] = int(copied_file_counts.get("object_meshes", 0)) + 1
        face_count = int(len(getattr(normalized, "faces", ())))
    else:
        cached_mesh_path = _copy_file(
            mesh_source_path,
            dest_dir=asset_dir,
            dest_stem="mesh",
            copied_file_counts=copied_file_counts,
            count_key="object_meshes",
        )
        face_count = _extract_face_count(payload) or 0

    thumbnail_path = _copy_file(
        _resolve_source_path(payload.get("thumbnail_path", payload.get("preview_path")), input_root=input_root, metadata_path=metadata_path),
        dest_dir=asset_dir,
        dest_stem="thumbnail",
        copied_file_counts=copied_file_counts,
        count_key="object_thumbnails",
    )
    appearance_embedding_path = _copy_file(
        _resolve_source_path(
            payload.get("appearance_embedding_path", payload.get("dino_embedding_path")),
            input_root=input_root,
            metadata_path=metadata_path,
        ),
        dest_dir=asset_dir,
        dest_stem="appearance_embedding",
        copied_file_counts=copied_file_counts,
        count_key="object_appearance_embeddings",
    )
    latent_source_path = _resolve_source_path(
        payload.get("latent_path"),
        input_root=input_root,
        metadata_path=metadata_path,
    )
    if latent_source_path is not None and latent_source_path.exists():
        latent_path = _copy_file(
            latent_source_path,
            dest_dir=asset_dir,
            dest_stem="latent",
            copied_file_counts=copied_file_counts,
            count_key="object_latents",
        )
    else:
        latent_file = (asset_dir / "latent.pt").resolve()
        _write_placeholder_latent(latent_file, mesh_path=Path(cached_mesh_path))
        latent_path = str(latent_file)
        copied_file_counts["placeholder_latents"] = int(copied_file_counts.get("placeholder_latents", 0)) + 1

    row: Dict[str, Any] = {
        "asset_id": asset_id,
        "source_dataset": f"urbanverse_{subset_key}",
        "source_uid": source_uid,
        "category": canonical_category,
        "source_category": source_category,
        "text_desc": description,
        "mesh_path": cached_mesh_path,
        "thumbnail_path": thumbnail_path,
        "latent_path": latent_path,
        "appearance_embedding_path": appearance_embedding_path,
        "license": _clean_text(payload.get("license")) or "unknown",
        "split": _clean_text(payload.get("split")) or "train",
        "asset_role": "street_furniture",
        "source": "urbanverse_subset_import",
        "generator_type": "urbanverse_subset_v1",
        "style_tags": _coerce_text_list(payload.get("style_tags", payload.get("tags"))),
        "affordance_tags": _coerce_text_list(payload.get("affordance_tags", payload.get("affordances"))),
        "canonical_front": _clean_text(payload.get("canonical_front", payload.get("front_axis"))),
        "description_source": description_source,
    }
    row.update(_extract_dimensions(payload))
    mass = payload.get("mass_kg", payload.get("mass"))
    friction = payload.get("friction", payload.get("friction_coeff"))
    if mass not in (None, ""):
        row["mass_kg"] = float(mass)
    if friction not in (None, ""):
        row["friction"] = float(friction)
    if face_count > 0:
        row["mesh_face_count"] = int(face_count)
        row["quality_metrics"] = {"face_count": int(face_count)}
    if tree_validation is not None:
        row.setdefault("quality_metrics", {})
        row["quality_metrics"] = {
            **dict(row.get("quality_metrics", {}) or {}),
            "tree_upright_validation": dict(tree_validation),
        }
        row["quality_notes"] = ["tree_upright_validated"]
    return {"kind": "imported", "row": row}


def _clean_object_rows(rows: Sequence[Mapping[str, Any]], *, manifest_dir: Path) -> List[Dict[str, Any]]:
    from scripts import asset_clean_manifest as manifest_cleaner

    return manifest_cleaner.clean_manifest_rows(rows, manifest_dir.resolve())


def _import_ground_material_row(
    *,
    payload: Mapping[str, Any],
    input_root: Path,
    metadata_path: Path,
    subset_key: str,
    cache_root: Path,
    copied_file_counts: MutableMapping[str, int],
) -> Dict[str, Any]:
    source_uid = _extract_source_uid(payload) or _clean_text(payload.get("material_id")) or _clean_text(payload.get("uid"))
    source_surface = _clean_text(payload.get("surface_type", payload.get("category", payload.get("type"))))
    if not source_uid:
        return {
            "kind": "skipped",
            "reason": "missing_source_uid",
            "audit": {"stage": "ground_materials", "source_uid": "", "reason": "missing_source_uid"},
        }
    surface_type = _alias_lookup(source_surface, _SURFACE_TYPE_ALIASES)
    if not surface_type:
        surface_type = _rescue_category({"description": _build_text_blob(payload)}, _SURFACE_TYPE_ALIASES)
    if not surface_type:
        return {
            "kind": "skipped",
            "reason": "no_surface_type_mapping",
            "audit": {
                "stage": "ground_materials",
                "source_uid": source_uid,
                "source_category": source_surface,
                "reason": "no_surface_type_mapping",
            },
        }

    albedo_source = _resolve_source_path(payload.get("albedo_path"), input_root=input_root, metadata_path=metadata_path)
    if albedo_source is None or not albedo_source.exists():
        return {
            "kind": "skipped",
            "reason": "missing_ground_source_file",
            "audit": {
                "stage": "ground_materials",
                "source_uid": source_uid,
                "source_category": source_surface,
                "reason": "missing_ground_source_file",
            },
        }
    material_id = f"urbanverse_{surface_type}_{_slugify(source_uid) or 'unknown'}"
    material_dir = (cache_root / "ground_materials" / material_id).resolve()
    row = {
        "material_id": material_id,
        "surface_type": surface_type,
        "source_dataset": f"urbanverse_{subset_key}",
        "license": _clean_text(payload.get("license")) or "unknown",
        "albedo_path": _copy_file(
            albedo_source,
            dest_dir=material_dir,
            dest_stem="albedo",
            copied_file_counts=copied_file_counts,
            count_key="ground_material_files",
        ),
        "normal_path": "",
        "roughness_path": "",
        "metallic_path": "",
        "preview_path": "",
        "style_tags": _coerce_text_list(payload.get("style_tags", payload.get("tags"))),
        "weather_tags": _coerce_text_list(payload.get("weather_tags")),
        "region_tags": _coerce_text_list(payload.get("region_tags")),
    }
    for field_name, dest_stem in (
        ("normal_path", "normal"),
        ("roughness_path", "roughness"),
        ("metallic_path", "metallic"),
        ("preview_path", "preview"),
    ):
        source_path = _resolve_source_path(payload.get(field_name), input_root=input_root, metadata_path=metadata_path)
        if source_path is None:
            continue
        if not source_path.exists():
            return {
                "kind": "skipped",
                "reason": "missing_ground_source_file",
                "audit": {
                    "stage": "ground_materials",
                    "source_uid": source_uid,
                    "source_category": source_surface,
                    "reason": "missing_ground_source_file",
                    "field": field_name,
                },
            }
        row[field_name] = _copy_file(
            source_path,
            dest_dir=material_dir,
            dest_stem=dest_stem,
            copied_file_counts=copied_file_counts,
            count_key="ground_material_files",
        )
    return {"kind": "imported", "row": row}


def _map_time_of_day(payload: Mapping[str, Any]) -> str:
    source_value = _clean_text(payload.get("time_of_day", payload.get("time")))
    mapped = _alias_lookup(source_value, _TIME_OF_DAY_ALIASES)
    if mapped:
        return mapped
    text_blob = _build_text_blob(payload)
    for canonical, aliases in _TIME_OF_DAY_ALIASES.items():
        for alias in aliases:
            if alias.lower() in text_blob:
                return canonical
    return "day"


def _import_sky_row(
    *,
    payload: Mapping[str, Any],
    input_root: Path,
    metadata_path: Path,
    subset_key: str,
    cache_root: Path,
    copied_file_counts: MutableMapping[str, int],
) -> Dict[str, Any]:
    source_uid = _extract_source_uid(payload) or _clean_text(payload.get("sky_id")) or _clean_text(payload.get("uid"))
    if not source_uid:
        return {
            "kind": "skipped",
            "reason": "missing_source_uid",
            "audit": {"stage": "skies", "source_uid": "", "reason": "missing_source_uid"},
        }
    hdri_source = _resolve_source_path(payload.get("hdri_path"), input_root=input_root, metadata_path=metadata_path)
    if hdri_source is None or not hdri_source.exists():
        return {
            "kind": "skipped",
            "reason": "missing_sky_source_file",
            "audit": {"stage": "skies", "source_uid": source_uid, "reason": "missing_sky_source_file"},
        }
    time_of_day = _map_time_of_day(payload)
    sky_id = f"urbanverse_{time_of_day}_{_slugify(source_uid) or 'unknown'}"
    sky_dir = (cache_root / "skies" / sky_id).resolve()
    row = {
        "sky_id": sky_id,
        "source_dataset": f"urbanverse_{subset_key}",
        "license": _clean_text(payload.get("license")) or "unknown",
        "hdri_path": _copy_file(
            hdri_source,
            dest_dir=sky_dir,
            dest_stem="sky",
            copied_file_counts=copied_file_counts,
            count_key="sky_files",
        ),
        "preview_path": "",
        "time_of_day": time_of_day,
        "weather_tags": _coerce_text_list(payload.get("weather_tags", payload.get("tags"))),
        "illumination_tags": _coerce_text_list(payload.get("illumination_tags")),
        "region_tags": _coerce_text_list(payload.get("region_tags")),
    }
    preview_source = _resolve_source_path(payload.get("preview_path"), input_root=input_root, metadata_path=metadata_path)
    if preview_source is not None:
        if not preview_source.exists():
            return {
                "kind": "skipped",
                "reason": "missing_sky_source_file",
                "audit": {
                    "stage": "skies",
                    "source_uid": source_uid,
                    "reason": "missing_sky_source_file",
                    "field": "preview_path",
                },
            }
        row["preview_path"] = _copy_file(
            preview_source,
            dest_dir=sky_dir,
            dest_stem="preview",
            copied_file_counts=copied_file_counts,
            count_key="sky_files",
        )
    return {"kind": "imported", "row": row}
