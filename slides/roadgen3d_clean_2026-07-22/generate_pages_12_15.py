import os

pages_dir = "/Users/shiqi/Coding/github/GIStudio/RoadGen3D/slides/roadgen3d_clean_2026-07-22/pages"

def write_page(filename, content):
    with open(os.path.join(pages_dir, filename), "w") as f:
        f.write(content)

# ============================================================
# Page 12: Viewer
# ============================================================
write_page("12_viewer.page", """pageType: content
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
      text: "11"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: Viewer：交互式 3D 查看与分析

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # Two columns
  - elementId: col1
    elementType: text
    bounds: [60, 150, 420, 260]
    content:
      fontSize: 15
      color: "$text"
      lineHeight: 1.7
      align: [left, top]
      text: |
        <p><strong>3D 渲染</strong></p>
        <p><span style="font-size:14px; color:$muted;">Three.js 实时渲染，支持轨道旋转、缩放、平移</span></p>
        <p style="margin-top:10px"><strong>评分可视化</strong></p>
        <p><span style="font-size:14px; color:$muted;">雷达图、柱状图、综合评分一目了然</span></p>
        <p style="margin-top:10px"><strong>方案对比</strong></p>
        <p><span style="font-size:14px; color:$muted;">多方案并列查看，历史记录回溯</span></p>

  - elementId: col2
    elementType: text
    bounds: [500, 150, 400, 260]
    content:
      fontSize: 15
      color: "$text"
      lineHeight: 1.7
      align: [left, top]
      text: |
        <p><strong>3D Pareto Scatter</strong></p>
        <p><span style="font-size:14px; color:$muted;">X/Y/Z 对应 walkability / safety / beauty</span></p>
        <p style="margin-top:10px"><strong>溯源矩阵</strong></p>
        <p><span style="font-size:14px; color:$muted;">展示知识来源、参数、约束的激活状态</span></p>
        <p style="margin-top:10px"><strong>Benchmark Explorer</strong></p>
        <p><span style="font-size:14px; color:$muted;">按 preset 过滤、聚类对比、参数着色</span></p>

  # Bottom: GLB recovery
  - elementId: glb-bg
    elementType: shape
    bounds: [60, 420, 840, 40]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: glb
    elementType: text
    bounds: [80, 420, 800, 40]
    content:
      fontSize: 14
      color: "$text"
      align: [left, middle]
      text: |
        <p>GLB 恢复策略：保留 GLB → 保留 layout JSON 可重建 → 全部丢失则重新生成</p>

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "12"
""")

# ============================================================
# Page 13: Scenarios
# ============================================================
write_page("13_scenarios.page", """pageType: content
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
      text: "12"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: 六种预设场景模板

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # Six items in 2x3 grid
  - elementId: s1-bg
    elementType: shape
    bounds: [60, 150, 270, 70]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: s1
    elementType: text
    bounds: [75, 155, 240, 60]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:16px; color:$primary;"><strong>步行友好</strong></span></p>
        <p><span style="font-size:13px; color:$muted;">行人优先，安全舒适</span></p>

  - elementId: s2-bg
    elementType: shape
    bounds: [350, 150, 270, 70]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: s2
    elementType: text
    bounds: [365, 155, 240, 60]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:16px; color:$primary;"><strong>商业活力</strong></span></p>
        <p><span style="font-size:13px; color:$muted;">商业活跃，人流密集</span></p>

  - elementId: s3-bg
    elementType: shape
    bounds: [640, 150, 260, 70]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: s3
    elementType: text
    bounds: [655, 155, 230, 60]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:16px; color:$primary;"><strong>公交优先</strong></span></p>
        <p><span style="font-size:13px; color:$muted;">公交导向，换乘便利</span></p>

  - elementId: s4-bg
    elementType: shape
    bounds: [60, 240, 270, 70]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: s4
    elementType: text
    bounds: [75, 245, 240, 60]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:16px; color:$primary;"><strong>公园景观</strong></span></p>
        <p><span style="font-size:13px; color:$muted;">绿化为主，休闲舒适</span></p>

  - elementId: s5-bg
    elementType: shape
    bounds: [350, 240, 270, 70]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: s5
    elementType: text
    bounds: [365, 245, 240, 60]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:16px; color:$primary;"><strong>安静居住</strong></span></p>
        <p><span style="font-size:13px; color:$muted;">住宅区安静，绿树成荫</span></p>

  - elementId: s6-bg
    elementType: shape
    bounds: [640, 240, 260, 70]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: s6
    elementType: text
    bounds: [655, 245, 230, 60]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:16px; color:$primary;"><strong>平衡街道</strong></span></p>
        <p><span style="font-size:13px; color:$muted;">各类使用者平衡兼顾</span></p>

  # Bottom note
  - elementId: note
    elementType: text
    bounds: [60, 350, 840, 60]
    content:
      fontSize: 15
      color: "$text"
      lineHeight: 1.6
      align: [left, top]
      text: |
        <p>选择模板 → 批量生成 → 评分对比 → 选中方案 3D 预览</p>
        <p><span style="font-size:13px; color:$muted;">也支持自定义 prompt / OSM 真实区域 / 参考标注 作为输入</span></p>

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "13"
""")

