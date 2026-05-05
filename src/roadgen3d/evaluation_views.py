"""Selection and encoding helpers for visual LLM evaluation views."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence


DEFAULT_EVALUATION_RENDER_VIEW_LIMIT = 8

_EVALUATION_VIEW_GROUPS: tuple[tuple[frozenset[str], int], ...] = (
    (frozenset({"pedestrian", "street"}), 1),
    (frozenset({"junction_pedestrian"}), 2),
    (frozenset({"bench_eye"}), 1),
    (frozenset({"window_view"}), 1),
    (frozenset({"rooftop"}), 1),
    (frozenset({"overview"}), 1),
)
_PRESERVED_METADATA_KEYS = (
    "kind",
    "camera",
    "target",
    "priority",
    "width",
    "height",
    "source",
)


def rendered_views_for_evaluation_from_layout(
    layout_path: str | Path,
    *,
    limit: int = DEFAULT_EVALUATION_RENDER_VIEW_LIMIT,
) -> List[Dict[str, Any]]:
    """Return encoded rendered views for visual evaluation from a layout file."""

    layout = Path(str(layout_path or "")).expanduser()
    if not layout.exists():
        return []
    try:
        payload = json.loads(layout.read_text(encoding="utf-8"))
    except Exception:
        return []
    return rendered_views_for_evaluation_from_payload(payload, limit=limit, base_dir=layout.parent)


def rendered_views_for_evaluation_from_payload(
    layout_payload: Mapping[str, Any],
    *,
    limit: int = DEFAULT_EVALUATION_RENDER_VIEW_LIMIT,
    base_dir: str | Path | None = None,
) -> List[Dict[str, Any]]:
    """Return encoded 3D captures, falling back to legacy rendered views."""

    summary = dict(layout_payload.get("summary", {}) or {})
    base = Path(base_dir).expanduser() if base_dir else None
    render_views_3d = list(summary.get("render_views_3d", []) or [])
    views_3d = encode_render_views_for_evaluation(
        rank_render_views_for_evaluation(render_views_3d),
        limit=limit,
        label_prefix="3D capture",
        base_dir=base,
    )
    if views_3d:
        return views_3d

    render_views = list(summary.get("render_views", []) or [])
    ranked_legacy = sorted(
        render_views,
        key=lambda item: (
            0 if str(item.get("name", "") or "").startswith("final_") else 1,
            str(item.get("name", "") or ""),
        ),
    )
    return encode_render_views_for_evaluation(
        ranked_legacy,
        limit=limit,
        label_prefix="Rendered view",
        base_dir=base,
    )


def rank_render_views_for_evaluation(
    render_views: Sequence[Mapping[str, Any]],
) -> List[Mapping[str, Any]]:
    """Rank captured views so LLM input covers the most important perspectives."""

    selected: List[tuple[int, Mapping[str, Any]]] = []
    used: set[int] = set()
    for kinds, quota in _EVALUATION_VIEW_GROUPS:
        candidates = [
            (idx, view)
            for idx, view in enumerate(render_views)
            if idx not in used and _view_kind(view) in kinds
        ]
        for idx, view in _pick_diverse(candidates, quota):
            used.add(idx)
            selected.append((idx, view))

    represented_kinds = {_view_kind(view) for _, view in selected if _view_kind(view)}
    remaining = [(idx, view) for idx, view in enumerate(render_views) if idx not in used]
    remaining.sort(
        key=lambda item: (
            0 if _view_kind(item[1]) not in represented_kinds else 1,
            -_priority(item[1]),
            _view_identifier(item[1]),
            item[0],
        )
    )
    selected.extend(remaining)
    return [view for _, view in selected]


def encode_render_views_for_evaluation(
    ranked: Sequence[Mapping[str, Any]],
    *,
    limit: int,
    label_prefix: str,
    base_dir: str | Path | None = None,
) -> List[Dict[str, Any]]:
    """Base64 encode ranked image files while preserving camera metadata."""

    views: List[Dict[str, Any]] = []
    max_count = max(1, int(limit))
    base = Path(base_dir).expanduser() if base_dir else None
    for index, view in enumerate(ranked):
        if len(views) >= max_count:
            break
        path = _resolve_image_path(view, base_dir=base)
        if path is None or not path.exists():
            continue
        mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        try:
            image_data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        except Exception:
            continue
        view_id = _view_identifier(view) or f"view_{index + 1}"
        encoded: Dict[str, Any] = {
            "view_id": view_id,
            "label": str(view.get("label", "") or view.get("title", "") or view.get("name", "") or f"{label_prefix} {index + 1}"),
            "image_data_url": image_data_url,
        }
        for key in _PRESERVED_METADATA_KEYS:
            if key in view and view.get(key) not in (None, ""):
                encoded[key] = view.get(key)
        views.append(encoded)
    return views


def _pick_diverse(
    candidates: Sequence[tuple[int, Mapping[str, Any]]],
    quota: int,
) -> List[tuple[int, Mapping[str, Any]]]:
    remaining = list(candidates)
    picked: List[tuple[int, Mapping[str, Any]]] = []
    while remaining and len(picked) < max(0, int(quota)):
        if not picked:
            best = max(remaining, key=lambda item: (_priority(item[1]), _view_identifier(item[1]), -item[0]))
        else:
            best = max(
                remaining,
                key=lambda item: (
                    _priority(item[1]),
                    _min_target_distance(item[1], [view for _, view in picked]),
                    _view_identifier(item[1]),
                    -item[0],
                ),
            )
        picked.append(best)
        remaining.remove(best)
    return picked


def _resolve_image_path(view: Mapping[str, Any], *, base_dir: Path | None) -> Path | None:
    path_text = str(view.get("path", "") or view.get("image_path", "") or "").strip()
    if not path_text:
        return None
    path = Path(path_text).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    return path


def _view_kind(view: Mapping[str, Any]) -> str:
    return str(view.get("kind", "") or "").strip().lower()


def _view_identifier(view: Mapping[str, Any]) -> str:
    return str(view.get("view_id", "") or view.get("name", "") or "").strip()


def _priority(view: Mapping[str, Any]) -> int:
    try:
        return int(view.get("priority", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _min_target_distance(view: Mapping[str, Any], selected: Sequence[Mapping[str, Any]]) -> float:
    point = _view_point(view)
    if point is None or not selected:
        return 0.0
    distances = []
    for other in selected:
        other_point = _view_point(other)
        if other_point is None:
            continue
        distances.append(sum((point[idx] - other_point[idx]) ** 2 for idx in range(3)) ** 0.5)
    return min(distances) if distances else 0.0


def _view_point(view: Mapping[str, Any]) -> tuple[float, float, float] | None:
    for key in ("target", "camera"):
        value = view.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 3:
            try:
                return (float(value[0]), float(value[1]), float(value[2]))
            except (TypeError, ValueError):
                continue
    return None
