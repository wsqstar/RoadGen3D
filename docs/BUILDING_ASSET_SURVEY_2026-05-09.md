# RoadGen3D Building Asset Survey - 2026-05-09

## Current Local Inventory

| Group | Count / Size | Path | Notes |
|---|---:|---|---|
| Registered building manifest | 63 records | `assets/building/buildings_manifest.jsonl` | 62 enabled for generation |
| Existing UrbanVerse GLB pool | 107 GLB / 2.5 GB | `assets/building/assets_std_glb_flat/` | Only 7 are currently registered |
| Existing UrbanVerse thumbnails | 108 PNG / 90 MB | `assets/building/assets_thumbnail_flat/` | Useful for coarse visual triage |
| Downloaded Kenney assets | 81 GLB / 26 MB extracted | `assets/building/external/` | 56 building GLB registered |

## Manifest Composition

| Source | Registered | Enabled | Height Range | Face Range | Fit |
|---|---:|---:|---:|---:|---|
| UrbanVerse | 7 | 6 | 8.0-48.0 m | 2,054-1,315,904 | Realistic but heavy; use selectively |
| Kenney City Kit (Commercial) | 35 | 35 | 5.6-43.76 m | 62-5,246 | Good low-poly city frontage / background |
| Kenney City Kit (Suburban) | 21 | 21 | 5.9-9.9 m | 770-2,062 | Good low-rise residential frontage |

## Internet Asset Assessment

Selected and downloaded:

- Kenney City Kit (Commercial): official page lists 50 files and Creative Commons CC0. It matches our stylized / low-poly generation path for city blocks, shopfronts, and background high-rises.
- Kenney City Kit (Suburban): official page lists 40 files and Creative Commons CC0. It fills the current gap for low-rise residential and campus-edge street scenes.

Considered but not imported:

- Quaternius / Poly Pizza medieval village assets are CC0 and GLB-ready, but the medieval style does not fit current urban/campus street generation as well as Kenney.
- Free3D, TurboSquid, and Sketchfab search results include some free or CC0 models, but licensing, login/download friction, inconsistent formats, or very heavy meshes make them weaker defaults for this repo.

## Changes Made

- Downloaded Kenney archives into `assets/building/external/downloads/`.
- Extracted sources under `assets/building/external/kenney_city_kit_commercial/` and `assets/building/external/kenney_city_kit_suburban/`.
- Added `assets/building/external/sources_2026-05-09.json` with source URLs, download URLs, license URLs, archive hashes, and registered asset lists.
- Added 56 Kenney building records to `assets/building/buildings_manifest.jsonl`.
- Enriched the existing 7 UrbanVerse manifest records with `text_desc`, `dimensions_m`, `raw_dimensions`, `mesh_face_count`, `mesh_vertex_count`, `quality_tier`, `style_tags`, and `material_family`.
- Preserved the existing disabled state for `urbanverse_building_715d6ef0f2e24fb19272b9872ad4d41e`.

## Render Check

Verified in Viewer / 3D Assets / 3D Asset Editor at `http://127.0.0.1:4173/#/asset-editor`:

- Manifest loads as `[building] Buildings manifest (63)`.
- Suburban sample `kenney_city_suburban_building_type_u` renders correctly around 9 m tall.
- Commercial sample `kenney_city_commercial_building_skyscraper_e` renders correctly around 30-40 m tall.
- UrbanVerse sample `urbanverse_building_3421e73f41fe48909c6948b66ed0c094` still renders correctly after metadata enrichment.

## Recommendation

Use Kenney assets as the default lightweight building palette for current 3D generation, especially during iteration and screenshot-first evaluation. Keep UrbanVerse assets as higher-realism candidates, but gate the very heavy meshes behind explicit selection or further simplification before using them broadly in batch generation.
