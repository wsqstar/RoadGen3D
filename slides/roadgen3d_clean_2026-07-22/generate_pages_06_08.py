import os

pages_dir = "/Users/shiqi/Coding/github/GIStudio/RoadGen3D/slides/roadgen3d_clean_2026-07-22/pages"

def write_page(filename, content):
    with open(os.path.join(pages_dir, filename), "w") as f:
        f.write(content)

# ============================================================
# Page 06: Workflow
# ============================================================
write_page("06_workflow.page", """pageType: content
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
      text: "05"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: 核心工作流：三步生成一条街道

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # Step 1 box
  - elementId: s1-bg
    elementType: shape
    bounds: [60, 160, 240, 140]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: s1-title
    elementType: text
    bounds: [80, 170, 200, 30]
    content:
      fontSize: 18
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "① 输入"

  - elementId: s1-body
    elementType: text
    bounds: [80, 205, 200, 80]
    content:
      fontSize: 14
      color: "$text"
      lineHeight: 1.5
      align: [left, top]
      text: |
        <p>OSM 地图数据</p>
        <p>参考平面标注</p>
        <p>场景目录 / 模板</p>
        <p>自然语言描述（可选）</p>

  # Arrow 1→2
  - elementId: arrow1
    elementType: line
    bounds: [300, 225, 40, 2]
    viewBox: [40, 2]
    points: "0,0 35,0"
    arrow: [null, "arrow"]
    border: {style: solid, width: 2, color: "$primary"}

  # Step 2 box
  - elementId: s2-bg
    elementType: shape
    bounds: [340, 160, 240, 140]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: s2-title
    elementType: text
    bounds: [360, 170, 200, 30]
    content:
      fontSize: 18
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "② 生成"

  - elementId: s2-body
    elementType: text
    bounds: [360, 205, 200, 80]
    content:
      fontSize: 14
      color: "$text"
      lineHeight: 1.5
      align: [left, top]
      text: |
        <p>道路骨架</p>
        <p>布局求解</p>
        <p>资产放置</p>
        <p>建筑生成</p>

  # Arrow 2→3
  - elementId: arrow2
    elementType: line
    bounds: [580, 225, 40, 2]
    viewBox: [40, 2]
    points: "0,0 35,0"
    arrow: [null, "arrow"]
    border: {style: solid, width: 2, color: "$primary"}

  # Step 3 box
  - elementId: s3-bg
    elementType: shape
    bounds: [620, 160, 280, 140]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: s3-title
    elementType: text
    bounds: [640, 170, 240, 30]
    content:
      fontSize: 18
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "③ 评估与预览"

  - elementId: s3-body
    elementType: text
    bounds: [640, 205, 240, 80]
    content:
      fontSize: 14
      color: "$text"
      lineHeight: 1.5
      align: [left, top]
      text: |
        <p>road-metrics 评分</p>
        <p>步行性 / 安全性 / 美观性</p>
        <p>3D Viewer 交互查看</p>
        <p>对比与迭代优化</p>

  # Output artifact
  - elementId: out-label
    elementType: text
    bounds: [60, 330, 840, 24]
    content:
      fontSize: 13
      color: "$muted"
      align: [center, middle]
      text: "输出产物"

  - elementId: out-box
    elementType: shape
    bounds: [260, 360, 440, 40]
    shapeName: rect
    fill: {type: solid, color: "#FFFFFF"}
    border: {style: solid, width: 1, color: "$border"}

  - elementId: out-text
    elementType: text
    bounds: [260, 360, 440, 40]
    content:
      fontSize: 14
      color: "$text"
      align: [center, middle]
      text: "scene_layout.json  +  scene.glb  +  evaluation.json"

  # Bottom: loop back
  - elementId: loop-text
    elementType: text
    bounds: [60, 430, 840, 30]
    content:
      fontSize: 14
      color: "$muted"
      align: [center, middle]
      text: |
        <p>评分不满意 → 自动/人工调整参数 → 重新生成 → 再次评估（闭环迭代）</p>

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "06"
""")

