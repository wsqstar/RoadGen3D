"""Manifest-backed object/material/sky backends for scene generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OBJECT_MANIFEST_V2_PATH = (ROOT / "data" / "real" / "real_assets_manifest_v2.jsonl").resolve()
DEFAULT_GROUND_MATERIAL_MANIFEST_PATH = (ROOT / "data" / "materials" / "ground_material_manifest.jsonl").resolve()
DEFAULT_SKY_MANIFEST_PATH = (ROOT / "data" / "materials" / "sky_manifest.jsonl").resolve()

_LEGACY_OPTIONAL_FIELDS: Tuple[str, ...] = (
    "style_tags",
    "quality_tier",
    "material_family",
    "hero_asset",
    "avoid_with_presets",
    "asset_role",
    "theme_tags",
    "frontage_width_m",
    "depth_m",
    "height_class",
    "source",
    "generator_type",
    "runtime_profile",
    "parameter_snapshot",
    "quality_metrics",
    "scene_eligible",
    "mesh_face_count",
    "quality_notes",
)
_SCENE_SURFACE_ROLES: Tuple[str, ...] = (
    "context_ground",
    "carriageway",
    "sidewalk",
    "clear_path",
    "furnishing",
    "transit_pad",
    "curb",
    "grass",
    "building_buffer",
    "tree_pit",
    "planting_soil",
    "crossing",
    "lane_mark",
    "lane_edge_mark",
    "bike_lane",
    "bus_lane",
    "parking_lane",
    "median_green",
    "grass_belt",
    "shared_street_surface",
    "colored_pavement",
)
_SURFACE_FALLBACKS: Dict[str, Tuple[str, ...]] = {
    "clear_path": ("sidewalk",),
    "furnishing": ("context_ground", "sidewalk"),
    "transit_pad": ("context_ground", "sidewalk"),
    "curb": ("context_ground",),
    "building_buffer": ("grass", "context_ground"),
    "planting_soil": ("tree_pit", "grass"),
    "crossing": ("sidewalk",),
    "lane_mark": ("carriageway",),
    "lane_edge_mark": ("lane_mark", "carriageway"),
    "bike_lane": ("carriageway",),
    "bus_lane": ("carriageway",),
    "parking_lane": ("carriageway",),
    "median_green": ("grass",),
    "grass_belt": ("grass",),
    "shared_street_surface": ("context_ground", "sidewalk"),
    "colored_pavement": ("sidewalk", "context_ground"),
}


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _resolve_path(path_text: object, base_dir: Path) -> str:
    text = _clean_text(path_text)
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return str(path)


def _coerce_text_list(value: object) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    else:
        items = list(value)
    return [str(item).strip() for item in items if str(item).strip()]


def _read_jsonl_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(dict(json.loads(line)))
    return rows


class ObjectAssetBackend:
    """Abstract object asset backend."""

    def load_rows(self, *, manifest_path: Path | None = None) -> Tuple[str, List[Dict[str, object]]]:
        raise NotImplementedError


@dataclass(frozen=True)
class GroundMaterialRecord:
    material_id: str
    surface_type: str
    source_dataset: str
    license: str
    albedo_path: str = ""
    normal_path: str = ""
    roughness_path: str = ""
    metallic_path: str = ""
    preview_path: str = ""
    style_tags: Tuple[str, ...] = ()
    weather_tags: Tuple[str, ...] = ()
    region_tags: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "material_id": self.material_id,
            "surface_type": self.surface_type,
            "source_dataset": self.source_dataset,
            "license": self.license,
            "albedo_path": self.albedo_path,
            "normal_path": self.normal_path,
            "roughness_path": self.roughness_path,
            "metallic_path": self.metallic_path,
            "preview_path": self.preview_path,
            "style_tags": list(self.style_tags),
            "weather_tags": list(self.weather_tags),
            "region_tags": list(self.region_tags),
        }


@dataclass(frozen=True)
class SkyRecord:
    sky_id: str
    source_dataset: str
    license: str
    hdri_path: str = ""
    preview_path: str = ""
    time_of_day: str = "day"
    weather_tags: Tuple[str, ...] = ()
    illumination_tags: Tuple[str, ...] = ()
    region_tags: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sky_id": self.sky_id,
            "source_dataset": self.source_dataset,
            "license": self.license,
            "hdri_path": self.hdri_path,
            "preview_path": self.preview_path,
            "time_of_day": self.time_of_day,
            "weather_tags": list(self.weather_tags),
            "illumination_tags": list(self.illumination_tags),
            "region_tags": list(self.region_tags),
        }


@dataclass(frozen=True)
class GroundMaterialSelection:
    backend_name: str
    material_ids_by_role: Dict[str, str]
    texture_overrides: Dict[str, str]
    source_datasets: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend_name": self.backend_name,
            "material_ids_by_role": dict(self.material_ids_by_role),
            "texture_overrides": dict(self.texture_overrides),
            "source_datasets": list(self.source_datasets),
        }


@dataclass(frozen=True)
class SkySelection:
    backend_name: str
    sky_id: str
    source_dataset: str
    hdri_path: str = ""
    time_of_day: str = "day"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backend_name": self.backend_name,
            "sky_id": self.sky_id,
            "source_dataset": self.source_dataset,
            "hdri_path": self.hdri_path,
            "time_of_day": self.time_of_day,
        }


class GroundMaterialBackend:
    def select_for_config(self, config: object) -> GroundMaterialSelection:
        raise NotImplementedError


class SkyBackend:
    def select_for_config(self, config: object) -> SkySelection | None:
        raise NotImplementedError


class ManifestObjectAssetBackend(ObjectAssetBackend):
    """Object asset backend backed by a v2 manifest with legacy fallback."""

    def __init__(
        self,
        *,
        manifest_path: str | Path | None = None,
        manifest_paths: Sequence[str | Path] | None = None,
        manifest_v2_path: str | Path | None = None,
    ) -> None:
        self.manifest_path = Path(manifest_path).expanduser().resolve() if manifest_path else None
        self.manifest_paths = tuple(
            Path(str(path)).expanduser().resolve()
            for path in (manifest_paths or ())
            if path not in (None, "") and str(path).strip()
        )
        self.manifest_v2_path = Path(manifest_v2_path).expanduser().resolve() if manifest_v2_path else None
        self.last_load_summary: Dict[str, object] = {}

    def load_rows(self, *, manifest_path: Path | None = None) -> Tuple[str, List[Dict[str, object]]]:
        manifest_sources: List[Path] = list(self.manifest_paths)
        if not manifest_sources:
            raw_legacy_path = manifest_path or self.manifest_path
            if raw_legacy_path:
                manifest_sources = [Path(raw_legacy_path).expanduser().resolve()]

        source_rows: List[Dict[str, object]] = []
        loaded_paths: List[str] = []
        missing_paths: List[str] = []
        for source_path in manifest_sources:
            if not source_path.exists():
                missing_paths.append(str(source_path))
                continue
            loaded_rows = _load_legacy_object_rows(source_path)
            for row in loaded_rows:
                enriched = dict(row)
                enriched.setdefault("manifest_source_path", str(source_path))
                source_rows.append(enriched)
            loaded_paths.append(str(source_path))

        v2_path = self.manifest_v2_path if self.manifest_v2_path and self.manifest_v2_path.exists() else None
        if v2_path is None:
            if not source_rows:
                raise FileNotFoundError(f"object manifest not found: {', '.join(missing_paths or [str(path) for path in manifest_sources])}")
            merged_rows = _merge_object_rows((), source_rows)
            self.last_load_summary = {
                "manifest_paths": loaded_paths,
                "missing_manifest_paths": missing_paths,
                "manifest_source_count": int(len(loaded_paths)),
                "manifest_row_count": int(len(source_rows)),
                "merged_asset_count": int(len(merged_rows)),
            }
            return "manifest_multi_merged", merged_rows

        overlay_rows = _load_object_manifest_v2_rows(v2_path)
        merged_rows = _merge_object_rows(source_rows, overlay_rows)
        self.last_load_summary = {
            "manifest_paths": loaded_paths + [str(v2_path)],
            "missing_manifest_paths": missing_paths,
            "manifest_source_count": int(len(loaded_paths) + 1),
            "manifest_row_count": int(len(source_rows) + len(overlay_rows)),
            "merged_asset_count": int(len(merged_rows)),
        }
        return "manifest_multi_merged", merged_rows


class ManifestGroundMaterialBackend(GroundMaterialBackend):
    """Ground-material backend backed by a JSONL manifest."""

    def __init__(self, *, manifest_path: str | Path | None = None) -> None:
        self.manifest_path = (
            Path(manifest_path).expanduser().resolve()
            if manifest_path
            else DEFAULT_GROUND_MATERIAL_MANIFEST_PATH
        )

    def select_for_config(self, config: object) -> GroundMaterialSelection:
        rows = _load_ground_material_rows(self.manifest_path)
        by_surface: Dict[str, List[GroundMaterialRecord]] = {}
        for row in rows:
            by_surface.setdefault(row.surface_type, []).append(row)

        material_ids_by_role: Dict[str, str] = {}
        texture_overrides: Dict[str, str] = {}
        datasets: List[str] = []
        query_blob = " ".join(
            [
                _clean_text(getattr(config, "query", "")),
                _clean_text(getattr(config, "objective_profile", "")),
                _clean_text(getattr(config, "design_rule_profile", "")),
                _clean_text(getattr(config, "city_context", "")),
                _clean_text(getattr(config, "style_preset", "")),
            ]
        ).lower()

        for surface_role in _SCENE_SURFACE_ROLES:
            candidates = list(by_surface.get(surface_role, ()))
            if not candidates:
                for fallback_surface in _SURFACE_FALLBACKS.get(surface_role, ()):
                    candidates = list(by_surface.get(fallback_surface, ()))
                    if candidates:
                        break
            if not candidates:
                continue
            selected = max(candidates, key=lambda item: _score_ground_material(item, query_blob))
            material_ids_by_role[surface_role] = selected.material_id
            if selected.albedo_path:
                texture_overrides[surface_role] = selected.albedo_path
            if selected.source_dataset:
                datasets.append(selected.source_dataset)

        return GroundMaterialSelection(
            backend_name="manifest_ground_materials",
            material_ids_by_role=material_ids_by_role,
            texture_overrides=texture_overrides,
            source_datasets=tuple(dict.fromkeys(dataset for dataset in datasets if dataset)),
        )


class ManifestSkyBackend(SkyBackend):
    """Sky backend backed by a JSONL manifest."""

    def __init__(self, *, manifest_path: str | Path | None = None) -> None:
        self.manifest_path = Path(manifest_path).expanduser().resolve() if manifest_path else DEFAULT_SKY_MANIFEST_PATH

    def select_for_config(self, config: object) -> SkySelection | None:
        rows = _load_sky_rows(self.manifest_path)
        if not rows:
            return None
        query_blob = " ".join(
            [
                _clean_text(getattr(config, "query", "")),
                _clean_text(getattr(config, "objective_profile", "")),
                _clean_text(getattr(config, "design_rule_profile", "")),
                _clean_text(getattr(config, "city_context", "")),
                _clean_text(getattr(config, "style_preset", "")),
            ]
        ).lower()
        selected = max(rows, key=lambda item: _score_sky(item, query_blob))
        return SkySelection(
            backend_name="manifest_sky",
            sky_id=selected.sky_id,
            source_dataset=selected.source_dataset,
            hdri_path=selected.hdri_path,
            time_of_day=selected.time_of_day,
        )


def _load_legacy_object_rows(manifest_path: Path) -> List[Dict[str, object]]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"real manifest not found: {manifest_path}")
    rows: List[Dict[str, object]] = []
    base_dir = manifest_path.parent.resolve()
    for line_no, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        required = ("asset_id", "category", "text_desc", "mesh_path", "latent_path")
        missing = [key for key in required if key not in payload or _clean_text(payload[key]) == ""]
        if missing:
            raise ValueError(
                f"missing required fields in line {line_no} ({manifest_path}): {', '.join(missing)}"
            )
        row: Dict[str, object] = {
            "asset_id": _clean_text(payload["asset_id"]),
            "category": _clean_text(payload["category"]).lower(),
            "text_desc": _clean_text(payload["text_desc"]),
            "mesh_path": _resolve_path(payload["mesh_path"], base_dir),
            "latent_path": _resolve_path(payload["latent_path"], base_dir),
        }
        for optional_key in _LEGACY_OPTIONAL_FIELDS:
            if optional_key in payload:
                row[optional_key] = payload[optional_key]
        if "asset_role" not in row:
            row["asset_role"] = "building" if row["category"] == "building" else "street_furniture"
        rows.append(row)
    if not rows:
        raise ValueError(f"real manifest is empty: {manifest_path}")
    return rows


def _merge_object_rows(
    legacy_rows: Sequence[Dict[str, object]],
    overlay_rows: Sequence[Dict[str, object]],
) -> List[Dict[str, object]]:
    merged: Dict[str, Dict[str, object]] = {
        str(row.get("asset_id", "")): dict(row)
        for row in legacy_rows
        if _clean_text(row.get("asset_id"))
    }
    for row in overlay_rows:
        asset_id = _clean_text(row.get("asset_id"))
        if not asset_id:
            continue
        merged[asset_id] = {**merged.get(asset_id, {}), **dict(row)}
    return list(merged.values())


def _load_object_manifest_v2_rows(manifest_path: Path) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    base_dir = manifest_path.parent.resolve()
    for line_no, payload in enumerate(_read_jsonl_rows(manifest_path), start=1):
        required = ("asset_id", "category", "text_desc", "mesh_path", "latent_path")
        missing = [key for key in required if key not in payload or _clean_text(payload[key]) == ""]
        if missing:
            raise ValueError(
                f"missing required fields in line {line_no} ({manifest_path}): {', '.join(missing)}"
            )
        row: Dict[str, object] = {
            "asset_id": _clean_text(payload["asset_id"]),
            "category": _clean_text(payload["category"]).lower(),
            "text_desc": _clean_text(payload["text_desc"]),
            "mesh_path": _resolve_path(payload["mesh_path"], base_dir),
            "latent_path": _resolve_path(payload["latent_path"], base_dir),
            "asset_role": _clean_text(payload.get("asset_role")) or (
                "building" if _clean_text(payload["category"]).lower() == "building" else "street_furniture"
            ),
            "source": _clean_text(payload.get("source_dataset") or payload.get("source") or "manifest_v2"),
            "source_dataset": _clean_text(payload.get("source_dataset")),
            "source_uid": _clean_text(payload.get("source_uid")),
            "source_category": _clean_text(payload.get("source_category")),
            "thumbnail_path": _resolve_path(payload.get("thumbnail_path"), base_dir),
            "appearance_embedding_path": _resolve_path(payload.get("appearance_embedding_path"), base_dir),
            "canonical_front": _clean_text(payload.get("canonical_front")),
            "license": _clean_text(payload.get("license")),
            "split": _clean_text(payload.get("split")),
        }
        for key in (
            "metric_width_m",
            "metric_depth_m",
            "metric_height_m",
            "mass_kg",
            "friction",
            "frontage_width_m",
            "depth_m",
            "mesh_face_count",
            "quality_tier",
        ):
            if key in payload and payload.get(key) not in (None, ""):
                row[key] = payload[key]
        for key in (
            "affordance_tags",
            "style_tags",
            "theme_tags",
            "quality_notes",
        ):
            if key in payload:
                row[key] = _coerce_text_list(payload.get(key))
        for key in (
            "material_family",
            "generator_type",
            "runtime_profile",
            "quality_metrics",
            "parameter_snapshot",
            "scene_eligible",
            "hero_asset",
            "avoid_with_presets",
            "height_class",
        ):
            if key in payload:
                row[key] = payload[key]
        rows.append(row)
    if not rows:
        raise ValueError(f"object manifest v2 is empty: {manifest_path}")
    return rows


def _load_ground_material_rows(manifest_path: Path) -> List[GroundMaterialRecord]:
    rows: List[GroundMaterialRecord] = []
    base_dir = manifest_path.parent.resolve()
    for payload in _read_jsonl_rows(manifest_path):
        material_id = _clean_text(payload.get("material_id"))
        surface_type = _clean_text(payload.get("surface_type")).lower()
        if not material_id or not surface_type:
            continue
        rows.append(
            GroundMaterialRecord(
                material_id=material_id,
                surface_type=surface_type,
                source_dataset=_clean_text(payload.get("source_dataset")),
                license=_clean_text(payload.get("license")),
                albedo_path=_resolve_path(payload.get("albedo_path"), base_dir),
                normal_path=_resolve_path(payload.get("normal_path"), base_dir),
                roughness_path=_resolve_path(payload.get("roughness_path"), base_dir),
                metallic_path=_resolve_path(payload.get("metallic_path"), base_dir),
                preview_path=_resolve_path(payload.get("preview_path"), base_dir),
                style_tags=tuple(_coerce_text_list(payload.get("style_tags"))),
                weather_tags=tuple(_coerce_text_list(payload.get("weather_tags"))),
                region_tags=tuple(_coerce_text_list(payload.get("region_tags"))),
            )
        )
    return rows


def _load_sky_rows(manifest_path: Path) -> List[SkyRecord]:
    rows: List[SkyRecord] = []
    base_dir = manifest_path.parent.resolve()
    for payload in _read_jsonl_rows(manifest_path):
        sky_id = _clean_text(payload.get("sky_id"))
        if not sky_id:
            continue
        rows.append(
            SkyRecord(
                sky_id=sky_id,
                source_dataset=_clean_text(payload.get("source_dataset")),
                license=_clean_text(payload.get("license")),
                hdri_path=_resolve_path(payload.get("hdri_path"), base_dir),
                preview_path=_resolve_path(payload.get("preview_path"), base_dir),
                time_of_day=_clean_text(payload.get("time_of_day")) or "day",
                weather_tags=tuple(_coerce_text_list(payload.get("weather_tags"))),
                illumination_tags=tuple(_coerce_text_list(payload.get("illumination_tags"))),
                region_tags=tuple(_coerce_text_list(payload.get("region_tags"))),
            )
        )
    return rows


def _score_ground_material(item: GroundMaterialRecord, query_blob: str) -> Tuple[int, int]:
    score = 0
    tag_matches = 0
    for tag in list(item.style_tags) + list(item.weather_tags) + list(item.region_tags):
        if tag.lower() in query_blob:
            score += 3
            tag_matches += 1
    if item.surface_type in query_blob:
        score += 1
    return (score, tag_matches)


def _score_sky(item: SkyRecord, query_blob: str) -> Tuple[int, int]:
    score = 0
    detail = 0
    if item.time_of_day and item.time_of_day.lower() in query_blob:
        score += 4
        detail += 1
    for tag in list(item.weather_tags) + list(item.illumination_tags) + list(item.region_tags):
        if tag.lower() in query_blob:
            score += 2
            detail += 1
    if "night" in query_blob and item.time_of_day.lower() == "night":
        score += 6
    if "golden" in query_blob and "warm" in [tag.lower() for tag in item.illumination_tags]:
        score += 4
    return (score, detail)


def collect_environment_source_datasets(
    ground_selection: GroundMaterialSelection | None,
    sky_selection: SkySelection | None,
) -> Tuple[str, ...]:
    datasets: List[str] = []
    if ground_selection is not None:
        datasets.extend(list(ground_selection.source_datasets))
    if sky_selection is not None and sky_selection.source_dataset:
        datasets.append(sky_selection.source_dataset)
    return tuple(dict.fromkeys(dataset for dataset in datasets if dataset))
