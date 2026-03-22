# Objaverse First-Wave RoadGen3D Cache

This note records the first Objaverse import pass for RoadGen3D street-scene assets.

## Source

- Objaverse docs: https://objaverse.allenai.org/docs/intro
- Import script: `scripts/m2_14_import_objaverse.py`
- Import module: `src/roadgen3d/objaverse_import.py`

## Current recommendation

The strongest first-wave Objaverse categories for this project are:

- `bench`
- `lamp`
- `trash`
- `mailbox`

These categories map cleanly onto the current RoadGen3D street furniture inventory and can be filtered from Objaverse 1.0 LVIS metadata with relatively low ambiguity.

Not recommended as first-wave default imports yet:

- `tree`
  Requires stronger botanical quality and upright validation than LVIS metadata alone can guarantee.
- `building`
  Building usage in this project is parcel- and frontage-aware; generic Objaverse buildings need a more careful fit filter.
- `bus_stop`
  No strong direct LVIS category signal in the current Objaverse 1.0 path.
- `hydrant`
  No strong direct LVIS category signal in the current Objaverse 1.0 path.
- `bollard`
  Current LVIS-alias proxies are too weak and can confuse signs/signals with true bollards.

## Cached outputs

- Cached GLBs root: `artifacts/objaverse_cache/hf-objaverse-v1/glbs`
- Manifest: `data/real/objaverse_assets_manifest.jsonl`
- Selection report: `artifacts/objaverse_cache/roadgen3d_objaverse_selection_report.json`

## Imported assets

### Bench

1. `objaverse_bench_a606688f1ef74049b6a23d759721db63`
   - Name: `Bench low poly`
   - Faces: `3432`
   - License: `by`
   - Cache: `artifacts/objaverse_cache/hf-objaverse-v1/glbs/000-121/a606688f1ef74049b6a23d759721db63.glb`
2. `objaverse_bench_a8a26f771d5845f092e71371a6e30566`
   - Name: `Basic Park Bench`
   - Faces: `2336`
   - License: `by-nc`
   - Cache: `artifacts/objaverse_cache/hf-objaverse-v1/glbs/000-132/a8a26f771d5845f092e71371a6e30566.glb`

### Lamp

1. `objaverse_lamp_f235e5b7a96b46489db4dce642742049`
   - Name: `Lampposts (Road & Park)`
   - Faces: `5541`
   - License: `by`
   - Cache: `artifacts/objaverse_cache/hf-objaverse-v1/glbs/000-131/f235e5b7a96b46489db4dce642742049.glb`
2. `objaverse_lamp_04a47d898e704e1a809d24433c409bf5`
   - Name: `Street Light`
   - Faces: `2274`
   - License: `by`
   - Cache: `artifacts/objaverse_cache/hf-objaverse-v1/glbs/000-000/04a47d898e704e1a809d24433c409bf5.glb`

### Trash

1. `objaverse_trash_f16b7d84113d4cba869412ee95769910`
   - Name: `Realism Study; Garbage Can`
   - Faces: `8716`
   - License: `by`
   - Cache: `artifacts/objaverse_cache/hf-objaverse-v1/glbs/000-078/f16b7d84113d4cba869412ee95769910.glb`
2. `objaverse_trash_d70e1d9cbef24eabbaea329896ea08a7`
   - Name: `Garbage Bin`
   - Faces: `1994`
   - License: `by`
   - Cache: `artifacts/objaverse_cache/hf-objaverse-v1/glbs/000-117/d70e1d9cbef24eabbaea329896ea08a7.glb`

### Mailbox

1. `objaverse_mailbox_582c0ce1ffee41179ab981146ae0ff87`
   - Name: `Game Ready Animated Mailbox / Postbox`
   - Faces: `3760`
   - License: `by`
   - Cache: `artifacts/objaverse_cache/hf-objaverse-v1/glbs/000-066/582c0ce1ffee41179ab981146ae0ff87.glb`
2. `objaverse_mailbox_37f7c6b207da4b669ef7a5d6e7dfd2c0`
   - Name: `Low Poly Mailbox`
   - Faces: `3220`
   - License: `by`
   - Cache: `artifacts/objaverse_cache/hf-objaverse-v1/glbs/000-074/37f7c6b207da4b669ef7a5d6e7dfd2c0.glb`

## Notes

- This pass uses Objaverse 1.0 metadata plus RoadGen-specific heuristics, not a full mesh-semantic classifier.
- The current importer prefers categories that already exist in the RoadGen manifest schema and street layout runtime.
- Bench filtering was tightened to avoid obviously stylized `Disney` / `Minecraft` style assets.
- Mailbox filtering was tightened to avoid extremely low-face candidates.
