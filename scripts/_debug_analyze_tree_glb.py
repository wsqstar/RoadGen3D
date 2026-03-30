#!/usr/bin/env python3
"""Debug analysis of tree GLB: original vs normalized."""

import sys
import numpy as np
import trimesh

ORIGINAL = "/Users/shiqi/.objaverse/hf-objaverse-v1/glbs/000-037/352c29c013434d6585e74332699310e2.glb"
NORMALIZED = "/Users/shiqi/Coding/github/GIStudio/RoadGen3D/data/real/meshes/objaverse_tree_352c29c013434d6585e74332699310e2.glb"

def hr(title):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")

def analyze_scene(path, label):
    hr(f"{label}: {path}")
    scene = trimesh.load(path, force="scene")
    print(f"\nType returned: {type(scene).__name__}")

    # --- Scene graph ---
    print(f"\n--- Scene Graph ---")
    print(f"  graph.nodes: {list(scene.graph.nodes)}")
    print(f"  graph.nodes_geometry: {list(scene.graph.nodes_geometry)}")
    print(f"  graph.transforms.node_data keys: {list(scene.graph.transforms.node_data.keys()) if hasattr(scene.graph, 'transforms') else 'N/A'}")

    # Print transforms for each geometry node
    for node_name in scene.graph.nodes_geometry:
        try:
            transform, geom_name = scene.graph[node_name]
            print(f"\n  Node '{node_name}' -> geometry '{geom_name}'")
            print(f"    Transform:\n{np.array2string(transform, precision=4, suppress_small=True)}")
        except Exception as e:
            print(f"\n  Node '{node_name}' -> error getting transform: {e}")

    # --- Geometry details ---
    print(f"\n--- Geometries ({len(scene.geometry)} total) ---")
    total_verts = 0
    total_faces = 0
    for name, geom in scene.geometry.items():
        verts = len(geom.vertices) if hasattr(geom, 'vertices') else 0
        faces = len(geom.faces) if hasattr(geom, 'faces') else 0
        total_verts += verts
        total_faces += faces
        print(f"\n  Geometry: '{name}'")
        print(f"    vertices: {verts}, faces: {faces}")
        print(f"    vertex dtype: {geom.vertices.dtype if hasattr(geom, 'vertices') else 'N/A'}")

        # Bounding box per geometry
        if hasattr(geom, 'bounds') and geom.bounds is not None:
            bb = geom.bounds
            print(f"    bounds min: [{bb[0][0]:.4f}, {bb[0][1]:.4f}, {bb[0][2]:.4f}]")
            print(f"    bounds max: [{bb[1][0]:.4f}, {bb[1][1]:.4f}, {bb[1][2]:.4f}]")
            span = bb[1] - bb[0]
            print(f"    span:       [{span[0]:.4f}, {span[1]:.4f}, {span[2]:.4f}]")

        # Material info
        if hasattr(geom, 'visual'):
            visual = geom.visual
            print(f"    visual kind: {visual.kind}  (type: {type(visual).__name__})")
            if visual.kind == 'texture':
                mat = visual.material
                print(f"    material type: {type(mat).__name__}")
                if hasattr(mat, 'name'):
                    print(f"    material name: {mat.name}")
                if hasattr(mat, 'baseColorFactor'):
                    print(f"    baseColorFactor: {mat.baseColorFactor}")
                if hasattr(mat, 'baseColorTexture') and mat.baseColorTexture is not None:
                    tex = mat.baseColorTexture
                    print(f"    baseColorTexture: {type(tex).__name__}, size={getattr(tex, 'size', 'N/A')}")
                elif hasattr(mat, 'image') and mat.image is not None:
                    img = mat.image
                    print(f"    texture image: {type(img).__name__}, size={getattr(img, 'size', 'N/A')}")
                else:
                    print(f"    texture image: None (no embedded texture)")

                # Check for other PBR maps
                for attr_name in ['normalTexture', 'metallicRoughnessTexture', 'emissiveTexture', 'occlusionTexture']:
                    val = getattr(mat, attr_name, None)
                    if val is not None:
                        print(f"    {attr_name}: {type(val).__name__}, size={getattr(val, 'size', 'N/A')}")

                # Check UV coords
                if hasattr(visual, 'uv') and visual.uv is not None:
                    print(f"    UV coords: shape={visual.uv.shape}")
                else:
                    print(f"    UV coords: None")

            elif visual.kind == 'vertex':
                print(f"    vertex_colors shape: {visual.vertex_colors.shape if hasattr(visual, 'vertex_colors') else 'N/A'}")
                if hasattr(visual, 'vertex_colors') and visual.vertex_colors is not None:
                    vc = visual.vertex_colors
                    print(f"    vertex_colors sample [0:3]: {vc[:3]}")
                    unique_colors = len(np.unique(vc, axis=0))
                    print(f"    unique vertex colors: {unique_colors}")
            elif visual.kind == 'face':
                print(f"    face_colors shape: {visual.face_colors.shape if hasattr(visual, 'face_colors') else 'N/A'}")
            else:
                print(f"    (no recognized visual kind)")
        else:
            print(f"    visual: None")

    print(f"\n  TOTAL vertices: {total_verts}, TOTAL faces: {total_faces}")

    # --- Overall scene bounds ---
    try:
        overall_bounds = scene.bounds
        if overall_bounds is not None:
            print(f"\n--- Overall Scene Bounds ---")
            print(f"  min: [{overall_bounds[0][0]:.4f}, {overall_bounds[0][1]:.4f}, {overall_bounds[0][2]:.4f}]")
            print(f"  max: [{overall_bounds[1][0]:.4f}, {overall_bounds[1][1]:.4f}, {overall_bounds[1][2]:.4f}]")
            span = overall_bounds[1] - overall_bounds[0]
            print(f"  span: [{span[0]:.4f}, {span[1]:.4f}, {span[2]:.4f}]")
    except Exception as e:
        print(f"\n  Could not compute overall bounds: {e}")

    # --- Check embedded images/textures ---
    print(f"\n--- Embedded Resources ---")
    if hasattr(scene, 'metadata') and scene.metadata:
        print(f"  scene.metadata keys: {list(scene.metadata.keys()) if isinstance(scene.metadata, dict) else type(scene.metadata)}")
    else:
        print(f"  scene.metadata: empty/None")

    # Check for textures via geometry materials
    texture_count = 0
    for name, geom in scene.geometry.items():
        if hasattr(geom, 'visual') and geom.visual.kind == 'texture':
            mat = geom.visual.material
            for attr in ['baseColorTexture', 'image', 'normalTexture', 'metallicRoughnessTexture', 'emissiveTexture', 'occlusionTexture']:
                val = getattr(mat, attr, None)
                if val is not None:
                    texture_count += 1
    print(f"  Total texture images across all geometries: {texture_count}")

    return scene, total_verts, total_faces


