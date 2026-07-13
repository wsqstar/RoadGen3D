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
            normalized.append(item)
            continue
        instance_id = str(command.get("instance_id", "") or "").strip()
        if not instance_id:
            raise SceneLayoutEditError(f"commands[{index}].instance_id is required.")
        item["instance_id"] = instance_id
        if op in {"move_instance", "add_instance"}:
            item["position_xyz"] = _finite_vector(command.get("position_xyz"), 3, f"commands[{index}].position_xyz")
        if op == "rotate_instance":
            item["yaw_deg"] = _finite_number(command.get("yaw_deg"), f"commands[{index}].yaw_deg") % 360.0
        if op == "scale_instance":
            scale = _finite_number(command.get("scale"), f"commands[{index}].scale")
            if not 0.01 <= scale <= 100:
                raise SceneLayoutEditError(f"commands[{index}].scale must be within 0.01..100.")
            item["scale"] = scale
        if op in {"add_instance", "replace_asset"}:
            item["asset_id"] = str(command.get("asset_id", "") or "").strip()
            if not item["asset_id"]:
                raise SceneLayoutEditError(f"commands[{index}].asset_id is required.")
            item["category"] = str(command.get("category") or "street_furniture")
        if op == "add_instance":
            item["yaw_deg"] = _finite_number(command.get("yaw_deg", 0), f"commands[{index}].yaw_deg") % 360.0
            item["scale"] = _finite_number(command.get("scale", 1), f"commands[{index}].scale")
            if not 0.01 <= item["scale"] <= 100:
                raise SceneLayoutEditError(f"commands[{index}].scale must be within 0.01..100.")
        if op == "duplicate_instance":
            item["new_instance_id"] = str(command.get("new_instance_id") or "").strip()
            if not item["new_instance_id"]:
                raise SceneLayoutEditError(f"commands[{index}].new_instance_id is required.")
            if command.get("position_xyz") is not None:
                item["position_xyz"] = _finite_vector(command.get("position_xyz"), 3, f"commands[{index}].position_xyz")
        if op == "set_building_style":
            item["style_id"] = str(command.get("style_id") or "").strip()
            if not item["style_id"]:
                raise SceneLayoutEditError(f"commands[{index}].style_id is required.")
        normalized.append(item)
    return normalized


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
                placement = _new_placement(instance_id, str(command["asset_id"]), "tree", point, 0.0, 1.0)
                placements.append(placement)
                by_id[instance_id] = placement
                applied.append({"command_id": str(command["command_id"]), "op": "add_instance", "instance_id": instance_id, "position_xyz": list(point)})
                inverse.append({"command_id": str(command["command_id"]), "command": {"op": "delete_instance", "instance_id": instance_id}})
            continue
        instance_id = str(command["instance_id"])
        if op == "add_instance":
            if instance_id in by_id:
                raise SceneLayoutEditError(f"Duplicate placement instance_id: {instance_id}")
            placement = _new_placement(instance_id, str(command["asset_id"]), str(command["category"]), command["position_xyz"], float(command["yaw_deg"]), float(command["scale"]))
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
            placement["asset_id"] = str(command["asset_id"])
            placement["category"] = str(command["category"])
            before = {"asset_id": old_asset, "category": old_category}
            undo = {"op": op, "instance_id": instance_id, "asset_id": old_asset, "category": old_category}
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


def _new_placement(instance_id: str, asset_id: str, category: str, position: Sequence[float], yaw_deg: float, scale: float) -> Dict[str, Any]:
    x, y, z = [float(item) for item in position]
    half = max(0.25, 0.5 * float(scale))
    return {"instance_id": instance_id, "asset_id": asset_id, "category": category, "position_xyz": [x, y, z], "yaw_deg": yaw_deg, "scale": scale, "bbox_xz": [x - half, x + half, z - half, z + half], "selection_source": "manual_scene_edit", "placement_group": "street_furniture", "placement_status": "edited_unvalidated", "editable": True}


def _placement_to_add(placement: Mapping[str, Any]) -> Dict[str, Any]:
    return {"op": "add_instance", "instance_id": str(placement.get("instance_id")), "asset_id": str(placement.get("asset_id") or "restored_asset"), "category": str(placement.get("category") or "street_furniture"), "position_xyz": list(placement.get("position_xyz") or [0, 0, 0]), "yaw_deg": float(placement.get("yaw_deg", 0) or 0), "scale": float(placement.get("scale", 1) or 1)}


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
            elif op == "scale_instance":
                old_scale = float(placement.get("scale", 1) or 1)
                updated[:3, :3] *= float(command["scale"]) / old_scale
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

    manifest_path = ROOT / "data" / "street_furniture" / "street_furniture_manifest.jsonl"
    if not manifest_path.is_file():
        raise SceneRebuildFailed("Structural scene edits require the street-furniture manifest.")
    try:
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
