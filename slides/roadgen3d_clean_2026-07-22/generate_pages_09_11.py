import os

pages_dir = "/Users/shiqi/Coding/github/GIStudio/RoadGen3D/slides/roadgen3d_clean_2026-07-22/pages"

def write_page(filename, content):
    with open(os.path.join(pages_dir, filename), "w") as f:
        f.write(content)

# ============================================================
# Page 09: Evaluation System
# ============================================================
write_page("09_evaluation.page", """pageType: content
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
      text: "08"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: 评估体系：如何评价一条街道？

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # Three dimensions as horizontal blocks
  - elementId: dim1-bar
    elementType: shape
    bounds: [60, 160, 8, 80]
    shapeName: rect
    fill: {type: solid, color: "$primary"}

  - elementId: dim1
    elementType: text
    bounds: [84, 160, 400, 80]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:20px; color:$primary;"><strong>Walkability 步行性</strong></span> <span style="font-size:14px; color:$muted;">权重 45%</span></p>
        <p style="margin-top:4px"><span style="font-size:14px; color:$text;">人行道宽度、无障碍设施、步行舒适度、树荫覆盖</span></p>

  - elementId: dim2-bar
    elementType: shape
    bounds: [60, 260, 8, 80]
    shapeName: rect
    fill: {type: solid, color: "$accent"}

  - elementId: dim2
    elementType: text
    bounds: [84, 260, 400, 80]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:20px; color:$primary;"><strong>Safety 安全性</strong></span> <span style="font-size:14px; color:$muted;">权重 35%</span></p>
        <p style="margin-top:4px"><span style="font-size:14px; color:$text;">交通隔离、照明设施、安全设施覆盖、交叉口设计</span></p>

  - elementId: dim3-bar
    elementType: shape
    bounds: [60, 360, 8, 80]
    shapeName: rect
    fill: {type: solid, color: "#9CA3AF"}

  - elementId: dim3
    elementType: text
    bounds: [84, 360, 400, 80]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:20px; color:$primary;"><strong>Beauty 美观性</strong></span> <span style="font-size:14px; color:$muted;">权重 20%</span></p>
        <p style="margin-top:4px"><span style="font-size:14px; color:$text;">植物配置协调性、街道家具搭配、整体视觉一致性</span></p>

  # Right side: radar description
  - elementId: radar-title
    elementType: text
    bounds: [560, 160, 340, 30]
    content:
      fontSize: 16
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "可视化输出"

  - elementId: radar-body
    elementType: text
    bounds: [560, 200, 340, 160]
    content:
      fontSize: 14
      color: "$text"
      lineHeight: 1.7
      align: [left, top]
      text: |
        <p>• 雷达图 — 三维度综合评分</p>
        <p>• 柱状图 — 各维度横向对比</p>
        <p>• 综合评分 — 加权总分 0-100</p>
        <p>• 详细指标 + 改进建议</p>

  # Independent module note
  - elementId: module-note
    elementType: text
    bounds: [560, 380, 340, 60]
    content:
      fontSize: 13
      color: "$muted"
      lineHeight: 1.5
      align: [left, top]
      text: |
        <p>评估引擎 road-metrics 为独立 Git Submodule，分层设计：</p>
        <p>提取器 → 基础指标 → 评分组合 → LLM 增强</p>

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "09"
""")

