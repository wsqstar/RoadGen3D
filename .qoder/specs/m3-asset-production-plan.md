# M3 Asset Production Plan: 120 Procedural Street Assets

## Goal
Replace existing 8 placeholder assets with 120 diverse procedural GLB meshes (8 categories x 15 style variants), then rebuild the full pipeline so Gradio "Run Street Compose" produces visually rich streets.

## Overview

| Step | Action | Output |
|------|--------|--------|
| 1 | Write `scripts/m3_02_generate_procedural_assets.py` | 120 GLB files in `data/real/meshes/` |
| 2 | Enhance CSV text descriptions | Updated `docs/m3_asset_task_list.csv` |
| 3 | Generate manifest | `data/real/real_assets_manifest.jsonl` (120 rows) |
| 4 | Run latent encoding | `data/real/latents/*.pt` (120 files) |
| 5 | Rebuild FAISS index | `artifacts/real/index_ip.faiss` + friends |
| 6 | Test street composition | `artifacts/real/scene.glb` + `scene_layout.json` |

---

## Step 1: Create `scripts/m3_02_generate_procedural_assets.py`

**New file.** Reads CSV, generates 120 parametric GLB meshes using trimesh.

### Architecture

```
main()
  -> parse CSV (docs/m3_asset_task_list.csv)
  -> for each row:
       mesh = GENERATORS[category](style_tag, target_dims)
       mesh = fit_to_target_dims(mesh, h, w, d)   # scale to CSV specs
       mesh = ground_at_y0(mesh)                    # base at Y=0
       mesh.export(data/real/meshes/{asset_id}.glb)
  -> write real_assets_manifest.jsonl (120 rows)
```

### Per-Category Geometry Strategy

Each generator returns a `trimesh.Trimesh` composed of primitives (box, cylinder, sphere, cone). The 15 style variants differ by:
- **Shape composition** (which primitives, how arranged)
- **Proportions** (thick/thin, tall/short legs/posts)
- **Color** (mapped from style_tag to a color palette)

| Category | Key Primitives | Variant Differentiation |
|----------|---------------|------------------------|
| **bench** | seat_box + back_box + leg_cylinders | Leg count (2/4/6), back height (none/low/high), slab vs slat seat, armrests |
| **lamp** | pole_cylinder + head_shape | Pole taper, head type (box/sphere/cone/multi), arm count, base width |
| **trash** | body (cylinder/box) + lid | Round vs square, dome/flat/cone lid, with/without pedal, frame vs solid |
| **tree** | trunk_cylinder + canopy (sphere/cone/multi) | Canopy type (sphere/cone/multi-sphere/flat), trunk bend, branching |
| **bus_stop** | posts + roof_box + side_panels | Post count, roof slope, panel count (0-3), with/without bench |
| **mailbox** | body (box/cylinder) + slot + post | Pillar vs wall-mount shape, dome/flat top, round vs rectangular body |
| **hydrant** | body_cylinder + valve_cylinders + cap | Body taper, valve count (2/3), cap type (dome/flat), base flange |
| **bollard** | post (cylinder/box) + cap | Round vs square cross-section, cap type (dome/flat/cone), reflective band |

### Color Palette (style_tag -> RGBA)

```python
STYLE_COLORS = {
    "modern":       [(128,128,128), (64,64,64)],      # grey + dark grey
    "classic":      [(101,67,33),   (34,100,34)],      # brown + forest green
    "industrial":   [(169,169,169), (105,105,105)],    # silver + dim grey
    "minimalist":   [(240,240,240), (200,200,200)],    # near white
    "ornate":       [(212,175,55),  (0,80,0)],         # gold + dark green
    "retro":        [(255,99,71),   (240,230,140)],    # tomato + khaki
    "modular":      [(70,130,180),  (60,179,113)],     # steel blue + sea green
    "eco":          [(107,142,35),  (139,90,43)],      # olive + saddle brown
    "brutalist":    [(112,128,144), (90,90,90)],       # slate grey
    "nordic":       [(222,184,135), (245,245,220)],    # burlywood + beige
    "japan_scandi": [(245,222,179), (188,143,143)],    # wheat + rosy brown
    "victorian":    [(28,28,28),    (72,61,139)],      # near black + slate blue
    "contemporary": [(192,192,192), (160,160,160)],    # silver
    "tactical":     [(85,107,47),   (255,215,0)],      # olive drab + gold
    "art_deco":     [(255,215,0),   (20,20,20)],       # gold + black
}
```

### Dimension Fitting

```python
def fit_to_target_dims(mesh, target_h, target_w, target_d):
    bounds = mesh.bounds
    span = bounds[1] - bounds[0]  # current [w, h, d] in mesh space
    # scale each axis independently to match target (within 5%)
    scale_x = target_w / max(span[0], 1e-6)
    scale_y = target_h / max(span[1], 1e-6)
    scale_z = target_d / max(span[2], 1e-6)
    scale = min(scale_x, scale_y, scale_z)  # uniform scale preserving aspect
    mesh.apply_scale(scale)
    # ground at Y=0
    mesh.apply_translation([0, -mesh.bounds[0][1], 0])
    return mesh
```

### Face Count Control

- Use low `sections` for cylinders (12-24) and low `subdivisions` for spheres (2-3)
- After merge, verify `len(mesh.faces) <= poly_budget_k * 1000`
- If over budget, reduce sections and retry

---

## Step 2: Enhance CSV Text Descriptions

Current descriptions are too generic (just style + base phrase). Each variant should have a unique, descriptive sentence for better CLIP retrieval differentiation.

Example improvements:
```
# Before
bench_001: "a modern outdoor street bench with durable weather-resistant materials"
bench_002: "a classic outdoor street bench with durable weather-resistant materials"

# After
bench_001: "a sleek modern steel park bench with flat slab seat and thin metal legs"
bench_002: "a classic wooden park bench with slatted seat, curved armrests and cast iron legs"
```

This directly impacts `_pick_category_candidate()` in street_layout.py which uses CLIP to match query text against asset `text_desc`.

---

## Step 3: Generate Manifest

Script generates `data/real/real_assets_manifest.jsonl` with 120 rows:

```json
{"asset_id":"bench_001","category":"bench","text_desc":"...","mesh_path":"/abs/path/data/real/meshes/bench_001.glb","latent_path":"/abs/path/data/real/latents/bench_001.pt","license":"cc-by-4.0","source":"procedural_generated","split":"train"}
```

Split allocation: first 12 per category = train, 13th = val, 14th-15th = test.

---

## Step 4-6: Run Existing Pipeline

```bash
# Step 4: Encode latents (mesh_ref mode, no Blender needed)
.venv/bin/python scripts/m2_11_encode_shapee_latents.py \
  --manifest data/real/real_assets_manifest.jsonl \
  --output-manifest data/real/real_assets_manifest.jsonl \
  --latents-dir data/real/latents \
  --encode-mode mesh_ref

# Step 5: Rebuild CLIP+FAISS index
.venv/bin/python scripts/m2_12_build_real_index.py \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only

# Step 6: Test street composition
.venv/bin/python scripts/m3_01_compose_street.py \
  --query "modern clean urban street" \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out-dir artifacts/real \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only \
  --export-format both
```

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `scripts/m3_02_generate_procedural_assets.py` | **CREATE** - Main generation script (~400-600 lines) |
| `docs/m3_asset_task_list.csv` | **MODIFY** - Enhance text_desc column for all 120 rows |
| `data/real/real_assets_manifest.jsonl` | **REPLACE** - Generated by script (120 rows) |
| `data/real/meshes/*.glb` | **REPLACE** - 120 new GLB files (old 8 deleted) |
| `data/real/latents/*.pt` | **REPLACE** - 120 new latent files (via pipeline) |
| `artifacts/real/index_ip.faiss` | **REPLACE** - Rebuilt by pipeline |
| `artifacts/real/id_map.json` | **REPLACE** - Rebuilt by pipeline |

---

## Verification

1. **Asset count check**: `ls data/real/meshes/*.glb | wc -l` should be 120
2. **Manifest validation**: All 120 rows have valid `asset_id`, `category`, `text_desc`, `mesh_path`
3. **Mesh quality**: Each GLB loads in trimesh without error, faces <= budget, base at Y=0
4. **Pipeline success**: All 3 pipeline scripts run without error
5. **Diversity check**: `scene_layout.json` shows instances from multiple categories with different asset_ids (not all the same variant)
6. **Scene output**: `artifacts/real/scene.glb` generated, file size > 50KB (indicating real geometry)