def analyze_merged_mesh(path, label):
    """Simulate what _load_mesh_as_single_mesh does and analyze the result."""
    hr(f"{label} - Merged (as single mesh)")
    scene = trimesh.load(path, force="scene")
    if isinstance(scene, trimesh.Scene):
        if not scene.geometry:
            print("  ERROR: empty scene!")
            return None
        merged = trimesh.util.concatenate(tuple(scene.geometry.values()))
    else:
        merged = scene

    print(f"  Type: {type(merged).__name__}")
    print(f"  vertices: {len(merged.vertices)}, faces: {len(merged.faces)}")

    bb = merged.bounds
    print(f"  bounds min: [{bb[0][0]:.4f}, {bb[0][1]:.4f}, {bb[0][2]:.4f}]")
    print(f"  bounds max: [{bb[1][0]:.4f}, {bb[1][1]:.4f}, {bb[1][2]:.4f}]")
    span = bb[1] - bb[0]
    print(f"  span: [{span[0]:.4f}, {span[1]:.4f}, {span[2]:.4f}]")

    # Visual info after merge
    if hasattr(merged, 'visual'):
        print(f"  visual kind: {merged.visual.kind}")
        if merged.visual.kind == 'vertex':
            vc = merged.visual.vertex_colors
            print(f"  vertex_colors shape: {vc.shape}")
            unique = len(np.unique(vc, axis=0))
            print(f"  unique vertex colors: {unique}")
        elif merged.visual.kind == 'texture':
            mat = merged.visual.material
            print(f"  material type: {type(mat).__name__}")
            has_tex = getattr(mat, 'baseColorTexture', None) or getattr(mat, 'image', None)
            print(f"  has texture image: {has_tex is not None}")
        elif merged.visual.kind == 'face':
            print(f"  face_colors shape: {merged.visual.face_colors.shape}")
    return merged


