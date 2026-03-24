type PrimitiveValue = string | number | boolean | null;
type RelativeDescriptor = Record<string, PrimitiveValue>;

type SceneMetadata = {
  resolutionUnit: string;
  origin: string;
  coordinateSystem: string;
};

type TreeRelation = {
  key: string;
  values: RelativeDescriptor;
};

type TreeNode = {
  id: string;
  speciesType: string;
  absolutePosition: { x: number; y: number };
  radius: number;
  relations: TreeRelation[];
};

type ParsedSceneGraph = {
  sceneId: string;
  layerId: string;
  layerType: string;
  layerDescription: string;
  sceneMetadata: SceneMetadata;
  trees: TreeNode[];
};

type RelationStats = {
  treeToTree: number;
  streetAxis: number;
  nearestIntersection: number;
  other: number;
};

const DEFAULT_SCENE_GRAPH_SOURCE = {
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
        ],
      },
    ],
  },
} as const;

const DEFAULT_SCENE_GRAPH_TEXT = JSON.stringify(DEFAULT_SCENE_GRAPH_SOURCE, null, 2);
const DEFAULT_SCENE_GRAPH = parseSceneGraphData(DEFAULT_SCENE_GRAPH_SOURCE);
const STREET_CENTERLINE_Y = 500;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getFiniteNumber(value: unknown, label: string): number {
  const numberValue = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numberValue)) {
    throw new Error(`${label} must be a finite number.`);
  }
  return numberValue;
}

function getString(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function getPrimitiveValue(value: unknown): PrimitiveValue | undefined {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean" || value === null) {
    return value;
  }
  return undefined;
}

function parseRelativeDescriptor(value: unknown): RelativeDescriptor | null {
  if (!isRecord(value)) {
    return null;
  }

  const descriptor: RelativeDescriptor = {};
  for (const [key, entry] of Object.entries(value)) {
    const primitive = getPrimitiveValue(entry);
    if (primitive !== undefined) {
      descriptor[key] = primitive;
    }
  }
  return Object.keys(descriptor).length > 0 ? descriptor : null;
}

function parseTreeNode(value: unknown, index: number): TreeNode {
  if (!isRecord(value)) {
    throw new Error(`Tree node at index ${index} must be an object.`);
  }

  const absolutePosition = value.absolute_position;
  if (!isRecord(absolutePosition)) {
    throw new Error(`Tree node ${index} is missing absolute_position.`);
  }

  const relations: TreeRelation[] = [];
  for (const [key, entry] of Object.entries(value)) {
    if (!key.startsWith("relative_to_")) {
      continue;
    }
    const descriptor = parseRelativeDescriptor(entry);
    if (descriptor) {
      relations.push({ key, values: descriptor });
    }
  }

  return {
    id: getString(value.id, `tree_${String(index + 1).padStart(2, "0")}`),
    speciesType: getString(value.species_type, "unknown"),
    absolutePosition: {
      x: getFiniteNumber(absolutePosition.x, `tree[${index}].absolute_position.x`),
      y: getFiniteNumber(absolutePosition.y, `tree[${index}].absolute_position.y`),
    },
    radius: value.radius === undefined ? 14 : getFiniteNumber(value.radius, `tree[${index}].radius`),
    relations,
  };
}

function parseSceneGraphData(data: unknown): ParsedSceneGraph {
  if (!isRecord(data)) {
    throw new Error("Scene graph JSON must be an object.");
  }

  const root = data.root;
  if (!isRecord(root)) {
    throw new Error("Scene graph JSON must contain a root object.");
  }

  const children = Array.isArray(root.children) ? root.children : [];
  const treeLayer = children.find((child) => isRecord(child) && Array.isArray(child.trees));
  if (!treeLayer || !isRecord(treeLayer)) {
    throw new Error("Scene graph JSON must contain root.children[].trees.");
  }

  const rawTrees = Array.isArray(treeLayer.trees) ? treeLayer.trees : [];
  if (!rawTrees.length) {
    throw new Error("Scene graph tree layer contains no trees.");
  }

  const sceneMetadata = isRecord(data.scene_metadata) ? data.scene_metadata : {};
  const trees = rawTrees.map((tree, index) => parseTreeNode(tree, index));

  return {
    sceneId: getString(root.id, "scene"),
    layerId: getString(treeLayer.id, "tree_layer"),
    layerType: getString(treeLayer.type, "layer"),
    layerDescription: getString(treeLayer.description, "Street vegetation distribution"),
    sceneMetadata: {
      resolutionUnit: getString(sceneMetadata.resolution_unit, "coordinate_pixels"),
      origin: getString(sceneMetadata.origin, "top_left"),
      coordinateSystem: getString(sceneMetadata.coordinate_system, "Cartesian_2D"),
    },
    trees,
  };
}

