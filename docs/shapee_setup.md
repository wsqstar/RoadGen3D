# Shape-E Setup Guide (M2)

This guide describes how to enable the `shapee` decoder mode in RoadGen3D.

## 1. Runtime Prerequisites

- Python: 3.11 or 3.12
- GPU optional (CPU supported for smoke tests)
- Blender: 3.3+ (required for official mesh-to-latent encoding workflows)

## 2. Install Dependencies

Base dependencies:

```bash
/Users/shiqi/.local/bin/uv pip install --python .venv/bin/python -r requirements-m1.txt
/Users/shiqi/.local/bin/uv pip install --python .venv/bin/python -r requirements-m2.txt
```

Install Shape-E package into the same environment (example):

```bash
.venv/bin/python -m pip install git+https://github.com/openai/shap-e.git
```

## 3. Model/Weight Location

Recommended local path:

- `/Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/shapee/`

If your Shape-E environment requires extra checkpoints/configs, place them under
this directory and pass it to scripts via `--shapee-model-dir`.

## 4. Blender Configuration

Install Blender 3.3+ and ensure the executable is on PATH, or pass an explicit
binary path when running custom encoding wrappers.

Quick check:

```bash
blender --version
```

## 5. End-to-End Commands

### 5.1 Build/refresh real index

```bash
.venv/bin/python scripts/m2_12_build_real_index.py \\
  --manifest data/real/real_assets_manifest.jsonl \\
  --artifacts artifacts/real \\
  --model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32 \\
  --local-files-only
```

### 5.2 Run pipeline with Shape-E decoder (fallback enabled)

```bash
.venv/bin/python scripts/m1_06_run_pipeline.py \\
  --query \"a wooden park bench\" \\
  --artifacts artifacts/real \\
  --assets artifacts/real/real_assets_for_pipeline.jsonl \\
  --model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/clip-vit-base-patch32 \\
  --local-files-only \\
  --decoder shapee \\
  --shapee-model-dir /Users/shiqi/Coding/github/GIStudio/RoadGen3D/models/shapee
```

If Shape-E decode fails, the pipeline falls back to placeholder decoder unless
`--shapee-strict` is provided.

## 6. Common Failure Modes

- `Shape-E runtime unavailable`: install `shap-e` package in `.venv`.
- `mesh_path does not exist`: verify manifest absolute/relative paths.
- OpenMP crash on macOS: set `KMP_DUPLICATE_LIB_OK=TRUE` before launch.
- `.bin` loading blocked: use `torch>=2.6` (already required in `requirements-m1.txt`).