# ============================================================
# Page 07: A/B Semantic Design Layers
# ============================================================
write_page("07_ab_layers.page", """pageType: content
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
      text: "06"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: A/B 语义设计层：解耦空间与风格

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # A Layer
  - elementId: a-label
    elementType: text
    bounds: [60, 150, 400, 36]
    content:
      fontSize: 22
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "A 层：骨架功能设计"

  - elementId: a-body
    elementType: text
    bounds: [60, 195, 400, 120]
    content:
      fontSize: 15
      color: "$text"
      lineHeight: 1.6
      align: [left, top]
      text: |
        <p>决定道路的空间结构与功能：</p>
        <ul>
          <li>道路骨架与横断面</li>
          <li>功能区划分（步行/公交/车辆）</li>
          <li>路面标注与优先级</li>
        </ul>

  - elementId: a-source
    elementType: text
    bounds: [60, 320, 400, 30]
    content:
      fontSize: 13
      color: "$muted"
      align: [left, middle]
      text: "来源：OSM/POI 推断、Viewer 标注、LLM 推理"

  # Vertical divider
  - elementId: v-divider
    elementType: line
    bounds: [480, 150, 1, 220]
    viewBox: [1, 220]
    points: "0,0 0,220"
    border: {style: solid, width: 1, color: "$border"}

  # B Layer
  - elementId: b-label
    elementType: text
    bounds: [520, 150, 400, 36]
    content:
      fontSize: 22
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "B 层：街道家具主题"

  - elementId: b-body
    elementType: text
    bounds: [520, 195, 380, 120]
    content:
      fontSize: 15
      color: "$text"
      lineHeight: 1.6
      align: [left, top]
      text: |
        <p>决定场景的视觉与设施风格：</p>
        <ul>
          <li>家具密度与资产组合</li>
          <li>建筑/家具生成偏好</li>
          <li>材质与渲染风格</li>
        </ul>

  - elementId: b-source
    elementType: text
    bounds: [520, 320, 380, 30]
    content:
      fontSize: 13
      color: "$muted"
      align: [left, middle]
      text: "来源：设计目标、LLM 推理、A 层回退推荐"

  # Resolution priority
  - elementId: priority-bg
    elementType: shape
    bounds: [60, 380, 840, 50]
    shapeName: rect
    fill: {type: solid, color: "$light"}

  - elementId: priority
    elementType: text
    bounds: [80, 380, 800, 50]
    content:
      fontSize: 15
      color: "$text"
      align: [left, middle]
      text: |
        <p>冲突解决优先级：<strong>人工标注 > LLM > OSM/POI 自动推断</strong></p>

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "07"
""")

# ============================================================
# Page 08: Generation Engine
# ============================================================
write_page("08_engine.page", """pageType: content
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
      text: "07"

  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: 生成引擎：从规则到 3D 场景

  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # Pipeline: 3 boxes in a row with arrows
  - elementId: p1-bg
    elementType: shape
    bounds: [60, 160, 260, 120]
    shapeName: rect
    fill: {type: solid, color: "#FFFFFF"}
    border: {style: solid, width: 2, color: "$primary"}

  - elementId: p1
    elementType: text
    bounds: [75, 170, 230, 100]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:18px; color:$primary;"><strong>StreetProgram</strong></span></p>
        <p style="margin-top:6px"><span style="font-size:14px; color:$text;">声明式街道描述</span></p>
        <p><span style="font-size:13px; color:$muted;">道路类型、横断面、功能带、家具需求</span></p>

  - elementId: a1
    elementType: line
    bounds: [320, 215, 40, 2]
    viewBox: [40, 2]
    points: "0,0 35,0"
    arrow: [null, "arrow"]
    border: {style: solid, width: 2, color: "$primary"}

  - elementId: p2-bg
    elementType: shape
    bounds: [360, 160, 260, 120]
    shapeName: rect
    fill: {type: solid, color: "#FFFFFF"}
    border: {style: solid, width: 2, color: "$primary"}

  - elementId: p2
    elementType: text
    bounds: [375, 170, 230, 100]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:18px; color:$primary;"><strong>ConstraintSet</strong></span></p>
        <p style="margin-top:6px"><span style="font-size:14px; color:$text;">硬/软设计规则</span></p>
        <p><span style="font-size:13px; color:$muted;">最小间距、POI 绑定、同比例缩放</span></p>

  - elementId: a2
    elementType: line
    bounds: [620, 215, 40, 2]
    viewBox: [40, 2]
    points: "0,0 35,0"
    arrow: [null, "arrow"]
    border: {style: solid, width: 2, color: "$primary"}

  - elementId: p3-bg
    elementType: shape
    bounds: [660, 160, 240, 120]
    shapeName: rect
    fill: {type: solid, color: "#FFFFFF"}
    border: {style: solid, width: 2, color: "$primary"}

  - elementId: p3
    elementType: text
    bounds: [675, 170, 210, 100]
    content:
      align: [left, top]
      text: |
        <p><span style="font-size:18px; color:$primary;"><strong>LayoutSolver</strong></span></p>
        <p style="margin-top:6px"><span style="font-size:14px; color:$text;">布局优化 + 碰撞检测</span></p>
        <p><span style="font-size:13px; color:$muted;">输出 slot_plans、conflicts、rule_eval</span></p>

  # Key algorithms section
  - elementId: algo-title
    elementType: text
    bounds: [60, 320, 400, 30]
    content:
      fontSize: 16
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "核心算法特性"

  - elementId: algo-body
    elementType: text
    bounds: [60, 355, 840, 120]
    content:
      fontSize: 14
      color: "$text"
      lineHeight: 1.7
      align: [left, top]
      text: |
        <p><strong>多矩形碰撞检测</strong> — 复杂 3D 网格分解为多个紧密边界框，粗检测+精检测两阶段，L 形长椅、带顶棚公交站可"咬合"排列</p>
        <p><strong>动态间距算法</strong> — 树/路灯用均匀分布求解器，长椅/垃圾桶用紧凑装箱求解器，保持原始长宽比不拉伸</p>
        <p><strong>资产检索</strong> — CLIP 文本编码 + FAISS 向量检索，按语义匹配放置 3D 资产</p>

  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "08"
""")

print("Created pages 06-08")