# ============================================================
# Page 10: Pareto Trace
# ============================================================
write_page("10_pareto.page", """pageType: content
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
      text: "09"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: Pareto Trace：批量探索最优解

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # Pipeline description
  - elementId: pipe
    elementType: text
    bounds: [60, 150, 840, 100]
    content:
      fontSize: 15
      color: "$text"
      lineHeight: 1.7
      align: [left, top]
      text: |
        <p><strong>Branch Run</strong> — 从 preset / prompt 出发，生成多个候选节点</p>
        <p><strong>100 Sample Trace</strong> — 持续扩展 frontier，最多保留 100 个已评分样本</p>
        <p><strong>Pareto Search</strong> — walkability / safety / beauty 三目标搜索，保留非支配解</p>

  # Artifact retention
  - elementId: retention-bg
    elementType: shape
    bounds: [60, 270, 400, 80]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: retention
    elementType: text
    bounds: [75, 280, 370, 60]
    content:
      fontSize: 14
      color: "$text"
      lineHeight: 1.5
      align: [left, top]
      text: |
        <p><strong>Artifact 保留策略</strong></p>
        <p>仅保留 top-k 评分方案的 GLB，其余存 scene_layout.json 可重建</p>

  # Correlation analysis
  - elementId: corr-bg
    elementType: shape
    bounds: [500, 270, 400, 80]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: corr
    elementType: text
    bounds: [515, 280, 370, 60]
    content:
      fontSize: 14
      color: "$text"
      lineHeight: 1.5
      align: [left, top]
      text: |
        <p><strong>相关性分析</strong></p>
        <p>参数 → 落地场景 → 评分，热力图 + 散点 + Feature Importance</p>

  # Three-layer data model
  - elementId: data-title
    elementType: text
    bounds: [60, 380, 400, 30]
    content:
      fontSize: 16
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "三层数据模型"

  - elementId: data-body
    elementType: text
    bounds: [60, 415, 840, 70]
    content:
      fontSize: 14
      color: "$text"
      lineHeight: 1.6
      align: [left, top]
      text: |
        <p><strong>输入层</strong> preset / prompt / config_patch  →  <strong>落地层</strong> scene_layout.json 参数抽取  →  <strong>结果层</strong> walkability / safety / beauty / overall</p>

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "10"
""")

# ============================================================
# Page 11: OSM Integration
# ============================================================
write_page("11_osm.page", """pageType: content
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
      text: "10"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: OSM 真实数据：从现实世界出发

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # OSM pipeline
  - elementId: osm-pipe
    elementType: text
    bounds: [60, 150, 840, 110]
    content:
      fontSize: 15
      color: "$text"
      lineHeight: 1.7
      align: [left, top]
      text: |
        <p><strong>OSM Ingest</strong> — Overpass API 获取道路、建筑、POI 数据</p>
        <p><strong>Road Discovery</strong> — 按 POI 密度、长度、相关性评分候选道路</p>
        <p><strong>POI-Driven Cross-Section</strong> — 根据附近 POI 动态调整人行道宽度</p>
        <p><strong>Semantic Classification</strong> — 自动标注学校/商业/住宅/公交等语义类型</p>

  # Two modes
  - elementId: mode-title
    elementType: text
    bounds: [60, 280, 400, 30]
    content:
      fontSize: 16
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "两种生成模式"

  - elementId: mode1
    elementType: text
    bounds: [60, 320, 420, 70]
    content:
      fontSize: 14
      color: "$text"
      lineHeight: 1.6
      align: [left, top]
      text: |
        <p><strong>osm</strong> — 单道路聚焦模式</p>
        <p><span style="font-size:13px; color:$muted;">从 AOI 中选一条 POI 丰富的道路生成场景</span></p>

  - elementId: mode2
    elementType: text
    bounds: [500, 320, 400, 70]
    content:
      fontSize: 14
      color: "$text"
      lineHeight: 1.6
      align: [left, top]
      text: |
        <p><strong>osm_multiblock</strong> — 多道路语义模式</p>
        <p><span style="font-size:13px; color:$muted;">保持 AOI 为连通路网，逐段分配语义 profile</span></p>

  # Socioeconomic fit
  - elementId: fit-bg
    elementType: shape
    bounds: [60, 410, 840, 50]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: fit
    elementType: text
    bounds: [80, 410, 800, 50]
    content:
      fontSize: 14
      color: "$text"
      align: [left, middle]
      text: |
        <p><strong>社会经济匹配</strong>：从 OSM landuse/amenity 推断周边社会经济状况，标记供给不足路段并自动推荐设计升级方案</p>

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "11"
""")

print("Created pages 09-11")
