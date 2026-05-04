# RoadGen3D Automated 3D Scene Generation & Evaluation Analysis

## Context

Analysis of the RoadGen3D project to understand its automated 3D scene generation and evaluation capabilities. This is a research/analysis task to document the current implementation state.

---

## Analysis Summary

### 1. Automated Pipeline - YES (Partially Implemented)

**Does the project have an automated pipeline?**

**YES** - The project has an auto-pipeline that can generate 3D scenes with minimal manual intervention:

#### Auto Pipeline Components:
| Component | File | Description |
|-----------|------|-------------|
| Iteration Controller | `src/roadgen3d/auto_pipeline/iteration_controller.py` | Orchestrates generate → render → evaluate → improve loop |
| Graph Loader | `src/roadgen3d/auto_pipeline/graph_loader.py` | Parses Viewer graph export → scene overrides |
| Scene Renderer | `src/roadgen3d/auto_pipeline/scene_renderer.py` | Generates top-down preview |
| Design Workflow | `src/roadgen3d/llm/design_workflow.py` | LLM + RAG for config generation |

#### Pipeline Flow:
```
User Intent → LLM Intent Parser → RAG Query → Evidence Retrieval →
Config Patch Generation → Street Compose → Layout Solver → Asset Placement →
Mesh Export → Top-down Render → LLM Evaluation → Score + Suggestions →
Iterate (if score improved) → Final Scene
```

#### CLI Entry Point:
```bash
python scripts/auto_scene_pipeline.py --graph graph.json --base-map base_map.png
```

---

### 2. Evaluation Mechanisms - YES (Comprehensive)

**Are there evaluation mechanisms?**

**YES** - Multiple evaluation systems in place:

#### a) Engineering Metrics (`eval_metrics.py`):
- `compute_overlap_rate()` - AABB collision detection
- `compute_dropped_slot_rate()` - Failed placement tracking
- `compute_spacing_uniformity()` - Distribution uniformity (CV)
- `compute_style_consistency()` - CLIP score
- `compute_balance_score()` - Left/right symmetry
- `compute_rule_satisfaction_rate()` - Design rule compliance
- `compute_topology_validity()` - Network topology
- `compute_cross_section_feasibility()` - Cross-section validation

#### b) LLM-based Evaluation (`design_workflow.py` + `prompts.py`):
- Vision-enabled scene evaluation (accepts image preview)
- Outputs: score (0-10), suggestions, config_patch
- Evaluates: visual aesthetics, spatial layout, diversity, compliance, pedestrian-friendliness

#### c) POI Constraint Rules (`poi_rules.py`):
- `entrance_clearance()` - 4m entrance opening (60% angular)
- `fire_access()` - Emergency vehicle clearance
- `bus_stop_clearance()` - Bus stop clearance zones
- `crossing_keep_clear()` - Crossing sight lines
- `traffic_signal_visibility()` - Traffic signal visibility
- `parking_entrance_clearance()` - Parking entrance clearance

#### d) Design Rules Profiles (`design_rules.py`):
- `BALANCED_RULES` - Balanced complete street profile
- `PEDESTRIAN_RULES` - Pedestrian priority profile
- Rule modes: "hard" (must satisfy) vs "soft" (penalized)

#### e) Compliance Evaluation (`compliance_eval.py`):
- Per-scene compliance scoring
- Batch compliance evaluation
- Tracks violations and constraint penalties

---

### 3. Iterative Refinement - YES (Auto-Pipeline)

**Is there an iterative process?**

**YES** - The `AutoIterationController` implements automated refinement:

```python
# iteration_controller.py
MAX_ITERATIONS = 5
EARLY_STOP_THRESHOLD = 2  # Consecutive rounds without improvement

def run(self):
    for iteration in range(self.max_iterations):
        # 1. Generate scene
        scene = compose_street_scene(config_patch)

        # 2. Render preview
        preview = render_topdown_preview(scene)

        # 3. LLM evaluate
        result = llm_evaluate(scene, preview)

        # 4. Check improvement
        if result.score > self.best_score:
            self.best_snapshot = snapshot
            self.config_patch = result.config_patch
        else:
            self.consecutive_no_improve += 1

        # 5. Early stop check
        if self.consecutive_no_improve >= EARLY_STOP_THRESHOLD:
            break
```

---

### 4. Key Automation Components

#### LLM Integration (`llm/` directory):
| File | Purpose |
|------|---------|
| `glm_client.py` | OpenAI-compatible LLM client with retry logic |
| `prompts.py` | Prompt templates for intent parsing, RAG, evaluation |
| `design_workflow.py` | DesignAssistantService - orchestrates LLM + RAG |

