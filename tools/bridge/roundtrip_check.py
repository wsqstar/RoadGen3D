#!/usr/bin/env python3
"""Round-trip checker for RoadPen <-> RoadGen3D bridge conversion."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SCRIPT = ROOT / "tools" / "bridge" / "format_bridge.py"
PYTHON_BIN = sys.executable
SCHEMA = "roadpen_roadgen3d_roundtrip_check_v1"


KNOWN_FORMATS = ("roadpen", "roadgen3d")
KNOWN_MODES = ("preview", "repair", "strict")


@dataclass
class RoundEdge:
    points: List[Tuple[float, float]]
    raw: Mapping[str, Any]


@dataclass
class RoadGraph:
    format: str
    nodes: List[Tuple[str, float, float]]
    edges: List[RoundEdge]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_float(value: Any, *, label: str = "value", default: float | None = None) -> float:
    try:
        if value is None:
            if default is None:
                raise TypeError
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        if default is None:
            raise ValueError(f"{label} must be number")
        return float(default)


def _as_int(value: Any, *, label: str = "value", default: int | None = None) -> int:
    try:
        if value is None:
            if default is None:
                raise TypeError
            return int(default)
        return int(value)
    except (TypeError, ValueError):
        if default is None:
            raise ValueError(f"{label} must be int")
        return int(default)


def _as_point_list(value: Any) -> List[Tuple[float, float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    out: List[Tuple[float, float]] = []
    for item in value:
        if isinstance(item, Mapping) and "x" in item and "y" in item:
            try:
                out.append((_as_float(item.get("x"), label="point.x"), _as_float(item.get("y"), label="point.y")))
            except Exception:
                continue
    return out


def _dedupe_adjacent(points: Sequence[Tuple[float, float]], eps: float = 1e-6) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for point in points:
        if not out:
            out.append((float(point[0]), float(point[1])))
            continue
        prev = out[-1]
        if math.hypot(point[0] - prev[0], point[1] - prev[1]) > eps:
            out.append((float(point[0]), float(point[1])))
    return out


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _segment_length(points: Sequence[Tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(_distance(points[i], points[i + 1]) for i in range(len(points) - 1))


def _resample_polyline(points: Sequence[Tuple[float, float]], n: int = 32) -> List[Tuple[float, float]]:
    if n <= 1:
        return list(points[:1])
    if len(points) < 2:
        return list(points) if points else [(0.0, 0.0)]

    dists = [0.0]
    for i in range(len(points) - 1):
        dists.append(dists[-1] + _distance(points[i], points[i + 1]))
    total = dists[-1]
    if total <= 1e-9:
        return [points[0]] * n

    sampled: List[Tuple[float, float]] = []
    targets = [i * total / (n - 1) for i in range(n)]
    seg = 0
    for t in targets:
        while seg + 1 < len(dists) and dists[seg + 1] < t:
            seg += 1
        if seg + 1 >= len(points):
            sampled.append(points[-1])
            continue
        a = points[seg]
        b = points[min(seg + 1, len(points) - 1)]
        span = max(dists[seg + 1] - dists[seg], 1e-9)
        alpha = (t - dists[seg]) / span
        sampled.append((a[0] + alpha * (b[0] - a[0]), a[1] + alpha * (b[1] - a[1])))
    return sampled


def _polyline_mean_distance(a: Sequence[Tuple[float, float]], b: Sequence[Tuple[float, float]]) -> float:
    if not a or not b:
        return float("inf")
    n = max(len(a), len(b), 1)
    sa = _resample_polyline(a, n=max(4, min(100, n * 2)))
    sb = _resample_polyline(b, n=max(4, len(sa)))
    if len(sa) != len(sb):
        sa = _resample_polyline(a, n=max(4, max(len(sa), len(sb))))
        sb = _resample_polyline(b, n=max(4, max(len(sa), len(sb))))
    pairs = zip(sa, sb)
    total = 0.0
    for p, q in pairs:
        total += _distance(p, q)
    return total / max(len(sa), 1)


def _sample_limit_count(total: int, sample_limit: int | None) -> int:
    if sample_limit is None or sample_limit <= 0:
        return total
    return min(total, sample_limit)


def _coerce_graph_format(payload: Mapping[str, Any], declared: str | None = None) -> str:
    if declared in KNOWN_FORMATS:
        return declared

    if "scene" in payload and isinstance(payload["scene"], Mapping):
        return "roadpen"
    if "nodes" in payload and "edges" in payload and "profiles" in payload:
        return "roadpen"
    if "centerlines" in payload and "junctions" in payload and "version" in payload:
        return "roadgen3d"
    if "annotation" in payload and isinstance(payload["annotation"], Mapping):
        return _coerce_graph_format(payload["annotation"], declared=None)

    if "__bridge_meta" in payload and payload.get("version") == "roadgen3d_reference_annotation_v2":
        return "roadgen3d"

    return "roadpen"


def _unwrap_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if "payload" in payload and isinstance(payload["payload"], Mapping):
        inner = payload["payload"]
        if "schema" in payload and isinstance(payload.get("schema"), str) and str(payload["schema"]).startswith("roadpen_roadgen3d_bridge"):
            return inner
    return payload


def _parse_roadpen(payload: Mapping[str, Any]) -> RoadGraph:
    nodes: List[Tuple[str, float, float]] = []
    node_map: Dict[str, Tuple[float, float]] = {}
    for node in payload.get("nodes", []):
        if not isinstance(node, Mapping):
            continue
        nid = str(node.get("id") or f"node_{len(nodes):04d}")
        if "x" in node and "y" in node:
            try:
                x = _as_float(node.get("x"), label="node.x")
                y = _as_float(node.get("y"), label="node.y")
            except ValueError:
                continue
            node_map[nid] = (x, y)
            nodes.append((nid, x, y))

    edges: List[RoundEdge] = []
    for edge in payload.get("edges", []):
        if not isinstance(edge, Mapping):
            continue
        from_id = str(edge.get("from") or "")
        to_id = str(edge.get("to") or "")
        if not from_id or not to_id:
            continue
        from_pt = node_map.get(from_id)
        to_pt = node_map.get(to_id)
        if from_pt is None or to_pt is None:
            continue
        controls = _as_point_list(edge.get("controlPoints"))
        controls = [(float(x), float(y)) for x, y in controls]
        points = _dedupe_adjacent([from_pt, *controls, to_pt])
        if len(points) < 2:
            continue
        edges.append(RoundEdge(points=points, raw=edge))
    return RoadGraph(format="roadpen", nodes=nodes, edges=edges)


def _parse_roadgen(payload: Mapping[str, Any]) -> RoadGraph:
    inner = payload
    if "annotation" in inner and isinstance(inner["annotation"], Mapping):
        inner = inner["annotation"]

    nodes: List[Tuple[str, float, float]] = []
    junction_seen = set[Tuple[float, float]]()
    for junc in inner.get("junctions", []):
        if isinstance(junc, Mapping) and "id" in junc and "x" in junc and "y" in junc:
            try:
                j_id = str(junc.get("id"))
                x = _as_float(junc.get("x"), label="junction.x")
                y = _as_float(junc.get("y"), label="junction.y")
            except ValueError:
                continue
            key = (x, y)
            if key not in junction_seen:
                junction_seen.add(key)
                nodes.append((j_id, x, y))

    edges: List[RoundEdge] = []
    for idx, cl in enumerate(inner.get("centerlines", [])):
        if not isinstance(cl, Mapping):
            continue
        fid = str(cl.get("id") or f"cl_{idx + 1:04d}")
        points = _as_point_list(cl.get("points"))
        points = _dedupe_adjacent([(float(x), float(y)) for x, y in points])
        if len(points) < 2:
            continue
        edges.append(RoundEdge(points=points, raw=cl))
        if points:
            nodes.append((f"{fid}_start", float(points[0][0]), float(points[0][1])))
            nodes.append((f"{fid}_end", float(points[-1][0]), float(points[-1][1])))
    if not nodes:
        # fallback for scenes without junction objects
        for idx, edge in enumerate(edges):
            if edge.points:
                nodes.append((f"{idx}_start", float(edge.points[0][0]), float(edge.points[0][1])))
                nodes.append((f"{idx}_end", float(edge.points[-1][0]), float(edge.points[-1][1])))
    return RoadGraph(format="roadgen3d", nodes=nodes, edges=edges)


def _build_graph_from_payload(payload: Mapping[str, Any], fmt: str) -> RoadGraph:
    payload = _unwrap_payload(payload)
    if fmt == "roadpen":
        return _parse_roadpen(payload)
    return _parse_roadgen(payload)


def _grid_key(point: Tuple[float, float], tol: float) -> Tuple[int, int]:
    scale = max(float(tol), 1e-6)
    return (int(round(point[0] / scale)), int(round(point[1] / scale)))


def _build_point_index(points: List[Tuple[str, float, float]], tol: float) -> Dict[Tuple[int, int], List[Tuple[str, Tuple[float, float]]]]:
    buckets: Dict[Tuple[int, int], List[Tuple[str, Tuple[float, float]]]] = defaultdict(list)
    for nid, x, y in points:
        buckets[_grid_key((x, y), tol)].append((nid, (x, y)))
    return buckets


def _pop_match(
    point: Tuple[float, float],
    buckets: Dict[Tuple[int, int], List[Tuple[str, Tuple[float, float]]]],
    tol: float,
    used: set[str],
    strict: bool = False,
) -> str | None:
    base = _grid_key(point, tol)
    cx, cy = base
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            key = (cx + dx, cy + dy)
            bucket = buckets.get(key, [])
            if not bucket:
                continue
            if strict:
                bucket.sort(key=lambda item: _distance(point, item[1]))
            for idx, (nid, pnt) in enumerate(bucket):
                if nid in used:
                    continue
                if _distance(point, pnt) <= tol:
                    used.add(nid)
                    if strict:
                        bucket[idx] = (bucket[idx][0], (float("inf"), float("inf")))
                    return nid
    return None


def _node_recall(src: RoadGraph, dst: RoadGraph, tol: float) -> Tuple[float, Dict[str, Any], Dict[str, str]]:
    if not src.nodes:
        return 1.0, {"src_nodes": 0, "dst_nodes": len(dst.nodes), "matched_nodes": 0}, {}

    index = _build_point_index(dst.nodes, tol)
    used: set[str] = set()
    matched: Dict[str, str] = {}
    for sid, x, y in src.nodes:
        m = _pop_match((x, y), index, tol, used, strict=True)
        if m is not None:
            matched[sid] = m
    recall = len(matched) / len(src.nodes) if src.nodes else 1.0
    info = {"src_nodes": len(src.nodes), "dst_nodes": len(dst.nodes), "matched_nodes": len(matched)}
    return recall, info, matched


def _edge_recall(src: RoadGraph, dst: RoadGraph, tol: float, len_drift: float) -> Tuple[float, Dict[str, Any], List[str]]:
    if not src.edges:
        return 1.0, {"src_edges": 0, "dst_edges": len(dst.edges), "matched_edges": 0}, []

    used: set[int] = set()
    matched = 0
    reasons: List[str] = []
    dst_points = [e.points for e in dst.edges]
    for edge in src.edges:
        matched_id = None
        best = float("inf")
        for idx, candidate in enumerate(dst_points):
            if idx in used:
                continue
            if len(candidate) < 2:
                continue
            d1 = _distance(edge.points[0], candidate[0]) + _distance(edge.points[-1], candidate[-1])
            d2 = _distance(edge.points[0], candidate[-1]) + _distance(edge.points[-1], candidate[0])
            if min(d1, d2) > tol * 4:
                continue
            mean1 = _polyline_mean_distance(edge.points, candidate)
            rev = _polyline_mean_distance(edge.points, list(reversed(candidate)))
            mean2 = min(mean1, rev)
            if mean2 <= tol * 2:
                len_src = _segment_length(edge.points)
                len_dst = _segment_length(candidate)
                len_rel = abs(len_src - len_dst) / max(max(len_src, len_dst), 1e-6)
                len_ok = len_rel <= max(0.35, len_drift * 8)
                if len_ok and mean2 < best:
                # tie-breaker by length closeness
                    matched_id = idx
                    best = mean2
        if matched_id is not None:
            used.add(matched_id)
            matched += 1
        else:
            reasons.append(f"unmatched edge with {len(edge.points)} points")
    info = {"src_edges": len(src.edges), "dst_edges": len(dst.edges), "matched_edges": matched}
    return matched / len(src.edges), info, reasons


def _components(nodes: List[Tuple[str, float, float]], edges: List[RoundEdge], tol: float) -> int:
    if not nodes:
        return 0

    idx_map: Dict[str, int] = {nid: i for i, (nid, _, _) in enumerate(nodes)}
    parent = list(range(len(nodes)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def unite(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for edge in edges:
        if len(edge.points) < 2:
            continue
        a = min(range(len(nodes)), key=lambda i: _distance((nodes[i][1], nodes[i][2]), edge.points[0]))
        b = min(range(len(nodes)), key=lambda i: _distance((nodes[i][1], nodes[i][2]), edge.points[-1]))
        if _distance((nodes[a][1], nodes[a][2]), edge.points[0]) <= tol and _distance((nodes[b][1], nodes[b][2]), edge.points[-1]) <= tol:
            unite(a, b)

    roots = {find(i) for i in range(len(nodes))}
    return len(roots)


def _topology_ok(
    src: RoadGraph,
    dst: RoadGraph,
    src_node_map: Dict[str, str],
    tol: float,
    node_match: Dict[str, str],
) -> Tuple[bool, Dict[str, Any], List[str]]:
    src_deg: Dict[str, int] = defaultdict(int)
    for edge in src.edges:
        if len(edge.points) >= 2:
            src_deg[edge.raw.get("from", "") if isinstance(edge.raw, Mapping) else ""] += 1
            src_deg[edge.raw.get("to", "") if isinstance(edge.raw, Mapping) else ""] += 1

    dst_deg: Dict[str, int] = defaultdict(int)
    for edge in dst.edges:
        if len(edge.points) < 2:
            continue
        if len(dst.nodes) >= 2:
            # fallback to endpoint lookup by tolerance for recovered topology
            idx_start = _pop_match(edge.points[0], _build_point_index(dst.nodes, tol), tol, used=set(), strict=False)
            idx_end = _pop_match(edge.points[-1], _build_point_index(dst.nodes, tol), tol, used=set(), strict=False)
            if idx_start is not None:
                dst_deg[idx_start] += 1
            if idx_end is not None:
                dst_deg[idx_end] += 1

    comps_src = _components(src.nodes, src.edges, tol)
    comps_dst = _components(dst.nodes, dst.edges, tol)
    comp_drift = abs(comps_src - comps_dst) / max(1, comps_src, comps_dst)
    deg_drift = 0.0
    total = 0.0
    for sid, did in node_match.items():
        sa = src_deg.get(sid, 0)
        sb = dst_deg.get(did, 0)
        deg_drift += abs(sa - sb)
        total += max(sa, 1)
    deg_ratio = deg_drift / max(total, 1.0)

    ok = comp_drift <= 0.33 and deg_ratio <= 0.5
    reasons = []
    if not ok:
        reasons.append(
            f"component drift={comp_drift:.3f}, degree drift={deg_ratio:.3f}, "
            f"src_components={comps_src}, dst_components={comps_dst}"
        )
    return ok, {"src_components": comps_src, "dst_components": comps_dst, "component_drift": comp_drift, "degree_drift": deg_ratio}, reasons


def _extract_metrics(
    source_payload: Mapping[str, Any],
    mid_payload: Mapping[str, Any],
    recovered_payload: Mapping[str, Any],
    source_fmt: str,
    tol: float,
    len_drift: float,
) -> Dict[str, Any]:
    src_graph = _build_graph_from_payload(source_payload, source_fmt)
    rec_graph = _build_graph_from_payload(recovered_payload, source_fmt)
    node_recall, node_info, node_match = _node_recall(src_graph, rec_graph, tol)
    edge_recall, edge_info, edge_unmatched = _edge_recall(src_graph, rec_graph, tol, len_drift)

    geo_src = sum(_segment_length(e.points) for e in src_graph.edges)
    geo_rcv = sum(_segment_length(e.points) for e in rec_graph.edges)
    geo_delta = abs(geo_src - geo_rcv) / max(geo_src, 1e-6)

    topology_ok, topo_info, topology_reasons = _topology_ok(
        src_graph,
        rec_graph,
        {},
        tol,
        node_match=node_match,
    )

    reasons = [r for r in edge_unmatched[:10]] + topology_reasons
    return {
        "geo_delta": geo_delta,
        "node_recall": node_recall,
        "edge_recall": edge_recall,
        "topology_ok": topology_ok,
        "topology": topo_info,
        "node_info": node_info,
        "edge_info": edge_info,
        "reasons": reasons,
        "source_total_length": geo_src,
        "recovered_total_length": geo_rcv,
    }


def _run_bridge(command: str, input_path: Path, mode: str) -> tuple[int, str | None, Mapping[str, Any] | None, str | None]:
    cmd = [
        PYTHON_BIN,
        str(BRIDGE_SCRIPT),
        command,
        str(input_path),
        "--mode",
        mode,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return proc.returncode, proc.stderr.strip(), None, proc.stdout.strip() or None
    if not proc.stdout.strip():
        return proc.returncode, proc.stderr.strip(), None, None
    try:
        payload = json.loads(proc.stdout)
        return 0, None, payload, proc.stderr.strip() or None
    except Exception as exc:
        return 1, f"failed to parse bridge output: {exc}", None, proc.stdout.strip()


def _extract_wrapper_summary(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    summary = dict(payload.get("bridge_summary", {}))
    if "command" in payload and "mode" in payload:
        summary.setdefault("command", payload.get("command"))
        summary.setdefault("mode", payload.get("mode"))
    return summary


def _collect_losses(wrap_a: Mapping[str, Any] | None, wrap_b: Mapping[str, Any] | None) -> Dict[str, Any]:
    first_warnings = list(wrap_a.get("warnings", [])) if isinstance(wrap_a, Mapping) else []
    second_warnings = list(wrap_b.get("warnings", [])) if isinstance(wrap_b, Mapping) else []
    first_losses = list(_extract_wrapper_summary(wrap_a).get("losses", [])) if isinstance(wrap_a, Mapping) else []
    second_losses = list(_extract_wrapper_summary(wrap_b).get("losses", [])) if isinstance(wrap_b, Mapping) else []
    return {
        "warning_count": len(first_warnings) + len(second_warnings),
        "warning_detail": {
            "first_step": first_warnings,
            "second_step": second_warnings,
        },
        "losses": {
            "first_step": first_losses,
            "second_step": second_losses,
        },
        "loss_union": sorted(set(first_losses + second_losses)),
        "first_summary": _extract_wrapper_summary(wrap_a),
        "second_summary": _extract_wrapper_summary(wrap_b),
    }


def _run_roundtrip_for_file(
    path: Path,
    source_format: str,
    mode: str,
    tol: float,
    len_drift: float,
) -> Dict[str, Any]:
    raw = _read_json(path)
    payload = _unwrap_payload(raw if isinstance(raw, Mapping) else {})
    if not isinstance(payload, Mapping):
        return {"input": str(path), "status": "fail", "conversion_ok": False, "reasons": ["input is not a JSON object"]}

    resolved = _coerce_graph_format(payload, declared=source_format if source_format in KNOWN_FORMATS else None)

    source_to_target = "roadpen-to-roadgen3d" if resolved == "roadpen" else "roadgen3d-to-roadpen"
    target_to_source = "roadgen3d-to-roadpen" if resolved == "roadpen" else "roadpen-to-roadgen3d"
    first = None
    second = None

    with tempfile.TemporaryDirectory(prefix="roadbridge_roundtrip_") as tmpdir:
        tmp_dir = Path(tmpdir)
        rc, err, w1, raw_out = _run_bridge(source_to_target, path, mode)
        if rc != 0 or w1 is None:
            return {
                "input": str(path),
                "source_format": resolved,
                "mode": mode,
                "source_to_target": source_to_target,
                "target_to_source": target_to_source,
                "commands": [source_to_target],
                "conversion_ok": False,
                "status": "fail",
                "reasons": [err or "bridge failed", raw_out or ""],
                "wrapper_bridge_summary": {
                    "first_step": None,
                    "second_step": None,
                },
                "loss_digest": {
                    "warning_count": 0,
                    "warning_detail": {"first_step": [err] if err else [], "second_step": []},
                    "losses": {"first_step": [], "second_step": []},
                    "loss_union": [],
                    "first_summary": {},
                    "second_summary": {},
                },
            }
        first = w1
        mid_payload = first.get("payload")
        if not isinstance(mid_payload, Mapping):
            return {
                "input": str(path),
                "source_format": resolved,
                "mode": mode,
                "source_to_target": source_to_target,
                "target_to_source": target_to_source,
                "commands": [source_to_target],
                "conversion_ok": False,
                "status": "fail",
                "reasons": ["bridge first step output does not include payload"],
                "wrapper_bridge_summary": {"first_step": _extract_wrapper_summary(first), "second_step": None},
                "loss_digest": _collect_losses(first, None),
            }

        mid_file = tmp_dir / "middle.json"
        mid_file.write_text(json.dumps(mid_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        rc2, err2, w2, raw_out2 = _run_bridge(target_to_source, mid_file, mode)
        if rc2 != 0 or w2 is None:
            return {
                "input": str(path),
                "source_format": resolved,
                "mode": mode,
                "source_to_target": source_to_target,
                "target_to_source": target_to_source,
                "commands": [source_to_target, target_to_source],
                "conversion_ok": False,
                "status": "fail",
                "reasons": [err2 or "bridge failed", raw_out2 or ""],
                "wrapper_bridge_summary": {
                    "first_step": _extract_wrapper_summary(first),
                    "second_step": None,
                },
                "loss_digest": _collect_losses(first, None),
            }
        second = w2

    final_payload = second.get("payload")
    if not isinstance(final_payload, Mapping):
        return {
            "input": str(path),
            "source_format": resolved,
            "mode": mode,
            "source_to_target": source_to_target,
            "target_to_source": target_to_source,
            "commands": [source_to_target, target_to_source],
            "conversion_ok": False,
            "status": "fail",
            "reasons": ["bridge second step output does not include payload"],
            "wrapper_bridge_summary": {
                "first_step": _extract_wrapper_summary(first),
                "second_step": _extract_wrapper_summary(second),
            },
            "loss_digest": _collect_losses(first, second),
        }

    metrics = _extract_metrics(
        source_payload=payload,
        mid_payload=mid_payload,
        recovered_payload=final_payload,
        source_fmt=resolved,
        tol=tol,
        len_drift=len_drift,
    )

    reasons = list(metrics.pop("reasons"))
    return {
        "input": str(path),
        "source_format": resolved,
        "mode": mode,
        "source_to_target": source_to_target,
        "target_to_source": target_to_source,
        "commands": [source_to_target, target_to_source],
        "conversion_ok": True,
        "status": "pass",
        "metrics": metrics,
        "wrapper_bridge_summary": {
            "first_step": _extract_wrapper_summary(first),
            "second_step": _extract_wrapper_summary(second),
        },
        "loss_digest": _collect_losses(first, second),
        "reasons": reasons,
    }


def _status_threshold(
    item: MutableMapping[str, Any],
    node_recall_thresh: float,
    edge_recall_thresh: float,
    len_drift_thresh: float,
    fail_on_warn: bool,
) -> str:
    if not item.get("conversion_ok", False):
        return "fail"
    metrics = item.get("metrics", {})
    fail = False
    warning = False

    geo_delta = float(metrics.get("geo_delta", 1.0))
    node_recall = float(metrics.get("node_recall", 0.0))
    edge_recall = float(metrics.get("edge_recall", 0.0))
    topology_ok = bool(metrics.get("topology_ok", False))

    if geo_delta > len_drift_thresh:
        fail = True
        item.setdefault("reasons", []).append(f"geo_delta={geo_delta:.4f} > len_drift_thresh={len_drift_thresh}")
    if node_recall < node_recall_thresh:
        fail = True
        item.setdefault("reasons", []).append(f"node_recall={node_recall:.3f} < {node_recall_thresh}")
    if edge_recall < edge_recall_thresh:
        fail = True
        item.setdefault("reasons", []).append(f"edge_recall={edge_recall:.3f} < {edge_recall_thresh}")
    if not topology_ok:
        fail = True
        item.setdefault("reasons", []).append("topology_ok=false")

    warn_count = int(item.get("loss_digest", {}).get("warning_count", 0))
    if fail_on_warn and warn_count > 0:
        fail = True
        item.setdefault("reasons", []).append(f"warning_count={warn_count} and --fail-on-warn set")

    if not fail and warn_count > 0:
        warning = True
    return "fail" if fail else ("warning" if warning else "pass")


def _collect_files(input_path: Path, pattern: str) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(p for p in input_path.rglob(pattern) if p.is_file())


def _summary_stats(items: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    items = list(items)
    total = len(items)
    statuses = {"pass": 0, "warning": 0, "fail": 0}
    geo = []
    node = []
    edge = []
    failures: List[str] = []
    for item in items:
        st = item.get("status", "fail")
        statuses[st if st in statuses else "fail"] += 1
        if "metrics" in item:
            geo.append(float(item["metrics"].get("geo_delta", 0.0)))
            node.append(float(item["metrics"].get("node_recall", 0.0)))
            edge.append(float(item["metrics"].get("edge_recall", 0.0)))
        if st == "fail":
            failures.append(str(item.get("input")))
    summary = {
        "total": total,
        "pass": statuses["pass"],
        "warning": statuses["warning"],
        "fail": statuses["fail"],
        "pass_rate": (statuses["pass"] + statuses["warning"]) / total if total else 0.0,
        "geo_delta_mean": (sum(geo) / len(geo)) if geo else 0.0,
        "node_recall_mean": (sum(node) / len(node)) if node else 0.0,
        "edge_recall_mean": (sum(edge) / len(edge)) if edge else 0.0,
        "failures": failures,
    }
    return summary


def _print_human_summary(report: Mapping[str, Any]) -> None:
    summary = report.get("summary", {})
    lines = [
        "[roundtrip_check] samples=%s pass=%s warning=%s fail=%s pass_rate=%.2f"
        % (
            summary.get("total", 0),
            summary.get("pass", 0),
            summary.get("warning", 0),
            summary.get("fail", 0),
            summary.get("pass_rate", 0.0),
        ),
    ]
    lines.append(
        "metrics_mean: geo_delta=%.4f node_recall=%.3f edge_recall=%.3f"
        % (
            summary.get("geo_delta_mean", 0.0),
            summary.get("node_recall_mean", 0.0),
            summary.get("edge_recall_mean", 0.0),
        )
    )
    lines.append("failures:")
    for f in summary.get("failures", []):
        lines.append(f"  - {f}")
    print("\n".join(lines))
    print(f"details: {len(report.get('items', []))} items")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Round-trip checker for RoadPen / RoadGen3D bridge")
    parser.add_argument("--input", required=True, type=Path, help="Input file or directory")
    parser.add_argument(
        "--format",
        choices=("auto", "roadpen", "roadgen3d"),
        default="auto",
        help="roadpen|roadgen3d|auto (default: auto)",
    )
    parser.add_argument("--mode", choices=KNOWN_MODES, default="preview", help="bridge conversion mode")
    parser.add_argument("--out", type=Path, default=None, help="Write JSON report to file")
    parser.add_argument("--sample-limit", type=int, default=None, help="Max files when directory mode")
    parser.add_argument("--glob", default="*.json", help="Glob for directory mode")
    parser.add_argument("--tol", type=float, default=1.0, help="Geometry matching tolerance in px")
    parser.add_argument(
        "--len-drift",
        type=float,
        default=0.05,
        help="Geo length drift threshold for pass/fail",
    )
    parser.add_argument("--node-recall", type=float, default=0.80, help="Minimum node recall")
    parser.add_argument("--edge-recall", type=float, default=0.60, help="Minimum edge recall")
    parser.add_argument("--fail-on-warn", action="store_true", help="Fail when warnings exist")
    parser.add_argument("--reverse", action="store_true", help="Compatibility flag: inverse direction when format is auto")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if not args.input.exists():
        print(f"input not found: {args.input}", file=sys.stderr)
        return 2

    source_format = args.format
    source_format = "auto" if source_format not in KNOWN_FORMATS and source_format != "auto" else source_format
    files = _collect_files(args.input, args.glob)
    if not files:
        print(f"no files matched: {args.glob}", file=sys.stderr)
        return 1

    if args.sample_limit is not None:
        files = files[: _sample_limit_count(len(files), args.sample_limit)]

    items: List[Mapping[str, Any]] = []
    for path in files:
        result = _run_roundtrip_for_file(
            path=path,
            source_format=source_format,
            mode=args.mode,
            tol=float(args.tol),
            len_drift=float(args.len_drift),
        )
        status = _status_threshold(
            result,  # type: ignore[arg-type]
            node_recall_thresh=float(args.node_recall),
            edge_recall_thresh=float(args.edge_recall),
            len_drift_thresh=float(args.len_drift),
            fail_on_warn=args.fail_on_warn,
        )
        result["status"] = status
        if args.reverse and source_format == "auto":
            result["reverse"] = True
            result["notes"] = "reverse flag enabled for auto format only"
        items.append(result)

    report = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "mode": args.mode,
        "format": source_format,
        "params": {
            "tol": args.tol,
            "len_drift": args.len_drift,
            "node_recall": args.node_recall,
            "edge_recall": args.edge_recall,
            "fail_on_warn": args.fail_on_warn,
        },
        "summary": _summary_stats(items),
        "items": items,
    }

    if args.out is not None:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_human_summary(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
