# M3 External AI Asset Production Guide

## 1) Objective
Produce **120 real street assets** for RoadGen3D M3 (8 categories x 15 variants):
- bench, lamp, trash, tree, bus_stop, mailbox, hydrant, bollard

Use task list:
- `/Users/shiqi/Coding/github/GIStudio/RoadGen3D/docs/m3_asset_task_list.csv`

## 2) Hard Requirements
- Output format: `.glb` only (PBR allowed).
- Coordinate system: Y-up, meter units.
- Grounding: object base should sit on Y=0.
- Clean mesh: watertight preferred, no broken normals, no huge non-manifold artifacts.
- Poly budget: follow `poly_budget_k` in CSV.
- Textures: max 2K, max 3 materials.
- Naming: use `asset_id` exactly.

## 3) Folder Contract (handoff package)
Each producer returns:
- `meshes/<asset_id>.glb`
- `previews/<asset_id>.png`
- `metadata/<asset_id>.json`

`metadata/<asset_id>.json` schema:
```json
{
  "asset_id": "bench_001",
  "category": "bench",
  "text_desc": "a modern outdoor street bench with durable weather-resistant materials",
  "license": "cc-by-4.0",
  "source": "ai_generated_team_x",
  "split": "train"
}
```

## 4) RoadGen3D Import Contract
After collecting assets, place files under:
- `/Users/shiqi/Coding/github/GIStudio/RoadGen3D/data/real/meshes`

Append one JSONL row per asset to:
- `/Users/shiqi/Coding/github/GIStudio/RoadGen3D/data/real/real_assets_manifest.jsonl`

Row template:
```json
{"asset_id":"bench_001","category":"bench","text_desc":"a modern outdoor street bench with durable weather-resistant materials","mesh_path":"/Users/shiqi/Coding/github/GIStudio/RoadGen3D/data/real/meshes/bench_001.glb","latent_path":"/Users/shiqi/Coding/github/GIStudio/RoadGen3D/data/real/latents/bench_001.pt","license":"cc-by-4.0","source":"ai_generated_team_x","split":"train"}
```

## 5) Rebuild Pipeline Commands
Run in project root:
```bash
.venv/bin/python scripts/m2_11_encode_shapee_latents.py \
  --manifest data/real/real_assets_manifest.jsonl \
  --output-manifest data/real/real_assets_manifest.jsonl \
  --latents-dir data/real/latents \
  --encode-mode mesh_ref

.venv/bin/python scripts/m2_12_build_real_index.py \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only

.venv/bin/python scripts/m3_01_compose_street.py \
  --query "modern clean urban street" \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out-dir artifacts/real \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only \
  --export-format both
```

## 6) Quality Gate Before Merge
- At least 10 assets per category available in manifest.
- No missing `mesh_path` / `category` / `text_desc`.
- `scripts/m3_01_compose_street.py` runs successfully.
- `artifacts/real/scene.glb` + `scene_layout.json` generated.
