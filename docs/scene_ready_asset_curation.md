# Scene-Ready Asset Curation

This project now supports a manifest-driven `scene_ready_first` asset curation mode for M3 street composition.

## Goal

Prefer assets that are safe to place directly into the exported 3D scene:

- use the original library mesh as-is
- block visibly broken low-poly trees and lamps from being selected first
- keep fallback behavior so scene generation still succeeds when a category has only weak assets

## Manifest Fields

The runtime now reads these optional manifest fields from `data/real/real_assets_manifest.jsonl`:

- `scene_eligible`: whether the asset can be used directly in the scene candidate set
- `quality_tier`: `0-3` scene quality rank
- `mesh_face_count`: cached mesh complexity signal
- `quality_notes`: human-readable cleaning notes

## Runtime Behavior

`scene_ready_first` is now the default `asset_curation_mode`.

Selection order is:

1. prefer `scene_eligible=true` candidates
2. rank by retrieval score plus scene curation score
3. for `bench` and `lamp`, allow `parametric_first`, but only when the parametric asset is also scene-ready
4. if all candidates are ineligible, fall back instead of failing the category

## Cleaning Script

Use the cleaner to refresh manifest metadata after adding or replacing assets:

```bash
.venv/bin/python scripts/m3_04_clean_asset_manifest.py --manifest data/real/real_assets_manifest.jsonl --write
```

The cleaner:

- computes `mesh_face_count`
- assigns `quality_tier`
- flags `scene_eligible`
- writes `quality_notes`

## Current Focus

The strictest blocking is currently applied to `lamp` and `tree`, because those two categories had the most visible quality issues in exported scenes.
