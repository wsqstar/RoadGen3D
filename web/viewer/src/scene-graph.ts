type RelativeDescriptor = Record<string, string | number>;

type TreeNode = {
  id: string;
  species_type: string;
  absolute_position: { x: number; y: number };
  radius: number;
} & Record<string, RelativeDescriptor | string | number | { x: number; y: number } | undefined>;

const SCENE_GRAPH_DATA = {
  scene_metadata: {
    resolution_unit: "coordinate_pixels",
    origin: "top_left",
    coordinate_system: "Cartesian_2D",
  },
  root: {
    id: "street_scene_01",
    type: "scene",
    children: [
      {
        id: "tree_layer",
        type: "layer",
        description: "Vegetation distribution along sidewalks",
        trees: [
          {
            id: "tree_01",
            species_type: "deciduous_urban",
            absolute_position: { x: 58, y: 75 },
            relative_to_nearest_intersection: { dx: -12, dy: -25 },
            radius: 15,
          },
          {
            id: "tree_02",
            species_type: "deciduous_urban",
            absolute_position: { x: 142, y: 105 },
            relative_to_nearest_intersection: { dx: 72, dy: 5 },
            radius: 18,
          },
          {
            id: "tree_03",
            species_type: "deciduous_urban",
            absolute_position: { x: 55, y: 305 },
            relative_to_nearest_intersection: { dx: -15, dy: 205 },
            radius: 16,
          },
          {
            id: "tree_04",
            species_type: "deciduous_urban",
            absolute_position: { x: 195, y: 420 },
            relative_to_street_axis: { dist_to_centerline: -45, side: "north" },
            radius: 14,
          },
          {
            id: "tree_05",
            species_type: "deciduous_urban",
            absolute_position: { x: 330, y: 420 },
            relative_to_tree_04: { dx: 135, dy: 0 },
            radius: 14,
          },
          {
            id: "tree_06",
            species_type: "deciduous_urban",
            absolute_position: { x: 465, y: 420 },
            relative_to_tree_05: { dx: 135, dy: 0 },
            radius: 14,
          },
          {
            id: "tree_07",
            species_type: "deciduous_urban",
            absolute_position: { x: 600, y: 420 },
            relative_to_tree_06: { dx: 135, dy: 0 },
            radius: 14,
          },
          {
            id: "tree_08",
            species_type: "deciduous_urban",
            absolute_position: { x: 735, y: 420 },
            relative_to_tree_07: { dx: 135, dy: 0 },
            radius: 14,
          },
          {
            id: "tree_09",
            species_type: "deciduous_urban",
            absolute_position: { x: 870, y: 420 },
            relative_to_tree_08: { dx: 135, dy: 0 },
            radius: 14,
          },
          {
            id: "tree_10",
            species_type: "deciduous_urban",
            absolute_position: { x: 38, y: 560 },
            relative_to_street_axis: { dist_to_centerline: 45, side: "south" },
            radius: 15,
          },
          {
            id: "tree_11",
            species_type: "deciduous_urban",
            absolute_position: { x: 72, y: 685 },
            relative_to_tree_10: { dx: 34, dy: 125 },
            radius: 15,
          },
          {
            id: "tree_12",
            species_type: "deciduous_urban",
            absolute_position: { x: 335, y: 585 },
            relative_to_street_axis: { dist_to_centerline: 55, side: "south" },
            radius: 14,
          },
          {
            id: "tree_13",
            species_type: "deciduous_urban",
            absolute_position: { x: 605, y: 585 },
            relative_to_tree_12: { dx: 270, dy: 0 },
            radius: 14,
          },
          {
            id: "tree_14",
            species_type: "deciduous_urban",
            absolute_position: { x: 875, y: 585 },
            relative_to_tree_13: { dx: 270, dy: 0 },
            radius: 14,
          },
        ] as TreeNode[],
      },
    ],
  },
} as const;

