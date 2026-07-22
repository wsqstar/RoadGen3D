import os

output_dir = "/Users/shiqi/Coding/github/GIStudio/RoadGen3D/slides/roadgen3d_clean_2026-07-22"
pages_dir = os.path.join(output_dir, "pages")

def write_page(filename, content):
    with open(os.path.join(pages_dir, filename), "w") as f:
        f.write(content)

# ============================================================
# Page 01: Cover
# ============================================================
write_page("01_cover.page", """pageType: cover
background:
  type: solid
  color: "#FFFFFF"
elements:
  # Left blue accent bar
  - elementId: accent-bar
    elementType: shape
    bounds: [0, 0, 6, 540]
    shapeName: rect
    fill: {type: solid, color: "$primary"}

  # Top thin line
  - elementId: top-line
    elementType: line
    bounds: [60, 80, 840, 1]
    viewBox: [840, 1]
    points: "0,0 840,0"
    border: {style: solid, width: 1, color: "$border"}

  # Main title
  - elementId: title
    elementType: text
    bounds: [60, 140, 600, 70]
    content:
      style: "$coverTitle"
      align: [left, middle]
      text: RoadGen3D

  # Subtitle
  - elementId: subtitle
    elementType: text
    bounds: [60, 220, 700, 80]
    content:
      style: "$coverSubtitle"
      align: [left, top]
      text: |
        <p>规则驱动的 3D 街道场景生成与评价系统</p>
        <p><span style="font-size:16px; color:$muted;">Rule-based, AI-assisted Urban Street Scene Generation</span></p>

  # Yellow accent line
  - elementId: yellow-line
    elementType: line
    bounds: [60, 320, 80, 3]
    viewBox: [80, 3]
    points: "0,0 80,0"
    border: {style: solid, width: 3, color: "$accent"}

  # Bottom info
  - elementId: bottom-info
    elementType: text
    bounds: [60, 460, 500, 50]
    content:
      style: "$caption"
      align: [left, bottom]
      text: |
        <p>GIStudio  ·  2026.07</p>

  # Page number area
  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "01"
""")

# ============================================================
# Page 02: What is street scene design?
# ============================================================
write_page("02_background.page", """pageType: content
background:
  type: solid
  color: "#FFFFFF"
elements:
  # Section number
  - elementId: sec-num
    elementType: text
    bounds: [60, 36, 60, 30]
    content:
      fontSize: 14
      color: "$primary"
      bold: true
      align: [left, middle]
      text: "01"

  # Title
  - elementId: title
    elementType: text
    bounds: [60, 70, 800, 44]
    content:
      style: "$title"
      align: [left, middle]
      text: 街道场景设计：不只是画一条路

  # Title underline
  - elementId: title-line
    elementType: line
    bounds: [60, 118, 60, 2]
    viewBox: [60, 2]
    points: "0,0 60,0"
    border: {style: solid, width: 2, color: "$accent"}

  # Left content: what is a street
  - elementId: body-text
    elementType: text
    bounds: [60, 140, 440, 280]
    content:
      style: "$body"
      align: [left, top]
      text: |
        <p><strong>街道 ≠ 道路</strong></p>
        <p style="margin-top:8px">街道是城市公共空间的核心，包含：</p>
        <ul>
          <li>车行道、人行道、绿化带</li>
          <li>街道家具：长椅、路灯、垃圾桶、公交站</li>
          <li>建筑界面与临街活动</li>
        </ul>
        <p style="margin-top:12px"><strong>好的街道设计关乎三个维度：</strong></p>
        <ul>
          <li>步行性 — 行人是否舒适、安全</li>
          <li>安全性 — 交通隔离、照明覆盖</li>
          <li>美观性 — 植物配置、家具协调</li>
        </ul>

  # Right: simple cross-section diagram using shapes
  - elementId: diagram-label
    elementType: text
    bounds: [560, 140, 340, 24]
    content:
      fontSize: 13
      color: "$muted"
      align: [center, middle]
      text: 典型街道横断面

  # Building left
  - elementId: bldg-left
    elementType: shape
    bounds: [560, 170, 60, 160]
    shapeName: rect
    fill: {type: solid, color: "#D1D5DB"}

  # Sidewalk left
  - elementId: sw-left
    elementType: shape
    bounds: [620, 170, 50, 160]
    shapeName: rect
    fill: {type: solid, color: "#E5E7EB"}

  # Grass belt left
  - elementId: grass-left
    elementType: shape
    bounds: [670, 170, 20, 160]
    shapeName: rect
    fill: {type: solid, color: "#A7F3D0"}

  # Road
  - elementId: road
    elementType: shape
    bounds: [690, 170, 80, 160]
    shapeName: rect
    fill: {type: solid, color: "#4B5563"}

  # Median
  - elementId: median
    elementType: shape
    bounds: [770, 170, 12, 160]
    shapeName: rect
    fill: {type: solid, color: "#6EE7B7"}

  # Road right
  - elementId: road-right
    elementType: shape
    bounds: [782, 170, 80, 160]
    shapeName: rect
    fill: {type: solid, color: "#4B5563"}

  # Grass belt right
  - elementId: grass-right
    elementType: shape
    bounds: [862, 170, 20, 160]
    shapeName: rect
    fill: {type: solid, color: "#A7F3D0"}

  # Sidewalk right
  - elementId: sw-right
    elementType: shape
    bounds: [882, 170, 50, 160]
    shapeName: rect
    fill: {type: solid, color: "#E5E7EB"}

  # Labels below diagram
  - elementId: labels
    elementType: text
    bounds: [560, 340, 380, 80]
    content:
      fontSize: 11
      color: "$muted"
      align: [center, top]
      text: |
        <p>建筑 | 人行道 | 绿化带 | 车行道 | 中分带 | 车行道 | 绿化带 | 人行道</p>

  # Bottom note
  - elementId: note
    elementType: text
    bounds: [60, 420, 840, 40]
    content:
      fontSize: 14
      color: "$muted"
      align: [left, top]
      text: |
        <p>传统流程：CAD 平面图 → 3D 建模 → 人工评估，单一场景耗时数周。</p>

  # Page number
  - elementId: page-num
    elementType: text
    bounds: [860, 500, 60, 24]
    content:
      style: "$caption"
      align: [right, middle]
      text: "02"
""")

print("Created pages 01-02")
