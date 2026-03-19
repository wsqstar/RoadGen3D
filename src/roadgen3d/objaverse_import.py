"""Objaverse import helpers for RoadGen3D asset curation and caching."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ObjaverseTargetSpec:
    """One RoadGen3D target category backed by Objaverse metadata heuristics."""

    roadgen_category: str
    lvis_categories: Tuple[str, ...]
    positive_keywords: Tuple[str, ...]
    negative_keywords: Tuple[str, ...] = ()
    theme_tags: Tuple[str, ...] = ()
    asset_role: str = "street_furniture"
    min_face_count: int = 80
    max_face_count: int = 25000


@dataclass(frozen=True)
class ObjaverseCandidate:
    """Scored Objaverse object candidate."""

    uid: str
    roadgen_category: str
    lvis_category: str
    score: float
    annotation: Mapping[str, Any]
    reasons: Tuple[str, ...] = ()

    def to_report_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "roadgen_category": self.roadgen_category,
            "lvis_category": self.lvis_category,
            "score": round(float(self.score), 4),
            "reasons": list(self.reasons),
            "name": str(self.annotation.get("name", "") or ""),
            "license": str(self.annotation.get("license", "") or ""),
            "faceCount": int(self.annotation.get("faceCount", 0) or 0),
            "vertexCount": int(self.annotation.get("vertexCount", 0) or 0),
            "viewerUrl": str(self.annotation.get("viewerUrl", "") or ""),
            "tags": _annotation_tag_names(self.annotation),
        }


@dataclass(frozen=True)
class ObjaverseImportResult:
    """Materialized Objaverse import outputs."""

    manifest_rows: Tuple[Dict[str, Any], ...]
    selected_candidates: Tuple[ObjaverseCandidate, ...]
    downloaded_paths: Dict[str, str]
    cache_root: Path
    report: Dict[str, Any] = field(default_factory=dict)


_DEFAULT_TARGET_SPECS: Tuple[ObjaverseTargetSpec, ...] = (
    ObjaverseTargetSpec(
        roadgen_category="bench",
        lvis_categories=("bench",),
        positive_keywords=("bench", "park", "outdoor", "street"),
        negative_keywords=("church", "pew", "indoor", "disney", "minecraft", "blockbench"),
        theme_tags=("commercial", "residential", "transit"),
        min_face_count=80,
        max_face_count=20000,
    ),
    ObjaverseTargetSpec(
        roadgen_category="lamp",
        lvis_categories=("streetlight", "lamppost"),
        positive_keywords=("streetlight", "street light", "lamppost", "outdoor", "urban", "road"),
        negative_keywords=("table lamp", "ceiling", "interior", "flashlight", "headlight"),
        theme_tags=("commercial", "transit", "civic"),
        min_face_count=100,
        max_face_count=30000,
    ),
    ObjaverseTargetSpec(
        roadgen_category="trash",
        lvis_categories=("trash_can",),
        positive_keywords=("trash", "garbage", "waste", "bin", "outdoor"),
        negative_keywords=("kitchen", "indoor"),
        theme_tags=("commercial", "transit", "residential"),
        min_face_count=80,
        max_face_count=18000,
    ),
    ObjaverseTargetSpec(
        roadgen_category="mailbox",
        lvis_categories=("mailbox_(at_home)", "mail_slot"),
        positive_keywords=("mailbox", "postbox", "mail slot", "mail"),
        negative_keywords=("wall", "door", "indoor"),
        theme_tags=("commercial", "residential"),
        min_face_count=100,
        max_face_count=16000,
    ),
    ObjaverseTargetSpec(
        roadgen_category="bollard",
        lvis_categories=("stop_sign", "traffic_light"),
        positive_keywords=("post", "sign", "traffic", "street", "roadside"),
        negative_keywords=("toy", "miniature", "car"),
        theme_tags=("commercial", "transit"),
        min_face_count=80,
        max_face_count=22000,
    ),
)

_RECOMMENDED_DEFAULT_CATEGORIES: Tuple[str, ...] = ("bench", "lamp", "trash", "mailbox")


def default_target_specs(categories: Optional[Sequence[str]] = None) -> Tuple[ObjaverseTargetSpec, ...]:
    """Return default RoadGen3D target specs, optionally filtered by category."""

    if not categories:
        return _DEFAULT_TARGET_SPECS
    requested = {str(category).strip().lower() for category in categories if str(category).strip()}
    return tuple(spec for spec in _DEFAULT_TARGET_SPECS if spec.roadgen_category in requested)


def recommended_default_categories() -> Tuple[str, ...]:
    """Return the recommended first-wave Objaverse categories for RoadGen3D."""

    return _RECOMMENDED_DEFAULT_CATEGORIES


def configure_objaverse_cache(cache_root: Path):
    """Import objaverse and redirect its cache into the project workspace."""

    module = importlib.import_module("objaverse")
    resolved = cache_root.expanduser().resolve()
    module.BASE_PATH = str(resolved)
    module._VERSIONED_PATH = str(resolved / "hf-objaverse-v1")
    return module


def load_lvis_annotations(cache_root: Path) -> Dict[str, List[str]]:
    """Load and cache the Objaverse LVIS category index."""

    objaverse = configure_objaverse_cache(cache_root)
    annotations = objaverse.load_lvis_annotations()
    return {str(key): [str(item) for item in value] for key, value in dict(annotations).items()}


def collect_lvis_candidate_uids(
    lvis_annotations: Mapping[str, Sequence[str]],
    specs: Sequence[ObjaverseTargetSpec],
) -> Dict[str, List[Tuple[str, str]]]:
    """Collect uid pools per RoadGen category from LVIS aliases."""

    out: Dict[str, List[Tuple[str, str]]] = {}
    for spec in specs:
        items: List[Tuple[str, str]] = []
        for lvis_category in spec.lvis_categories:
            for uid in lvis_annotations.get(lvis_category, ()) or ():
                items.append((str(uid), str(lvis_category)))
        deduped: List[Tuple[str, str]] = []
        seen: set[str] = set()
        for uid, lvis_category in items:
            if uid in seen:
                continue
            seen.add(uid)
            deduped.append((uid, lvis_category))
        out[spec.roadgen_category] = deduped
    return out


def load_annotation_subset(cache_root: Path, uids: Sequence[str]) -> Dict[str, Any]:
    """Load a subset of Objaverse metadata rows."""

    ordered = [str(uid) for uid in uids if str(uid)]
    if not ordered:
        return {}
    objaverse = configure_objaverse_cache(cache_root)
    return dict(objaverse.load_annotations(ordered))


def score_candidate(
    annotation: Mapping[str, Any],
    spec: ObjaverseTargetSpec,
    *,
    lvis_category: str,
) -> Optional[ObjaverseCandidate]:
    """Score one Objaverse annotation for one RoadGen category."""

    uid = str(annotation.get("uid", "") or "")
    if not uid:
        return None
    if not bool(annotation.get("isDownloadable", False)):
        return None
    face_count = int(annotation.get("faceCount", 0) or 0)
    if face_count < int(spec.min_face_count) or face_count > int(spec.max_face_count):
        return None
    license_name = str(annotation.get("license", "") or "").strip()
    if license_name and "editorial" in license_name.lower():
        return None

    text_parts = [
        str(annotation.get("name", "") or ""),
        str(annotation.get("description", "") or ""),
        str(lvis_category or ""),
        " ".join(_annotation_tag_names(annotation)),
        " ".join(_annotation_category_names(annotation)),
    ]
    text_blob = " ".join(part.strip().lower() for part in text_parts if part and part.strip())
    score = 1.0
    reasons: List[str] = [f"lvis:{lvis_category}"]

    keyword_hits = [keyword for keyword in spec.positive_keywords if keyword in text_blob]
    if keyword_hits:
        score += 0.45 * len(keyword_hits)
        reasons.extend(f"kw+:{keyword}" for keyword in keyword_hits[:6])
    negative_hits = [keyword for keyword in spec.negative_keywords if keyword in text_blob]
    if negative_hits:
        score -= 0.65 * len(negative_hits)
        reasons.extend(f"kw-:{keyword}" for keyword in negative_hits[:4])

    if "lowpoly" in text_blob or "low poly" in text_blob:
        score += 0.2
        reasons.append("tag:lowpoly")
    if "outdoor" in text_blob or "street" in text_blob or "urban" in text_blob:
        score += 0.2
    if license_name:
        score += 0.1
        reasons.append(f"license:{license_name}")

    if score <= 0.0:
        return None
    return ObjaverseCandidate(
        uid=uid,
        roadgen_category=spec.roadgen_category,
        lvis_category=str(lvis_category),
        score=float(score),
        annotation=annotation,
        reasons=tuple(reasons),
    )


def select_top_candidates(
    annotations_by_uid: Mapping[str, Mapping[str, Any]],
    uid_pools_by_category: Mapping[str, Sequence[Tuple[str, str]]],
    specs: Sequence[ObjaverseTargetSpec],
    *,
    max_per_category: int,
) -> Tuple[ObjaverseCandidate, ...]:
    """Select top Objaverse candidates per RoadGen category."""

    spec_by_category = {spec.roadgen_category: spec for spec in specs}
    selected: List[ObjaverseCandidate] = []
    for roadgen_category, uid_pairs in uid_pools_by_category.items():
        spec = spec_by_category.get(roadgen_category)
        if spec is None:
            continue
        scored: List[ObjaverseCandidate] = []
        for uid, lvis_category in uid_pairs:
            annotation = annotations_by_uid.get(uid)
            if annotation is None:
                continue
            candidate = score_candidate(annotation, spec, lvis_category=lvis_category)
            if candidate is not None:
                scored.append(candidate)
        scored.sort(
            key=lambda item: (
                float(item.score),
                int(item.annotation.get("faceCount", 0) or 0),
                str(item.annotation.get("name", "") or ""),
            ),
            reverse=True,
        )
        selected.extend(scored[: max(int(max_per_category), 0)])
    return tuple(selected)


def download_selected_objects(
    cache_root: Path,
    candidates: Sequence[ObjaverseCandidate],
    *,
    download_processes: int = 1,
) -> Dict[str, str]:
    """Download and cache selected Objaverse GLBs."""

    objaverse = configure_objaverse_cache(cache_root)
    uids = [candidate.uid for candidate in candidates]
    if not uids:
        return {}
    return {str(key): str(value) for key, value in dict(objaverse.load_objects(uids, download_processes=int(download_processes))).items()}


def compose_manifest_row(
    candidate: ObjaverseCandidate,
    *,
    mesh_path: str,
    latents_dir: Path,
    split: str = "train",
) -> Dict[str, Any]:
    """Convert one selected Objaverse candidate into RoadGen3D manifest format."""

    annotation = candidate.annotation
    text_desc = compose_text_description(annotation)
    thumbnail_url = _largest_thumbnail_url(annotation)
    asset_id = f"objaverse_{candidate.roadgen_category}_{candidate.uid}"
    return {
        "asset_id": asset_id,
        "category": candidate.roadgen_category,
        "asset_role": "building" if candidate.roadgen_category == "building" else "street_furniture",
        "theme_tags": list(_default_theme_tags(candidate.roadgen_category)),
        "text_desc": text_desc,
        "mesh_path": str(mesh_path),
        "latent_path": str((latents_dir / f"{asset_id}.pt").resolve()),
        "license": str(annotation.get("license", "") or "unknown"),
        "source": "objaverse_import",
        "split": str(split).strip().lower() or "train",
        "generator_type": "objaverse_v1",
        "mesh_face_count": int(annotation.get("faceCount", 0) or 0),
        "quality_metrics": {
            "face_count": int(annotation.get("faceCount", 0) or 0),
            "vertex_count": int(annotation.get("vertexCount", 0) or 0),
        },
        "objaverse_uid": candidate.uid,
        "objaverse_uri": str(annotation.get("uri", "") or ""),
        "objaverse_viewer_url": str(annotation.get("viewerUrl", "") or ""),
        "objaverse_thumbnail_url": thumbnail_url,
        "objaverse_lvis_category": candidate.lvis_category,
        "objaverse_score": round(float(candidate.score), 4),
        "objaverse_reasons": list(candidate.reasons),
        "face_count": int(annotation.get("faceCount", 0) or 0),
        "vertex_count": int(annotation.get("vertexCount", 0) or 0),
        "is_downloadable": bool(annotation.get("isDownloadable", False)),
        "tags": _annotation_tag_names(annotation),
    }


def import_objaverse_assets(
    *,
    cache_root: Path,
    latents_dir: Path,
    requested_categories: Sequence[str],
    max_per_category: int = 8,
    download_processes: int = 1,
    split: str = "train",
) -> ObjaverseImportResult:
    """Run the Objaverse import pipeline into RoadGen3D-compatible rows."""

    specs = default_target_specs(requested_categories)
    if not specs:
        raise ValueError("No valid Objaverse target categories were requested.")
    lvis_annotations = load_lvis_annotations(cache_root)
    uid_pools_by_category = collect_lvis_candidate_uids(lvis_annotations, specs)
    requested_uids = sorted({uid for pairs in uid_pools_by_category.values() for uid, _ in pairs})
    annotations_by_uid = load_annotation_subset(cache_root, requested_uids)
    selected = select_top_candidates(
        annotations_by_uid,
        uid_pools_by_category,
        specs,
        max_per_category=int(max_per_category),
    )
    downloaded_paths = download_selected_objects(
        cache_root,
        selected,
        download_processes=int(download_processes),
    )
    manifest_rows = tuple(
        compose_manifest_row(
            candidate,
            mesh_path=downloaded_paths[candidate.uid],
            latents_dir=latents_dir,
            split=split,
        )
        for candidate in selected
        if candidate.uid in downloaded_paths
    )
    report = {
        "cache_root": str(cache_root.resolve()),
        "requested_categories": list(requested_categories),
        "recommended_default_categories": list(recommended_default_categories()),
        "selected_count": int(len(selected)),
        "downloaded_count": int(len(downloaded_paths)),
        "selected_by_category": _count_by_key(selected, key=lambda item: item.roadgen_category),
        "available_lvis_counts": {
            category: int(len(uid_pools_by_category.get(category, ()) or ()))
            for category in [spec.roadgen_category for spec in specs]
        },
        "downloaded_paths": {str(key): str(value) for key, value in downloaded_paths.items()},
        "manifest_asset_ids": [str(row.get("asset_id", "") or "") for row in manifest_rows],
        "selected_candidates": [candidate.to_report_dict() for candidate in selected],
    }
    return ObjaverseImportResult(
        manifest_rows=manifest_rows,
        selected_candidates=selected,
        downloaded_paths=downloaded_paths,
        cache_root=cache_root.resolve(),
        report=report,
    )


def write_manifest_rows(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write manifest rows as JSONL."""

    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(dict(row), ensure_ascii=True) for row in rows) + "\n"
    path.write_text(text, encoding="utf-8")


