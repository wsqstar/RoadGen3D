import os

pages_dir = "/Users/shiqi/Coding/github/GIStudio/RoadGen3D/slides/roadgen3d_clean_2026-07-22/pages"

def write_page(filename, content):
    with open(os.path.join(pages_dir, filename), "w") as f:
        f.write(content)

# ============================================================
# Page 03: Problem / Pain points
# ============================================================
write_page("03_problem.page", """pageType: content
background:
  type: solid
  color: "#FFFFFF"
elements:
  - elementId: sec-num
    elementType: text
    bounds: [60, 36, 60, 30]
    content:
      fontSize: 14
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "02"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: 传统方法的四个瓶颈

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # Four items stacked vertically
  - elementId: item1-num
    elementType: text
    bounds: [60, 150, 50, 36]
    content:
      fontSize: 28
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "01"

  - elementId: item1
    elementType: text
    bounds: [120, 150, 780, 70]
    content:
      style: "$body"
      align: [left, top]
      text: |
        <p><strong>建模效率低</strong></p>
        <p><span style="font-size:14px; color:$muted;">手工 3D 建模一座街区需要数周，难以快速迭代多种方案。</span></p>

  - elementId: divider1
    elementType: line
    bounds: [60, 230, 840, 1]
    viewBox: [840, 1]
    points: "0,0 840,0"
    border: {style: solid, width: 1, color: "$border"}

  - elementId: item2-num
    elementType: text
    bounds: [60, 250, 50, 36]
    content:
      fontSize: 28
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "02"

  - elementId: item2
    elementType: text
    bounds: [120, 250, 780, 70]
    content:
      style: "$body"
      align: [left, top]
      text: |
        <p><strong>方案探索受限</strong></p>
        <p><span style="font-size:14px; color:$muted;">设计师只能尝试少数几种布局，无法系统性地覆盖参数空间。</span></p>

  - elementId: divider2
    elementType: line
    bounds: [60, 330, 840, 1]
    viewBox: [840, 1]
    points: "0,0 840,0"
    border: {style: solid, width: 1, color: "$border"}

  - elementId: item3-num
    elementType: text
    bounds: [60, 350, 50, 36]
    content:
      fontSize: 28
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "03"

  - elementId: item3
    elementType: text
    bounds: [120, 350, 780, 70]
    content:
      style: "$body"
      align: [left, top]
      text: |
        <p><strong>评价缺乏量化标准</strong></p>
        <p><span style="font-size:14px; color:$muted;">"好看"、"舒服"难以度量，不同评审标准不一。</span></p>

  - elementId: divider3
    elementType: line
    bounds: [60, 430, 840, 1]
    viewBox: [840, 1]
    points: "0,0 840,0"
    border: {style: solid, width: 1, color: "$border"}

  - elementId: item4-num
    elementType: text
    bounds: [60, 450, 50, 36]
    content:
      fontSize: 28
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "04"

  - elementId: item4
    elementType: text
    bounds: [120, 450, 780, 60]
    content:
      style: "$body"
      align: [left, top]
      text: |
        <p><strong>2D 到 3D 断层</strong></p>
        <p><span style="font-size:14px; color:$muted;">平面图与三维场景脱节，设计意图难以保真落地。</span></p>

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "03"
""")

# ============================================================
# Page 04: What is RoadGen3D
# ============================================================
write_page("04_what_is.page", """pageType: content
background:
  type: solid
  color: "#FFFFFF"
elements:
  - elementId: sec-num
    elementType: text
    bounds: [60, 36, 60, 30]
    content:
      fontSize: 14
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "03"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: RoadGen3D 是什么

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # One-sentence definition
  - elementId: definition
    elementType: text
    bounds: [60, 150, 840, 60]
    content:
      fontSize: 20
      color: "$text"
      lineHeight: 1.5
      align: [left, top]
      text: |
        <p>一个<strong><span style="color:$primary;">规则/约束驱动、AI 辅助</span></strong>的 3D 街道场景生成与评价框架。</p>

  # Three keywords
  - elementId: kw1-bg
    elementType: shape
    bounds: [60, 240, 260, 100]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: kw1
    elementType: text
    bounds: [80, 250, 220, 80]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:24px; color:$primary;"><strong>可解释</strong></span></p>
        <p style="margin-top:6px"><span style="font-size:14px; color:$muted;">生成过程透明，规则可追溯</span></p>

  - elementId: kw2-bg
    elementType: shape
    bounds: [350, 240, 260, 100]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: kw2
    elementType: text
    bounds: [370, 250, 220, 80]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:24px; color:$primary;"><strong>可评价</strong></span></p>
        <p style="margin-top:6px"><span style="font-size:14px; color:$muted;">多维度量化评分，有据可依</span></p>

  - elementId: kw3-bg
    elementType: shape
    bounds: [640, 240, 260, 100]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: kw3
    elementType: text
    bounds: [660, 250, 220, 80]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:24px; color:$primary;"><strong>可迭代</strong></span></p>
        <p style="margin-top:6px"><span style="font-size:14px; color:$muted;">评分反馈驱动持续优化</span></p>

  # Bottom note
  - elementId: note
    elementType: text
    bounds: [60, 380, 840, 80]
    content:
      style: "$body"
      align: [left, top]
      text: |
        <p>核心思想：不直接从输入跳到 3D 网格，而是经过<strong>显式中间表示</strong> —</p>
        <p>StreetProgram → ConstraintSet → LayoutSolver → scene_layout.json → scene.glb</p>

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "04"
""")

# ============================================================
# Page 05: Architecture
# ============================================================
write_page("05_architecture.page", """pageType: content
background:
  type: solid
  color: "#FFFFFF"
elements:
  - elementId: sec-num
    elementType: text
    bounds: [60, 36, 60, 30]
    content:
      fontSize: 14
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "04"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: 四层架构

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # Table
  - elementId: arch-table
    elementType: table
    bounds: [60, 150, 840, 280]
    columnWidths: [0.22, 0.35, 0.43]
    rowHeights: [0.18, 0.205, 0.205, 0.205, 0.205]
    style: "$default"
    rows:
      - - text: "层级"
        - text: "职责"
        - text: "关键技术"
      - - text: "Web 交互层"
        - text: "用户交互、3D 可视化"
        - text: "Three.js, Vite, TypeScript"
      - - text: "API 层"
        - text: "业务逻辑编排、任务队列"
        - text: "FastAPI, Pydantic"
      - - text: "引擎层"
        - text: "场景生成、约束求解、布局优化"
        - text: "Python, NumPy, CLIP, PuLP"
      - - text: "评估层"
        - text: "多维度质量评估"
        - text: "road-metrics (Submodule)"

  - elementId: note
    elementType: text
    bounds: [60, 450, 840, 40]
    content:
      fontSize: 14
      color: "$muted"
      align: [left, top]
      text: |
        <p>评估层 road-metrics 为独立 Git Submodule，可单独安装使用。</p>

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "05"
""")

print("Created pages 03-05")
