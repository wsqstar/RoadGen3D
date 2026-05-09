#!/usr/bin/env python3
"""Split a grouped traffic-sign GLB into separate GLB assets.

This script is intentionally conservative:

* it does not modify the source GLB
* it does not update any RoadGen3D manifest
* it can be started with normal Python and will re-run itself in Blender
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ROOT
    / "data"
    / "street_furniture"
    / "assets_std_glb_flat"
    / "bucket_0002"
    / "std_02c4941681264875a84b0f0b0c5c0ee1.glb"
)
DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "split_glb_signs" / "02c4941681264875a84b0f0b0c5c0ee1"
DEFAULT_MAC_BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")


def _argv_after_blender_separator(argv: Sequence[str]) -> list[str]:
    if "--" in argv:
        return list(argv[argv.index("--") + 1 :])
    return list(argv[1:])


def _resolve_path(path: Path) -> Path:
    path = path.expanduser()
    if path.is_absolute():
        return path.resolve()
    return (ROOT / path).resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split a grouped traffic-sign GLB into clustered loose-part GLBs.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Input GLB path. Defaults to the 02c494 traffic-sign group asset.",
    )
    parser.add_argument("--method", choices=("auto", "primitive", "projection", "loose-3d"), default="projection")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for sign_001.glb, sign_002.glb, ... outputs.",
    )
    parser.add_argument(
        "--cluster-distance",
        type=float,
        default=0.05,
        help="World-space AABB gap threshold for --method loose-3d.",
    )
    parser.add_argument(
        "--projection-margin",
        type=float,
        default=0.03,
        help="2D projection bbox expansion margin for --method projection.",
    )
    parser.add_argument(
        "--min-diagonal",
        type=float,
        default=0.0,
        help="Drop loose parts with a bounding-box diagonal smaller than this value.",
    )
    parser.add_argument(
        "--preserve-offset",
        action="store_true",
        help="Keep each cluster at its original world-space offset instead of recentering it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Import, separate, and cluster, but do not export GLB files.",
    )
    parser.add_argument(
        "--write-preview",
        action="store_true",
        help="Write clusters_projection.json and projection SVG previews.",
    )
    parser.add_argument(
        "--blender",
        type=Path,
        default=None,
        help="Optional Blender executable path. Also supports BLENDER_BIN.",
    )
    parser.add_argument(
        "--run-in-blender",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    args.input = _resolve_path(args.input)
    if args.output_dir is None:
        if args.method in ("auto", "primitive", "projection"):
            args.output_dir = DEFAULT_OUTPUT_DIR / args.method
        else:
            args.output_dir = DEFAULT_OUTPUT_DIR
    args.output_dir = _resolve_path(args.output_dir)
    if args.cluster_distance < 0:
        parser.error("--cluster-distance must be non-negative")
    if args.projection_margin < 0:
        parser.error("--projection-margin must be non-negative")
    if args.min_diagonal < 0:
        parser.error("--min-diagonal must be non-negative")
    return args


def _candidate_to_executable(value: Path | str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    resolved = shutil.which(text)
    if resolved:
        return resolved
    path = Path(text).expanduser()
    if path.exists():
        return str(path.resolve())
    return None


def find_blender(explicit: Path | None) -> str:
    for candidate in (
        explicit,
        os.environ.get("BLENDER_BIN"),
        shutil.which("blender"),
        DEFAULT_MAC_BLENDER,
    ):
        executable = _candidate_to_executable(candidate)
        if executable:
            return executable
    raise RuntimeError(
        "Could not find Blender. Pass --blender, set BLENDER_BIN, add blender to PATH, "
        f"or install Blender at {DEFAULT_MAC_BLENDER}."
    )


def run_via_blender(args: argparse.Namespace) -> int:
    blender = find_blender(args.blender)
    cmd = [
        blender,
        "--background",
        "--python",
        str(Path(__file__).resolve()),
        "--",
        "--run-in-blender",
        "--input",
        str(args.input),
        "--output-dir",
        str(args.output_dir),
        "--method",
        str(args.method),
        "--cluster-distance",
        str(args.cluster_distance),
        "--projection-margin",
        str(args.projection_margin),
        "--min-diagonal",
        str(args.min_diagonal),
    ]
    if args.preserve_offset:
        cmd.append("--preserve-offset")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.write_preview:
        cmd.append("--write-preview")
    return subprocess.run(cmd, check=False).returncode


class DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def run_in_blender(args: argparse.Namespace) -> int:
    try:
        import bpy
        from mathutils import Matrix, Vector
    except ImportError as exc:
        raise RuntimeError("This part of the script must run inside Blender's Python.") from exc

    if not args.input.exists():
        raise FileNotFoundError(f"Input GLB does not exist: {args.input}")

    def clear_scene() -> None:
        bpy.ops.object.mode_set(mode="OBJECT") if bpy.ops.object.mode_set.poll() else None
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()

    def mesh_objects() -> list:
        return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]

    def world_bounds(obj) -> tuple[Vector, Vector]:
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        mins = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
        maxs = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
        return mins, maxs

    def diagonal(bounds: tuple[Vector, Vector]) -> float:
        return float((bounds[1] - bounds[0]).length)

    def aabb_gap(left: tuple[Vector, Vector], right: tuple[Vector, Vector]) -> float:
        gaps = []
        for axis in range(3):
            if left[1][axis] < right[0][axis]:
                gaps.append(float(right[0][axis] - left[1][axis]))
            elif right[1][axis] < left[0][axis]:
                gaps.append(float(left[0][axis] - right[1][axis]))
            else:
                gaps.append(0.0)
        return float(Vector(gaps).length)

    def combine_bounds(items: Iterable[tuple[Vector, Vector]]) -> tuple[Vector, Vector]:
        bounds = list(items)
        mins = Vector(
            (
                min(bound[0].x for bound in bounds),
                min(bound[0].y for bound in bounds),
                min(bound[0].z for bound in bounds),
            )
        )
        maxs = Vector(
            (
                max(bound[1].x for bound in bounds),
                max(bound[1].y for bound in bounds),
                max(bound[1].z for bound in bounds),
            )
        )
        return mins, maxs

    def bounds_center(bounds: tuple[Vector, Vector]) -> Vector:
        return (bounds[0] + bounds[1]) * 0.5

    def face_components(mesh) -> list[list[int]]:
        vertex_to_faces: dict[int, list[int]] = defaultdict(list)
        for polygon in mesh.polygons:
            for vertex_index in polygon.vertices:
                vertex_to_faces[int(vertex_index)].append(int(polygon.index))

        visited: set[int] = set()
        components: list[list[int]] = []
        for polygon in mesh.polygons:
            start = int(polygon.index)
            if start in visited:
                continue
            queue = deque([start])
            visited.add(start)
            component: list[int] = []
            while queue:
                face_index = queue.popleft()
                component.append(face_index)
                for vertex_index in mesh.polygons[face_index].vertices:
                    for neighbor in vertex_to_faces[int(vertex_index)]:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append(neighbor)
            components.append(component)
        return components

    def component_bounds(obj, face_indices: Sequence[int]) -> tuple[Vector, Vector]:
        source_mesh = obj.data
        used_vertices = set()
        for face_index in face_indices:
            used_vertices.update(int(vertex_index) for vertex_index in source_mesh.polygons[face_index].vertices)
        points = [obj.matrix_world @ source_mesh.vertices[vertex_index].co for vertex_index in used_vertices]
        mins = Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points)))
        maxs = Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points)))
        return mins, maxs

    def create_cluster_object(cluster: Sequence[dict], cluster_index: int, anchor: Vector):
        vertex_map: dict[tuple[str, int], int] = {}
        vertex_sources: list[tuple[object, int]] = []
        vertices = []
        faces = []
        source_polygons = []
        material_slots = []
        material_map: dict[tuple[str, int], int] = {}

        def material_index_for(source_mesh, old_index: int) -> int:
            key = (source_mesh.name, int(old_index))
            if key in material_map:
                return material_map[key]
            material = source_mesh.materials[old_index] if old_index < len(source_mesh.materials) else None
            if material is None:
                material_map[key] = 0
                return 0
            for index, existing in enumerate(material_slots):
                if existing == material:
                    material_map[key] = index
                    return index
            material_slots.append(material)
            material_map[key] = len(material_slots) - 1
            return material_map[key]

        for component in cluster:
            source = component["source"]
            source_mesh = source.data
            source_key = source.name
            for face_index in component["face_indices"]:
                polygon = source_mesh.polygons[face_index]
                face = []
                for old_vertex_index in polygon.vertices:
                    old_vertex_index = int(old_vertex_index)
                    key = (source_key, old_vertex_index)
                    if key not in vertex_map:
                        vertex_map[key] = len(vertices)
                        vertex_sources.append((source_mesh, old_vertex_index))
                        world_co = source.matrix_world @ source_mesh.vertices[old_vertex_index].co
                        if not args.preserve_offset:
                            world_co = world_co - anchor
                        vertices.append(world_co)
                    face.append(vertex_map[key])
                faces.append(face)
                source_polygons.append((source_mesh, polygon))

        new_mesh = bpy.data.meshes.new(f"sign_{cluster_index:03d}_mesh")
        new_mesh.from_pydata(vertices, [], faces)
        new_mesh.update()

        for source_mesh, source_polygon in source_polygons:
            material_index_for(source_mesh, source_polygon.material_index)
        for material in material_slots:
            new_mesh.materials.append(material)

        for new_polygon, (source_mesh, source_polygon) in zip(new_mesh.polygons, source_polygons):
            new_polygon.material_index = material_index_for(source_mesh, source_polygon.material_index)
            new_polygon.use_smooth = source_polygon.use_smooth

        uv_layer_names = []
        for source_mesh, _source_polygon in source_polygons:
            for source_uv_layer in source_mesh.uv_layers:
                if source_uv_layer.name not in uv_layer_names:
                    uv_layer_names.append(source_uv_layer.name)

        for layer_name in uv_layer_names:
            new_uv_layer = new_mesh.uv_layers.new(name=layer_name)
            for new_polygon, (source_mesh, source_polygon) in zip(new_mesh.polygons, source_polygons):
                source_uv_layer = source_mesh.uv_layers.get(layer_name)
                if source_uv_layer is None:
                    continue
                for offset, new_loop_index in enumerate(new_polygon.loop_indices):
                    old_loop_index = source_polygon.loop_indices[offset]
                    new_uv_layer.data[new_loop_index].uv = source_uv_layer.data[old_loop_index].uv

        color_layer_specs = []
        for source_mesh, _source_polygon in source_polygons:
            for source_color_layer in source_mesh.color_attributes:
                spec = (source_color_layer.name, source_color_layer.data_type, source_color_layer.domain)
                if spec not in color_layer_specs:
                    color_layer_specs.append(spec)

        for layer_name, data_type, domain in color_layer_specs:
            new_color_layer = new_mesh.color_attributes.new(name=layer_name, type=data_type, domain=domain)
            if domain == "CORNER":
                for new_polygon, (source_mesh, source_polygon) in zip(new_mesh.polygons, source_polygons):
                    source_color_layer = source_mesh.color_attributes.get(layer_name)
                    if source_color_layer is None or source_color_layer.domain != "CORNER":
                        continue
                    for offset, new_loop_index in enumerate(new_polygon.loop_indices):
                        old_loop_index = source_polygon.loop_indices[offset]
                        new_color_layer.data[new_loop_index].color = source_color_layer.data[old_loop_index].color
            elif domain == "POINT":
                for new_vertex_index, (source_mesh, old_vertex_index) in enumerate(vertex_sources):
                    source_color_layer = source_mesh.color_attributes.get(layer_name)
                    if source_color_layer is None or source_color_layer.domain != "POINT":
                        continue
                    new_color_layer.data[new_vertex_index].color = source_color_layer.data[old_vertex_index].color

        new_obj = bpy.data.objects.new(f"sign_{cluster_index:03d}", new_mesh)
        new_obj.matrix_world = Matrix.Identity(4)
        bpy.context.collection.objects.link(new_obj)
        return new_obj

    def build_loose_part_components(objects: Sequence) -> list[dict]:
        loose_part_components = []
        for obj in list(objects):
            if obj.name not in bpy.data.objects:
                continue
            obj.data.update()
            print(f"Analyzing mesh object: {obj.name} faces={len(obj.data.polygons)}", flush=True)
            components = face_components(obj.data)
            print(f"  loose components: {len(components)}", flush=True)
            for component_index, face_indices in enumerate(components, start=1):
                bounds = component_bounds(obj, face_indices)
                loose_part_components.append(
                    {
                        "source": obj,
                        "face_indices": face_indices,
                        "bounds": bounds,
                        "name": f"{obj.name}_loose_{component_index:03d}",
                    }
                )
        return loose_part_components

    def build_primitive_components(objects: Sequence) -> list[dict]:
        primitive_components = []
        for obj in list(objects):
            if obj.name not in bpy.data.objects:
                continue
            obj.data.update()
            material_faces: dict[int, list[int]] = defaultdict(list)
            for polygon in obj.data.polygons:
                material_faces[int(polygon.material_index)].append(int(polygon.index))
            print(
                f"Analyzing mesh primitives: {obj.name} material_groups={len(material_faces)} faces={len(obj.data.polygons)}",
                flush=True,
            )
            for material_index in sorted(material_faces):
                face_indices = material_faces[material_index]
                if not face_indices:
                    continue
                material = None
                if 0 <= material_index < len(obj.material_slots):
                    material = obj.material_slots[material_index].material
                bounds = component_bounds(obj, face_indices)
                primitive_components.append(
                    {
                        "source": obj,
                        "face_indices": face_indices,
                        "bounds": bounds,
                        "name": f"{obj.name}_primitive_{material_index:03d}",
                        "primitive_group_id": material_index,
                        "material_index": material_index,
                        "material_name": material.name if material else "",
                    }
                )
        return primitive_components

    def cluster_components_loose_3d(components: Sequence[dict]) -> list[list[dict]]:
        if not components:
            return []
        component_bounds = [component["bounds"] for component in components]
        sets = DisjointSet(len(components))
        for left in range(len(components)):
            for right in range(left + 1, len(components)):
                if aabb_gap(component_bounds[left], component_bounds[right]) <= args.cluster_distance:
                    sets.union(left, right)

        grouped: dict[int, list] = {}
        for index, obj in enumerate(components):
            grouped.setdefault(sets.find(index), []).append(obj)

        clusters = list(grouped.values())
        clusters.sort(
            key=lambda cluster: tuple(
                round(value, 6)
                for value in bounds_center(combine_bounds(component["bounds"] for component in cluster))
            )
        )
        return clusters

    def rect_for_cluster(cluster: Sequence[dict], plane: str, margin: float = 0.0) -> tuple[float, float, float, float]:
        bounds = combine_bounds(component["bounds"] for component in cluster)
        return projection_rect(bounds, plane, margin)

    def color_for_index(index: int) -> str:
        hue = (index * 137) % 360
        return f"hsl({hue}, 72%, 45%)"

    def write_projection_svg(clusters: Sequence[Sequence[dict]], path: Path, plane: str) -> None:
        rects = [rect_for_cluster(cluster, plane) for cluster in clusters]
        if not rects:
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" />\n', encoding="utf-8")
            return

        min_x = min(rect[0] for rect in rects)
        min_y = min(rect[1] for rect in rects)
        max_x = max(rect[2] for rect in rects)
        max_y = max(rect[3] for rect in rects)
        width = 1200
        height = 900
        pad = 40
        span_x = max(max_x - min_x, 1e-6)
        span_y = max(max_y - min_y, 1e-6)
        scale = min((width - pad * 2) / span_x, (height - pad * 2) / span_y)

        def sx(value: float) -> float:
            return pad + (value - min_x) * scale

        def sy(value: float) -> float:
            return height - pad - (value - min_y) * scale

        lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#f8fafc" />',
            f'<text x="24" y="30" font-family="monospace" font-size="18" fill="#0f172a">{plane} projection clusters={len(clusters)}</text>',
        ]
        for index, rect in enumerate(rects, start=1):
            x = sx(rect[0])
            y = sy(rect[3])
            rect_width = max((rect[2] - rect[0]) * scale, 1.0)
            rect_height = max((rect[3] - rect[1]) * scale, 1.0)
            color = color_for_index(index)
            lines.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{rect_width:.2f}" height="{rect_height:.2f}" '
                f'fill="{color}" fill-opacity="0.10" stroke="{color}" stroke-width="2" />'
            )
            lines.append(
                f'<text x="{x + 4:.2f}" y="{y + 16:.2f}" font-family="monospace" font-size="12" fill="{color}">{index:03d}</text>'
            )
        lines.append("</svg>")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def bounds_to_list(bounds: tuple[Vector, Vector]) -> list[list[float]]:
        return [
            [float(bounds[0].x), float(bounds[0].y), float(bounds[0].z)],
            [float(bounds[1].x), float(bounds[1].y), float(bounds[1].z)],
        ]

    def write_projection_report(
        clusters: Sequence[Sequence[dict]],
        loose_count: int,
        kept_count: int,
        skipped_count: int,
        actual_method: str,
        fallback_reason: str | None,
    ) -> None:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for index, cluster in enumerate(clusters, start=1):
            bounds = combine_bounds(component["bounds"] for component in cluster)
            center = bounds_center(bounds)
            span = bounds[1] - bounds[0]
            primitive_group_ids = [
                component.get("primitive_group_id")
                for component in cluster
                if component.get("primitive_group_id") is not None
            ]
            material_indices = [
                component.get("material_index")
                for component in cluster
                if component.get("material_index") is not None
            ]
            material_names = [
                component.get("material_name")
                for component in cluster
                if component.get("material_name")
            ]
            rows.append(
                {
                    "index": index,
                    "component_count": len(cluster),
                    "face_count": sum(len(component["face_indices"]) for component in cluster),
                    "bounds": bounds_to_list(bounds),
                    "center": [float(center.x), float(center.y), float(center.z)],
                    "span": [float(span.x), float(span.y), float(span.z)],
                    "top_rect": list(rect_for_cluster(cluster, "top")),
                    "front_rect": list(rect_for_cluster(cluster, "front")),
                    "primitive_group_ids": primitive_group_ids,
                    "material_indices": material_indices,
                    "material_names": material_names,
                }
            )
        total_face_count = sum(row["face_count"] for row in rows)
        report = {
            "input": str(args.input),
            "method": actual_method,
            "requested_method": args.method,
            "actual_method": actual_method,
            "fallback_reason": fallback_reason,
            "projection_margin": args.projection_margin,
            "loose_parts": loose_count,
            "kept_parts": kept_count,
            "skipped_tiny_parts": skipped_count,
            "cluster_count": len(rows),
            "face_count": total_face_count,
            "clusters": rows,
        }
        report_text = json.dumps(report, indent=2)
        (args.output_dir / "clusters_split.json").write_text(report_text, encoding="utf-8")
        (args.output_dir / "clusters_projection.json").write_text(report_text, encoding="utf-8")
        write_projection_svg(clusters, args.output_dir / "projection_top.svg", "top")
        write_projection_svg(clusters, args.output_dir / "projection_front.svg", "front")

    def projection_rect(bounds: tuple[Vector, Vector], plane: str, margin: float) -> tuple[float, float, float, float]:
        if plane == "top":
            rect = (bounds[0].x, bounds[0].y, bounds[1].x, bounds[1].y)
        elif plane == "front":
            rect = (bounds[0].x, bounds[0].z, bounds[1].x, bounds[1].z)
        else:
            raise ValueError(f"Unsupported projection plane: {plane}")
        return (rect[0] - margin, rect[1] - margin, rect[2] + margin, rect[3] + margin)

    def sort_clusters(clusters: list[list[dict]]) -> list[list[dict]]:
        clusters.sort(
            key=lambda cluster: tuple(
                round(value, 6)
                for value in bounds_center(combine_bounds(component["bounds"] for component in cluster))
            )
        )
        return clusters

    def primitive_unsuitable_reason(components: Sequence[dict]) -> str | None:
        if len(components) <= 1:
            return f"need at least 2 non-empty primitive groups, got {len(components)}"
        margin = float(args.projection_margin)
        rects = [projection_rect(component["bounds"], "top", margin) for component in components]
        for left in range(len(rects)):
            for right in range(left + 1, len(rects)):
                if rects_overlap(rects[left], rects[right]):
                    left_id = components[left].get("primitive_group_id", left)
                    right_id = components[right].get("primitive_group_id", right)
                    return f"primitive top-projection bboxes overlap: {left_id} and {right_id}"
        return None

    def rects_overlap(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
        return not (left[2] < right[0] or right[2] < left[0] or left[3] < right[1] or right[3] < left[1])

    def cluster_components_projection(components: Sequence[dict]) -> list[list[dict]]:
        if not components:
            return []
        margin = float(args.projection_margin)
        cell_size = max(margin * 2.0, 1e-4)
        rects = [projection_rect(component["bounds"], "top", margin) for component in components]
        sets = DisjointSet(len(components))
        grid: dict[tuple[int, int], list[int]] = defaultdict(list)

        for index, rect in enumerate(rects):
            min_x = math.floor(rect[0] / cell_size)
            max_x = math.floor(rect[2] / cell_size)
            min_y = math.floor(rect[1] / cell_size)
            max_y = math.floor(rect[3] / cell_size)
            candidates: set[int] = set()
            for cell_x in range(min_x, max_x + 1):
                for cell_y in range(min_y, max_y + 1):
                    candidates.update(grid.get((cell_x, cell_y), ()))
            for candidate in candidates:
                if rects_overlap(rect, rects[candidate]):
                    sets.union(index, candidate)
            for cell_x in range(min_x, max_x + 1):
                for cell_y in range(min_y, max_y + 1):
                    grid[(cell_x, cell_y)].append(index)

        grouped: dict[int, list] = {}
        for index, component in enumerate(components):
            grouped.setdefault(sets.find(index), []).append(component)

        clusters = list(grouped.values())
        clusters.sort(
            key=lambda cluster: tuple(
                round(value, 6)
                for value in bounds_center(combine_bounds(component["bounds"] for component in cluster))
            )
        )
        return clusters

    def delete_objects(objects: Sequence) -> None:
        bpy.ops.object.mode_set(mode="OBJECT") if bpy.ops.object.mode_set.poll() else None
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        bpy.ops.object.delete()

    def export_cluster(cluster: Sequence, index: int) -> Path:
        cluster_bounds = combine_bounds(component["bounds"] for component in cluster)
        center = bounds_center(cluster_bounds)
        anchor = Vector((center.x, center.y, cluster_bounds[0].z))
        exported_obj = create_cluster_object(cluster, index, anchor)
        output_path = args.output_dir / f"sign_{index:03d}.glb"

        try:
            bpy.ops.object.select_all(action="DESELECT")
            exported_obj.select_set(True)
            bpy.context.view_layer.objects.active = exported_obj
            bpy.ops.export_scene.gltf(
                filepath=str(output_path),
                export_format="GLB",
                use_selection=True,
            )
        finally:
            delete_objects([exported_obj])
        return output_path

    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(args.input))

    imported_meshes = mesh_objects()
    original_count = len(imported_meshes)
    print(f"Imported mesh objects: {len(imported_meshes)}", flush=True)
    actual_method = args.method
    fallback_reason = None
    loose_parts = []
    kept_parts = []
    skipped_parts = []
    clusters = []

    if args.method in ("auto", "primitive"):
        primitive_parts = build_primitive_components(imported_meshes)
        primitive_kept_parts = []
        primitive_skipped_parts = []
        for component in primitive_parts:
            if diagonal(component["bounds"]) >= args.min_diagonal:
                primitive_kept_parts.append(component)
            else:
                primitive_skipped_parts.append(component)
        primitive_reason = primitive_unsuitable_reason(primitive_kept_parts)
        if primitive_reason is None:
            actual_method = "primitive"
            loose_parts = primitive_parts
            kept_parts = primitive_kept_parts
            skipped_parts = primitive_skipped_parts
            clusters = sort_clusters([[component] for component in kept_parts])
        elif args.method == "primitive":
            raise RuntimeError(f"Primitive split is not suitable for {args.input}: {primitive_reason}")
        else:
            actual_method = "projection"
            fallback_reason = primitive_reason

    if not clusters:
        loose_parts = build_loose_part_components(imported_meshes)
        kept_parts = []
        skipped_parts = []
        for component in loose_parts:
            if diagonal(component["bounds"]) >= args.min_diagonal:
                kept_parts.append(component)
            else:
                skipped_parts.append(component)

        if args.method == "loose-3d":
            actual_method = "loose-3d"
            clusters = cluster_components_loose_3d(kept_parts)
        else:
            actual_method = "projection"
            clusters = cluster_components_projection(kept_parts)

    print(f"Input: {args.input}")
    print(f"Original mesh objects: {original_count}")
    print(f"Loose parts: {len(loose_parts)}")
    print(f"Kept parts: {len(kept_parts)}")
    print(f"Skipped tiny parts: {len(skipped_parts)}")
    print(f"Clusters: {len(clusters)}")
    print(f"Requested method: {args.method}")
    print(f"Actual method: {actual_method}")
    if fallback_reason:
        print(f"Fallback reason: {fallback_reason}")
    print(f"Cluster distance: {args.cluster_distance}")
    print(f"Projection margin: {args.projection_margin}")
    print(f"Min diagonal: {args.min_diagonal}")

    for index, cluster in enumerate(clusters, start=1):
        cluster_bounds = combine_bounds(component["bounds"] for component in cluster)
        center = bounds_center(cluster_bounds)
        span = cluster_bounds[1] - cluster_bounds[0]
        print(
            "Cluster "
            f"{index:03d}: parts={len(cluster)} "
            f"center=({center.x:.4f}, {center.y:.4f}, {center.z:.4f}) "
            f"span=({span.x:.4f}, {span.y:.4f}, {span.z:.4f})"
        )

    if args.write_preview:
        write_projection_report(
            clusters,
            len(loose_parts),
            len(kept_parts),
            len(skipped_parts),
            actual_method,
            fallback_reason,
        )
        print(f"Wrote projection preview files to {args.output_dir}")

    if args.dry_run:
        print("Dry run: no GLB files exported.")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for index, cluster in enumerate(clusters, start=1):
        output_path = export_cluster(cluster, index)
        print(f"Exported: {output_path}")

    print(f"Done. Exported {len(clusters)} GLB files to {args.output_dir}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(_argv_after_blender_separator(sys.argv) if argv is None else argv)
    if args.run_in_blender:
        return run_in_blender(args)
    return run_via_blender(args)


if __name__ == "__main__":
    raise SystemExit(main())