function formatRelativeDescriptor(tree: TreeNode): string {
  const entry = Object.entries(tree).find(([key]) => key.startsWith("relative_to_"));
  if (!entry) {
    return "No relative constraint";
  }
  const [rawKey, values] = entry;
  const anchor = rawKey.replace("relative_to_", "").split("_").join(" ");
  const parts = Object.entries(values as RelativeDescriptor).map(([key, value]) => `${key}: ${value}`);
  return `${anchor} -> ${parts.join(", ")}`;
}

function buildSceneGraphSvg(trees: readonly TreeNode[]): string {
  const centerlineY = 500;
  const laneTop = 455;
  const laneBottom = 545;
  const northBandY = 420;
  const southBandY = 585;
  const relationLines = [
    [195, 420, 330, 420],
    [330, 420, 465, 420],
    [465, 420, 600, 420],
    [600, 420, 735, 420],
    [735, 420, 870, 420],
    [38, 560, 72, 685],
    [335, 585, 605, 585],
    [605, 585, 875, 585],
  ];

  return `
    <svg viewBox="0 0 1000 1000" class="scene-graph-canvas" role="img" aria-label="Street vegetation scene graph">
      <defs>
        <pattern id="scene-grid" width="50" height="50" patternUnits="userSpaceOnUse">
          <path d="M 50 0 L 0 0 0 50" fill="none" stroke="rgba(70,86,74,0.10)" stroke-width="1" />
        </pattern>
      </defs>
      <rect x="0" y="0" width="1000" height="1000" fill="#f5f1e8" />
      <rect x="0" y="0" width="1000" height="1000" fill="url(#scene-grid)" />
      <rect x="0" y="${laneTop}" width="1000" height="${laneBottom - laneTop}" rx="18" fill="#495463" opacity="0.95" />
      <line x1="0" y1="${centerlineY}" x2="1000" y2="${centerlineY}" stroke="#f5e6a4" stroke-width="6" stroke-dasharray="28 18" />
      <line x1="0" y1="${northBandY}" x2="1000" y2="${northBandY}" stroke="#7c8b73" stroke-width="3" stroke-dasharray="8 12" opacity="0.55" />
      <line x1="0" y1="${southBandY}" x2="1000" y2="${southBandY}" stroke="#7c8b73" stroke-width="3" stroke-dasharray="8 12" opacity="0.55" />
      <text x="24" y="492" class="scene-axis-label">street centerline y≈500</text>
      <text x="24" y="408" class="scene-axis-label">north sidewalk band</text>
      <text x="24" y="628" class="scene-axis-label">south sidewalk band</text>
      ${relationLines
        .map(
          ([x1, y1, x2, y2]) =>
            `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="scene-relation-line" />`,
        )
        .join("")}
      ${trees
        .map((tree) => {
          const { x, y } = tree.absolute_position;
          const labelX = Math.min(x + 16, 920);
          const labelY = y - 18;
          const coreRadius = tree.radius;
          const haloRadius = tree.radius + 6;
          const crownRadius = Math.max(3, tree.radius * 0.33);
          return `
            <g class="scene-tree-node" data-tree-id="${tree.id}">
              <circle cx="${x}" cy="${y}" r="${haloRadius}" fill="rgba(98,140,83,0.15)" />
              <circle cx="${x}" cy="${y}" r="${coreRadius}" fill="#628c53" stroke="#27452a" stroke-width="3" />
              <circle cx="${x}" cy="${y}" r="${crownRadius}" fill="#8fbf73" opacity="0.8" />
              <text x="${labelX}" y="${labelY}" class="scene-tree-label">${tree.id}</text>
            </g>
          `;
        })
        .join("")}
      <circle cx="0" cy="0" r="7" fill="#d94841" />
      <text x="16" y="28" class="scene-origin-label">(0, 0)</text>
      <text x="820" y="976" class="scene-origin-label">1000 × 1000 canvas</text>
    </svg>
  `;
}