#### Scene Generation Engines:
| File | Purpose |
|------|---------|
| `street_layout.py` | Main scene composition (template/OSM/MetaUrban) |
| `street_program.py` | Declarative street representation |
| `layout_solver.py` | MILP-based constraint solving |
| `layout_policy.py` | MLP-based learnable asset selection |
| `placement_field.py` | Energy-based spatial optimization |

#### Evaluation Systems:
| File | Purpose |
|------|---------|
| `eval_metrics.py` | Engineering metrics (overlap, spacing, etc.) |
| `compliance_eval.py` | Design rule compliance scoring |
| `poi_rules.py` | POI-specific constraint rules |
| `design_rules.py` | Declarative design rule profiles |

---

### 5. Complete Workflow

#### Full Pipeline from Intent to 3D Scene:

```
┌─────────────────────────────────────────────────────────────────┐
│ STEP 1: User Intent (Natural Language)                          │
│ Example: "高密度城市核心区，混合功能街道，行人流量大"             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 2: LLM Intent Parsing (design_workflow.py)                 │
│ - Parses goals, style, safety priorities                        │
│ - Identifies follow-up questions                                │
│ - Generates RAG search queries                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 3: RAG Evidence Retrieval                                  │
│ - Translates Chinese to English                                  │
│ - Searches Complete Streets PDF guide                           │
│ - Supports GraphRAG, PDF RAG, or Hybrid                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 4: Config Patch Generation (LLM + RAG)                    │
│ Outputs: compose_config_patch with street parameters            │
│ - road_width_m, sidewalk_width_m, lane_count                    │
│ - design_rule_profile, style_preset                             │
│ - citations_by_field (evidence references)                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 5: Street Program Inference (street_program.py)            │
│ - Generates declarative StreetProgram from config              │
│ - Resolves constraints and defaults                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 6: Layout Solver (layout_solver.py)                        │
│ - MILP-based constraint solving                                  │
│ - Collision detection and avoidance                             │
│ - Band and slot placement                                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 7: Asset Placement (placement_field.py)                    │
│ - Energy-based spatial optimization                              │
│ - POI attraction scoring                                         │
│ - Pairwise interaction scoring                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 8: 3D Mesh Export                                          │
│ - Exports to GLB/PLY format                                      │
│ - Texture application                                           │
│ - Viewer integration                                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 9: Top-down Preview Render (scene_renderer.py)             │
│ - Matplotlib-based preview generation                            │
│ - Used for LLM evaluation                                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 10: LLM Evaluation (design_workflow.py)                    │
│ - Vision-enabled scene assessment                                │
│ - Outputs: score (0-10), suggestions, config_patch              │
│ - Evaluates: aesthetics, layout, diversity, compliance        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 11: Iteration (AutoIterationController)                    │
│ - If score improved → apply patch → loop back to Step 5         │
│ - If no improvement (x2) → early stop                            │
│ - Keep best result in final/                                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ OUTPUT: Final 3D Scene                                          │
│ - scene.glb - 3D mesh file                                      │
│ - scene_layout.json - Scene metadata                            │
│ - preview.png - Top-down visualization                          │
│ - eval_report.json - Evaluation scores                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Findings

### ✅ Strengths

1. **Comprehensive Automation Pipeline**: Complete end-to-end pipeline from natural language intent to 3D scene
2. **Multi-level Evaluation**: Engineering metrics + LLM vision evaluation + rule compliance
3. **Iterative Refinement**: Auto-pipeline with early stopping and best-score tracking
4. **Evidence-based Generation**: RAG integration ensures designs are grounded in design guidelines
5. **Flexible Layout Modes**: Template, OSM, MetaUrban, Graph Template support
6. **Declarative Design Rules**: Hard/soft constraints with penalty scoring

### ⚠️ Current Limitations

1. **Heuristic Fallback**: StreetProgram uses `heuristic_v1`, not learned generator
2. **Banded Layout Solver**: Uses heuristic, not MILP optimization
3. **Scoped street networks**: Course delivery supports `graph_template` cross junctions; open-ended arbitrary street networks are still out of scope
4. **Single-modal Retrieval**: CLIP text-only, no cross-modal training (OpenShape/ULIP)
5. **LLM Dependency**: Requires API access for automated generation

---

## Verification

To verify the automation capabilities:

```bash
# 1. Check auto-pipeline files exist
ls -la src/roadgen3d/auto_pipeline/

# 2. Run auto evaluation script
python scripts/run_auto_eval.py

# 3. Run manual auto pipeline
python scripts/auto_scene_pipeline.py --graph data/samples/sample_graph.json

# 4. Check evaluation metrics
python -c "from src.roadgen3d.eval_metrics import *; print('Engineering metrics available')"

# 5. Check LLM evaluation prompt
python -c "from src.roadgen3d.llm.prompts import build_scene_evaluation_messages; print('LLM evaluation prompt available')"
```
