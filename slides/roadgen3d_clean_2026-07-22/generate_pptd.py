import os

output_dir = "/Users/shiqi/Coding/github/GIStudio/RoadGen3D/slides/roadgen3d_clean_2026-07-22"
pages_dir = os.path.join(output_dir, "pages")

# ============================================================
# 1. Main .pptd file
# ============================================================
pptd_content = """version: v2
title: RoadGen3D
size: [960, 540]
theme:
  colors:
    primary: "#00539F"
    accent: "#FFD100"
    text: "#1F2937"
    muted: "#6B7280"
    light: "#F3F4F6"
    border: "#E5E7EB"
  textStyles:
    title:
      fontSize: 36
      color: "$text"
      bold: true
      lineHeight: 1.2
    subtitle:
      fontSize: 20
      color: "$muted"
      lineHeight: 1.4
    body:
      fontSize: 16
      color: "$text"
      lineHeight: 1.5
    caption:
      fontSize: 12
      color: "$muted"
      lineHeight: 1.4
    coverTitle:
      fontSize: 52
      color: "$primary"
      bold: true
      lineHeight: 1.1
    coverSubtitle:
      fontSize: 22
      color: "$text"
      lineHeight: 1.4
    sectionTitle:
      fontSize: 40
      color: "$primary"
      bold: true
      lineHeight: 1.2
    highlight:
      fontSize: 18
      color: "$primary"
      bold: true
      lineHeight: 1.4
    number:
      fontSize: 48
      color: "$primary"
      bold: true
      lineHeight: 1.0
  tableStyles:
    default:
      cellStyle:
        fontSize: 14
        color: "$text"
        border: [null, null, {style: solid, width: 1, color: "$border"}, null]
        align: [left, middle]
      firstRowStyle:
        fontSize: 14
        color: "$primary"
        bold: true
        border: [null, null, {style: solid, width: 2, color: "$primary"}, null]
        align: [left, middle]
pages:
  - pages/01_cover.page
  - pages/02_background.page
  - pages/03_problem.page
  - pages/04_what_is.page
  - pages/05_architecture.page
  - pages/06_workflow.page
  - pages/07_ab_layers.page
  - pages/08_engine.page
  - pages/09_evaluation.page
  - pages/10_pareto.page
  - pages/11_osm.page
  - pages/12_viewer.page
  - pages/13_scenarios.page
  - pages/14_summary.page
  - pages/15_final.page
"""

with open(os.path.join(output_dir, "roadgen3d_clean.pptd"), "w") as f:
    f.write(pptd_content)

print("Created main .pptd file")