export function mountSceneGraphPage(root: HTMLElement): () => void {
  const treeLayer = SCENE_GRAPH_DATA.root.children[0];
  const trees = treeLayer.trees;
  const northTrees = trees.filter((tree) => tree.absolute_position.y < 500).length;
  const southTrees = trees.length - northTrees;
  const averageRadius = trees.reduce((sum, tree) => sum + tree.radius, 0) / trees.length;
  const eventController = new AbortController();
  const { signal } = eventController;

  root.innerHTML = `
    <div class="scene-page">
      <div class="scene-page-topbar">
        <div>
          <div class="scene-page-kicker">Viewer / Scene Graph</div>
          <h1 class="scene-page-title">Street Vegetation Scene Graph</h1>
          <p class="scene-page-subtitle">
            基于 1000 × 1000 坐标系，对街道树木的绝对位置、相对关系和道路避让约束进行可视化。
          </p>
        </div>
        <div class="scene-page-actions">
          <button id="scene-page-back" class="viewer-nav-button" type="button">Back to Viewer</button>
        </div>
      </div>

      <div class="scene-page-layout">
        <section class="scene-panel scene-panel-canvas">
          <div class="scene-panel-header">
            <h2>Spatial Layout</h2>
            <p>道路中心线、南北人行带和树列间距关系同步显示。</p>
          </div>
          ${buildSceneGraphSvg(trees)}
        </section>

        <aside class="scene-sidebar">
          <section class="scene-panel scene-metrics">
            <div class="scene-panel-header">
              <h2>Summary</h2>
              <p>从结构化场景图提取的快速统计。</p>
            </div>
            <div class="scene-metric-grid">
              <div>
                <span class="scene-metric-label">Tree Count</span>
                <strong>${trees.length}</strong>
              </div>
              <div>
                <span class="scene-metric-label">North Side</span>
                <strong>${northTrees}</strong>
              </div>
              <div>
                <span class="scene-metric-label">South Side</span>
                <strong>${southTrees}</strong>
              </div>
              <div>
                <span class="scene-metric-label">Avg Radius</span>
                <strong>${averageRadius.toFixed(1)}</strong>
              </div>
            </div>
          </section>

          <section class="scene-panel">
            <div class="scene-panel-header">
              <h2>Layout Logic</h2>
              <p>页面内直接解释数据中的城市空间规律。</p>
            </div>
            <ul class="scene-bullet-list">
              <li>北侧树列 <code>tree_04</code> 到 <code>tree_09</code> 以 <code>dx = 135</code> 呈连续均匀排列。</li>
              <li>南侧 <code>tree_12</code> 到 <code>tree_14</code> 形成更稀疏的 <code>dx = 270</code> 等距序列。</li>
              <li>树木整体避开 <code>y ≈ 500</code> 的道路中心区域，符合街道附属设施布置逻辑。</li>
            </ul>
          </section>

          <section class="scene-panel">
            <div class="scene-panel-header">
              <h2>Node Table</h2>
              <p>展示每棵树的绝对坐标和主相对约束。</p>
            </div>
            <div class="scene-table-wrap">
              <table class="scene-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Absolute</th>
                    <th>Radius</th>
                    <th>Relative</th>
                  </tr>
                </thead>
                <tbody>
                  ${trees
                    .map(
                      (tree) => `
                        <tr>
                          <td>${tree.id}</td>
                          <td>(${tree.absolute_position.x}, ${tree.absolute_position.y})</td>
                          <td>${tree.radius}</td>
                          <td>${formatRelativeDescriptor(tree)}</td>
                        </tr>
                      `,
                    )
                    .join("")}
                </tbody>
              </table>
            </div>
          </section>
        </aside>
      </div>
    </div>
  `;

  const backButton = root.querySelector<HTMLButtonElement>("#scene-page-back");
  backButton?.addEventListener(
    "click",
    () => {
      window.location.hash = "#viewer";
    },
    { signal },
  );

  return () => {
    eventController.abort();
  };
}