def simulate_normalization_and_upright(path, label):
    """Reproduce the exact normalization + upright validation pipeline."""
    hr(f"{label} - Normalization & Upright Validation")

    # 1) Load as single mesh (like _load_mesh_as_single_mesh)
    scene = trimesh.load(path, force="scene")
    if isinstance(scene, trimesh.Scene) and scene.geometry:
        mesh = trimesh.util.concatenate(tuple(scene.geometry.values()))
    else:
        mesh = scene

    print(f"  After merge: {len(mesh.vertices)} verts, {len(mesh.faces)} faces")
    print(f"  Pre-normalize bounds min: {mesh.bounds[0]}")
    print(f"  Pre-normalize bounds max: {mesh.bounds[1]}")

    # 2) normalize_grounded_mesh (no rotation)
    # _normalize_mesh
    bbox = mesh.bounds
    center = bbox.mean(axis=0)
    span = bbox[1] - bbox[0]
    max_span = float(max(span.max(), 1e-6))
    normalized = mesh.copy()
    normalized.apply_translation(-center)
    normalized.apply_scale(1.0 / max_span)

    print(f"\n  After _normalize_mesh:")
    print(f"    center used: {center}")
    print(f"    max_span used: {max_span}")
    print(f"    bounds min: {normalized.bounds[0]}")
    print(f"    bounds max: {normalized.bounds[1]}")

    # _ground_mesh_to_y_zero
    min_y = float(normalized.bounds[0][1])
    normalized.apply_translation([0.0, -min_y, 0.0])

    print(f"\n  After _ground_mesh_to_y_zero:")
    print(f"    min_y offset: {min_y}")
    print(f"    bounds min: {normalized.bounds[0]}")
    print(f"    bounds max: {normalized.bounds[1]}")
    norm_span = normalized.bounds[1] - normalized.bounds[0]
    print(f"    span: [{norm_span[0]:.4f}, {norm_span[1]:.4f}, {norm_span[2]:.4f}]")

    # 3) validate_tree_upright
    print(f"\n  --- Tree Upright Validation ---")
    bounds = np.asarray(normalized.bounds, dtype=np.float64)
    span = bounds[1] - bounds[0]
    width = float(span[0])
    height = float(span[1])
    depth = float(span[2])
    min_y_val = float(bounds[0][1])

    print(f"    width={width:.4f}, height={height:.4f}, depth={depth:.4f}")
    print(f"    min_y={min_y_val:.6f}")
    print(f"    height > max(width,depth)? {height > max(width, depth)} ({height:.4f} vs {max(width,depth):.4f})")

    # Trunk axis analysis
    vertices = np.asarray(normalized.vertices, dtype=np.float64)
    trunk_slice_ratio = 0.35
    lower_max_y = min_y_val + height * trunk_slice_ratio
    print(f"    trunk_slice_ratio={trunk_slice_ratio}, lower_max_y={lower_max_y:.4f}")

    try:
        sampled_points = np.asarray(normalized.sample(4096), dtype=np.float64)
    except Exception:
        sampled_points = vertices
    trunk_points = sampled_points[sampled_points[:, 1] <= lower_max_y]
    print(f"    trunk_points from sampling: {trunk_points.shape[0]}")
    if trunk_points.shape[0] < 8:
        trunk_points = vertices[vertices[:, 1] <= lower_max_y]
        print(f"    trunk_points from vertices (fallback): {trunk_points.shape[0]}")
    if trunk_points.shape[0] >= 3:
        centered = trunk_points - trunk_points.mean(axis=0, keepdims=True)
        covariance = np.cov(centered, rowvar=False)
        eigvals, eigvecs = np.linalg.eigh(covariance)
        principal_axis = eigvecs[:, int(np.argmax(eigvals))]
        principal_axis = principal_axis / max(np.linalg.norm(principal_axis), 1e-9)
        world_up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        dot = float(np.clip(abs(np.dot(principal_axis, world_up)), -1.0, 1.0))
        angle_deg = float(np.degrees(np.arccos(dot)))
        print(f"    eigenvalues: {eigvals}")
        print(f"    principal_axis: {principal_axis}")
        print(f"    angle from Y-up: {angle_deg:.2f} deg")
        print(f"    PASSES 15 deg threshold? {angle_deg <= 15.0}")

        # Also check the second and third eigenvalues/vectors
        for i, (val, vec) in enumerate(zip(eigvals, eigvecs.T)):
            dot_i = float(abs(np.dot(vec, world_up)))
            angle_i = float(np.degrees(np.arccos(np.clip(dot_i, -1.0, 1.0))))
            print(f"    eigenvec[{i}]: val={val:.6f}, axis={vec}, angle_from_Y={angle_i:.2f} deg")

        # Visualize the trunk region
        print(f"\n    --- Trunk region analysis ---")
        trunk_x_range = trunk_points[:, 0].max() - trunk_points[:, 0].min()
        trunk_y_range = trunk_points[:, 1].max() - trunk_points[:, 1].min()
        trunk_z_range = trunk_points[:, 2].max() - trunk_points[:, 2].min()
        print(f"    trunk X range: {trunk_x_range:.4f}")
        print(f"    trunk Y range: {trunk_y_range:.4f}")
        print(f"    trunk Z range: {trunk_z_range:.4f}")
        print(f"    This tells us the trunk spread: if X or Z >> Y, it means the trunk region is flat/spread, not vertical")

    # 4) Analysis of multi-tree issue
    print(f"\n  --- Multi-sub-mesh Analysis ---")
    scene2 = trimesh.load(path, force="scene")
    if isinstance(scene2, trimesh.Scene):
        for gname, geom in scene2.geometry.items():
            gb = geom.bounds
            gcenter = gb.mean(axis=0)
            gspan = gb[1] - gb[0]
            print(f"    sub-mesh '{gname}': center=[{gcenter[0]:.3f},{gcenter[1]:.3f},{gcenter[2]:.3f}], span=[{gspan[0]:.3f},{gspan[1]:.3f},{gspan[2]:.3f}], verts={len(geom.vertices)}")

    return normalized