def append_manifest_rows(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    """Append manifest rows while de-duplicating by asset_id."""

    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: List[Dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing.append(json.loads(line))
    by_asset_id: Dict[str, Dict[str, Any]] = {
        str(row.get("asset_id", "") or ""): dict(row)
        for row in existing
        if str(row.get("asset_id", "") or "")
    }
    appended = 0
    for row in rows:
        asset_id = str(row.get("asset_id", "") or "")
        if not asset_id:
            continue
        if asset_id not in by_asset_id:
            appended += 1
        by_asset_id[asset_id] = dict(row)
    write_manifest_rows(path, by_asset_id.values())
    return int(appended)


def write_report_json(path: Path, report: Mapping[str, Any]) -> None:
    """Write an Objaverse selection report."""

    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(report), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def compose_text_description(annotation: Mapping[str, Any]) -> str:
    """Compose a compact retrieval description from Objaverse metadata."""

    parts: List[str] = []
    for value in (
        str(annotation.get("name", "") or "").strip(),
        str(annotation.get("description", "") or "").strip(),
    ):
        if value and value not in parts:
            parts.append(value)
    tag_names = [tag for tag in _annotation_tag_names(annotation) if tag]
    if tag_names:
        parts.append("tags: " + ", ".join(tag_names[:8]))
    return ". ".join(parts)[:512]


def _annotation_tag_names(annotation: Mapping[str, Any]) -> List[str]:
    tags = annotation.get("tags", []) or []
    out: List[str] = []
    for tag in tags:
        if isinstance(tag, Mapping):
            name = str(tag.get("name", "") or "").strip()
        else:
            name = str(tag).strip()
        if name and name not in out:
            out.append(name)
    return out


def _annotation_category_names(annotation: Mapping[str, Any]) -> List[str]:
    categories = annotation.get("categories", []) or []
    out: List[str] = []
    for category in categories:
        if isinstance(category, Mapping):
            name = str(category.get("name", "") or category.get("slug", "") or "").strip()
        else:
            name = str(category).strip()
        if name and name not in out:
            out.append(name)
    return out


def _largest_thumbnail_url(annotation: Mapping[str, Any]) -> str:
    images = (((annotation.get("thumbnails", {}) or {}).get("images", []) or []))
    best_url = ""
    best_area = -1
    for image in images:
        if not isinstance(image, Mapping):
            continue
        width = int(image.get("width", 0) or 0)
        height = int(image.get("height", 0) or 0)
        area = width * height
        url = str(image.get("url", "") or "").strip()
        if url and area > best_area:
            best_area = area
            best_url = url
    return best_url


def _default_theme_tags(category: str) -> Tuple[str, ...]:
    mapping = {
        "bench": ("commercial", "residential", "transit"),
        "lamp": ("commercial", "transit", "civic"),
        "trash": ("commercial", "transit"),
        "mailbox": ("commercial", "residential"),
        "bollard": ("commercial", "transit"),
    }
    return mapping.get(str(category).strip().lower(), ("commercial",))


def _count_by_key(items: Sequence[Any], *, key) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        bucket = str(key(item))
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts
