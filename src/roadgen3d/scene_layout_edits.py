"""Immutable, optimistic-concurrency scene-layout placement edits."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import struct
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from .json_safe import make_json_safe

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EDITABLE_ROOT = (ROOT / "artifacts").resolve()
DEFAULT_REVISION_ROOT = (DEFAULT_EDITABLE_ROOT / "scene_layout_edits").resolve()
_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class SceneLayoutEditError(RuntimeError):
    status_code = 422
    code = "invalid_scene_edit_request"

    def __init__(self, message: str, *, current: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.current = dict(current or {})

    def detail(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.current:
            payload["current"] = dict(self.current)
        return payload


class SceneLayoutPathForbidden(SceneLayoutEditError):
    status_code = 403
    code = "scene_layout_path_forbidden"


class SceneLayoutNotFound(SceneLayoutEditError):
    status_code = 404
    code = "scene_layout_not_found"


class SceneRevisionConflict(SceneLayoutEditError):
    status_code = 409
    code = "scene_revision_conflict"


class SceneRebuildFailed(SceneLayoutEditError):
    status_code = 500
    code = "scene_rebuild_failed"


@dataclass(frozen=True)
class SceneRevision:
    layout_path: str
    scene_glb_path: str
    lineage_id: str
    revision: int
    sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layout_path": self.layout_path,
            "scene_glb_path": self.scene_glb_path,
            "lineage_id": self.lineage_id,
            "revision": self.revision,
            "sha256": self.sha256,
        }


def apply_scene_layout_edits(
    *,
    layout_path: str | Path,
    base_revision: int,
    base_sha256: str,
    commands: Sequence[Mapping[str, Any]],
    editable_root: Path = DEFAULT_EDITABLE_ROOT,
    revision_root: Path = DEFAULT_REVISION_ROOT,
    transform_policy: str = "expert_grounded",
) -> Dict[str, Any]:
    source_path = _resolve_editable_layout(layout_path, editable_root)
    source_bytes = source_path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    try:
        source_payload = json.loads(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneLayoutEditError("scene_layout.json is not valid UTF-8 JSON.") from exc
    if not isinstance(source_payload, dict):
        raise SceneLayoutEditError("scene_layout.json must contain an object.")

    source_edit = source_payload.get("scene_edit") if isinstance(source_payload.get("scene_edit"), Mapping) else {}
    source_revision = int(source_edit.get("revision", 0) or 0)
    lineage_id = str(source_edit.get("lineage_id") or _lineage_id(source_path, source_sha))
    lock = _lineage_lock(lineage_id)
    with lock:
        current = _current_revision(source_path, source_payload, source_sha, lineage_id, revision_root)
        if int(base_revision) != int(current.revision) or str(base_sha256).lower() != current.sha256:
            raise SceneRevisionConflict(
                "The scene changed after it was loaded. Reload the current revision before editing.",
                current=current.to_dict(),
            )
        if source_revision != current.revision or source_sha != current.sha256:
            raise SceneRevisionConflict(
                "The requested layout is not the latest revision.",
                current=current.to_dict(),
            )

        normalized_commands = _normalize_commands(commands)
        normalized_commands, support_validation = _ground_and_validate_commands(
            source_path,
            source_payload,
            normalized_commands,
            transform_policy=transform_policy,
        )
        candidate, applied, inverse = _apply_commands(copy.deepcopy(source_payload), normalized_commands)
        next_revision = current.revision + 1
        final_dir = revision_root / lineage_id / f"rev-{next_revision:06d}"
        if final_dir.exists():
            raise SceneRevisionConflict("The next revision already exists.", current=current.to_dict())
        stage_dir = final_dir.parent / f".{final_dir.name}.tmp-{uuid.uuid4().hex}"
        stage_dir.mkdir(parents=True, exist_ok=False)
        final_layout_path = final_dir / "scene_layout.json"
        final_glb_path = final_dir / "scene.glb"
        stage_layout_path = stage_dir / "scene_layout.json"
        stage_glb_path = stage_dir / "scene.glb"
        try:
            outputs = dict(candidate.get("outputs") or {})
            outputs["scene_layout"] = str(final_layout_path)
            outputs["scene_glb"] = str(final_glb_path)
            candidate["outputs"] = outputs
            candidate["scene_edit"] = {
                "schema_version": "roadgen3d.scene_edit.v1",
                "lineage_id": lineage_id,
                "revision": next_revision,
                "parent_revision": current.revision,
                "parent_layout_sha256": current.sha256,
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "commands": normalized_commands,
                "transform_policy": transform_policy,
                "support_validation": support_validation,
            }
            summary = dict(candidate.get("summary") or {})
            summary.update({
                "scene_edit_validation_status": "pending_re_evaluation",
                "scene_edit_invalidated": [
                    "evaluation",
                    "rendered_views",
                    "placement_metrics",
                ],
                "scene_edit_revision": next_revision,
            })
            summary.pop("render_views", None)
            summary.pop("render_views_3d", None)
            candidate["summary"] = summary
            layout_bytes = _json_bytes(candidate)
            stage_layout_path.write_bytes(layout_bytes)
            if all(str(item.get("op")) in {"move_instance", "rotate_instance", "scale_instance"} for item in normalized_commands):
                _rewrite_glb_transforms(
                    source_layout_path=source_path,
                    source_payload=source_payload,
                    candidate_payload=candidate,
                    commands=normalized_commands,
                    destination=stage_glb_path,
                )
            else:
                _rebuild_glb_for_structural_edits(stage_layout_path, stage_glb_path)
            if not stage_glb_path.is_file() or stage_glb_path.stat().st_size <= 0:
                raise SceneRebuildFailed("Scene GLB transform update produced no output.")
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage_dir, final_dir)
        except SceneLayoutEditError:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise SceneRebuildFailed(f"Failed to materialize scene revision: {exc}") from exc

        published_bytes = final_layout_path.read_bytes()
        revision_sha = hashlib.sha256(published_bytes).hexdigest()
        revision = SceneRevision(
            layout_path=str(final_layout_path),
            scene_glb_path=str(final_glb_path),
            lineage_id=lineage_id,
            revision=next_revision,
            sha256=revision_sha,
        )
        undo_commands = [
            {"command_id": f"undo:{item.get('command_id', uuid.uuid4().hex)}", **dict(item["command"])}
            for item in reversed(inverse)
        ]
        return {
            "source": current.to_dict(),
            "revision": revision.to_dict(),
            "applied_commands": applied,
            "undo": {
                "base": {"revision": next_revision, "sha256": revision_sha},
                "commands": undo_commands,
            },
        }


def scene_revision_for_layout(layout_path: str | Path) -> Dict[str, Any]:
    path = Path(layout_path).expanduser().resolve()
    data = path.read_bytes()
    payload = json.loads(data.decode("utf-8"))
    edit = payload.get("scene_edit") if isinstance(payload.get("scene_edit"), Mapping) else {}
    digest = hashlib.sha256(data).hexdigest()
    return {
        "lineage_id": str(edit.get("lineage_id") or _lineage_id(path, digest)),
        "revision": int(edit.get("revision", 0) or 0),
        "sha256": digest,
    }


def _resolve_editable_layout(value: str | Path, editable_root: Path) -> Path:
    root = Path(editable_root).expanduser().resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SceneLayoutPathForbidden("Layout must stay inside the configured artifacts root.") from exc
    if path.name != "scene_layout.json":
        raise SceneLayoutPathForbidden("Only scene_layout.json files are editable.")
    if not path.exists() or not path.is_file():
        raise SceneLayoutNotFound("Editable scene_layout.json was not found.")
    if path.stat().st_size > 50 * 1024 * 1024:
        raise SceneLayoutEditError("scene_layout.json exceeds the 50 MiB edit limit.")
    return path


def _current_revision(
    source_path: Path,
    source_payload: Mapping[str, Any],
    source_sha: str,
    lineage_id: str,
    revision_root: Path,
) -> SceneRevision:
    edit = source_payload.get("scene_edit") if isinstance(source_payload.get("scene_edit"), Mapping) else {}
    source_revision = int(edit.get("revision", 0) or 0)
    latest_path = source_path
    latest_payload = source_payload
    latest_sha = source_sha
    latest_revision = source_revision
    lineage_dir = Path(revision_root).resolve() / lineage_id
    if lineage_dir.is_dir():
        for candidate in sorted(lineage_dir.glob("rev-*/scene_layout.json")):
            if not candidate.is_file():
                continue
            try:
                candidate_bytes = candidate.read_bytes()
                candidate_payload = json.loads(candidate_bytes.decode("utf-8"))
                candidate_edit = candidate_payload.get("scene_edit") or {}
                candidate_revision = int(candidate_edit.get("revision", 0) or 0)
                candidate_glb = Path(str((candidate_payload.get("outputs") or {}).get("scene_glb", "")))
            except Exception:
                continue
            if candidate_revision > latest_revision and candidate_glb.is_file():
                latest_revision = candidate_revision
                latest_path = candidate
                latest_payload = candidate_payload
                latest_sha = hashlib.sha256(candidate_bytes).hexdigest()
    glb_path = _layout_glb_path(latest_path, latest_payload)
    return SceneRevision(
        layout_path=str(latest_path),
        scene_glb_path=str(glb_path),
        lineage_id=lineage_id,
        revision=latest_revision,
        sha256=latest_sha,
    )


def _normalize_commands(commands: Sequence[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    if not isinstance(commands, Sequence) or isinstance(commands, (str, bytes)) or not (1 <= len(commands) <= 100):
        raise SceneLayoutEditError("commands must contain between 1 and 100 scene edit commands.")
    command_ids: set[str] = set()
    normalized = []
    supported = {
        "move_instance", "rotate_instance", "scale_instance", "delete_instance",
        "add_instance", "duplicate_instance", "replace_asset", "set_building_style",
        "auto_plant_trees",
    }
    for index, command in enumerate(commands):
        if not isinstance(command, Mapping):
            raise SceneLayoutEditError(f"commands[{index}] must be an object.")
        op = str(command.get("op", "") or "").strip()
        if op not in supported:
            raise SceneLayoutEditError(f"commands[{index}].op must be one of {sorted(supported)}.")
        command_id = str(command.get("command_id", "") or "").strip()
        if not command_id or command_id in command_ids:
            raise SceneLayoutEditError(f"commands[{index}].command_id must be nonempty and unique.")
        command_ids.add(command_id)
        item: Dict[str, Any] = {"command_id": command_id, "op": op}
        if op == "auto_plant_trees":
            points = command.get("points_xyz")
            if not isinstance(points, Sequence) or isinstance(points, (str, bytes)) or not (1 <= len(points) <= 100):
                raise SceneLayoutEditError(f"commands[{index}].points_xyz must contain 1..100 positions.")
            item.update({
                "points_xyz": [_finite_vector(point, 3, f"commands[{index}].points_xyz") for point in points],
                "asset_id": str(command.get("asset_id") or "tree_auto_candidate"),
                "category": "tree",
                "instance_prefix": str(command.get("instance_prefix") or f"auto-tree-{command_id}"),
            })
            if command.get("asset_ref") is not None:
                item["asset_ref"] = _normalize_asset_ref(command.get("asset_ref"), f"commands[{index}].asset_ref")
            normalized.append(item)
            continue
        instance_id = str(command.get("instance_id", "") or "").strip()
        if not instance_id:
            raise SceneLayoutEditError(f"commands[{index}].instance_id is required.")
        item["instance_id"] = instance_id
        if op in {"move_instance", "add_instance"}:
            item["position_xyz"] = _finite_vector(command.get("position_xyz"), 3, f"commands[{index}].position_xyz")
        if op == "move_instance":
            item["height_offset_m"] = _finite_number(command.get("height_offset_m", 0), f"commands[{index}].height_offset_m")
            if not 0.0 <= item["height_offset_m"] <= 10.0:
                raise SceneLayoutEditError(f"commands[{index}].height_offset_m must be within 0..10.")
        if op == "rotate_instance":
            item["yaw_deg"] = _finite_number(command.get("yaw_deg"), f"commands[{index}].yaw_deg") % 360.0
        if op == "scale_instance":
            scale = _finite_number(command.get("scale"), f"commands[{index}].scale")
            if not 0.25 <= scale <= 4.0:
                raise SceneLayoutEditError(f"commands[{index}].scale must be within 0.25..4.0.")
            item["scale"] = scale
        if op in {"add_instance", "replace_asset"}:
            item["asset_id"] = str(command.get("asset_id", "") or "").strip()
            if not item["asset_id"]:
                raise SceneLayoutEditError(f"commands[{index}].asset_id is required.")
            item["category"] = str(command.get("category") or "street_furniture")
            if command.get("asset_ref") is not None:
                item["asset_ref"] = _normalize_asset_ref(command.get("asset_ref"), f"commands[{index}].asset_ref")
                if item["asset_ref"]["assetId"] != item["asset_id"]:
                    raise SceneLayoutEditError(f"commands[{index}].asset_ref.assetId must match asset_id.")
                item["category"] = item["asset_ref"]["category"]
        if op == "add_instance":
            item["yaw_deg"] = _finite_number(command.get("yaw_deg", 0), f"commands[{index}].yaw_deg") % 360.0
            item["scale"] = _finite_number(command.get("scale", 1), f"commands[{index}].scale")
            if not 0.25 <= item["scale"] <= 4.0:
                raise SceneLayoutEditError(f"commands[{index}].scale must be within 0.25..4.0.")
            item["height_offset_m"] = _finite_number(command.get("height_offset_m", 0), f"commands[{index}].height_offset_m")
            if not 0.0 <= item["height_offset_m"] <= 10.0:
                raise SceneLayoutEditError(f"commands[{index}].height_offset_m must be within 0..10.")
        if op == "duplicate_instance":
            item["new_instance_id"] = str(command.get("new_instance_id") or "").strip()
            if not item["new_instance_id"]:
                raise SceneLayoutEditError(f"commands[{index}].new_instance_id is required.")
            if command.get("position_xyz") is not None:
                item["position_xyz"] = _finite_vector(command.get("position_xyz"), 3, f"commands[{index}].position_xyz")
                item["height_offset_m"] = _finite_number(command.get("height_offset_m", 0), f"commands[{index}].height_offset_m")
                if not 0.0 <= item["height_offset_m"] <= 10.0:
                    raise SceneLayoutEditError(f"commands[{index}].height_offset_m must be within 0..10.")
        if op == "set_building_style":
            item["style_id"] = str(command.get("style_id") or "").strip()
            if not item["style_id"]:
                raise SceneLayoutEditError(f"commands[{index}].style_id is required.")
        normalized.append(item)
    return normalized


def _normalize_asset_ref(value: Any, label: str) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        raise SceneLayoutEditError(f"{label} must be an object.")
    result = {
        "manifestName": str(value.get("manifestName") or value.get("manifest_name") or "").strip(),
        "assetId": str(value.get("assetId") or value.get("asset_id") or "").strip(),
        "fingerprint": str(value.get("fingerprint") or "").strip(),
        "category": str(value.get("category") or "").strip().lower(),
        "label": str(value.get("label") or value.get("assetId") or value.get("asset_id") or "Asset").strip(),
    }
    missing = [key for key in ("manifestName", "assetId", "fingerprint", "category") if not result[key]]
    if missing:
        raise SceneLayoutEditError(f"{label} is missing: {', '.join(missing)}.")
    try:
        from .services.asset_manifest_registry import (
            AssetManifestConflictError,
            AssetReferenceError,
            resolve_registered_asset,
        )

        resolved = resolve_registered_asset(
            result["manifestName"],
            result["assetId"],
            expected_fingerprint=result["fingerprint"],
            require_ready=True,
        )
    except (AssetManifestConflictError, AssetReferenceError, ValueError) as exc:
        raise SceneLayoutEditError(str(exc)) from exc
    public = resolved["public"]
    result.update({
        "fingerprint": str(public["fingerprint"]),
        "category": str(public["category"]),
        "label": str(public["label"]),
    })
    return result


def _ground_and_validate_commands(
    source_layout_path: Path,
    source_payload: Mapping[str, Any],
    commands: Sequence[Mapping[str, Any]],
    *,
    transform_policy: str,
) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    policy = str(transform_policy or "").strip().lower()
    if policy not in {"expert_grounded", "course_grounded"}:
        raise SceneLayoutEditError("transform_policy must be expert_grounded or course_grounded.")
    placements = {
        str(item.get("instance_id") or ""): item
        for item in (source_payload.get("placements") or [])
        if isinstance(item, Mapping)
    }
    positional: list[tuple[int, str, str, float, float]] = []
    normalized = [copy.deepcopy(dict(item)) for item in commands]
    for index, item in enumerate(normalized):
        op = str(item.get("op") or "")
        if op not in {"move_instance", "add_instance", "duplicate_instance", "auto_plant_trees"}:
            continue
        if op == "auto_plant_trees":
            for point_index, point in enumerate(item.get("points_xyz") or []):
                positional.append((index, f"{item['instance_prefix']}-{point_index + 1:03d}", "tree", float(point[0]), float(point[2])))
                point[1] = 0.0
            continue
        if op == "add_instance":
            category = str(item.get("category") or "street_furniture")
        else:
            source = placements.get(str(item.get("instance_id") or ""))
            category = str((source or {}).get("category") or "street_furniture")
        point = item.get("position_xyz")
        if not isinstance(point, list):
            continue
        height_offset = 0.0 if policy == "course_grounded" else float(item.get("height_offset_m", 0.0) or 0.0)
        point[1] = height_offset
        positional.append((index, str(item.get("instance_id") or ""), category, float(point[0]), float(point[2])))
    if not positional:
        return normalized, {"status": "not_required", "checked": []}

    node_roles = dict(((source_payload.get("surface_diagnostic") or {}).get("node_roles") or {}))
    if not node_roles:
        # Legacy layouts predate surface roles. Keep them editable, but make
        # the missing server-side support proof explicit in provenance.
        return normalized, {
            "status": "legacy_surface_roles_unavailable",
            "checked": [],
            "warning": "No surface_diagnostic.node_roles were available; positions were grounded to the legacy scene datum.",
        }
    glb_path = _layout_glb_path(source_layout_path, source_payload)
    hits = _surface_roles_at_points(glb_path, node_roles, [(item[3], item[4]) for item in positional])
    checked: list[Dict[str, Any]] = []
    for (_, instance_id, category, x, z), role in zip(positional, hits):
        allowed = {"planting", "furnishing", "frontage"} if category.strip().lower() == "tree" else {"sidewalk", "furnishing", "frontage"}
        if role is None:
            raise SceneLayoutEditError(
                f"Placement '{instance_id}' has no valid support surface at ({x:.3f}, {z:.3f})."
            )
        if role not in allowed:
            raise SceneLayoutEditError(
                f"Placement '{instance_id}' cannot use support surface '{role}' for category '{category}'."
            )
        checked.append({"instance_id": instance_id, "category": category, "position_xz": [x, z], "support_role": role})
    return normalized, {"status": "validated", "checked": checked}


def _surface_roles_at_points(
    glb_path: Path,
    node_roles: Mapping[str, Any],
    points_xz: Sequence[tuple[float, float]],
) -> list[str | None]:
    try:
        import numpy as np
        import trimesh
    except ImportError as exc:
        raise SceneRebuildFailed("trimesh and numpy are required for support-surface validation.") from exc
    if not glb_path.is_file():
        raise SceneRebuildFailed("Support-surface validation requires the source GLB.")
    scene = trimesh.load(glb_path, force="scene", process=False)
    candidates: list[tuple[str, Any]] = []
    for node_name in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph.get(node_name)
        mesh = scene.geometry.get(geometry_name)
        if mesh is None or not hasattr(mesh, "triangles"):
            continue
        role = _normalized_surface_role(
            node_roles.get(str(node_name), node_roles.get(str(geometry_name), "")),
            str(node_name),
        )
        if role not in {"carriageway", "curb", "sidewalk", "furnishing", "frontage", "planting", "crossing", "context_ground", "building"}:
            continue
        vertices = trimesh.transform_points(np.asarray(mesh.vertices, dtype=float), np.asarray(transform, dtype=float))
        faces = np.asarray(mesh.faces, dtype=int)
        if faces.size == 0:
            continue
        candidates.append((role, vertices[faces]))
    result: list[str | None] = []
    for x, z in points_xz:
        best: tuple[float, str] | None = None
        point = np.asarray([float(x), float(z)], dtype=float)
        for role, triangles in candidates:
            projected = triangles[:, :, (0, 2)]
            v0 = projected[:, 1] - projected[:, 0]
            v1 = projected[:, 2] - projected[:, 0]
            v2 = point - projected[:, 0]
            denominator = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
            usable = np.abs(denominator) > 1e-10
            if not np.any(usable):
                continue
            a = np.zeros(len(triangles), dtype=float)
            b = np.zeros(len(triangles), dtype=float)
            a[usable] = (v2[usable, 0] * v1[usable, 1] - v1[usable, 0] * v2[usable, 1]) / denominator[usable]
            b[usable] = (v0[usable, 0] * v2[usable, 1] - v2[usable, 0] * v0[usable, 1]) / denominator[usable]
            inside = usable & (a >= -1e-7) & (b >= -1e-7) & (a + b <= 1.0 + 1e-7)
            for triangle_index in np.flatnonzero(inside):
                y = float(
                    triangles[triangle_index, 0, 1]
                    + a[triangle_index] * (triangles[triangle_index, 1, 1] - triangles[triangle_index, 0, 1])
                    + b[triangle_index] * (triangles[triangle_index, 2, 1] - triangles[triangle_index, 0, 1])
                )
                if best is None or y > best[0]:
                    best = (y, role)
        result.append(best[1] if best else None)
    return result


def _normalized_surface_role(value: Any, node_name: str) -> str:
    role = str(value or "").strip().lower()
    if role == "plaza" or role == "building_buffer":
        return "frontage"
    if role == "tree_pit" or role in {"planting", "planting_area"}:
        return "planting"
    if role:
        return role
    name = str(node_name).strip().lower()
    for prefix, fallback in (
        ("sidewalk", "sidewalk"), ("furnishing", "furnishing"),
        ("frontage", "frontage"), ("tree_pit", "planting"),
        ("planting", "planting"), ("curb", "curb"),
        ("carriageway", "carriageway"), ("crossing", "crossing"),
        ("context_ground", "context_ground"), ("building", "building"),
    ):
        if name.startswith(prefix):
            return fallback
    return ""


def _apply_commands(payload: Dict[str, Any], commands: Sequence[Mapping[str, Any]]) -> Tuple[Dict[str, Any], list[Dict[str, Any]], list[Dict[str, Any]]]:
    placements = payload.get("placements")
    if not isinstance(placements, list):
        raise SceneLayoutEditError("scene_layout.json placements must be an array.")
    by_id: Dict[str, Dict[str, Any]] = {}
    for index, placement in enumerate(placements):
        if not isinstance(placement, dict):
            raise SceneLayoutEditError(f"placements[{index}] must be an object.")
        instance_id = str(placement.get("instance_id", "") or "").strip()
        if not instance_id or instance_id in by_id:
            raise SceneLayoutEditError("All editable placements require unique nonempty instance_id values.")
        by_id[instance_id] = placement
    building_rows = payload.get("building_placements")
    building_rows = building_rows if isinstance(building_rows, list) else []
    applied: list[Dict[str, Any]] = []
    inverse: list[Dict[str, Any]] = []
    for command in commands:
        op = str(command["op"])
        if op == "auto_plant_trees":
            for point_index, point in enumerate(command["points_xyz"]):
                instance_id = f"{command['instance_prefix']}-{point_index + 1:03d}"
                if instance_id in by_id:
                    raise SceneLayoutEditError(f"Duplicate generated tree instance_id: {instance_id}")
                placement = _new_placement(
                    instance_id, str(command["asset_id"]), "tree", point, 0.0, 1.0,
                    asset_ref=command.get("asset_ref"),
                )
                placements.append(placement)
                by_id[instance_id] = placement
                applied.append({"command_id": str(command["command_id"]), "op": "add_instance", "instance_id": instance_id, "position_xyz": list(point)})
                inverse.append({"command_id": str(command["command_id"]), "command": {"op": "delete_instance", "instance_id": instance_id}})
            continue
        instance_id = str(command["instance_id"])
        if op == "add_instance":
            if instance_id in by_id:
                raise SceneLayoutEditError(f"Duplicate placement instance_id: {instance_id}")
            placement = _new_placement(
                instance_id,
                str(command["asset_id"]),
                str(command["category"]),
                command["position_xyz"],
                float(command["yaw_deg"]),
                float(command["scale"]),
                asset_ref=command.get("asset_ref"),
            )
            placements.append(placement)
            by_id[instance_id] = placement
            applied.append({"command_id": command["command_id"], "op": op, "instance_id": instance_id})
            inverse.append({"command_id": command["command_id"], "command": {"op": "delete_instance", "instance_id": instance_id}})
            continue
        placement = by_id.get(instance_id)
        if placement is None:
            raise SceneLayoutEditError(f"Unknown placement instance_id: {instance_id}")
        _require_editable(placement, instance_id)
        if op == "move_instance":
            old = _position(placement.get("position_xyz"), f"placement '{instance_id}'")
            new = list(command["position_xyz"])
            delta = [new[0] - old[0], new[1] - old[1], new[2] - old[2]]
            placement["position_xyz"] = new
            placement["bbox_xz"] = _translated_bbox(placement.get("bbox_xz"), delta[0], delta[2], instance_id)
            _sync_building_rows(building_rows, instance_id, position=new, delta=delta)
            before = {"position_xyz": old}
            undo = {"op": op, "instance_id": instance_id, "position_xyz": old}
        elif op == "rotate_instance":
            old_yaw = float(placement.get("yaw_deg", 0.0) or 0.0)
            placement["yaw_deg"] = float(command["yaw_deg"])
            before = {"yaw_deg": old_yaw}
            undo = {"op": op, "instance_id": instance_id, "yaw_deg": old_yaw}
        elif op == "scale_instance":
            old_scale = float(placement.get("scale", 1.0) or 1.0)
            placement["scale"] = float(command["scale"])
            before = {"scale": old_scale}
            undo = {"op": op, "instance_id": instance_id, "scale": old_scale}
        elif op == "delete_instance":
            snapshot = copy.deepcopy(placement)
            placements.remove(placement)
            del by_id[instance_id]
            before = {"placement": snapshot}
            undo = _placement_to_add(snapshot)
        elif op == "duplicate_instance":
            new_id = str(command["new_instance_id"])
            if new_id in by_id:
                raise SceneLayoutEditError(f"Duplicate placement instance_id: {new_id}")
            clone = copy.deepcopy(placement)
            clone["instance_id"] = new_id
            if "position_xyz" in command:
                old = _position(placement.get("position_xyz"), f"placement '{instance_id}'")
                new = list(command["position_xyz"])
                clone["position_xyz"] = new
                clone["bbox_xz"] = _translated_bbox(clone.get("bbox_xz"), new[0] - old[0], new[2] - old[2], new_id)
            placements.append(clone)
            by_id[new_id] = clone
            before = {"source_instance_id": instance_id}
            undo = {"op": "delete_instance", "instance_id": new_id}
        elif op == "replace_asset":
            old_asset, old_category = str(placement.get("asset_id", "")), str(placement.get("category", ""))
            old_asset_ref = copy.deepcopy(placement.get("asset_ref")) if isinstance(placement.get("asset_ref"), Mapping) else None
            placement["asset_id"] = str(command["asset_id"])
            placement["category"] = str(command["category"])
            if isinstance(command.get("asset_ref"), Mapping):
                placement["asset_ref"] = copy.deepcopy(dict(command["asset_ref"]))
            else:
                placement.pop("asset_ref", None)
            before = {"asset_id": old_asset, "category": old_category, "asset_ref": old_asset_ref}
            undo = {"op": op, "instance_id": instance_id, "asset_id": old_asset, "category": old_category}
            if old_asset_ref:
                undo["asset_ref"] = old_asset_ref
        elif op == "set_building_style":
            old_style = str(placement.get("style_id") or placement.get("theme_id") or "")
            placement["style_id"] = str(command["style_id"])
            placement["theme_id"] = str(command["style_id"])
            for building in building_rows:
                if isinstance(building, dict) and str(building.get("instance_id")) == instance_id:
                    building["style_id"] = str(command["style_id"])
                    building["theme_id"] = str(command["style_id"])
            before = {"style_id": old_style}
            undo = {"op": op, "instance_id": instance_id, "style_id": old_style or "default"}
        else:
            raise SceneLayoutEditError(f"Unsupported normalized command: {op}")
        if op not in {"delete_instance", "duplicate_instance"}:
            placement["placement_status"] = "edited_unvalidated"
        applied.append({"command_id": str(command["command_id"]), "op": op, "instance_id": instance_id, "before": before})
        inverse.append({"command_id": str(command["command_id"]), "command": undo})
    return payload, applied, inverse


def _finite_number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SceneLayoutEditError(f"{label} must be numeric.") from exc
    if not math.isfinite(result) or abs(result) > 1_000_000:
        raise SceneLayoutEditError(f"{label} must be finite and within scene limits.")
    return result


def _finite_vector(value: Any, length: int, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise SceneLayoutEditError(f"{label} must contain {length} numbers.")
    return [_finite_number(item, label) for item in value]


def _require_editable(placement: Mapping[str, Any], instance_id: str) -> None:
    if bool(placement.get("editable") is False) or str(placement.get("selection_source", "")) == "osm_white_massing":
        raise SceneLayoutEditError(f"Placement '{instance_id}' is immutable context massing.")


def _new_placement(
    instance_id: str,
    asset_id: str,
    category: str,
    position: Sequence[float],
    yaw_deg: float,
    scale: float,
    *,
    asset_ref: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    x, y, z = [float(item) for item in position]
    half = max(0.25, 0.5 * float(scale))
    placement = {"instance_id": instance_id, "asset_id": asset_id, "category": category, "position_xyz": [x, y, z], "yaw_deg": yaw_deg, "scale": scale, "bbox_xz": [x - half, x + half, z - half, z + half], "selection_source": "manual_scene_edit", "placement_group": "street_furniture", "placement_status": "edited_unvalidated", "editable": True}
    if isinstance(asset_ref, Mapping):
        placement["asset_ref"] = copy.deepcopy(dict(asset_ref))
    return placement


def _placement_to_add(placement: Mapping[str, Any]) -> Dict[str, Any]:
    command = {"op": "add_instance", "instance_id": str(placement.get("instance_id")), "asset_id": str(placement.get("asset_id") or "restored_asset"), "category": str(placement.get("category") or "street_furniture"), "position_xyz": list(placement.get("position_xyz") or [0, 0, 0]), "yaw_deg": float(placement.get("yaw_deg", 0) or 0), "scale": float(placement.get("scale", 1) or 1)}
    if isinstance(placement.get("asset_ref"), Mapping):
        command["asset_ref"] = copy.deepcopy(dict(placement["asset_ref"]))
    return command


def _sync_building_rows(building_rows: Sequence[Any], instance_id: str, *, position: Sequence[float], delta: Sequence[float]) -> None:
    for building in building_rows:
        if isinstance(building, dict) and str(building.get("instance_id", "")) == instance_id:
            if "position_xyz" in building:
                building["position_xyz"] = list(position)
            if "bbox_xz" in building:
                building["bbox_xz"] = _translated_bbox(building.get("bbox_xz"), float(delta[0]), float(delta[2]), instance_id)


def _rewrite_glb_transforms(
    *,
    source_layout_path: Path,
    source_payload: Mapping[str, Any],
    candidate_payload: Mapping[str, Any],
    commands: Sequence[Mapping[str, Any]],
    destination: Path,
) -> None:
    try:
        import numpy as np
        import trimesh
    except ImportError as exc:
        raise SceneRebuildFailed("trimesh and numpy are required for persistent scene edits.") from exc
    source_glb = _layout_glb_path(source_layout_path, source_payload)
    if not source_glb.is_file():
        raise SceneRebuildFailed("The source layout does not reference an existing GLB.")
    scene = trimesh.load(source_glb, force="scene", process=False)
    placements = {str(item.get("instance_id")): dict(item) for item in source_payload.get("placements", []) if isinstance(item, Mapping)}
    for command in commands:
        instance_id = str(command["instance_id"])
        placement = placements[instance_id]
        op = str(command["op"])
        matched = False
        for node_name in list(scene.graph.nodes):
            node_text = str(node_name)
            if node_text != instance_id and not node_text.startswith(instance_id + "_"):
                continue
            transform, geometry = scene.graph.get(node_name)
            updated = np.array(transform, dtype=float, copy=True)
            if op == "move_instance":
                old = _position(placement.get("position_xyz"), f"placement '{instance_id}'")
                new = [float(item) for item in command["position_xyz"]]
                updated[:3, 3] += np.array([new[0] - old[0], new[1] - old[1], new[2] - old[2]], dtype=float)
            elif op == "rotate_instance":
                old_yaw = float(placement.get("yaw_deg", 0) or 0)
                delta_rad = math.radians(float(command["yaw_deg"]) - old_yaw)
                rotation = np.array([[math.cos(delta_rad), 0, math.sin(delta_rad)], [0, 1, 0], [-math.sin(delta_rad), 0, math.cos(delta_rad)]], dtype=float)
                updated[:3, :3] = updated[:3, :3] @ rotation
                anchor = np.array(_position(placement.get("position_xyz"), f"placement '{instance_id}'"), dtype=float)
                updated[:3, 3] = anchor + rotation @ (updated[:3, 3] - anchor)
            elif op == "scale_instance":
                old_scale = float(placement.get("scale", 1) or 1)
                factor = float(command["scale"]) / old_scale
                anchor = np.array(_position(placement.get("position_xyz"), f"placement '{instance_id}'"), dtype=float)
                updated[:3, :3] *= factor
                updated[:3, 3] = anchor + (updated[:3, 3] - anchor) * factor
            scene.graph.update(frame_to=node_name, matrix=updated, geometry=geometry)
            matched = True
        if not matched:
            raise SceneRebuildFailed(f"GLB contains no node for placement '{instance_id}'.")
        if op == "move_instance":
            placement["position_xyz"] = list(command["position_xyz"])
        elif op == "rotate_instance":
            placement["yaw_deg"] = float(command["yaw_deg"])
        elif op == "scale_instance":
            placement["scale"] = float(command["scale"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    exported = scene.export(file_type="glb")
    if not isinstance(exported, (bytes, bytearray)):
        raise SceneRebuildFailed("GLB export returned an unexpected payload.")
    exported_bytes = _restore_glb_instance_metadata(
        bytes(exported),
        source_glb.read_bytes(),
        commands=commands,
        candidate_payload=candidate_payload,
    )
    destination.write_bytes(exported_bytes)


def _restore_glb_instance_metadata(
    exported: bytes,
    source: bytes,
    *,
    commands: Sequence[Mapping[str, Any]],
    candidate_payload: Mapping[str, Any],
) -> bytes:
    exported_doc, exported_tail, version = _decode_glb_json(exported)
    source_doc, _, _ = _decode_glb_json(source)
    source_nodes = {
        str(node.get("name", "")): node
        for node in source_doc.get("nodes", [])
        if isinstance(node, Mapping)
    }
    exported_nodes = exported_doc.get("nodes")
    if not isinstance(exported_nodes, list):
        raise SceneRebuildFailed("Exported GLB has no node table.")
    final_placements = {str(item.get("instance_id")): item for item in candidate_payload.get("placements", []) if isinstance(item, Mapping)}
    for instance_id in {str(command["instance_id"]) for command in commands}:
        placement = final_placements.get(instance_id)
        if placement is None:
            raise SceneRebuildFailed(f"Edited placement disappeared before GLB metadata update: {instance_id}")
        patched = 0
        for node in exported_nodes:
            if not isinstance(node, dict):
                continue
            name = str(node.get("name", ""))
            if name != instance_id and not name.startswith(instance_id + "_"):
                continue
            source_node = source_nodes.get(name)
            source_extras = source_node.get("extras") if isinstance(source_node, Mapping) else None
            if isinstance(source_extras, Mapping):
                extras = copy.deepcopy(dict(source_extras))
            else:
                extras = {
                    "schema": "roadgen3d_instance_metadata_v1",
                    "instance_id": instance_id,
                    "category": str(placement.get("category", "")),
                    "asset_id": str(placement.get("asset_id", "")),
                    "position_xyz": list(placement.get("position_xyz") or [0, 0, 0]),
                    "bbox_xz": list(placement.get("bbox_xz") or []),
                }
            extras.update({
                "position_xyz": list(placement.get("position_xyz") or [0, 0, 0]),
                "bbox_xz": list(placement.get("bbox_xz") or []),
                "yaw_deg": float(placement.get("yaw_deg", 0) or 0),
                "scale": float(placement.get("scale", 1) or 1),
                "asset_id": str(placement.get("asset_id", "")),
                "category": str(placement.get("category", "")),
            })
            node["extras"] = extras
            patched += 1
        if patched == 0:
            raise SceneRebuildFailed(f"Exported GLB contains no node metadata for '{instance_id}'.")
    json_payload = json.dumps(
        exported_doc,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    padded_json = json_payload + (b" " * ((-len(json_payload)) % 4))
    total_length = 12 + 8 + len(padded_json) + len(exported_tail)
    return (
        struct.pack("<4sII", b"glTF", version, total_length)
        + struct.pack("<II", len(padded_json), 0x4E4F534A)
        + padded_json
        + exported_tail
    )


def _rebuild_glb_for_structural_edits(layout_path: Path, destination: Path) -> None:
    from .street_layout import rebuild_glb_from_layout
    from .services.asset_manifest_registry import build_scene_edit_manifest

    try:
        payload = json.loads(layout_path.read_text(encoding="utf-8"))
        manifest_result = build_scene_edit_manifest(
            [
                *[item for item in (payload.get("placements") or []) if isinstance(item, Mapping)],
                *[item for item in (payload.get("environment_placements") or []) if isinstance(item, Mapping)],
            ],
            destination=layout_path.parent / "rebuild" / "scene_edit_assets.jsonl",
        )
        manifest_path = Path(str(manifest_result["manifest_path"]))
        scene_edit = dict(payload.get("scene_edit") or {})
        scene_edit["asset_provenance"] = list(manifest_result.get("assets") or [])
        payload["scene_edit"] = scene_edit
        layout_path.write_bytes(_json_bytes(payload))
        result = rebuild_glb_from_layout(layout_path=layout_path, manifest_path=manifest_path, out_dir=layout_path.parent / "rebuild")
        rebuilt = Path(str(result.get("scene_glb", ""))).resolve()
        if not rebuilt.is_file():
            raise RuntimeError("rebuild_glb_from_layout returned no scene_glb")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(rebuilt, destination)
    except SceneLayoutEditError:
        raise
    except Exception as exc:
        raise SceneRebuildFailed(f"Failed to rebuild structural scene edits: {exc}") from exc


def _decode_glb_json(data: bytes) -> tuple[Dict[str, Any], bytes, int]:
    if len(data) < 20:
        raise SceneRebuildFailed("GLB is truncated.")
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    json_length, json_type = struct.unpack_from("<II", data, 12)
    if magic != b"glTF" or version != 2 or declared_length != len(data) or json_type != 0x4E4F534A:
        raise SceneRebuildFailed("GLB header or JSON chunk is invalid.")
    json_end = 20 + json_length
    if json_end > len(data):
        raise SceneRebuildFailed("GLB JSON chunk is truncated.")
    try:
        document = json.loads(data[20:json_end].decode("utf-8").rstrip("\x00 "))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneRebuildFailed("GLB JSON chunk is invalid.") from exc
    if not isinstance(document, dict):
        raise SceneRebuildFailed("GLB JSON document must be an object.")
    return document, data[json_end:], version


def _translated_bbox(value: Any, dx: float, dz: float, instance_id: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise SceneLayoutEditError(f"Placement '{instance_id}' has no valid bbox_xz; move rejected.")
    try:
        xmin, xmax, zmin, zmax = (float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise SceneLayoutEditError(f"Placement '{instance_id}' has invalid bbox_xz.") from exc
    if not all(math.isfinite(item) for item in (xmin, xmax, zmin, zmax)) or xmin > xmax or zmin > zmax:
        raise SceneLayoutEditError(f"Placement '{instance_id}' has invalid bbox_xz.")
    return [xmin + dx, xmax + dx, zmin + dz, zmax + dz]


def _position(value: Any, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise SceneLayoutEditError(f"{label} has invalid position_xyz.")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise SceneLayoutEditError(f"{label} has non-finite position_xyz.")
    return result


def _layout_glb_path(layout_path: Path, payload: Mapping[str, Any]) -> Path:
    raw = str((payload.get("outputs") or {}).get("scene_glb", "") or "").strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = layout_path.parent / path
    return path.resolve()


def _lineage_id(path: Path, digest: str) -> str:
    material = f"{path.resolve()}\0{digest}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:24]


def _lineage_lock(lineage_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(lineage_id, threading.Lock())


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(make_json_safe(payload), ensure_ascii=True, indent=2) + "\n").encode("utf-8")