# ============================================================
# Page 14: Summary & Outlook
# ============================================================
write_page("14_summary.page", """pageType: content
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
      text: "13"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: 当前能力与边界

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # Left: completed
  - elementId: done-title
    elementType: text
    bounds: [60, 150, 400, 30]
    content:
      fontSize: 18
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "✓ 已实现"

  - elementId: done
    elementType: text
    bounds: [60, 190, 420, 240]
    content:
      fontSize: 15
      color: "$text"
      lineHeight: 1.7
      align: [left, top]
      text: |
        <p>• 规则驱动的场景生成管线</p>
        <p>• StreetProgram / ConstraintSet / LayoutSolver 显式结构</p>
        <p>• 多维度量化评估（walkability / safety / beauty）</p>
        <p>• Pareto 批量搜索与相关性分析</p>
        <p>• OSM 真实数据接入与语义分块</p>
        <p>• Three.js 交互式 3D 查看器</p>
        <p>• RAG 知识库 + LLM 可选辅助</p>

  # Right: boundaries
  - elementId: limit-title
    elementType: text
    bounds: [520, 150, 400, 30]
    content:
      fontSize: 18
      color: "#9CA3AF"
      bold: true
      align: [left, middle]
      text: "○ 当前边界"

  - elementId: limit
    elementType: text
    bounds: [520, 190, 380, 240]
    content:
      fontSize: 15
      color: "$text"
      lineHeight: 1.7
      align: [left, top]
      text: |
        <p>• 非严格神经符号模型</p>
        <p>• 未覆盖交通仿真与信号控制</p>
        <p>• 未覆盖道路工程级规范库</p>
        <p>• LLM 参数推导为可选增强，主路径 skip_llm</p>
        <p>• 评价基于相关性分析，不声明严格因果</p>

  # Divider
  - elementId: mid-divider
    elementType: line
    bounds: [480, 150, 1, 280]
    viewBox: [1, 280]
    points: "0,0 0,280"
    border: {style: solid, width: 1, color: "$border"}

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "14"
""")

# ============================================================
# Page 15: Final
# ============================================================
write_page("15_final.page", """pageType: final
background:
  type: solid
  color: "#FFFFFF"
elements:
  # Left blue bar
  - elementId: accent-bar
    elementType: shape
    bounds: [0, 0, 6, 540]
    shapeName: rect
    fill: {type: solid, color: "$primary"}

  # Top line
  - elementId: top-line
    elementType: line
    bounds: [60, 80, 840, 1]
    viewBox: [840, 1]
    points: "0,0 840,0"
    border: {style: solid, width: 1, color: "$border"}

  # Title
  - elementId: title
    elementType: text
    bounds: [60, 180, 600, 60]
    content:
      style: "$coverTitle"
      align: [left, middle]
      text: RoadGen3D

  # Subtitle
  - elementId: subtitle
    elementType: text
    bounds: [60, 250, 700, 60]
    content:
      style: "$coverSubtitle"
      align: [left, top]
      text: |
        <p>github.com/GIStudio/RoadGen3D</p>

  # Yellow line
  - elementId: yellow-line
    elementType: line
    bounds: [60, 330, 80, 3]
    viewBox: [80, 3]
    points: "0,0 80,0"
    border: {style: solid, width: 3, color: "$accent"}

  # Contact
  - elementId: contact
    elementType: text
    bounds: [60, 360, 500, 60]
    content:
      fontSize: 16
      color: "$muted"
      lineHeight: 1.5
      align: [left, top]
      text: |
        <p>GIStudio  ·  城市空间设计与生成研究组</p>

  # Page number
  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "15"
""")

print("Created pages 12-15")
