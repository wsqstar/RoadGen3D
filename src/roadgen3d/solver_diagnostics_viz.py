"""Solver diagnostics aggregation and Plotly rendering helpers."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

_BAND_KIND_COLORS: Dict[str, str] = {
    "furnishing": "#4c956c",
    "clear_path": "#2a9d8f",
    "transit_edge": "#457b9d",
    "carriageway": "#6c757d",
}

_SIDE_ORDER: Dict[str, int] = {
    "left": 0,
    "center": 1,
    "": 1,
    "both": 1,
    "right": 2,
}

_KIND_ORDER: Dict[str, int] = {
    "furnishing": 0,
    "clear_path": 1,
    "transit_edge": 2,
    "carriageway": 3,
}

_CROSS_SECTION_HEIGHTS: Dict[str, float] = {
    "furnishing": 1.1,
    "clear_path": 0.95,
    "transit_edge": 1.05,
    "carriageway": 0.78,
}


def _require_plotly() -> Tuple[Any, Any]:
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception:
        return None, None
    return go, make_subplots


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _mapping_list(values: object) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in values or ():
        if isinstance(item, Mapping):
            rows.append(dict(item))
    return rows


def _layout_mode(layout_payload: Mapping[str, Any]) -> str:
    summary = dict(layout_payload.get("summary", {}) or {})
    config = dict(layout_payload.get("config", {}) or {})
    return _safe_str(summary.get("layout_mode") or config.get("layout_mode") or "template").lower() or "template"


def _solver_payload(layout_payload: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(layout_payload.get("solver", {}) or {})


def _summary_payload(layout_payload: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(layout_payload.get("summary", {}) or {})


def _program_payload(layout_payload: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(layout_payload.get("street_program", {}) or {})


def _dedupe_strings(values: Iterable[object]) -> List[str]:
    ordered: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = _safe_str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _truncate_constraints(values: Sequence[str], limit: int = 10) -> List[str]:
    entries = _dedupe_strings(values)
    if len(entries) <= limit:
        return entries
    hidden = len(entries) - limit
    return list(entries[:limit]) + [f"+{hidden} more"]


def _band_label(row: Mapping[str, Any]) -> str:
    side = _safe_str(row.get("side"))
    name = _safe_str(row.get("band_name") or row.get("band_kind") or "band")
    if side in {"left", "right"}:
        return f"{name} ({side})"
    return name


def _sorted_band_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            _SIDE_ORDER.get(_safe_str(row.get("side")).lower(), 9),
            _KIND_ORDER.get(_safe_str(row.get("band_kind")).lower(), 9),
            _safe_str(row.get("band_name")).lower(),
        ),
    )


def _normalize_band_rows(raw_rows: Sequence[Mapping[str, Any]], *, layout_mode: str) -> Tuple[List[Dict[str, Any]], str]:
    if not raw_rows:
        title = "OSM aggregated band view" if layout_mode == "osm" else "Template band view"
        return [], title

    normalized: List[Dict[str, Any]] = []
    if layout_mode == "osm":
        grouped: Dict[Tuple[str, str, str], List[Mapping[str, Any]]] = {}
        for row in raw_rows:
            key = (
                _safe_str(row.get("side")).lower(),
                _safe_str(row.get("band_kind")).lower(),
                _safe_str(row.get("band_name")),
            )
            grouped.setdefault(key, []).append(row)
        for (side, kind, name), group in grouped.items():
            active_names = _dedupe_strings(
                name
                for row in group
                for name in list(row.get("active_constraint_names", []) or [])
            )
            count = len(group)
            normalized.append(
                {
                    "band_name": name or kind or "band",
                    "band_kind": kind,
                    "side": side,
                    "width_m": sum(_safe_float(row.get("width_m")) for row in group) / max(count, 1),
                    "min_width_m": sum(_safe_float(row.get("min_width_m")) for row in group) / max(count, 1),
                    "max_width_m": sum(_safe_float(row.get("max_width_m")) for row in group) / max(count, 1),
                    "slack_m": sum(_safe_float(row.get("slack_m")) for row in group) / max(count, 1),
                    "objective_weight": sum(_safe_float(row.get("objective_weight")) for row in group) / max(count, 1),
                    "active_constraint_names": active_names,
                    "segment_count": int(count),
                    "aggregated": True,
                }
            )
        title = "OSM aggregated band view"
    else:
        for row in raw_rows:
            normalized.append(
                {
                    "band_name": _safe_str(row.get("band_name") or row.get("band_kind") or "band"),
                    "band_kind": _safe_str(row.get("band_kind")).lower(),
                    "side": _safe_str(row.get("side")).lower(),
                    "width_m": _safe_float(row.get("width_m")),
                    "min_width_m": _safe_float(row.get("min_width_m")),
                    "max_width_m": _safe_float(row.get("max_width_m")),
                    "slack_m": _safe_float(row.get("slack_m")),
                    "objective_weight": _safe_float(row.get("objective_weight")),
                    "active_constraint_names": _dedupe_strings(list(row.get("active_constraint_names", []) or [])),
                    "segment_count": 1,
                    "aggregated": False,
                }
            )
        title = "Template band view"

    rows = _sorted_band_rows(normalized)
    for row in rows:
        row["label"] = _band_label(row)
    return rows, title


def _normalize_throughput_rows(payload: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    by_mode = dict(payload.get("by_mode", {}) or {})
    rows: List[Dict[str, Any]] = []
    for mode, values in by_mode.items():
        if not isinstance(values, Mapping):
            continue
        rows.append(
            {
                "mode": _safe_str(mode),
                "required": _safe_float(values.get("required")),
                "actual": _safe_float(values.get("actual")),
                "satisfied": bool(values.get("satisfied", False)),
            }
        )
    rows.sort(key=lambda row: row["mode"])
    return rows, bool(payload.get("overall_satisfied", False))


def _fallback_band_rows(layout_payload: Mapping[str, Any], *, layout_mode: str) -> Tuple[List[Dict[str, Any]], str, str]:
    program = _program_payload(layout_payload)
    summary = _summary_payload(layout_payload)
    program_bands = _mapping_list(program.get("bands", []))
    if program_bands:
        raw_rows = [
            {
                "band_name": _safe_str(row.get("name") or row.get("band_name") or row.get("kind") or "band"),
                "band_kind": _safe_str(row.get("kind") or row.get("band_kind")),
                "side": _safe_str(row.get("side")),
                "width_m": _safe_float(row.get("width_m")),
                "min_width_m": _safe_float(row.get("width_m")),
                "max_width_m": _safe_float(row.get("width_m")),
                "slack_m": 0.0,
                "objective_weight": 0.0,
                "active_constraint_names": [],
            }
            for row in program_bands
        ]
        rows, title = _normalize_band_rows(raw_rows, layout_mode=layout_mode)
        return rows, title, "street_program"

    summary_rows: List[Dict[str, Any]] = []
    left_clear = _safe_float(summary.get("left_clear_path_width_m"))
    left_furnishing = _safe_float(summary.get("left_furnishing_width_m"))
    road_width = _safe_float(summary.get("road_width_m") or summary.get("carriageway_width_m"))
    right_edge_width = _safe_float(summary.get("right_furnishing_width_m"))
    right_clear = _safe_float(summary.get("right_clear_path_width_m"))
    if left_clear > 0.0:
        summary_rows.append(
            {
                "band_name": "left_clear_path",
                "band_kind": "clear_path",
                "side": "left",
                "width_m": left_clear,
                "min_width_m": left_clear,
                "max_width_m": left_clear,
            }
        )
    if left_furnishing > 0.0:
        summary_rows.append(
            {
                "band_name": "left_furnishing",
                "band_kind": "furnishing",
                "side": "left",
                "width_m": left_furnishing,
                "min_width_m": left_furnishing,
                "max_width_m": left_furnishing,
            }
        )
    if road_width > 0.0:
        summary_rows.append(
            {
                "band_name": "carriageway",
                "band_kind": "carriageway",
                "side": "center",
                "width_m": road_width,
                "min_width_m": road_width,
                "max_width_m": road_width,
            }
        )
    if right_edge_width > 0.0:
        summary_rows.append(
            {
                "band_name": "right_furnishing",
                "band_kind": "furnishing",
                "side": "right",
                "width_m": right_edge_width,
                "min_width_m": right_edge_width,
                "max_width_m": right_edge_width,
            }
        )
    if right_clear > 0.0:
        summary_rows.append(
            {
                "band_name": "right_clear_path",
                "band_kind": "clear_path",
                "side": "right",
                "width_m": right_clear,
                "min_width_m": right_clear,
                "max_width_m": right_clear,
            }
        )
    if summary_rows:
        rows, title = _normalize_band_rows(summary_rows, layout_mode=layout_mode)
        return rows, title, "summary_fields"

    title = "OSM aggregated band view" if layout_mode == "osm" else "Template band view"
    return [], title, "none"


def _cross_section_order(rows: Sequence[Mapping[str, Any]], *, side: str) -> List[Dict[str, Any]]:
    current = [dict(row) for row in rows if _safe_str(row.get("side")).lower() == side]
    if side == "left":
        priority = {"clear_path": 0, "furnishing": 1, "transit_edge": 1, "carriageway": 9}
    else:
        priority = {"furnishing": 0, "transit_edge": 0, "clear_path": 1, "carriageway": 9}
    return sorted(
        current,
        key=lambda row: (
            priority.get(_safe_str(row.get("band_kind")).lower(), 8),
            _safe_str(row.get("band_name")).lower(),
        ),
    )


def _cross_section_segments(
    band_rows: Sequence[Mapping[str, Any]],
    *,
    layout_payload: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    left_rows = _cross_section_order(band_rows, side="left")
    right_rows = _cross_section_order(band_rows, side="right")
    center_rows = [dict(row) for row in band_rows if _safe_str(row.get("band_kind")).lower() == "carriageway"]
    summary = _summary_payload(layout_payload)
    if center_rows:
        carriageway = dict(center_rows[0])
    else:
        road_width = _safe_float(summary.get("road_width_m") or summary.get("carriageway_width_m"))
        if road_width <= 0.0:
            carriageway = {}
        else:
            carriageway = {
                "band_name": "carriageway",
                "band_kind": "carriageway",
                "side": "center",
                "width_m": road_width,
                "label": "carriageway",
            }

    total_left = sum(_safe_float(row.get("width_m")) for row in left_rows)
    carriageway_width = _safe_float(carriageway.get("width_m"))
    total_right = sum(_safe_float(row.get("width_m")) for row in right_rows)
    start_x = -(total_left + carriageway_width / 2.0)
    cursor = float(start_x)
    segments: List[Dict[str, Any]] = []
    for row in left_rows:
        width = _safe_float(row.get("width_m"))
        if width <= 0.0:
            continue
        segments.append(
            {
                "x0": cursor,
                "x1": cursor + width,
                "row": dict(row),
            }
        )
        cursor += width
    if carriageway_width > 0.0:
        segments.append(
            {
                "x0": cursor,
                "x1": cursor + carriageway_width,
                "row": dict(carriageway),
            }
        )
        cursor += carriageway_width
    for row in right_rows:
        width = _safe_float(row.get("width_m"))
        if width <= 0.0:
            continue
        segments.append(
            {
                "x0": cursor,
                "x1": cursor + width,
                "row": dict(row),
            }
        )
        cursor += width
    return segments


def _build_annotation(summary: Mapping[str, Any]) -> str:
    lines = [
        f"backend_requested: {_safe_str(summary.get('backend_requested') or 'unknown')}",
        f"backend_used: {_safe_str(summary.get('backend_used') or 'unknown')}",
        f"objective_profile: {_safe_str(summary.get('objective_profile') or 'balanced')}",
        f"fallback_reason: {_safe_str(summary.get('fallback_reason') or 'no fallback')}",
        f"active_constraints: {', '.join(summary.get('active_constraints_display', []) or ['none'])}",
    ]
    return "<br>".join(lines)


def _empty_figure(summary: Mapping[str, Any], *, band_title: str) -> Any:
    go, make_subplots = _require_plotly()
    if go is None or make_subplots is None:
        return None
    fig = make_subplots(
        rows=2,
        cols=1,
        vertical_spacing=0.18,
        subplot_titles=(f"Band Width Diagnostics ({band_title})", "Throughput Diagnostics"),
    )
    fig.add_annotation(
        text="No band_solutions available.",
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.82,
        showarrow=False,
        font={"size": 13, "color": "#666666"},
    )
    fig.add_annotation(
        text="No throughput diagnostics available.",
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.18,
        showarrow=False,
        font={"size": 13, "color": "#666666"},
    )
    fig.add_annotation(
        text=_build_annotation(summary),
        xref="paper",
        yref="paper",
        x=0.98,
        y=1.12,
        showarrow=False,
        align="left",
        xanchor="right",
        yanchor="top",
        bordercolor="#d9d9d9",
        borderwidth=1,
        bgcolor="rgba(255,255,255,0.92)",
        font={"size": 11},
    )
    fig.update_layout(
        title="Solver Diagnostics",
        template="plotly_white",
        height=780,
        margin={"l": 48, "r": 24, "t": 120, "b": 48},
    )
    return fig


def _build_plot(
    *,
    band_rows: Sequence[Mapping[str, Any]],
    throughput_rows: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    band_title: str,
) -> Any:
    go, make_subplots = _require_plotly()
    if go is None or make_subplots is None:
        return None
    if not band_rows and not throughput_rows:
        return _empty_figure(summary, band_title=band_title)

    fig = make_subplots(
        rows=2,
        cols=1,
        vertical_spacing=0.18,
        subplot_titles=(f"Band Width Diagnostics ({band_title})", "Throughput Diagnostics"),
    )

    if band_rows:
        customdata = [
            [
                _safe_str(row.get("band_name")),
                _safe_str(row.get("band_kind")),
                _safe_str(row.get("side") or "center"),
                _safe_float(row.get("min_width_m")),
                _safe_float(row.get("max_width_m")),
                _safe_float(row.get("slack_m")),
                _safe_float(row.get("objective_weight")),
                int(row.get("segment_count", 1) or 1),
                ", ".join(row.get("active_constraint_names", []) or []) or "none",
            ]
            for row in band_rows
        ]
        fig.add_trace(
            go.Bar(
                x=[_safe_float(row.get("width_m")) for row in band_rows],
                y=[_safe_str(row.get("label")) for row in band_rows],
                orientation="h",
                name="actual width",
                marker={
                    "color": [
                        _BAND_KIND_COLORS.get(_safe_str(row.get("band_kind")).lower(), "#6c757d")
                        for row in band_rows
                    ]
                },
                customdata=customdata,
                hovertemplate=(
                    "band=%{customdata[0]}<br>"
                    "kind=%{customdata[1]}<br>"
                    "side=%{customdata[2]}<br>"
                    "actual=%{x:.2f} m<br>"
                    "min=%{customdata[3]:.2f} m<br>"
                    "max=%{customdata[4]:.2f} m<br>"
                    "slack=%{customdata[5]:.2f} m<br>"
                    "objective_weight=%{customdata[6]:.2f}<br>"
                    "segment_count=%{customdata[7]}<br>"
                    "active_constraints=%{customdata[8]}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=[_safe_float(row.get("min_width_m")) for row in band_rows],
                y=[_safe_str(row.get("label")) for row in band_rows],
                mode="markers",
                name="min width",
                marker={"symbol": "diamond", "size": 9, "color": "#1d3557"},
                hovertemplate="min width=%{x:.2f} m<extra></extra>",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=[_safe_float(row.get("max_width_m")) for row in band_rows],
                y=[_safe_str(row.get("label")) for row in band_rows],
                mode="markers",
                name="max width",
                marker={"symbol": "x", "size": 9, "color": "#e76f51"},
                hovertemplate="max width=%{x:.2f} m<extra></extra>",
            ),
            row=1,
            col=1,
        )
        fig.update_yaxes(autorange="reversed", row=1, col=1)
        fig.update_xaxes(title_text="Width (m)", row=1, col=1)
    else:
        fig.add_annotation(
            text="No band_solutions available.",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.82,
            showarrow=False,
            font={"size": 13, "color": "#666666"},
        )

    if throughput_rows:
        actual_colors = [
            "#2a9d8f" if bool(row.get("satisfied")) else "#d90429"
            for row in throughput_rows
        ]
        fig.add_trace(
            go.Bar(
                x=[_safe_float(row.get("actual")) for row in throughput_rows],
                y=[_safe_str(row.get("mode")) for row in throughput_rows],
                orientation="h",
                name="actual",
                marker={"color": actual_colors},
                customdata=[
                    [
                        _safe_float(row.get("required")),
                        bool(row.get("satisfied")),
                    ]
                    for row in throughput_rows
                ],
                hovertemplate=(
                    "mode=%{y}<br>"
                    "actual=%{x:.2f}<br>"
                    "required=%{customdata[0]:.2f}<br>"
                    "satisfied=%{customdata[1]}<extra></extra>"
                ),
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Bar(
                x=[_safe_float(row.get("required")) for row in throughput_rows],
                y=[_safe_str(row.get("mode")) for row in throughput_rows],
                orientation="h",
                name="required",
                marker={"color": "#8d99ae"},
                opacity=0.58,
                hovertemplate="mode=%{y}<br>required=%{x:.2f}<extra></extra>",
            ),
            row=2,
            col=1,
        )
        fig.update_xaxes(title_text="Mode Throughput", row=2, col=1)
    else:
        fig.add_annotation(
            text="No throughput diagnostics available.",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.18,
            showarrow=False,
            font={"size": 13, "color": "#666666"},
        )

    fig.add_annotation(
        text=_build_annotation(summary),
        xref="paper",
        yref="paper",
        x=0.98,
        y=1.12,
        showarrow=False,
        align="left",
        xanchor="right",
        yanchor="top",
        bordercolor="#d9d9d9",
        borderwidth=1,
        bgcolor="rgba(255,255,255,0.92)",
        font={"size": 11},
    )
    fig.update_layout(
        title="Solver Diagnostics",
        template="plotly_white",
        barmode="group",
        height=780,
        margin={"l": 48, "r": 24, "t": 120, "b": 48},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0.0},
    )
    return fig


def build_solver_diagnostics(layout_payload: Mapping[str, Any]) -> Tuple[Any, Dict[str, Any], List[Dict[str, Any]]]:
    """Build a Plotly diagnostics figure and summary from a layout payload."""

    solver = _solver_payload(layout_payload)
    summary_payload = _summary_payload(layout_payload)
    layout_mode = _layout_mode(layout_payload)
    raw_bands = _mapping_list(solver.get("band_solutions", []))
    band_rows, band_view = _normalize_band_rows(raw_bands, layout_mode=layout_mode)
    throughput_source = solver.get("throughput_feasibility", summary_payload.get("throughput_feasibility", {}))
    throughput_rows, overall_satisfied = _normalize_throughput_rows(
        dict(throughput_source) if isinstance(throughput_source, Mapping) else {}
    )
    active_constraints = _dedupe_strings(
        solver.get("active_constraints", summary_payload.get("active_constraints", []))
    )
    fallback_reason = (
        _safe_str(solver.get("fallback_reason"))
        or _safe_str(summary_payload.get("solver_fallback_reason"))
        or "no fallback"
    )
    summary = {
        "layout_mode": layout_mode,
        "band_view": band_view,
        "band_row_count": int(len(band_rows)),
        "throughput_mode_count": int(len(throughput_rows)),
        "backend_requested": _safe_str(
            solver.get("backend_requested") or summary_payload.get("solver_backend_requested") or summary_payload.get("layout_solver_requested")
        ),
        "backend_used": _safe_str(
            solver.get("backend_used") or summary_payload.get("solver_backend_used") or summary_payload.get("layout_solver_used")
        ),
        "objective_profile": _safe_str(
            solver.get("objective_profile") or summary_payload.get("objective_profile") or "balanced"
        ),
        "fallback_reason": fallback_reason,
        "active_constraint_count": int(len(active_constraints)),
        "active_constraints_display": _truncate_constraints(active_constraints, limit=10),
        "overall_throughput_satisfied": bool(overall_satisfied),
        "throughput_feasibility": {
            "overall_satisfied": bool(overall_satisfied),
            "by_mode": {
                str(row["mode"]): {
                    "required": float(row["required"]),
                    "actual": float(row["actual"]),
                    "satisfied": bool(row["satisfied"]),
                }
                for row in throughput_rows
            },
        },
        "objective_score_breakdown": dict(solver.get("objective_score_breakdown", summary_payload.get("objective_score_breakdown", {})) or {}),
    }
    fig = _build_plot(
        band_rows=band_rows,
        throughput_rows=throughput_rows,
        summary=summary,
        band_title=band_view,
    )
    return fig, summary, band_rows


def build_cross_section_preview(layout_payload: Mapping[str, Any]) -> Tuple[Any, Dict[str, Any]]:
    """Build a 2D cross-section preview figure from solver/program payloads."""

    go, _make_subplots = _require_plotly()
    if go is None:
        return None, {}

    layout_mode = _layout_mode(layout_payload)
    solver = _solver_payload(layout_payload)
    raw_bands = _mapping_list(solver.get("band_solutions", []))
    if raw_bands:
        band_rows, band_view = _normalize_band_rows(raw_bands, layout_mode=layout_mode)
        source = "solver_bands"
    else:
        band_rows, band_view, source = _fallback_band_rows(layout_payload, layout_mode=layout_mode)

    segments = _cross_section_segments(band_rows, layout_payload=layout_payload)
    backend_used = _safe_str(
        solver.get("backend_used") or _summary_payload(layout_payload).get("solver_backend_used") or "unknown"
    )
    objective_profile = _safe_str(
        solver.get("objective_profile") or _summary_payload(layout_payload).get("objective_profile") or "balanced"
    )
    total_width = 0.0
    if segments:
        total_width = float(max(segment["x1"] for segment in segments) - min(segment["x0"] for segment in segments))
    else:
        summary = _summary_payload(layout_payload)
        total_width = _safe_float(summary.get("row_width_m") or summary.get("road_width_m"))

    summary_payload = {
        "view_mode": "osm_aggregated" if layout_mode == "osm" else "template",
        "band_view": band_view,
        "data_source": source,
        "backend_used": backend_used,
        "objective_profile": objective_profile,
        "total_width_m": float(total_width),
        "band_count": int(len(band_rows)),
        "bands": [
            {
                "band_name": _safe_str(row.get("band_name")),
                "band_kind": _safe_str(row.get("band_kind")),
                "side": _safe_str(row.get("side")),
                "width_m": _safe_float(row.get("width_m")),
            }
            for row in band_rows
        ],
    }

    fig = go.Figure()
    if not segments:
        fig.add_annotation(
            text="No cross-section data available.",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font={"size": 14, "color": "#666666"},
        )
        fig.update_layout(
            title="Cross-Section Preview",
            template="plotly_white",
            height=360,
            margin={"l": 36, "r": 24, "t": 64, "b": 48},
        )
        return fig, summary_payload

    min_x = min(segment["x0"] for segment in segments)
    max_x = max(segment["x1"] for segment in segments)
    for segment in segments:
        row = dict(segment["row"])
        kind = _safe_str(row.get("band_kind")).lower()
        label = _safe_str(row.get("band_name") or row.get("label") or kind or "band")
        width = float(segment["x1"] - segment["x0"])
        height = float(_CROSS_SECTION_HEIGHTS.get(kind, 1.0))
        color = _BAND_KIND_COLORS.get(kind, "#8d99ae")
        fig.add_shape(
            type="rect",
            x0=float(segment["x0"]),
            x1=float(segment["x1"]),
            y0=0.0,
            y1=height,
            line={"color": "#ffffff", "width": 2},
            fillcolor=color,
        )
        fig.add_trace(
            go.Scatter(
                x=[float((segment["x0"] + segment["x1"]) / 2.0)],
                y=[height / 2.0],
                mode="markers",
                marker={"size": 16, "opacity": 0.0},
                showlegend=False,
                hovertemplate=(
                    f"band={label}<br>"
                    f"kind={kind}<br>"
                    f"side={_safe_str(row.get('side') or 'center')}<br>"
                    f"width={width:.2f} m<extra></extra>"
                ),
            )
        )
        fig.add_annotation(
            x=float((segment["x0"] + segment["x1"]) / 2.0),
            y=height / 2.0,
            text=f"{label}<br>{width:.2f} m",
            showarrow=False,
            font={"size": 11, "color": "#ffffff" if kind == "carriageway" else "#102a43"},
            align="center",
        )

    fig.add_shape(
        type="line",
        x0=0.0,
        x1=0.0,
        y0=-0.05,
        y1=1.22,
        line={"color": "#495057", "width": 1.5, "dash": "dash"},
    )
    fig.add_annotation(
        x=0.0,
        y=1.26,
        text="Road center",
        showarrow=False,
        font={"size": 10, "color": "#495057"},
    )
    fig.add_annotation(
        x=0.99,
        y=1.13,
        xref="paper",
        yref="paper",
        xanchor="right",
        yanchor="top",
        align="left",
        showarrow=False,
        bordercolor="#d9d9d9",
        borderwidth=1,
        bgcolor="rgba(255,255,255,0.92)",
        font={"size": 11},
        text=(
            f"view: {band_view}<br>"
            f"backend: {backend_used}<br>"
            f"objective: {objective_profile}<br>"
            f"source: {source}<br>"
            f"total_width: {total_width:.2f} m"
        ),
    )
    fig.update_layout(
        title="Cross-Section Preview",
        template="plotly_white",
        height=360,
        margin={"l": 36, "r": 24, "t": 72, "b": 48},
    )
    fig.update_xaxes(title_text="Lateral Position (m)", range=[float(min_x) - 0.4, float(max_x) + 0.4])
    fig.update_yaxes(
        title_text="Band Layer",
        range=[-0.08, 1.35],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
    )
    return fig, summary_payload