function parseSceneGraphText(text: string): ParsedSceneGraph {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown JSON parse error.";
    throw new Error(`Invalid JSON: ${message}`);
  }
  return parseSceneGraphData(parsed);
}

function getNumericDescriptorValue(descriptor: RelativeDescriptor, key: string): number | null {
  const value = descriptor[key];
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function summarizeRelations(trees: readonly TreeNode[]): RelationStats {
  const stats: RelationStats = {
    treeToTree: 0,
    streetAxis: 0,
    nearestIntersection: 0,
    other: 0,
  };

  for (const tree of trees) {
    for (const relation of tree.relations) {
      if (relation.key.startsWith("relative_to_tree_")) {
        stats.treeToTree += 1;
      } else if (relation.key === "relative_to_street_axis") {
        stats.streetAxis += 1;
      } else if (relation.key === "relative_to_nearest_intersection") {
        stats.nearestIntersection += 1;
      } else {
        stats.other += 1;
      }
    }
  }

  return stats;
}

function formatRelativeDescriptor(tree: TreeNode): string {
  const firstRelation = tree.relations[0];
  if (!firstRelation) {
    return "No relative constraint";
  }
  const anchor = firstRelation.key.replace("relative_to_", "").split("_").join(" ");
  const parts = Object.entries(firstRelation.values).map(([key, value]) => `${key}: ${value}`);
  return `${anchor} -> ${parts.join(", ")}`;
}

function buildRelationVisualMarkup(trees: readonly TreeNode[]): string {
  const treeById = new Map<string, TreeNode>(trees.map((tree) => [tree.id, tree]));
  const intersectionAnchors = new Map<string, { x: number; y: number; label: string }>();
  const lineMarkup: string[] = [];

  for (const tree of trees) {
    const { x, y } = tree.absolutePosition;
    for (const relation of tree.relations) {
      if (relation.key.startsWith("relative_to_tree_")) {
        const anchorId = relation.key.replace("relative_to_", "");
        const anchorTree = treeById.get(anchorId);
        if (!anchorTree) {
          continue;
        }
        lineMarkup.push(
          `<line x1="${anchorTree.absolutePosition.x}" y1="${anchorTree.absolutePosition.y}" x2="${x}" y2="${y}" class="scene-relation-line" />`,
        );
        continue;
      }

      if (relation.key === "relative_to_street_axis") {
        lineMarkup.push(
          `<line x1="${x}" y1="${STREET_CENTERLINE_Y}" x2="${x}" y2="${y}" class="scene-relation-line scene-relation-line-axis" />`,
        );
        continue;
      }

      if (relation.key === "relative_to_nearest_intersection") {
        const dx = getNumericDescriptorValue(relation.values, "dx");
        const dy = getNumericDescriptorValue(relation.values, "dy");
        if (dx === null || dy === null) {
          continue;
        }
        const anchorX = x - dx;
        const anchorY = y - dy;
        lineMarkup.push(
          `<line x1="${anchorX}" y1="${anchorY}" x2="${x}" y2="${y}" class="scene-relation-line scene-relation-line-intersection" />`,
        );
        const anchorKey = `${anchorX.toFixed(1)}:${anchorY.toFixed(1)}`;
        intersectionAnchors.set(anchorKey, { x: anchorX, y: anchorY, label: "intersection" });
      }
    }
  }

  const anchorMarkup = Array.from(intersectionAnchors.values())
    .map(
      (anchor) => `
        <g class="scene-anchor-marker">
          <rect x="${anchor.x - 7}" y="${anchor.y - 7}" width="14" height="14" rx="3" />
          <text x="${Math.min(anchor.x + 12, 900)}" y="${Math.max(anchor.y - 10, 24)}" class="scene-anchor-label">
            ${anchor.label}
          </text>
        </g>
      `,
    )
    .join("");

  return `${lineMarkup.join("")}${anchorMarkup}`;
}

function buildSceneGraphSvg(graph: ParsedSceneGraph): string {
  const { trees } = graph;

  return `
    <svg viewBox="0 0 1000 1000" class="scene-graph-canvas" role="img" aria-label="Street vegetation scene graph">
      <line x1="0" y1="${STREET_CENTERLINE_Y}" x2="1000" y2="${STREET_CENTERLINE_Y}" stroke="#f5e6a4" stroke-width="6" stroke-dasharray="28 18" opacity="0.92" />
      ${buildRelationVisualMarkup(trees)}
      ${trees
        .map((tree) => {
          const { x, y } = tree.absolutePosition;
          const labelX = Math.min(x + 16, 920);
          const labelY = Math.max(y - 18, 20);
          const coreRadius = tree.radius;
          const haloRadius = tree.radius + 6;
          const crownRadius = Math.max(3, tree.radius * 0.33);
          return `
            <g class="scene-tree-node" data-tree-id="${tree.id}">
              <circle cx="${x}" cy="${y}" r="${haloRadius}" fill="rgba(98,140,83,0.18)" />
              <circle cx="${x}" cy="${y}" r="${coreRadius}" fill="#628c53" stroke="#27452a" stroke-width="3" />
              <circle cx="${x}" cy="${y}" r="${crownRadius}" fill="#8fbf73" opacity="0.8" />
              <text x="${labelX}" y="${labelY}" class="scene-tree-label">${tree.id}</text>
            </g>
          `;
        })
        .join("")}
      <circle cx="0" cy="0" r="7" fill="#d94841" />
      <text x="16" y="28" class="scene-origin-label">(0, 0)</text>
      <text x="16" y="976" class="scene-origin-label">${graph.sceneMetadata.coordinateSystem} · ${graph.sceneMetadata.origin}</text>
      <text x="820" y="976" class="scene-origin-label">1000 × 1000 canvas</text>
    </svg>
  `;
}

function buildSummaryMarkup(graph: ParsedSceneGraph): string {
  const northTrees = graph.trees.filter((tree) => tree.absolutePosition.y < STREET_CENTERLINE_Y).length;
  const southTrees = graph.trees.length - northTrees;
  const averageRadius =
    graph.trees.reduce((sum, tree) => sum + tree.radius, 0) / Math.max(1, graph.trees.length);
  const relationStats = summarizeRelations(graph.trees);

  return `
    <div>
      <span class="scene-metric-label">Tree Count</span>
      <strong>${graph.trees.length}</strong>
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
    <div>
      <span class="scene-metric-label">Tree Links</span>
      <strong>${relationStats.treeToTree}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Axis Anchors</span>
      <strong>${relationStats.streetAxis}</strong>
    </div>
  `;
}

function buildMetadataMarkup(graph: ParsedSceneGraph): string {
  const relationStats = summarizeRelations(graph.trees);
  const xs = graph.trees.map((tree) => tree.absolutePosition.x);
  const ys = graph.trees.map((tree) => tree.absolutePosition.y);
  const extentText =
    xs.length && ys.length
      ? `x ${Math.min(...xs)}-${Math.max(...xs)} · y ${Math.min(...ys)}-${Math.max(...ys)}`
      : "n/a";

  return `
    <div class="scene-fact-card">
      <span class="scene-fact-label">Scene ID</span>
      <strong>${graph.sceneId}</strong>
    </div>
    <div class="scene-fact-card">
      <span class="scene-fact-label">Layer</span>
      <strong>${graph.layerId}</strong>
    </div>
    <div class="scene-fact-card">
      <span class="scene-fact-label">Coordinate System</span>
      <strong>${graph.sceneMetadata.coordinateSystem}</strong>
    </div>
    <div class="scene-fact-card">
      <span class="scene-fact-label">Origin</span>
      <strong>${graph.sceneMetadata.origin}</strong>
    </div>
    <div class="scene-fact-card">
      <span class="scene-fact-label">Resolution Unit</span>
      <strong>${graph.sceneMetadata.resolutionUnit}</strong>
    </div>
    <div class="scene-fact-card">
      <span class="scene-fact-label">Extents</span>
      <strong>${extentText}</strong>
    </div>
    <div class="scene-fact-card scene-fact-card-wide">
      <span class="scene-fact-label">Description</span>
      <strong>${graph.layerDescription}</strong>
    </div>
    <div class="scene-fact-card scene-fact-card-wide">
      <span class="scene-fact-label">Relations</span>
      <strong>
        tree-to-tree ${relationStats.treeToTree} · street-axis ${relationStats.streetAxis} ·
        intersections ${relationStats.nearestIntersection} · other ${relationStats.other}
      </strong>
    </div>
  `;
}

function buildInsightMarkup(graph: ParsedSceneGraph): string {
  const relationStats = summarizeRelations(graph.trees);
  const firstTree = graph.trees[0];
  const lastTree = graph.trees[graph.trees.length - 1];
  const extentX = graph.trees.map((tree) => tree.absolutePosition.x);
  const extentY = graph.trees.map((tree) => tree.absolutePosition.y);

  return `
    <li>当前图层 <code>${graph.layerId}</code> 共载入 <code>${graph.trees.length}</code> 棵树，可直接叠加到底图上核对位置。</li>
    <li>树间关系统计为 <code>${relationStats.treeToTree}</code> 条 tree-to-tree、<code>${relationStats.streetAxis}</code> 条 street-axis、<code>${relationStats.nearestIntersection}</code> 条 intersection anchor。</li>
    <li>空间范围覆盖 <code>x ${Math.min(...extentX)}-${Math.max(...extentX)}</code> 与 <code>y ${Math.min(...extentY)}-${Math.max(...extentY)}</code>，起止节点为 <code>${firstTree?.id ?? "n/a"}</code> 到 <code>${lastTree?.id ?? "n/a"}</code>。</li>
  `;
}

function buildTableRowsMarkup(graph: ParsedSceneGraph): string {
  return graph.trees
    .map(
      (tree) => `
        <tr>
          <td>${tree.id}</td>
          <td>${tree.speciesType}</td>
          <td>(${tree.absolutePosition.x}, ${tree.absolutePosition.y})</td>
          <td>${tree.radius}</td>
          <td>${formatRelativeDescriptor(tree)}</td>
        </tr>
      `,
    )
    .join("");
}

function setStatus(element: HTMLElement | null, message: string, tone: "neutral" | "error" | "success"): void {
  if (!element) {
    return;
  }
  element.textContent = message;
  element.dataset.tone = tone;
}

export function mountSceneGraphPage(root: HTMLElement): () => void {
  const eventController = new AbortController();
  const { signal } = eventController;

  root.innerHTML = `
    <div class="scene-page">
      <div class="scene-page-topbar">
        <div>
          <div class="scene-page-kicker">Viewer / Scene Graph</div>
          <h1 class="scene-page-title">Street Vegetation Scene Graph</h1>
          <p class="scene-page-subtitle">
            底层导入原始 PNG，上层导入或粘贴 Gemini 输出的 scene graph JSON，并直接叠加核对。
          </p>
        </div>
        <div class="scene-page-actions">
          <button id="scene-page-back" class="viewer-nav-button" type="button">Back to Viewer</button>
        </div>
      </div>

      <div class="scene-page-layout">
        <section class="scene-panel scene-panel-canvas">
          <div class="scene-panel-header">
            <h2>Spatial Overlay</h2>
            <p>底层可导入原始 PNG，上层可通过粘贴或 JSON 文件导入 scene graph 叠加层。</p>
          </div>
          <div class="scene-layer-toolbar">
            <label class="scene-file-button" for="scene-image-input">Import Original PNG</label>
            <input id="scene-image-input" class="scene-file-input" type="file" accept="image/png,image/*" />
            <button id="scene-image-reset" class="scene-toolbar-button" type="button" disabled>Clear Image</button>
          </div>
          <div class="scene-layer-controls">
            <label class="scene-layer-toggle" for="scene-show-original">
              <input id="scene-show-original" type="checkbox" checked />
              <span>Original Image</span>
            </label>
            <label class="scene-layer-toggle" for="scene-show-graph">
              <input id="scene-show-graph" type="checkbox" checked />
              <span>Scene Graph</span>
            </label>
            <label class="scene-range-control" for="scene-original-opacity">
              <span>Original Opacity</span>
              <input id="scene-original-opacity" type="range" min="0" max="100" value="100" />
            </label>
            <label class="scene-range-control" for="scene-graph-opacity">
              <span>Graph Opacity</span>
              <input id="scene-graph-opacity" type="range" min="0" max="100" value="78" />
            </label>
          </div>
          <div id="scene-image-meta" class="scene-image-meta">
            尚未导入原图。导入 PNG 后，页面会把它映射到同一个 1000 × 1000 坐标平面。
          </div>
          <div id="scene-layer-stage" class="scene-layer-stage" data-has-image="false">
            <div id="scene-image-empty" class="scene-image-empty">
              Import a PNG image to compare the parsed scene graph against the original street view.
            </div>
            <img id="scene-original-image" class="scene-original-image" alt="Original street scene" hidden />
            <div id="scene-graph-overlay" class="scene-graph-overlay"></div>
          </div>
        </section>

        <aside class="scene-sidebar">
          <section class="scene-panel">
            <div class="scene-panel-header">
              <h2>Scene Graph Input</h2>
              <p>直接粘贴 JSON，或者导入 Gemini 导出的 <code>.json</code> 文件。</p>
            </div>
            <div class="scene-import-toolbar">
              <label class="scene-file-button" for="scene-json-input">Import Graph JSON</label>
              <input id="scene-json-input" class="scene-file-input" type="file" accept=".json,application/json" />
              <button id="scene-graph-apply" class="scene-toolbar-button" type="button">Apply Pasted JSON</button>
              <button id="scene-graph-reset" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">
                Reset Sample
              </button>
            </div>
            <div class="scene-json-wrap">
              <textarea
                id="scene-graph-json"
                class="scene-json-input"
                spellcheck="false"
                placeholder="Paste scene graph JSON here"
              ></textarea>
            </div>
            <div id="scene-graph-status" class="scene-status" data-tone="neutral">
              Using bundled sample scene graph.
            </div>
          </section>

          <section class="scene-panel scene-metrics">
            <div class="scene-panel-header">
              <h2>Summary</h2>
              <p>根据当前导入的 scene graph 自动刷新。</p>
            </div>
            <div id="scene-summary-grid" class="scene-metric-grid"></div>
          </section>

          <section class="scene-panel">
            <div class="scene-panel-header">
              <h2>Scene Metadata</h2>
              <p>坐标系、图层说明和关系类型统计。</p>
            </div>
            <div id="scene-meta-grid" class="scene-fact-grid"></div>
          </section>

          <section class="scene-panel">
            <div class="scene-panel-header">
              <h2>Graph Notes</h2>
              <p>根据当前 JSON 自动生成的结构摘要。</p>
            </div>
            <ul id="scene-insight-list" class="scene-bullet-list"></ul>
          </section>

          <section class="scene-panel">
            <div class="scene-panel-header">
              <h2>Node Table</h2>
              <p>展示每棵树的绝对坐标、半径和首个相对约束。</p>
            </div>
            <div class="scene-table-wrap">
              <table class="scene-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Species</th>
                    <th>Absolute</th>
                    <th>Radius</th>
                    <th>Relative</th>
                  </tr>
                </thead>
                <tbody id="scene-table-body"></tbody>
              </table>
            </div>
          </section>
        </aside>
      </div>
    </div>
  `;

  const backButton = root.querySelector<HTMLButtonElement>("#scene-page-back");
  const imageInput = root.querySelector<HTMLInputElement>("#scene-image-input");
  const resetImageButton = root.querySelector<HTMLButtonElement>("#scene-image-reset");
  const showOriginalInput = root.querySelector<HTMLInputElement>("#scene-show-original");
  const showGraphInput = root.querySelector<HTMLInputElement>("#scene-show-graph");
  const originalOpacityInput = root.querySelector<HTMLInputElement>("#scene-original-opacity");
  const graphOpacityInput = root.querySelector<HTMLInputElement>("#scene-graph-opacity");
  const imageMetaEl = root.querySelector<HTMLElement>("#scene-image-meta");
  const layerStageEl = root.querySelector<HTMLElement>("#scene-layer-stage");
  const imageEmptyEl = root.querySelector<HTMLElement>("#scene-image-empty");
  const originalImageEl = root.querySelector<HTMLImageElement>("#scene-original-image");
  const graphOverlayEl = root.querySelector<HTMLElement>("#scene-graph-overlay");
  const graphJsonInput = root.querySelector<HTMLTextAreaElement>("#scene-graph-json");
  const graphFileInput = root.querySelector<HTMLInputElement>("#scene-json-input");
  const applyGraphButton = root.querySelector<HTMLButtonElement>("#scene-graph-apply");
  const resetGraphButton = root.querySelector<HTMLButtonElement>("#scene-graph-reset");
  const graphStatusEl = root.querySelector<HTMLElement>("#scene-graph-status");
  const summaryGridEl = root.querySelector<HTMLElement>("#scene-summary-grid");
  const metaGridEl = root.querySelector<HTMLElement>("#scene-meta-grid");
  const insightListEl = root.querySelector<HTMLElement>("#scene-insight-list");
  const tableBodyEl = root.querySelector<HTMLElement>("#scene-table-body");
  let currentObjectUrl = "";
  let currentGraph = DEFAULT_SCENE_GRAPH;

  if (graphJsonInput) {
    graphJsonInput.value = DEFAULT_SCENE_GRAPH_TEXT;
  }

  function revokeCurrentObjectUrl(): void {
    if (currentObjectUrl) {
      URL.revokeObjectURL(currentObjectUrl);
      currentObjectUrl = "";
    }
  }

  function updateLayerState(): void {
    const hasImage = Boolean(currentObjectUrl);
    const showOriginal = showOriginalInput?.checked ?? true;
    const showGraph = showGraphInput?.checked ?? true;
    const originalOpacity = Number(originalOpacityInput?.value ?? "100") / 100;
    const graphOpacity = Number(graphOpacityInput?.value ?? "78") / 100;

    if (layerStageEl) {
      layerStageEl.dataset.hasImage = hasImage ? "true" : "false";
    }
    if (imageEmptyEl) {
      imageEmptyEl.hidden = hasImage;
    }
    if (originalImageEl) {
      originalImageEl.hidden = !hasImage || !showOriginal;
      originalImageEl.style.opacity = String(originalOpacity);
    }
    if (graphOverlayEl) {
      graphOverlayEl.hidden = !showGraph;
      graphOverlayEl.style.opacity = String(graphOpacity);
    }
    if (resetImageButton) {
      resetImageButton.disabled = !hasImage;
    }
  }

  function renderSceneGraph(graph: ParsedSceneGraph, statusMessage: string, tone: "neutral" | "success"): void {
    currentGraph = graph;
    if (graphOverlayEl) {
      graphOverlayEl.innerHTML = buildSceneGraphSvg(graph);
    }
    if (summaryGridEl) {
      summaryGridEl.innerHTML = buildSummaryMarkup(graph);
    }
    if (metaGridEl) {
      metaGridEl.innerHTML = buildMetadataMarkup(graph);
    }
    if (insightListEl) {
      insightListEl.innerHTML = buildInsightMarkup(graph);
    }
    if (tableBodyEl) {
      tableBodyEl.innerHTML = buildTableRowsMarkup(graph);
    }
    setStatus(graphStatusEl, statusMessage, tone);
    updateLayerState();
  }

  async function loadOriginalImage(file: File): Promise<void> {
    if (!originalImageEl || !imageMetaEl) {
      return;
    }

    revokeCurrentObjectUrl();
    const objectUrl = URL.createObjectURL(file);
    currentObjectUrl = objectUrl;

    await new Promise<void>((resolve, reject) => {
      originalImageEl.onload = () => resolve();
      originalImageEl.onerror = () => reject(new Error("Failed to load the selected PNG."));
      originalImageEl.src = objectUrl;
    });

    const { naturalWidth, naturalHeight } = originalImageEl;
    imageMetaEl.textContent =
      `${file.name} · ${naturalWidth} × ${naturalHeight}px · displayed as the base layer in the 1000 × 1000 scene coordinate frame.`;
    if (imageInput) {
      imageInput.value = "";
    }
    updateLayerState();
  }

  function resetOriginalImage(): void {
    revokeCurrentObjectUrl();
    if (originalImageEl) {
      originalImageEl.removeAttribute("src");
      originalImageEl.hidden = true;
    }
    if (imageInput) {
      imageInput.value = "";
    }
    if (imageMetaEl) {
      imageMetaEl.textContent = "尚未导入原图。导入 PNG 后，页面会把它映射到同一个 1000 × 1000 坐标平面。";
    }
    updateLayerState();
  }

  function applySceneGraphText(text: string, sourceLabel: string, updateTextarea: boolean): void {
    const graph = parseSceneGraphText(text);
    if (updateTextarea && graphJsonInput) {
      graphJsonInput.value = JSON.stringify(JSON.parse(text), null, 2);
    }
    renderSceneGraph(graph, sourceLabel, "success");
  }

  renderSceneGraph(currentGraph, "Using bundled sample scene graph.", "neutral");

  backButton?.addEventListener(
    "click",
    () => {
      window.location.hash = "#viewer";
    },
    { signal },
  );
  imageInput?.addEventListener(
    "change",
    async () => {
      const file = imageInput.files?.[0];
      if (!file || !imageMetaEl) {
        return;
      }
      try {
        imageMetaEl.textContent = `正在导入 ${file.name}...`;
        await loadOriginalImage(file);
      } catch (error) {
        resetOriginalImage();
        imageMetaEl.textContent = error instanceof Error ? error.message : "导入 PNG 失败。";
      }
    },
    { signal },
  );
  resetImageButton?.addEventListener(
    "click",
    () => {
      resetOriginalImage();
    },
    { signal },
  );
  showOriginalInput?.addEventListener("change", updateLayerState, { signal });
  showGraphInput?.addEventListener("change", updateLayerState, { signal });
  originalOpacityInput?.addEventListener("input", updateLayerState, { signal });
  graphOpacityInput?.addEventListener("input", updateLayerState, { signal });
  applyGraphButton?.addEventListener(
    "click",
    () => {
      const text = graphJsonInput?.value.trim() ?? "";
      if (!text) {
        setStatus(graphStatusEl, "Paste scene graph JSON first.", "error");
        return;
      }
      try {
        applySceneGraphText(text, "Scene graph updated from pasted JSON.", true);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to parse scene graph JSON.";
        setStatus(graphStatusEl, message, "error");
      }
    },
    { signal },
  );
  graphFileInput?.addEventListener(
    "change",
    async () => {
      const file = graphFileInput.files?.[0];
      if (!file) {
        return;
      }
      try {
        const text = await file.text();
        if (graphJsonInput) {
          graphJsonInput.value = text;
        }
        applySceneGraphText(text, `Scene graph loaded from ${file.name}.`, false);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to import scene graph file.";
        setStatus(graphStatusEl, message, "error");
      } finally {
        graphFileInput.value = "";
      }
    },
    { signal },
  );
  resetGraphButton?.addEventListener(
    "click",
    () => {
      if (graphJsonInput) {
        graphJsonInput.value = DEFAULT_SCENE_GRAPH_TEXT;
      }
      renderSceneGraph(DEFAULT_SCENE_GRAPH, "Reset to bundled sample scene graph.", "neutral");
    },
    { signal },
  );

  return () => {
    revokeCurrentObjectUrl();
    eventController.abort();
  };
}
