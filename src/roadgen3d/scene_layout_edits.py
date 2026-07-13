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
            _rewrite_glb_transforms(
                source_layout_path=source_path,
                source_payload=source_payload,
                commands=normalized_commands,
                destination=stage_glb_path,
            )
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
            {
                "command_id": f"undo:{item['command_id']}",
                "op": "move_instance",
                "instance_id": item["instance_id"],
                "position_xyz": item["before_position_xyz"],
            }
            for item in reversed(applied)
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
        raise SceneLayoutEditError("commands must contain between 1 and 100 move commands.")
    command_ids: set[str] = set()
    targets: set[str] = set()
    normalized = []
    for index, command in enumerate(commands):
        if not isinstance(command, Mapping) or str(command.get("op", "")) != "move_instance":
            raise SceneLayoutEditError(f"commands[{index}] must be a move_instance command.")
        command_id = str(command.get("command_id", "") or "").strip()
        instance_id = str(command.get("instance_id", "") or "").strip()
        if not command_id or command_id in command_ids:
            raise SceneLayoutEditError(f"commands[{index}].command_id must be nonempty and unique.")
        if not instance_id or instance_id in targets:
            raise SceneLayoutEditError(f"commands[{index}].instance_id must be nonempty and unique in the batch.")
        raw_position = command.get("position_xyz")
        if not isinstance(raw_position, (list, tuple)) or len(raw_position) != 3:
            raise SceneLayoutEditError(f"commands[{index}].position_xyz must contain three numbers.")
        try:
            position = [float(item) for item in raw_position]
        except (TypeError, ValueError) as exc:
            raise SceneLayoutEditError(f"commands[{index}].position_xyz must be numeric.") from exc
        if not all(math.isfinite(item) and abs(item) <= 1_000_000 for item in position):
            raise SceneLayoutEditError(f"commands[{index}].position_xyz must be finite and within scene limits.")
        command_ids.add(command_id)
        targets.add(instance_id)
        normalized.append({
            "command_id": command_id,
            "op": "move_instance",
            "instance_id": instance_id,
            "position_xyz": position,
        })
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
        instance_id = str(command["instance_id"])
        placement = by_id.get(instance_id)
        if placement is None:
            raise SceneLayoutEditError(f"Unknown placement instance_id: {instance_id}")
        if bool(placement.get("editable") is False) or str(placement.get("selection_source", "")) == "osm_white_massing":
            raise SceneLayoutEditError(f"Placement '{instance_id}' is immutable context massing.")
        old = _position(placement.get("position_xyz"), f"placement '{instance_id}'")
        new = [float(item) for item in command["position_xyz"]]
        delta = [new[0] - old[0], new[1] - old[1], new[2] - old[2]]
        placement["position_xyz"] = new
        placement["bbox_xz"] = _translated_bbox(placement.get("bbox_xz"), delta[0], delta[2], instance_id)
        placement["placement_status"] = "edited_unvalidated"
        for building in building_rows:
            if isinstance(building, dict) and str(building.get("instance_id", "")) == instance_id:
                if "position_xyz" in building:
                    building["position_xyz"] = list(new)
                if "bbox_xz" in building:
                    building["bbox_xz"] = _translated_bbox(building.get("bbox_xz"), delta[0], delta[2], instance_id)
        applied.append({
            "command_id": str(command["command_id"]),
            "op": "move_instance",
            "instance_id": instance_id,
            "before_position_xyz": old,
            "position_xyz": new,
        })
        inverse.append({"instance_id": instance_id, "position_xyz": old})
    return payload, applied, inverse


def _rewrite_glb_transforms(
    *,
    source_layout_path: Path,
    source_payload: Mapping[str, Any],
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
    placements = {str(item.get("instance_id")): item for item in source_payload.get("placements", []) if isinstance(item, Mapping)}
    for command in commands:
        instance_id = str(command["instance_id"])
        old = _position(placements[instance_id].get("position_xyz"), f"placement '{instance_id}'")
        new = [float(item) for item in command["position_xyz"]]
        delta = np.array([new[0] - old[0], new[1] - old[1], new[2] - old[2]], dtype=float)
        matched = False
        for node_name in list(scene.graph.nodes):
            node_text = str(node_name)
            if node_text != instance_id and not node_text.startswith(instance_id + "_"):
                continue
            transform, geometry = scene.graph.get(node_name)
            updated = np.array(transform, dtype=float, copy=True)
            updated[:3, 3] += delta
            scene.graph.update(frame_to=node_name, matrix=updated, geometry=geometry)
            matched = True
        if not matched:
            raise SceneRebuildFailed(f"GLB contains no node for placement '{instance_id}'.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    exported = scene.export(file_type="glb")
    if not isinstance(exported, (bytes, bytearray)):
        raise SceneRebuildFailed("GLB export returned an unexpected payload.")
    exported_bytes = _restore_glb_instance_metadata(
        bytes(exported),
        source_glb.read_bytes(),
        commands=commands,
        placements=placements,
    )
    destination.write_bytes(exported_bytes)


def _restore_glb_instance_metadata(
    exported: bytes,
    source: bytes,
    *,
    commands: Sequence[Mapping[str, Any]],
    placements: Mapping[str, Mapping[str, Any]],
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
    for command in commands:
        instance_id = str(command["instance_id"])
        old = _position(placements[instance_id].get("position_xyz"), f"placement '{instance_id}'")
        new = [float(item) for item in command["position_xyz"]]
        dx, dz = new[0] - old[0], new[2] - old[2]
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
                placement = placements[instance_id]
                extras = {
                    "schema": "roadgen3d_instance_metadata_v1",
                    "instance_id": instance_id,
                    "category": str(placement.get("category", "")),
                    "asset_id": str(placement.get("asset_id", "")),
                    "position_xyz": list(old),
                    "bbox_xz": list(placement.get("bbox_xz") or []),
                }
            extras["position_xyz"] = list(new)
            for key in ("bbox_xz", "source_bbox"):
                value = extras.get(key)
                if isinstance(value, (list, tuple)) and len(value) == 4:
                    extras[key] = [
                        float(value[0]) + dx,
                        float(value[1]) + dx,
                        float(value[2]) + dz,
                        float(value[3]) + dz,
                    ]
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
