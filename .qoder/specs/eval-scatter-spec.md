# Eval Scatter Plot Spec

## Concept & Vision

An interactive evaluation scatter plot tool for comparing street design metrics across multiple runs. Users can select 1 or 2 metrics to visualize - a single metric shows score distribution histogram, while two metrics show a scatter plot with optional regression lines and Pareto frontier.

## Design Language

- **Aesthetic**: Clean scientific visualization, inspired by Observable notebooks
- **Colors**:
  - Primary: `#2c3e50` (dark blue-gray for text/axes)
  - Accent: `#3498db` (blue for points)
  - Highlight: `#e74c3c` (red for outliers/best)
  - Pareto: `#27ae60` (green for frontier points)
- **Typography**: System monospace for data, sans-serif for labels
- **Spacing**: Generous padding for readability

## Features

### 1. Metric Selection
- Dropdown for X-axis metric (required)
- Dropdown for Y-axis metric (optional - if empty, show histogram)
- Available metrics from eval results:
  - walkability_index, safety_score, beauty_score, evaluation_score
  - 11 walkability indicators (SID_CLR, CLEAR_CONT, etc.)
  - safety features (LIGHT_UNI, CROSS_PROV, etc.)
  - beauty features (presentation_score, active_front_ratio, etc.)
  - engineering metrics (spacing_uniformity, style_consistency, balance_score)

### 2. Data Loading
- Load from CSV files (layout_eval output: `artifacts/m4/eval_per_scene.csv`)
- Support multiple CSV files for comparison (e.g., rule vs learned)
- Group column for distinguishing sources (auto-detect or manual)

### 3. Visualization Modes

#### Single Metric (Histogram)
- Score distribution with configurable bins (default: 20)
- Mean/median/percentile lines
- Hover to see count details

#### Two Metrics (Scatter)
- Scatter points colored by source/group
- Optional regression line
- Optional Pareto frontier highlighting
- Hover to see scene_id and all metrics

### 4. Export
- Save as PNG
- Save as interactive HTML (plotly)

## Technical Approach

- **Backend**: Python function `scripts/eval_scatter.py`
- **Visualization**: matplotlib (with plotly export option)
- **Input**: CSV files from `scripts/layout_eval.py`
- **Output**: PNG/HTML files + optional console table

## API / CLI

```bash
python scripts/eval_scatter.py \
    --input artifacts/m4/rule/eval_per_scene.csv \
            artifacts/m4/learned/eval_per_scene.csv \
    --x walkability_index \
    --y safety_score \
    --group-by policy_used \
    --output artifacts/scatter_walkability_vs_safety.png \
    --show-pareto
```

## Component Inventory

### CLI Arguments
| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| --input | path[] | required | CSV file(s) to load |
| --x | str | required | X-axis metric |
| --y | str | optional | Y-axis metric (omit for histogram) |
| --group-by | str | optional | Column to group colors by |
| --bins | int | 20 | Histogram bins (single metric only) |
| --show-regression | bool | false | Show regression line |
| --show-pareto | bool | false | Highlight Pareto frontier |
| --output | path | auto | Output file path |
| --format | png\|html | png | Output format |
| --title | str | auto | Chart title |

### Output Files
- `eval_scatter_{x}_{y}.png` - Static image
- `eval_scatter_{x}_{y}.html` - Interactive HTML (optional)
