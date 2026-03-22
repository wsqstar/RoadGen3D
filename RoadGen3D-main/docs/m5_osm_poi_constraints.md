# M5: OSM Road Placement Zones + POI Constraint Engine + Compliance Evaluation

## Overview

M5 adds real-world OSM road geometry support, POI-aware constraint scoring, and compliance metrics to the street scene composition pipeline. It is fully backward-compatible with existing M3/M4 workflows.

## New Dependencies

```bash
pip install -r requirements-m5.txt
# shapely>=2.0, pyproj>=3.6, requests>=2.28
```

## CLI Workflow

### 1. Fetch OSM data

```bash
python scripts/m5_01_fetch_osm.py --bbox 116.39 39.90 116.40 39.91
```

Results are cached in `artifacts/m5/osm_cache/`. Pass `--force-refetch` to re-download.

### 2. Build placement zones (optional, for debugging)

```bash
python scripts/m5_02_build_placement_zones.py --bbox 116.39 39.90 116.40 39.91
```

Exports `artifacts/m5/zones.geojson` for visual inspection.

### 3. Compose street scene with OSM + constraints

```bash
python scripts/m3_01_compose_street.py \
    --query "urban residential" \
    --layout-mode osm \
    --constraint-mode soft \
    --aoi-bbox 116.39 39.90 116.40 39.91
```

**New CLI arguments** (all have defaults, non-breaking):

| Flag | Default | Description |
|---|---|---|
| `--layout-mode` | `template` | `template` (straight road) or `osm` (real geometry) |
| `--constraint-mode` | `soft` | `off` or `soft` (POI penalty scoring) |
| `--aoi-bbox` | None | `MIN_LON MIN_LAT MAX_LON MAX_LAT` (required for osm mode) |
| `--osm-cache-dir` | `artifacts/m5/osm_cache` | Cache directory for Overpass data |
| `--constraint-weight` | `0.45` | Weight of feasibility in utility scoring |
| `--constraint-veto-threshold` | `0.95` | Penalty above this triggers veto |
| `--poi-rule-set` | `entrance_fire_bus_stop_v1` | Rule set name |

### 4. Evaluate compliance

```bash
python scripts/m5_10_eval_compliance.py --scene-dir artifacts/m4/eval_scenes/rule
```

Outputs `artifacts/m5/compliance_report.json` and `compliance_per_scene.csv`.

## POI Rule Set: `entrance_fire_bus_stop_v1`

Three soft-constraint rules:

| Rule | POI Type | Sigma (m) | Effect |
|---|---|---|---|
| `entrance_clearance` | Building entrance | 2.5 | Penalises furniture near doorways |
| `fire_access` | Fire hydrant | 3.0 | Keeps clearance around hydrants |
| `bus_stop_clearance` | Bus stop | 4.0 | Keeps bus stop areas accessible |

Each rule applies a category-specific penalty weight. The scoring formula:

```
penalty_r = w_cat * exp(-distance / sigma)
total_penalty = sum(penalty_r)
feasibility = exp(-total_penalty)
utility = (1 - w) * retrieval_score + w * feasibility
```

## Compliance Report Fields

In `scene_layout.json` summary:

- `layout_mode` / `constraint_mode` - traceability
- `compliance_rate_total` - fraction of placements with no violated rules
- `violations_total` - count of placements that violated at least one rule
- `rule_violation_counts` - per-rule violation counts
- `avg_constraint_penalty` / `avg_feasibility_score` - averages across placements

Each placement additionally contains:
- `constraint_penalty` - total penalty for this instance
- `feasibility_score` - exp(-penalty)
- `violated_rules` - list of rule names that were violated

## Backward Compatibility

- `layout_mode=template` + `constraint_mode=off` produces identical output to M4
- All new `StreetComposeConfig` and `StreetPlacement` fields have defaults
- Existing tests pass without modification
