# M4 Learning and Evaluation Specification

This document defines the M4 extension on top of M3:
- learnable slot-level placement policy (`rule` distilled -> `learned`)
- engineering evaluation loop (JSON + CSV reports)

## 1. Scope

M4 keeps existing M1/M2/M3 behavior and adds optional components:
- `scripts/m4_01_collect_policy_data.py`: collect slot decision data from current rule policy
- `scripts/m4_02_train_layout_policy.py`: train a lightweight MLP policy
- `scripts/m4_10_eval_engineering.py`: benchmark `rule`/`learned` using stable engineering metrics

Out of scope in M4:
- VLM online scoring
- end-to-end 3D generator training
- advanced road network generation

## 2. Data Schema

### 2.1 Distilled policy dataset (`policy_train.jsonl`)

Each line stores one slot decision:

```json
{
  "scene_id": "q000_s0000",
  "query": "modern clean urban street",
  "seed": 0,
  "category": "bench",
  "slot_idx": 0,
  "slot_x": -39.5,
  "slot_z": 5.25,
  "road_params": {
    "length_m": 80.0,
    "road_width_m": 8.0,
    "sidewalk_width_m": 2.5,
    "lane_count": 2,
    "density": 1.0
  },
  "candidate_asset_ids": ["bench_01", "bench_03"],
  "candidate_scores": [0.91, 0.74],
  "candidate_categories": ["bench", "bench"],
  "chosen_asset_id": "bench_01",
  "chosen_index": 0,
  "chosen_source": "faiss_softmax",
  "used_asset_ids_before_slot": ["bench_02"],
  "dropped": false
}
```

## 3. Learned Policy

### 3.1 Feature extraction

`src/roadgen3d/layout_features.py` builds fixed 32-d candidate features:
- slot geometry and road params
- candidate retrieval score/rank
- usage flag (`used_asset_ids_before_slot`)
- periodic slot signals
- stable hash features (query/category/asset)
- category one-hot

### 3.2 Model

`src/roadgen3d/layout_policy.py`:
- MLP: `32 -> 64 -> 32 -> 1`
- ReLU activations, dropout `0.1`
- per-slot candidate scoring; slot softmax for sampling

### 3.3 Loss

- Primary: slot-level cross entropy over candidates
- Auxiliary: entropy regularization (`0.01`) to reduce collapse

### 3.4 Training defaults

- `epochs=20`
- `batch_size=256`
- `lr=1e-3`
- `weight_decay=1e-4`
- `patience=3` early stopping
- train/val split by `scene_id` hash (`90/10`)

### 3.5 Artifacts

- `artifacts/m4/layout_policy.pt`
- `artifacts/m4/layout_policy_meta.json`
- `artifacts/m4/train_curve.json`
- `artifacts/m4/train_summary.json`

## 4. Inference Integration

`compose_street_scene(...)` supports:
- `placement_policy="rule"|"learned"`
- `policy_ckpt=<path>`
- `policy_temperature=0.12`

If `learned` checkpoint load fails, pipeline falls back to `rule` and records reason in outputs.

`selection_source` now includes:
- `policy_softmax`
- `policy_relaxed_repeat`
- `faiss_softmax`
- `faiss_relaxed_repeat`
- `fallback_pool`

## 5. Engineering Evaluation

`scripts/m4_10_eval_engineering.py` outputs:
- `artifacts/m4/eval_report.json`
- `artifacts/m4/eval_per_scene.csv`

Metrics:
- `instance_count`
- `diversity_ratio`
- `dropped_slot_rate = dropped_slots / (instance_count + dropped_slots)`
- `overlap_rate` (pairwise AABB overlap ratio)
- `retrieval_top3_category_hit`
- `latency_ms_total`
- `latency_ms_per_instance`

When mode is `learned`, report includes optional rule baseline and deltas (`comparison_vs_rule`).

## 6. Runbook

### 6.1 Collect distilled data

```bash
.venv/bin/python scripts/m4_01_collect_policy_data.py \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out artifacts/m4/policy_train.jsonl \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only
```

### 6.2 Train policy

```bash
.venv/bin/python scripts/m4_02_train_layout_policy.py \
  --data artifacts/m4/policy_train.jsonl \
  --out-dir artifacts/m4 \
  --device cpu
```

### 6.3 Compose with learned policy

```bash
.venv/bin/python scripts/m3_01_compose_street.py \
  --query "modern clean urban street" \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out-dir artifacts/real \
  --placement-policy learned \
  --policy-ckpt artifacts/m4/layout_policy.pt \
  --policy-temperature 0.12 \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only
```

### 6.4 Evaluate engineering metrics

```bash
.venv/bin/python scripts/m4_10_eval_engineering.py \
  --queries data/eval/queries_m4.txt \
  --manifest data/real/real_assets_manifest.jsonl \
  --artifacts artifacts/real \
  --out-dir artifacts/m4 \
  --placement-policy learned \
  --policy-ckpt artifacts/m4/layout_policy.pt \
  --compare-rule \
  --model-dir models/clip-vit-base-patch32 \
  --local-files-only
```

## 7. Failure and Fallback

- Missing/invalid `policy_ckpt`: fallback to `rule` with readable message.
- Empty category pool or invalid manifest: fail fast with explicit category/path diagnostics.
- Evaluation script reports partial failures via non-zero exit and stderr.

## 8. VLM Next-Step Interface (Not Implemented)

M4 keeps room for semantic scoring:
- future input: `{query, scene_layout.json, scene preview renders}`
- future output: `vlm_alignment_score in [0,1]`
- integration point: append into `eval_report.json` as additional metric block

No VLM dependency is required in M4.
