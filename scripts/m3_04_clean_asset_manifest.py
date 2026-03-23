from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple


_BLOCKED_ASSET_IDS = {
    "objaverse_tree_7c97aea203b34df6bb615d0d3567d984",
}


def _require_trimesh():
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("trimesh is required to clean the asset manifest.") from exc
    return trimesh


def _resolve_path(path_text: object, base_dir: Path) -> Path:
    path = Path(str(path_text)).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _load_rows(manifest_path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_rows(manifest_path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    text = "\n".join(json.dumps(dict(row), ensure_ascii=True) for row in rows) + "\n"
    manifest_path.write_text(text, encoding="utf-8")


def _mesh_face_count_from_path(mesh_path: Path) -> int:
    trimesh = _require_trimesh()
    mesh_or_scene = trimesh.load(mesh_path, force="scene")
    if isinstance(mesh_or_scene, trimesh.Scene):
        return int(sum(len(getattr(geometry, "faces", ())) for geometry in mesh_or_scene.geometry.values()))
    return int(len(getattr(mesh_or_scene, "faces", ())))


def _mesh_face_count(row: Mapping[str, Any], manifest_dir: Path) -> int:
    explicit = row.get("mesh_face_count")
    if explicit is not None:
        try:
            return max(0, int(explicit))
        except (TypeError, ValueError):
            pass
    metrics = row.get("quality_metrics")
    if isinstance(metrics, Mapping) and metrics.get("face_count") is not None:
        try:
            return max(0, int(metrics["face_count"]))
        except (TypeError, ValueError):
            pass
    mesh_path = _resolve_path(row["mesh_path"], manifest_dir)
    if not mesh_path.exists():
        return 0
    return _mesh_face_count_from_path(mesh_path)


def _generator_type(row: Mapping[str, Any]) -> str:
    generator = str(row.get("generator_type", "") or "").strip().lower()
    source = str(row.get("source", "") or "").strip().lower()
    if generator.startswith("parametric") or source == "parametric_generated":
        return "parametric"
    if source == "procedural_fallback":
        return "procedural_fallback"
    if generator:
        return generator
    if source == "procedural_generated":
        return "legacy"
    return source or "legacy"


def _is_preview(row: Mapping[str, Any]) -> bool:
    return str(row.get("runtime_profile", "") or "").strip().lower() == "preview"


def _preview_should_be_demoted(row: Mapping[str, Any]) -> bool:
    category = str(row.get("category", "") or "").strip().lower()
    return category in {"bench", "lamp"} and _generator_type(row) == "parametric" and _is_preview(row)


def _raw_quality_notes(row: Mapping[str, Any]) -> List[str]:
    notes = row.get("quality_notes")
    if notes is None:
        return []
    if isinstance(notes, str):
        return [notes.strip()] if notes.strip() else []
    return [str(item).strip() for item in notes if str(item).strip()]


def _tree_upright_validated(row: Mapping[str, Any]) -> bool:
    if "tree_upright_validated" in _raw_quality_notes(row):
        return True
    metrics = row.get("quality_metrics")
    if isinstance(metrics, Mapping):
        validation = metrics.get("tree_upright_validation")
        if isinstance(validation, Mapping):
            return not bool(str(validation.get("failure_reason", "")).strip())
    return False


def _tree_requires_external_real_asset(row: Mapping[str, Any]) -> bool:
    category = str(row.get("category", "") or "").strip().lower()
    if category != "tree":
        return False
    provenance = _generator_type(row)
    if provenance in {"parametric", "legacy", "procedural_fallback"}:
        return True
    source = str(row.get("source", "") or "").strip().lower()
    if source in {"procedural_generated", "parametric_generated", "procedural_fallback"}:
        return True
    return False


def _thresholds_for_category(category: str) -> Tuple[int, int, int]:
    thresholds = {
        "tree": (120, 350, 1000),
        "lamp": (100, 300, 1100),
        "bench": (100, 280, 900),
        "bus_stop": (180, 600, 1600),
        "bollard": (60, 180, 500),
        "hydrant": (80, 220, 650),
        "trash": (80, 220, 650),
        "mailbox": (80, 220, 650),
        "building": (300, 1200, 4000),
    }
    return thresholds.get(category, (80, 220, 700))


def _quality_tier(row: Mapping[str, Any], face_count: int) -> int:
    category = str(row.get("category", "") or "").strip().lower()
    tier1, tier2, tier3 = _thresholds_for_category(category)
    if face_count < tier1:
        tier = 0
    elif face_count < tier2:
        tier = 1
    elif face_count < tier3:
        tier = 2
    else:
        tier = 3
    if _generator_type(row) == "parametric":
        if _is_preview(row):
            tier = max(0, tier - 1)
        else:
            tier = min(3, tier + 1)
    return int(tier)


def _scene_eligible(row: Mapping[str, Any], face_count: int, quality_tier: int) -> bool:
    asset_id = str(row.get("asset_id", "") or "").strip()
    if asset_id in _BLOCKED_ASSET_IDS:
        return False
    category = str(row.get("category", "") or "").strip().lower()
    if face_count <= 0:
        return False
    if _preview_should_be_demoted(row):
        return False
    if _tree_requires_external_real_asset(row):
        return False
    if category == "tree" and not _tree_upright_validated(row):
        return False
    if category in {"lamp", "tree"} and quality_tier <= 0:
        return False
    if category in {"lamp", "tree"} and _is_preview(row) and quality_tier < 2:
        return False
    if category == "building" and quality_tier <= 0:
        return False
    return True


def _custom_quality_notes(row: Mapping[str, Any]) -> List[str]:
    managed_exact = {
        "scene_ready",
        "scene_blocked",
        "known_bad_asset_blocked",
        "low_poly_visual_asset",
        "preview_runtime",
        "preview_demoted_after_production_seed",
        "procedural_tree_disabled_for_scene_generation",
        "tree_upright_validation_required",
    }
    managed_prefixes = ("mesh_face_count=", "quality_tier=", "generator=")
    notes = row.get("quality_notes")
    if notes is None:
        return []
    if isinstance(notes, str):
        raw_notes = [notes]
    else:
        raw_notes = [str(item).strip() for item in notes if str(item).strip()]
    preserved: List[str] = []
    for note in raw_notes:
        if note in managed_exact:
            continue
        if any(note.startswith(prefix) for prefix in managed_prefixes):
            continue
        if note not in preserved:
            preserved.append(note)
    return preserved


def _quality_notes(row: Mapping[str, Any], face_count: int, quality_tier: int, scene_eligible: bool) -> List[str]:
    notes: List[str] = list(_custom_quality_notes(row))
    notes.extend((f"mesh_face_count={face_count}", f"quality_tier={quality_tier}"))
    asset_id = str(row.get("asset_id", "") or "").strip()
    category = str(row.get("category", "") or "").strip().lower()
    if asset_id in _BLOCKED_ASSET_IDS:
        notes.append("known_bad_asset_blocked")
    if scene_eligible:
        notes.append("scene_ready")
    else:
        notes.append("scene_blocked")
    if category in {"lamp", "tree"} and quality_tier <= 0:
        notes.append("low_poly_visual_asset")
    if _is_preview(row):
        notes.append("preview_runtime")
    if _preview_should_be_demoted(row):
        notes.append("preview_demoted_after_production_seed")
    if _tree_requires_external_real_asset(row):
        notes.append("procedural_tree_disabled_for_scene_generation")
    elif category == "tree" and not _tree_upright_validated(row):
        notes.append("tree_upright_validation_required")
    provenance = _generator_type(row)
    if provenance:
        notes.append(f"generator={provenance}")
    deduped: List[str] = []
    for note in notes:
        if note not in deduped:
            deduped.append(note)
    return deduped


def _clean_row(row: Mapping[str, Any], manifest_dir: Path) -> Dict[str, Any]:
    cleaned = dict(row)
    face_count = _mesh_face_count(cleaned, manifest_dir)
    quality_tier = _quality_tier(cleaned, face_count)
    scene_eligible = _scene_eligible(cleaned, face_count, quality_tier)
    cleaned["mesh_face_count"] = int(face_count)
    cleaned["quality_tier"] = int(quality_tier)
    cleaned["scene_eligible"] = bool(scene_eligible)
    cleaned["quality_notes"] = _quality_notes(cleaned, face_count, quality_tier, scene_eligible)
    return cleaned


def clean_manifest_rows(rows: Iterable[Mapping[str, Any]], manifest_dir: Path) -> List[Dict[str, Any]]:
    return [_clean_row(row, manifest_dir) for row in rows]


def _summarize(rows: Iterable[Mapping[str, Any]]) -> str:
    by_category: Dict[str, Counter] = defaultdict(Counter)
    total = Counter()
    for row in rows:
        category = str(row.get("category", "") or "")
        if bool(row.get("scene_eligible", False)):
            by_category[category]["scene_ready"] += 1
            total["scene_ready"] += 1
        else:
            by_category[category]["blocked"] += 1
            total["blocked"] += 1
        by_category[category][f"tier_{int(row.get('quality_tier', 0))}"] += 1
        total[f"tier_{int(row.get('quality_tier', 0))}"] += 1
    lines = [
        f"rows={sum(total[key] for key in ('scene_ready', 'blocked'))}",
        f"scene_ready={total['scene_ready']}",
        f"blocked={total['blocked']}",
    ]
    for category in sorted(by_category):
        counts = by_category[category]
        lines.append(
            (
                f"{category}: scene_ready={counts['scene_ready']} blocked={counts['blocked']} "
                f"tier0={counts['tier_0']} tier1={counts['tier_1']} "
                f"tier2={counts['tier_2']} tier3={counts['tier_3']}"
            )
        )
    return "\n".join(lines)


def summarize_rows(rows: Iterable[Mapping[str, Any]]) -> str:
    return _summarize(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Annotate asset manifest rows with scene-readiness metadata.")
    parser.add_argument(
        "--manifest",
        default="data/real/real_assets_manifest.jsonl",
        help="Path to the manifest JSONL file.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the cleaned rows back to the manifest in place.",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    rows = _load_rows(manifest_path)
    cleaned_rows = clean_manifest_rows(rows, manifest_path.parent.resolve())
    print(summarize_rows(cleaned_rows))
    if args.write:
        _write_rows(manifest_path, cleaned_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