def compare_files(orig_scene, norm_scene, orig_verts, norm_verts, orig_faces, norm_faces):
    hr("COMPARISON: Original vs Normalized")
    print(f"  Original  - geometries: {len(orig_scene.geometry)}, total verts: {orig_verts}, total faces: {orig_faces}")
    print(f"  Normalized - geometries: {len(norm_scene.geometry)}, total verts: {norm_verts}, total faces: {norm_faces}")
    print(f"  Vertex count match: {orig_verts == norm_verts}")
    print(f"  Face count match: {orig_faces == norm_faces}")

    # Check material preservation
    orig_has_texture = any(
        g.visual.kind == 'texture' and (getattr(g.visual.material, 'baseColorTexture', None) or getattr(g.visual.material, 'image', None))
        for g in orig_scene.geometry.values()
        if hasattr(g, 'visual')
    )
    norm_has_texture = any(
        g.visual.kind == 'texture' and (getattr(g.visual.material, 'baseColorTexture', None) or getattr(g.visual.material, 'image', None))
        for g in norm_scene.geometry.values()
        if hasattr(g, 'visual')
    )
    print(f"\n  Original has texture images: {orig_has_texture}")
    print(f"  Normalized has texture images: {norm_has_texture}")
    if orig_has_texture and not norm_has_texture:
        print(f"  *** ISSUE: Textures were STRIPPED during normalization! ***")

    # Check material types
    orig_mat_kinds = set()
    norm_mat_kinds = set()
    for g in orig_scene.geometry.values():
        if hasattr(g, 'visual'):
            orig_mat_kinds.add(g.visual.kind)
    for g in norm_scene.geometry.values():
        if hasattr(g, 'visual'):
            norm_mat_kinds.add(g.visual.kind)
    print(f"  Original visual kinds: {orig_mat_kinds}")
    print(f"  Normalized visual kinds: {norm_mat_kinds}")


if __name__ == "__main__":
    # Analyze original
    orig_scene, orig_verts, orig_faces = analyze_scene(ORIGINAL, "ORIGINAL")
    orig_merged = analyze_merged_mesh(ORIGINAL, "ORIGINAL")

    # Analyze normalized
    norm_scene, norm_verts, norm_faces = analyze_scene(NORMALIZED, "NORMALIZED")

    # Comparison
    compare_files(orig_scene, norm_scene, orig_verts, norm_verts, orig_faces, norm_faces)

    # Run the normalization pipeline on the original to check upright validation
    simulate_normalization_and_upright(ORIGINAL, "ORIGINAL")

    hr("SUMMARY OF FINDINGS")
    print("See above for detailed analysis.")
