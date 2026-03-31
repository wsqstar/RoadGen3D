import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { GLTFExporter } from "three/examples/jsm/exporters/GLTFExporter.js";
import { CSS2DRenderer, CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";

/* ── Types ─────────────────────────────────────────────────────────── */

type AssetRecord = {
  asset_id: string;
  category: string;
  asset_role?: string;
  theme_tags?: string[];
  text_desc?: string;
  mesh_path: string;
  latent_path?: string;
  license?: string;
  source?: string;
  split?: string;
  generator_type?: string;
  mesh_face_count?: number;
  quality_metrics?: { face_count?: number; vertex_count?: number };
  quality_tier?: number;
  scene_eligible?: boolean;
  quality_notes?: string[];
  tags?: string[];
  face_count?: number;
  vertex_count?: number;
  // Scale and orientation fields
  scale?: number;
  scale_xyz?: [number, number, number];
  yaw_deg?: number;
  canonical_front?: string; // e.g., "+X", "-Z"
  dimensions_m?: { width?: number; height?: number; depth?: number };
  [key: string]: unknown;
};

type ManifestInfo = {
  name: string;
  label: string;
  count: number;
};

type SceneChildInfo = {
  name: string;
  type: string;
  vertexCount: number;
  faceCount: number;
  uuid: string;
  bbox: { w: number; h: number; d: number };
  isDuplicate: boolean;
  duplicateGroup: number;
};

type AssetEditorState = {
  manifestName: string;
  assets: AssetRecord[];
  filteredAssets: AssetRecord[];
  selectedAssetId: string | null;
  selectedObjects: Set<string>;
  scaleValue: number;
  renderMode: "solid" | "wireframe";
  searchQuery: string;
  categoryFilter: string;
  qualityTierFilter: string;
  sceneChildren: SceneChildInfo[];
  selectionMode: boolean;
  selectedMeshes: Set<THREE.Mesh>;
  // Pagination state
  totalAssets: number;
  loadedOffset: number;
  hasMoreAssets: boolean;
  isLoadingMore: boolean;
  // Scale and orientation state
  yawValue: number;
  frontDirection: string;
  modelDimensions: { width?: number; height?: number; depth?: number } | null;
};

/* ── Helpers ───────────────────────────────────────────────────────── */

function qs<T extends HTMLElement>(parent: ParentNode, sel: string): T {
  const el = parent.querySelector<T>(sel);
  if (!el) throw new Error(`Required element not found: ${sel}`);
  return el;
}

function shortId(assetId: string): string {
  if (assetId.length > 36) return assetId.slice(0, 12) + "..." + assetId.slice(-6);
  return assetId;
}

function categoryBadgeClass(cat: string): string {
  const map: Record<string, string> = {
    tree: "badge-tree",
    lamp: "badge-lamp",
    bench: "badge-bench",
    sign: "badge-sign",
    car: "badge-car",
    building: "badge-building",
  };
  return map[cat] ?? "badge-default";
}

function tierColor(tier: number | undefined): string {
  if (tier === undefined || tier === null) return "#9ca3af";
  if (tier >= 4) return "#16a34a";
  if (tier >= 3) return "#2563eb";
  if (tier >= 2) return "#d97706";
  return "#dc2626";
}

/* ── API ───────────────────────────────────────────────────────────── */

async function fetchManifests(): Promise<ManifestInfo[]> {
  const res = await fetch("/api/asset-manifests");
  if (!res.ok) throw new Error(`Failed to fetch manifests: ${res.status}`);
  const data = await res.json();
  return data.manifests ?? [];
}

type ManifestAssetsResponse = {
  assets: AssetRecord[];
  total: number;
  offset: number;
  limit: number;
  hasMore: boolean;
};

async function fetchManifestAssets(
  name: string,
  offset: number = 0,
  limit: number = 100,
): Promise<ManifestAssetsResponse> {
  const res = await fetch(
    `/api/asset-manifest?name=${encodeURIComponent(name)}&offset=${offset}&limit=${limit}`,
  );
  if (!res.ok) throw new Error(`Failed to fetch manifest: ${res.status}`);
  const data = await res.json();
  return {
    assets: data.assets ?? [],
    total: data.total ?? 0,
    offset: data.offset ?? offset,
    limit: data.limit ?? limit,
    hasMore: data.hasMore ?? false,
  };
}

async function saveAssetMetadata(
  manifestName: string,
  assetId: string,
  updates: Record<string, unknown>,
): Promise<void> {
  const res = await fetch("/api/asset-manifest/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ manifest_name: manifestName, asset_id: assetId, updates }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error ?? `Save failed: ${res.status}`);
  }
}

async function deleteAssetRecord(
  manifestName: string,
  assetId: string,
): Promise<void> {
  const res = await fetch("/api/asset-manifest/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ manifest_name: manifestName, asset_id: assetId }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error ?? `Delete failed: ${res.status}`);
  }
}

/* ── Three.js Preview ──────────────────────────────────────────────── */

type PreviewContext = {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  animId: number;
  currentModel: THREE.Group | null;
  bboxHelper: THREE.Box3Helper | null;
  gridHelper: THREE.GridHelper;
  wireframeMaterial: THREE.MeshBasicMaterial;
  originalMaterials: Map<THREE.Mesh, THREE.Material | THREE.Material[]>;
  selectionBox: SelectionBox | null;
  selectionHelper: SelectionHelper | null;
  // Scale bar and orientation
  labelRenderer: CSS2DRenderer;
  scaleBarGroup: THREE.Group | null;
  frontArrow: THREE.ArrowHelper | null;
};

type SelectionBox = {
  startPoint: THREE.Vector2;
  endPoint: THREE.Vector2;
  isSelecting: boolean;
  domElement: HTMLElement;
};

type SelectionHelper = {
  element: HTMLDivElement;
  startPoint: THREE.Vector2;
  pointTopLeft: THREE.Vector2;
  pointBottomRight: THREE.Vector2;
  isDown: boolean;
  enabled: boolean;
};

/* ── Scale Bar Helper ──────────────────────────────────────────────── */

function createScaleBar(scene: THREE.Scene): THREE.Group {
  const group = new THREE.Group();
  group.name = "scaleBar";

  const length = 5; // 5 meters
  const tickInterval = 1; // 1 meter ticks
  const tickHeight = 0.1;
  const majorTickHeight = 0.2;

  // Main line along X-axis
  const mainLineGeometry = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, 0.01, 0),
    new THREE.Vector3(length, 0.01, 0),
  ]);
  const mainLineMaterial = new THREE.LineBasicMaterial({ color: 0xffffff, linewidth: 2 });
  const mainLine = new THREE.Line(mainLineGeometry, mainLineMaterial);
  group.add(mainLine);

  // Create tick marks and labels
  for (let i = 0; i <= length; i += tickInterval) {
    const isMajor = i % 1 === 0;
    const height = isMajor ? majorTickHeight : tickHeight;

    // Tick mark
    const tickGeometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(i, 0.01, 0),
      new THREE.Vector3(i, 0.01, -height),
    ]);
    const tickMaterial = new THREE.LineBasicMaterial({ color: 0xffffff });
    const tick = new THREE.Line(tickGeometry, tickMaterial);
    group.add(tick);

    // Label for major ticks
    if (isMajor && i > 0) {
      const labelDiv = document.createElement("div");
      labelDiv.className = "ae-ruler-label";
      labelDiv.textContent = `${i}m`;
      labelDiv.style.cssText = `
        color: #ffffff;
        font-family: "SF Mono", "Roboto Mono", monospace;
        font-size: 11px;
        font-weight: 600;
        background: rgba(0, 0, 0, 0.6);
        padding: 2px 4px;
        border-radius: 3px;
        white-space: nowrap;
      `;
      const label = new CSS2DObject(labelDiv);
      label.position.set(i, 0.01, -height - 0.15);
      group.add(label);
    }
  }

  // Origin label
  const originLabelDiv = document.createElement("div");
  originLabelDiv.className = "ae-ruler-label";
  originLabelDiv.textContent = "0";
  originLabelDiv.style.cssText = `
    color: #ffffff;
    font-family: "SF Mono", "Roboto Mono", monospace;
    font-size: 10px;
    font-weight: 600;
    background: rgba(0, 0, 0, 0.6);
    padding: 2px 4px;
    border-radius: 3px;
    white-space: nowrap;
  `;
  const originLabel = new CSS2DObject(originLabelDiv);
  originLabel.position.set(0, 0.01, -majorTickHeight - 0.15);
  group.add(originLabel);

  scene.add(group);
  return group;
}

function createPreviewScene(container: HTMLElement): PreviewContext {
  const width = container.clientWidth || 600;
  const height = container.clientHeight || 400;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(width, height);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x2a2a2e);

  const camera = new THREE.PerspectiveCamera(50, width / height, 0.01, 1000);
  camera.position.set(3, 2, 3);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.target.set(0, 0.5, 0);
  controls.update();

  const ambient = new THREE.HemisphereLight(0xddeeff, 0x8899aa, 1.2);
  scene.add(ambient);
  const dirLight = new THREE.DirectionalLight(0xffffff, 1.5);
  dirLight.position.set(5, 8, 5);
  scene.add(dirLight);

  const gridHelper = new THREE.GridHelper(10, 20, 0x555555, 0x333333);
  scene.add(gridHelper);

  // CSS2D Renderer for labels
  const labelRenderer = new CSS2DRenderer();
  labelRenderer.setSize(width, height);
  labelRenderer.domElement.style.position = "absolute";
  labelRenderer.domElement.style.top = "0";
  labelRenderer.domElement.style.pointerEvents = "none";
  container.appendChild(labelRenderer.domElement);

  // Scale bar
  const scaleBarGroup = createScaleBar(scene);

  // Front direction arrow (initially hidden)
  const frontArrow = new THREE.ArrowHelper(
    new THREE.Vector3(0, 0, 1), // +Z direction
    new THREE.Vector3(0, 0, 0),
    1, // length
    0x00ff88, // color
    0.2, // head length
    0.1, // head width
  );
  frontArrow.visible = false;
  scene.add(frontArrow);

  const wireframeMaterial = new THREE.MeshBasicMaterial({
    color: 0x88ccff,
    wireframe: true,
  });

  const selectionHelper = createSelectionHelper(container);

  const ctx: PreviewContext = {
    renderer,
    scene,
    camera,
    controls,
    animId: 0,
    currentModel: null,
    bboxHelper: null,
    gridHelper,
    wireframeMaterial,
    originalMaterials: new Map(),
    selectionBox: null,
    selectionHelper,
    labelRenderer,
    scaleBarGroup,
    frontArrow,
  };

  function animate() {
    ctx.animId = requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
  }
  animate();

  const onResize = () => {
    const w = container.clientWidth || 600;
    const h = container.clientHeight || 400;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    labelRenderer.setSize(w, h);
  };
  const resizeObs = new ResizeObserver(onResize);
  resizeObs.observe(container);

  return ctx;
}

function loadModelIntoPreview(
  ctx: PreviewContext,
  glbUrl: string,
): Promise<{ model: THREE.Group; children: SceneChildInfo[] }> {
  return new Promise((resolve, reject) => {
    const loader = new GLTFLoader();
    loader.load(
      glbUrl,
      (gltf) => {
        // Remove previous model
        if (ctx.currentModel) {
          ctx.scene.remove(ctx.currentModel);
          ctx.currentModel.traverse((child) => {
            if ((child as THREE.Mesh).isMesh) {
              (child as THREE.Mesh).geometry.dispose();
            }
          });
        }
        if (ctx.bboxHelper) {
          ctx.scene.remove(ctx.bboxHelper);
          ctx.bboxHelper = null;
        }
        ctx.originalMaterials.clear();

        const model = gltf.scene;
        ctx.currentModel = model;
        ctx.scene.add(model);

        // Center model
        const box = new THREE.Box3().setFromObject(model);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        model.position.sub(center);
        model.position.y += size.y / 2;

        // Fit camera
        const maxDim = Math.max(size.x, size.y, size.z);
        const dist = maxDim * 1.8;
        ctx.camera.position.set(dist * 0.7, dist * 0.5, dist * 0.7);
        ctx.controls.target.set(0, size.y / 2, 0);
        ctx.controls.update();

        // Analyze children
        const children = analyzeChildren(model);
        resolve({ model, children });
      },
      undefined,
      (err) => reject(err),
    );
  });
}

function analyzeChildren(model: THREE.Group): SceneChildInfo[] {
  const children: SceneChildInfo[] = [];
  const meshGroups = new Map<string, number[]>();

  model.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) {
      const mesh = child as THREE.Mesh;
      const geom = mesh.geometry;
      const vCount = geom.attributes.position ? geom.attributes.position.count : 0;
      const fCount = geom.index ? geom.index.count / 3 : vCount / 3;

      const box = new THREE.Box3().setFromObject(mesh);
      const size = box.getSize(new THREE.Vector3());

      const info: SceneChildInfo = {
        name: mesh.name || `unnamed_${children.length}`,
        type: mesh.type,
        vertexCount: vCount,
        faceCount: Math.round(fCount),
        uuid: mesh.uuid,
        bbox: { w: +size.x.toFixed(4), h: +size.y.toFixed(4), d: +size.z.toFixed(4) },
        isDuplicate: false,
        duplicateGroup: -1,
      };
      children.push(info);

      // Duplicate key: vertex count + rounded bbox
      const key = `${vCount}|${size.x.toFixed(3)}|${size.y.toFixed(3)}|${size.z.toFixed(3)}`;
      if (!meshGroups.has(key)) meshGroups.set(key, []);
      meshGroups.get(key)!.push(children.length - 1);
    }
  });

  // Mark duplicates
  let groupIdx = 0;
  for (const indices of meshGroups.values()) {
    if (indices.length > 1) {
      for (const idx of indices) {
        children[idx].isDuplicate = true;
        children[idx].duplicateGroup = groupIdx;
      }
      groupIdx++;
    }
  }

  return children;
}

function toggleWireframe(ctx: PreviewContext, enabled: boolean) {
  if (!ctx.currentModel) return;
  ctx.currentModel.traverse((child) => {
    if ((child as THREE.Mesh).isMesh) {
      const mesh = child as THREE.Mesh;
      if (enabled) {
        ctx.originalMaterials.set(mesh, mesh.material as THREE.Material | THREE.Material[]);
        mesh.material = ctx.wireframeMaterial;
      } else {
        const orig = ctx.originalMaterials.get(mesh);
        if (orig) mesh.material = orig;
      }
    }
  });
}

function toggleBbox(ctx: PreviewContext, show: boolean) {
  if (!ctx.currentModel) return;
  if (show) {
    if (ctx.bboxHelper) ctx.scene.remove(ctx.bboxHelper);
    const box = new THREE.Box3().setFromObject(ctx.currentModel);
    ctx.bboxHelper = new THREE.Box3Helper(box, 0x00ff88);
    ctx.scene.add(ctx.bboxHelper);
  } else {
    if (ctx.bboxHelper) {
      ctx.scene.remove(ctx.bboxHelper);
      ctx.bboxHelper = null;
    }
  }
}

function zoomToFit(ctx: PreviewContext) {
  if (!ctx.currentModel) return;
  const box = new THREE.Box3().setFromObject(ctx.currentModel);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z);
  const dist = maxDim * 1.8;
  ctx.camera.position.set(center.x + dist * 0.7, center.y + dist * 0.5, center.z + dist * 0.7);
  ctx.controls.target.copy(center);
  ctx.controls.update();
}

function applyScale(ctx: PreviewContext, factor: number) {
  if (!ctx.currentModel) return;
  ctx.currentModel.scale.setScalar(factor);
  zoomToFit(ctx);
}

function applyYaw(ctx: PreviewContext, yawDeg: number) {
  if (!ctx.currentModel) return;
  // Normalize yaw to [0, 360)
  const normalizedYaw = ((yawDeg % 360) + 360) % 360;
  ctx.currentModel.rotation.y = (normalizedYaw * Math.PI) / 180;
  // Update front arrow rotation if visible
  if (ctx.frontArrow) {
    ctx.frontArrow.rotation.y = (normalizedYaw * Math.PI) / 180;
  }
}

function updateFrontArrow(ctx: PreviewContext, frontDirection: string, yawDeg: number = 0) {
  if (!ctx.frontArrow) return;

  // Direction vectors for each canonical front
  const directions: Record<string, THREE.Vector3> = {
    "+X": new THREE.Vector3(1, 0, 0),
    "-X": new THREE.Vector3(-1, 0, 0),
    "+Z": new THREE.Vector3(0, 0, 1),
    "-Z": new THREE.Vector3(0, 0, -1),
  };

  const dir = directions[frontDirection] || directions["+Z"];
  ctx.frontArrow.setDirection(dir);
  ctx.frontArrow.rotation.y = (yawDeg * Math.PI) / 180;
  ctx.frontArrow.visible = true;
}

function getModelDimensions(ctx: PreviewContext): { width: number; height: number; depth: number } | null {
  if (!ctx.currentModel) return null;
  const box = new THREE.Box3().setFromObject(ctx.currentModel);
  const size = box.getSize(new THREE.Vector3());
  return {
    width: +size.x.toFixed(3),
    height: +size.y.toFixed(3),
    depth: +size.z.toFixed(3),
  };
}

function exportGlb(scene: THREE.Object3D): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const exporter = new GLTFExporter();
    exporter.parse(
      scene,
      (result) => {
        resolve(result as ArrayBuffer);
      },
      (error) => reject(error),
      { binary: true },
    );
  });
}

function triggerDownload(data: ArrayBuffer, filename: string) {
  const blob = new Blob([data], { type: "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/* ── Selection Box (Rectangle Selection) ──────────────────────────── */

function createSelectionHelper(container: HTMLElement): SelectionHelper {
  const element = document.createElement("div");
  element.style.cssText = `
    position: absolute;
    border: 2px dashed #00a8ff;
    background: rgba(0, 168, 255, 0.1);
    pointer-events: none;
    display: none;
    z-index: 100;
  `;
  container.style.position = "relative";
  container.appendChild(element);

  return {
    element,
    startPoint: new THREE.Vector2(),
    pointTopLeft: new THREE.Vector2(),
    pointBottomRight: new THREE.Vector2(),
    isDown: false,
    enabled: true,
  };
}

function updateSelectionBox(
  helper: SelectionHelper,
  startX: number,
  startY: number,
  currentX: number,
  currentY: number,
) {
  const x = Math.min(startX, currentX);
  const y = Math.min(startY, currentY);
  const width = Math.abs(currentX - startX);
  const height = Math.abs(currentY - startY);

  helper.element.style.left = `${x}px`;
  helper.element.style.top = `${y}px`;
  helper.element.style.width = `${width}px`;
  helper.element.style.height = `${height}px`;
  helper.element.style.display = "block";

  helper.pointTopLeft.set(x, y);
  helper.pointBottomRight.set(x + width, y + height);
}

function hideSelectionBox(helper: SelectionHelper) {
  helper.element.style.display = "none";
  helper.isDown = false;
}

function getMeshesInSelectionArea(
  ctx: PreviewContext,
  helper: SelectionHelper,
): THREE.Mesh[] {
  if (!ctx.currentModel) return [];

  const rect = ctx.renderer.domElement.getBoundingClientRect();
  const selectedMeshes: THREE.Mesh[] = [];

  ctx.currentModel.traverse((child) => {
    if (!(child as THREE.Mesh).isMesh) return;
    const mesh = child as THREE.Mesh;

    // Get mesh bounding box center in screen space
    const box = new THREE.Box3().setFromObject(mesh);
    const center = box.getCenter(new THREE.Vector3());
    center.project(ctx.camera);

    const screenX = (center.x * 0.5 + 0.5) * rect.width;
    const screenY = (-center.y * 0.5 + 0.5) * rect.height;

    // Check if center is within selection box
    if (
      screenX >= helper.pointTopLeft.x &&
      screenX <= helper.pointBottomRight.x &&
      screenY >= helper.pointTopLeft.y &&
      screenY <= helper.pointBottomRight.y
    ) {
      selectedMeshes.push(mesh);
    }
  });

  return selectedMeshes;
}

function highlightMesh(ctx: PreviewContext, mesh: THREE.Mesh, highlighted: boolean) {
  if (highlighted) {
    if (!ctx.originalMaterials.has(mesh)) {
      ctx.originalMaterials.set(mesh, mesh.material as THREE.Material | THREE.Material[]);
    }
    const highlightMaterial = new THREE.MeshBasicMaterial({
      color: 0x00ff88,
      transparent: true,
      opacity: 0.5,
    });
    mesh.material = highlightMaterial;
  } else {
    const original = ctx.originalMaterials.get(mesh);
    if (original) {
      mesh.material = original;
    }
  }
}

function deleteSelectedMeshes(ctx: PreviewContext, meshes: THREE.Mesh[]): number {
  let deletedCount = 0;
  for (const mesh of meshes) {
    if (mesh.parent) {
      mesh.parent.remove(mesh);
      if (mesh.geometry) mesh.geometry.dispose();
      if (mesh.material) {
        if (Array.isArray(mesh.material)) {
          mesh.material.forEach((m) => m.dispose());
        } else {
          mesh.material.dispose();
        }
      }
      deletedCount++;
    }
  }
  ctx.originalMaterials.clear();
  return deletedCount;
}

/* ── Toast ─────────────────────────────────────────────────────────── */

function showToast(root: HTMLElement, message: string, type: "success" | "error" = "success") {
  let container = root.querySelector(".ae-toast-container") as HTMLDivElement;
  if (!container) {
    container = document.createElement("div");
    container.className = "ae-toast-container";
    root.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = `ae-toast ae-toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.classList.add("ae-toast-show"), 10);
  setTimeout(() => {
    toast.classList.remove("ae-toast-show");
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

/* ── Main Mount ────────────────────────────────────────────────────── */

export function mountAssetEditor(root: HTMLElement): () => void {
  const state: AssetEditorState = {
    manifestName: "",
    assets: [],
    filteredAssets: [],
    selectedAssetId: null,
    selectedObjects: new Set(),
    scaleValue: 1,
    renderMode: "solid",
    searchQuery: "",
    categoryFilter: "",
    qualityTierFilter: "",
    sceneChildren: [],
    selectionMode: false,
    selectedMeshes: new Set(),
    totalAssets: 0,
    loadedOffset: 0,
    hasMoreAssets: false,
    isLoadingMore: false,
    yawValue: 0,
    frontDirection: "+Z",
    modelDimensions: null,
  };

  let previewCtx: PreviewContext | null = null;
  let destroyed = false;

  root.innerHTML = `
    <div class="scene-page">
      <div class="scene-page-topbar">
        <div>
          <div class="scene-page-kicker">Viewer / 3D Asset Editor</div>
          <h1 class="scene-page-title">3D Asset Editor</h1>
          <p class="scene-page-subtitle">Browse, inspect, and manage project 3D assets</p>
        </div>
        <div class="scene-page-actions">
          <select id="ae-manifest-select" class="ae-manifest-select">
            <option value="">-- Select Manifest --</option>
          </select>
          <button id="ae-back-btn" class="viewer-nav-button" type="button">Back to Viewer</button>
        </div>
      </div>

      <div class="asset-editor-layout">
        <!-- Left: Gallery -->
        <div class="asset-gallery-panel">
          <div class="ae-filter-bar">
            <input id="ae-search" type="text" placeholder="Search assets..." class="ae-search-input" />
            <select id="ae-category-filter" class="ae-filter-select">
              <option value="">All Categories</option>
            </select>
            <select id="ae-tier-filter" class="ae-filter-select">
              <option value="">All Tiers</option>
              <option value="5">T5 — Excellent</option>
              <option value="4">T4 — Good</option>
              <option value="3">T3 — Production</option>
              <option value="2">T2 — Moderate</option>
              <option value="1">T1 — Low-poly</option>
              <option value="0">T0 — Unusable</option>
            </select>
          </div>
          <div class="ae-gallery-stats" id="ae-gallery-stats"></div>
          <div class="ae-gallery-grid" id="ae-gallery-grid"></div>
          <div class="ae-load-more-section" id="ae-load-more-section" style="display:none;">
            <button id="ae-load-more-btn" class="ae-load-more-btn" type="button">Load More</button>
            <span id="ae-load-more-info" class="ae-load-more-info"></span>
          </div>
        </div>

        <!-- Right: Detail -->
        <div class="asset-detail-panel" id="ae-detail-panel">
          <div class="ae-empty-state" id="ae-empty-state">
            <div class="ae-empty-icon">&#9881;</div>
            <p>Select an asset from the gallery to inspect</p>
          </div>

          <div class="ae-detail-content" id="ae-detail-content" style="display:none;">
            <!-- Preview Canvas -->
            <div class="ae-preview-section">
              <div class="ae-preview-toolbar">
                <button id="ae-mode-solid" class="ae-toolbar-btn active" title="Solid render">Solid</button>
                <button id="ae-mode-wire" class="ae-toolbar-btn" title="Wireframe">Wire</button>
                <span class="ae-toolbar-sep"></span>
                <button id="ae-toggle-bbox" class="ae-toolbar-btn" title="Bounding box">BBox</button>
                <button id="ae-zoom-fit" class="ae-toolbar-btn" title="Zoom to fit">Fit</button>
                <span class="ae-toolbar-sep"></span>
                <button id="ae-toggle-select" class="ae-toolbar-btn" title="Rectangle selection mode">Select</button>
                <button id="ae-delete-selected" class="ae-toolbar-btn ae-btn-danger" title="Delete selected objects" disabled>Delete</button>
                <span class="ae-toolbar-sep"></span>
                <button id="ae-delete-record" class="ae-toolbar-btn ae-btn-danger" title="Delete this asset from manifest" disabled>Del Record</button>
              </div>
              <div class="ae-preview-canvas" id="ae-preview-canvas"></div>
            </div>

            <!-- Info Panel -->
            <div class="ae-info-section" id="ae-info-section">
              <h3 class="ae-section-title">Asset Information</h3>
              <div class="ae-info-grid" id="ae-info-grid"></div>
            </div>

            <!-- Scene Objects -->
            <div class="ae-objects-section" id="ae-objects-section" style="display:none;">
              <h3 class="ae-section-title">Scene Objects <span id="ae-dup-count" class="ae-dup-badge" style="display:none;"></span></h3>
              <div class="ae-object-list" id="ae-object-list"></div>
            </div>

            <!-- Actions -->
            <div class="ae-actions-bar">
              <button id="ae-save-btn" class="ae-action-btn ae-btn-primary" disabled>Save Metadata</button>
              <div class="ae-scale-group">
                <label class="ae-scale-label">Scale:</label>
                <input id="ae-scale-input" type="number" class="ae-scale-input" value="1" min="0.01" max="100" step="0.1" />
                <button id="ae-apply-scale" class="ae-action-btn">Apply</button>
                <button id="ae-export-btn" class="ae-action-btn">Export GLB</button>
              </div>
              <div class="ae-orientation-group">
                <label class="ae-yaw-label">Yaw (°):</label>
                <input id="ae-yaw-input" type="number" class="ae-yaw-input" value="0" min="-180" max="360" step="1" />
                <button id="ae-apply-yaw" class="ae-action-btn">Apply</button>
              </div>
              <div class="ae-front-group">
                <label class="ae-front-label">Front:</label>
                <select id="ae-front-select" class="ae-front-select">
                  <option value="+X">+X</option>
                  <option value="-X">-X</option>
                  <option value="+Z" selected>+Z</option>
                  <option value="-Z">-Z</option>
                </select>
              </div>
              <span class="ae-actions-sep"></span>
              <button id="ae-remove-dups-btn" class="ae-action-btn ae-btn-warning" disabled>Remove Duplicates</button>
              <button id="ae-split-btn" class="ae-action-btn ae-btn-secondary" disabled>Split Selected</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;

  /* ── DOM refs ──────────────────────────────────────────────────── */
  const manifestSelect = qs<HTMLSelectElement>(root, "#ae-manifest-select");
  const backBtn = qs<HTMLButtonElement>(root, "#ae-back-btn");
  const searchInput = qs<HTMLInputElement>(root, "#ae-search");
  const categoryFilter = qs<HTMLSelectElement>(root, "#ae-category-filter");
  const tierFilter = qs<HTMLSelectElement>(root, "#ae-tier-filter");
  const galleryStats = qs<HTMLDivElement>(root, "#ae-gallery-stats");
  const galleryGrid = qs<HTMLDivElement>(root, "#ae-gallery-grid");
  const detailPanel = qs<HTMLDivElement>(root, "#ae-detail-panel");
  const emptyState = qs<HTMLDivElement>(root, "#ae-empty-state");
  const detailContent = qs<HTMLDivElement>(root, "#ae-detail-content");
  const previewCanvas = qs<HTMLDivElement>(root, "#ae-preview-canvas");
  const infoGrid = qs<HTMLDivElement>(root, "#ae-info-grid");
  const objectSection = qs<HTMLDivElement>(root, "#ae-objects-section");
  const objectList = qs<HTMLDivElement>(root, "#ae-object-list");
  const dupCount = qs<HTMLSpanElement>(root, "#ae-dup-count");
  const saveBtn = qs<HTMLButtonElement>(root, "#ae-save-btn");
  const scaleInput = qs<HTMLInputElement>(root, "#ae-scale-input");
  const applyScaleBtn = qs<HTMLButtonElement>(root, "#ae-apply-scale");
  const exportBtn = qs<HTMLButtonElement>(root, "#ae-export-btn");
  const removeDupsBtn = qs<HTMLButtonElement>(root, "#ae-remove-dups-btn");
  const splitBtn = qs<HTMLButtonElement>(root, "#ae-split-btn");
  const modeSolid = qs<HTMLButtonElement>(root, "#ae-mode-solid");
  const modeWire = qs<HTMLButtonElement>(root, "#ae-mode-wire");
  const toggleBboxBtn = qs<HTMLButtonElement>(root, "#ae-toggle-bbox");
  const zoomFitBtn = qs<HTMLButtonElement>(root, "#ae-zoom-fit");
  const toggleSelectBtn = qs<HTMLButtonElement>(root, "#ae-toggle-select");
  const deleteSelectedBtn = qs<HTMLButtonElement>(root, "#ae-delete-selected");
  const deleteRecordBtn = qs<HTMLButtonElement>(root, "#ae-delete-record");
  const loadMoreSection = qs<HTMLDivElement>(root, "#ae-load-more-section");
  const loadMoreBtn = qs<HTMLButtonElement>(root, "#ae-load-more-btn");
  const loadMoreInfo = qs<HTMLSpanElement>(root, "#ae-load-more-info");
  const yawInput = qs<HTMLInputElement>(root, "#ae-yaw-input");
  const applyYawBtn = qs<HTMLButtonElement>(root, "#ae-apply-yaw");
  const frontSelect = qs<HTMLSelectElement>(root, "#ae-front-select");

  /* ── Navigation ────────────────────────────────────────────────── */
  backBtn.addEventListener("click", () => {
    window.location.hash = "";
  });

  /* ── Manifest loading ──────────────────────────────────────────── */
  async function initManifests() {
    try {
      const manifests = await fetchManifests();
      for (const m of manifests) {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = `${m.label} (${m.count})`;
        manifestSelect.appendChild(opt);
      }
    } catch (err) {
      showToast(root, `Failed to load manifests: ${err}`, "error");
    }
  }

  manifestSelect.addEventListener("change", async () => {
    const name = manifestSelect.value;
    if (!name) return;
    state.manifestName = name;
    state.selectedAssetId = null;
    state.selectedObjects.clear();
    state.assets = [];
    state.loadedOffset = 0;
    state.hasMoreAssets = false;
    showEmptyState();
    
    try {
      const response = await fetchManifestAssets(name, 0, 100);
      state.assets = response.assets;
      state.totalAssets = response.total;
      state.loadedOffset = response.offset + response.assets.length;
      state.hasMoreAssets = response.hasMore;
      
      updateCategoryFilter();
      applyFilters();
      updateLoadMoreSection();
    } catch (err) {
      showToast(root, `Failed to load manifest: ${err}`, "error");
    }
  });

  /* ── Load More ─────────────────────────────────────────────────── */
  function updateLoadMoreSection() {
    if (state.hasMoreAssets) {
      loadMoreSection.style.display = "";
      loadMoreBtn.disabled = state.isLoadingMore;
      loadMoreInfo.textContent = `Loaded ${state.assets.length} of ${state.totalAssets.toLocaleString()} assets`;
    } else {
      loadMoreSection.style.display = "none";
    }
  }

  loadMoreBtn.addEventListener("click", async () => {
    if (!state.manifestName || state.isLoadingMore || !state.hasMoreAssets) return;
    
    state.isLoadingMore = true;
    loadMoreBtn.disabled = true;
    loadMoreBtn.textContent = "Loading...";
    
    try {
      const response = await fetchManifestAssets(state.manifestName, state.loadedOffset, 100);
      state.assets = [...state.assets, ...response.assets];
      state.loadedOffset += response.assets.length;
      state.hasMoreAssets = response.hasMore;
      
      updateCategoryFilter();
      applyFilters();
      updateLoadMoreSection();
    } catch (err) {
      showToast(root, `Failed to load more: ${err}`, "error");
    } finally {
      state.isLoadingMore = false;
      loadMoreBtn.disabled = false;
      loadMoreBtn.textContent = "Load More";
    }
  });

  /* ── Category filter ───────────────────────────────────────────── */
  function updateCategoryFilter() {
    const cats = new Set<string>();
    for (const a of state.assets) {
      if (a.category) cats.add(a.category);
    }
    categoryFilter.innerHTML = '<option value="">All Categories</option>';
    for (const cat of Array.from(cats).sort()) {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat;
      categoryFilter.appendChild(opt);
    }
  }

  /* ── Filters ───────────────────────────────────────────────────── */
  function applyFilters() {
    const q = state.searchQuery.toLowerCase();
    const cat = state.categoryFilter;
    const tier = state.qualityTierFilter;

    state.filteredAssets = state.assets.filter((a) => {
      if (q) {
        const text = `${a.asset_id} ${a.category} ${a.text_desc ?? ""} ${(a.tags ?? []).join(" ")}`.toLowerCase();
        if (!text.includes(q)) return false;
      }
      if (cat && a.category !== cat) return false;
      if (tier && String(a.quality_tier) !== tier) return false;
      return true;
    });

    renderGallery();
  }

  searchInput.addEventListener("input", () => {
    state.searchQuery = searchInput.value;
    applyFilters();
  });
  categoryFilter.addEventListener("change", () => {
    state.categoryFilter = categoryFilter.value;
    applyFilters();
  });
  tierFilter.addEventListener("change", () => {
    state.qualityTierFilter = tierFilter.value;
    applyFilters();
  });

  /* ── Gallery rendering ─────────────────────────────────────────── */
  function renderGallery() {
    galleryGrid.innerHTML = "";
    
    // Show loaded count vs total count
    const loadedText = state.totalAssets > state.assets.length
      ? `${state.assets.length.toLocaleString()} loaded of ${state.totalAssets.toLocaleString()} total`
      : `${state.assets.length.toLocaleString()}`;
    galleryStats.textContent = `${state.filteredAssets.length} shown · ${loadedText} assets`;

    for (const asset of state.filteredAssets) {
      const card = document.createElement("div");
      card.className = "ae-asset-card" + (asset.asset_id === state.selectedAssetId ? " active" : "");
      card.dataset.assetId = asset.asset_id;

      const fCount = asset.face_count ?? asset.mesh_face_count ?? 0;
      const vCount = asset.vertex_count ?? asset.quality_metrics?.vertex_count ?? 0;
      const tier = asset.quality_tier;
      const eligible = asset.scene_eligible;
      const cat = asset.category || "unknown";

      card.innerHTML = `
        <div class="ae-card-header">
          <span class="ae-card-category ${categoryBadgeClass(cat)}">${cat}</span>
          ${eligible ? '<span class="ae-card-eligible" title="Scene eligible">&#10003;</span>' : ""}
        </div>
        <div class="ae-card-body">
          <div class="ae-card-id">${shortId(asset.asset_id)}</div>
          <div class="ae-card-stats">
            <span title="Faces">${fCount.toLocaleString()}f</span>
            <span title="Vertices">${vCount.toLocaleString()}v</span>
          </div>
        </div>
        <div class="ae-card-footer">
          <span class="ae-card-tier" style="color:${tierColor(tier)}">
            ${tier !== undefined ? `T${tier}` : "T?"}
          </span>
          <span class="ae-card-source">${asset.source ?? ""}</span>
        </div>
      `;

      card.addEventListener("click", () => selectAsset(asset.asset_id));
      galleryGrid.appendChild(card);
    }
  }

  /* ── Asset selection ───────────────────────────────────────────── */
  async function selectAsset(assetId: string) {
    state.selectedAssetId = assetId;
    state.selectedObjects.clear();
    state.sceneChildren = [];

    // Load existing scale, yaw, and front direction from asset record
    const asset = state.assets.find((a) => a.asset_id === assetId);
    state.scaleValue = asset?.scale ?? 1;
    state.yawValue = asset?.yaw_deg ?? 0;
    state.frontDirection = asset?.canonical_front ?? "+Z";
    state.modelDimensions = asset?.dimensions_m ?? null;

    scaleInput.value = String(state.scaleValue);
    yawInput.value = String(state.yawValue);
    frontSelect.value = state.frontDirection;

    // Update gallery selection
    galleryGrid.querySelectorAll(".ae-asset-card").forEach((el) => {
      el.classList.toggle("active", (el as HTMLElement).dataset.assetId === assetId);
    });

    if (!asset) return;

    emptyState.style.display = "none";
    detailContent.style.display = "";
    saveBtn.disabled = false;
    deleteRecordBtn.disabled = false;

    // Render info
    renderInfoPanel(asset);

    // Init Three.js preview if needed
    if (!previewCtx) {
      previewCtx = createPreviewScene(previewCanvas);
    }

    // Load GLB
    const meshPath = asset.mesh_path;
    if (meshPath) {
      const glbUrl = `/api/file?path=${encodeURIComponent(meshPath)}`;
      try {
        const { children } = await loadModelIntoPreview(previewCtx, glbUrl);
        state.sceneChildren = children;
        renderObjectList();
        updateActionButtons();

        // Compute and store model dimensions
        const dims = getModelDimensions(previewCtx);
        if (dims) {
          state.modelDimensions = dims;
          // Update dimensions display
          updateDimensionsDisplay(dims);
        }

        // Apply existing yaw from asset record
        if (state.yawValue !== 0) {
          applyYaw(previewCtx, state.yawValue);
        }

        // Show front arrow with saved direction
        updateFrontArrow(previewCtx, state.frontDirection, state.yawValue);
      } catch (err) {
        showToast(root, `Failed to load GLB: ${err}`, "error");
      }
    }
  }

  function showEmptyState() {
    emptyState.style.display = "";
    detailContent.style.display = "none";
    saveBtn.disabled = true;
    deleteRecordBtn.disabled = true;
  }

  /* ── Dimensions display ──────────────────────────────────────────── */
  function updateDimensionsDisplay(dims: { width?: number; height?: number; depth?: number } | null) {
    const dimsEl = document.getElementById("ae-dimensions-value");
    if (dimsEl && dims) {
      dimsEl.textContent = `${(dims.width ?? 0).toFixed(2)} × ${(dims.height ?? 0).toFixed(2)} × ${(dims.depth ?? 0).toFixed(2)}`;
    }
  }

  /* ── Info panel ────────────────────────────────────────────────── */
  function renderInfoPanel(asset: AssetRecord) {
    const fCount = asset.face_count ?? asset.mesh_face_count ?? 0;
    const vCount = asset.vertex_count ?? asset.quality_metrics?.vertex_count ?? 0;
    const dims = asset.dimensions_m ?? state.modelDimensions;
    const dimsText = dims
      ? `${(dims.width ?? 0).toFixed(2)} × ${(dims.height ?? 0).toFixed(2)} × ${(dims.depth ?? 0).toFixed(2)}`
      : "—";

    infoGrid.innerHTML = `
      <div class="ae-info-row ae-info-label">Asset ID</div>
      <div class="ae-info-row ae-info-value ae-mono">${asset.asset_id}</div>

      <div class="ae-info-row ae-info-label">Category</div>
      <div class="ae-info-row ae-info-value">${asset.category ?? "-"}</div>

      <div class="ae-info-row ae-info-label">Source</div>
      <div class="ae-info-row ae-info-value">${asset.source ?? "-"}</div>

      <div class="ae-info-row ae-info-label">License</div>
      <div class="ae-info-row ae-info-value">${asset.license ?? "-"}</div>

      <div class="ae-info-row ae-info-label">Faces / Vertices</div>
      <div class="ae-info-row ae-info-value ae-mono">${fCount.toLocaleString()} / ${vCount.toLocaleString()}</div>

      <div class="ae-info-row ae-info-label">Dimensions (m)</div>
      <div class="ae-info-row ae-info-value ae-mono" id="ae-dimensions-value">W×H×D: ${dimsText}</div>

      <div class="ae-info-row ae-info-label">Mesh Path</div>
      <div class="ae-info-row ae-info-value ae-mono ae-path">${asset.mesh_path ?? "-"}</div>

      <div class="ae-info-row ae-info-label">Description</div>
      <div class="ae-info-row ae-info-value ae-desc">${asset.text_desc ?? "-"}</div>

      <div class="ae-info-row ae-info-label">Quality Tier</div>
      <div class="ae-info-row ae-info-value">
        <select id="ae-edit-tier" class="ae-edit-select">
          <option value="">--</option>
          ${[1, 2, 3, 4, 5].map((t) => `<option value="${t}" ${asset.quality_tier === t ? "selected" : ""}>Tier ${t}</option>`).join("")}
        </select>
      </div>

      <div class="ae-info-row ae-info-label">Scene Eligible</div>
      <div class="ae-info-row ae-info-value">
        <input id="ae-edit-eligible" type="checkbox" ${asset.scene_eligible ? "checked" : ""} />
      </div>

      <div class="ae-info-row ae-info-label">Tags</div>
      <div class="ae-info-row ae-info-value">
        <input id="ae-edit-tags" type="text" class="ae-edit-input" value="${(asset.tags ?? []).join(", ")}" />
      </div>
    `;
  }

  /* ── Object list ───────────────────────────────────────────────── */
  function renderObjectList() {
    const children = state.sceneChildren;
    if (children.length === 0) {
      objectSection.style.display = "none";
      return;
    }
    objectSection.style.display = "";

    const dupGroups = new Set(children.filter((c) => c.isDuplicate).map((c) => c.duplicateGroup));
    if (dupGroups.size > 0) {
      dupCount.style.display = "";
      dupCount.textContent = `${dupGroups.size} duplicate group(s)`;
    } else {
      dupCount.style.display = "none";
    }

    objectList.innerHTML = "";
    for (const child of children) {
      const row = document.createElement("label");
      row.className = "ae-object-row" + (child.isDuplicate ? " ae-object-dup" : "");
      row.innerHTML = `
        <input type="checkbox" class="ae-object-check" data-uuid="${child.uuid}" />
        <span class="ae-object-name">${child.name}</span>
        <span class="ae-object-stats">${child.vertexCount}v ${child.faceCount}f</span>
        ${child.isDuplicate ? '<span class="ae-object-dup-tag">dup</span>' : ""}
      `;
      const check = row.querySelector<HTMLInputElement>(".ae-object-check")!;
      check.addEventListener("change", () => {
        if (check.checked) {
          state.selectedObjects.add(child.uuid);
        } else {
          state.selectedObjects.delete(child.uuid);
        }
        updateActionButtons();
      });
      objectList.appendChild(row);
    }
  }

  /* ── Action buttons state ──────────────────────────────────────── */
  function updateActionButtons() {
    const hasDups = state.sceneChildren.some((c) => c.isDuplicate);
    const hasSelection = state.selectedObjects.size > 0;
    removeDupsBtn.disabled = !hasDups;
    splitBtn.disabled = !hasSelection;
  }

  /* ── Preview toolbar ───────────────────────────────────────────── */
  modeSolid.addEventListener("click", () => {
    state.renderMode = "solid";
    modeSolid.classList.add("active");
    modeWire.classList.remove("active");
    if (previewCtx) toggleWireframe(previewCtx, false);
  });

  modeWire.addEventListener("click", () => {
    state.renderMode = "wireframe";
    modeWire.classList.add("active");
    modeSolid.classList.remove("active");
    if (previewCtx) toggleWireframe(previewCtx, true);
  });

  let bboxVisible = false;
  toggleBboxBtn.addEventListener("click", () => {
    bboxVisible = !bboxVisible;
    toggleBboxBtn.classList.toggle("active", bboxVisible);
    if (previewCtx) toggleBbox(previewCtx, bboxVisible);
  });

  zoomFitBtn.addEventListener("click", () => {
    if (previewCtx) zoomToFit(previewCtx);
  });

  /* ── Selection Box (Rectangle Selection) ───────────────────────── */
  function updateDeleteButtonState() {
    deleteSelectedBtn.disabled = state.selectedMeshes.size === 0;
  }

  function clearMeshSelection() {
    if (!previewCtx) return;
    for (const mesh of state.selectedMeshes) {
      highlightMesh(previewCtx, mesh, false);
    }
    state.selectedMeshes.clear();
    updateDeleteButtonState();
  }

  function setupSelectionEvents() {
    if (!previewCtx?.selectionHelper) return;

    const canvas = previewCtx.renderer.domElement;
    const helper = previewCtx.selectionHelper;

    canvas.addEventListener("pointerdown", (e) => {
      if (!state.selectionMode || e.button !== 0) return;

      // Don't start selection if clicking on controls
      if ((e.target as HTMLElement).closest(".ae-preview-toolbar")) return;

      helper.isDown = true;
      helper.startPoint.set(e.offsetX, e.offsetY);
      e.preventDefault();
    });

    canvas.addEventListener("pointermove", (e) => {
      if (!state.selectionMode || !helper.isDown) return;

      updateSelectionBox(helper, helper.startPoint.x, helper.startPoint.y, e.offsetX, e.offsetY);
    });

    canvas.addEventListener("pointerup", (e) => {
      if (!state.selectionMode || !helper.isDown) return;

      hideSelectionBox(helper);

      // Get meshes in selection area
      if (previewCtx) {
        const selectedMeshes = getMeshesInSelectionArea(previewCtx, helper);

        // Clear previous selection if not holding Ctrl/Cmd
        if (!e.ctrlKey && !e.metaKey) {
          clearMeshSelection();
        }

        // Add new selection
        for (const mesh of selectedMeshes) {
          if (!state.selectedMeshes.has(mesh)) {
            state.selectedMeshes.add(mesh);
            highlightMesh(previewCtx, mesh, true);
          }
        }

        updateDeleteButtonState();

        if (selectedMeshes.length > 0) {
          showToast(root, `Selected ${state.selectedMeshes.size} object(s)`);
        }
      }
    });

    // Cancel selection on pointer leave
    canvas.addEventListener("pointerleave", () => {
      if (helper.isDown) {
        hideSelectionBox(helper);
      }
    });
  }

  toggleSelectBtn.addEventListener("click", () => {
    state.selectionMode = !state.selectionMode;
    toggleSelectBtn.classList.toggle("active", state.selectionMode);

    if (previewCtx) {
      // Disable orbit controls when in selection mode
      previewCtx.controls.enabled = !state.selectionMode;

      if (state.selectionMode) {
        previewCtx.renderer.domElement.style.cursor = "crosshair";
        showToast(root, "Selection mode: Drag to select objects");
        setupSelectionEvents();
      } else {
        previewCtx.renderer.domElement.style.cursor = "";
        clearMeshSelection();
      }
    }
  });

  deleteSelectedBtn.addEventListener("click", () => {
    if (!previewCtx || state.selectedMeshes.size === 0) return;

    const meshesToDelete = Array.from(state.selectedMeshes);
    const deletedCount = deleteSelectedMeshes(previewCtx, meshesToDelete);

    // Update state
    state.selectedMeshes.clear();
    updateDeleteButtonState();

    // Re-analyze scene
    if (previewCtx.currentModel) {
      state.sceneChildren = analyzeChildren(previewCtx.currentModel);
      renderObjectList();
      updateActionButtons();
    }

    showToast(root, `Deleted ${deletedCount} object(s)`);
  });

  /* ── Delete asset record ───────────────────────────────────────── */
  deleteRecordBtn.addEventListener("click", async () => {
    if (!state.selectedAssetId || !state.manifestName) return;
    
    const asset = state.assets.find((a) => a.asset_id === state.selectedAssetId);
    if (!asset) return;
    
    // Confirm deletion
    const confirmed = confirm(
      `Delete this asset from manifest?\n\nAsset ID: ${asset.asset_id}\nCategory: ${asset.category || "unknown"}\n\nThis action cannot be undone.`
    );
    if (!confirmed) return;
    
    try {
      await deleteAssetRecord(state.manifestName, state.selectedAssetId);
      
      // Remove from local state
      const idx = state.assets.findIndex((a) => a.asset_id === state.selectedAssetId);
      if (idx !== -1) {
        state.assets.splice(idx, 1);
        state.totalAssets--;
      }
      
      // Clear selection
      state.selectedAssetId = null;
      showEmptyState();
      
      // Re-render gallery
      applyFilters();
      
      showToast(root, "Asset record deleted");
    } catch (err) {
      showToast(root, `Delete failed: ${err}`, "error");
    }
  });

  /* ── Scale ─────────────────────────────────────────────────────── */
  applyScaleBtn.addEventListener("click", () => {
    const val = parseFloat(scaleInput.value);
    if (isNaN(val) || val <= 0) {
      showToast(root, "Scale must be a positive number", "error");
      return;
    }
    state.scaleValue = val;
    if (previewCtx) applyScale(previewCtx, val);
  });

  /* ── Yaw (Orientation) ─────────────────────────────────────────── */
  applyYawBtn.addEventListener("click", () => {
    const val = parseFloat(yawInput.value);
    if (isNaN(val)) {
      showToast(root, "Yaw must be a number", "error");
      return;
    }
    // Normalize to [0, 360)
    const normalizedYaw = ((val % 360) + 360) % 360;
    state.yawValue = normalizedYaw;
    yawInput.value = String(normalizedYaw);
    if (previewCtx) {
      applyYaw(previewCtx, normalizedYaw);
      updateFrontArrow(previewCtx, state.frontDirection, normalizedYaw);
    }
    showToast(root, `Yaw set to ${normalizedYaw}°`);
  });

  /* ── Front Direction ───────────────────────────────────────────── */
  frontSelect.addEventListener("change", () => {
    state.frontDirection = frontSelect.value;
    if (previewCtx) {
      updateFrontArrow(previewCtx, state.frontDirection, state.yawValue);
      showToast(root, `Front direction set to ${state.frontDirection}`);
    }
  });

  /* ── Export ─────────────────────────────────────────────────────── */
  exportBtn.addEventListener("click", async () => {
    if (!previewCtx?.currentModel) return;
    try {
      const cloned = previewCtx.currentModel.clone();
      const data = await exportGlb(cloned);
      const asset = state.assets.find((a) => a.asset_id === state.selectedAssetId);
      const name = asset?.asset_id ?? "exported";
      triggerDownload(data, `${name}_scaled_${state.scaleValue}.glb`);
      showToast(root, "GLB exported successfully");
    } catch (err) {
      showToast(root, `Export failed: ${err}`, "error");
    }
  });

  /* ── Save metadata ─────────────────────────────────────────────── */
  saveBtn.addEventListener("click", async () => {
    if (!state.selectedAssetId || !state.manifestName) return;

    const tierEl = root.querySelector<HTMLSelectElement>("#ae-edit-tier");
    const eligibleEl = root.querySelector<HTMLInputElement>("#ae-edit-eligible");
    const tagsEl = root.querySelector<HTMLInputElement>("#ae-edit-tags");
    if (!tierEl || !eligibleEl || !tagsEl) return;

    const updates: Record<string, unknown> = {};
    const tierVal = tierEl.value ? parseInt(tierEl.value, 10) : undefined;
    if (tierVal !== undefined) updates.quality_tier = tierVal;
    updates.scene_eligible = eligibleEl.checked;
    updates.tags = tagsEl.value.split(",").map((t) => t.trim()).filter(Boolean);

    // Add scale, yaw, front direction, and dimensions
    if (state.scaleValue !== 1) {
      updates.scale = state.scaleValue;
    }
    if (state.yawValue !== 0) {
      updates.yaw_deg = state.yawValue;
    }
    if (state.frontDirection !== "+Z") {
      updates.canonical_front = state.frontDirection;
    }
    if (state.modelDimensions) {
      updates.dimensions_m = {
        width: state.modelDimensions.width,
        height: state.modelDimensions.height,
        depth: state.modelDimensions.depth,
      };
    }

    try {
      await saveAssetMetadata(state.manifestName, state.selectedAssetId, updates);
      // Update local state
      const asset = state.assets.find((a) => a.asset_id === state.selectedAssetId);
      if (asset) {
        if (tierVal !== undefined) asset.quality_tier = tierVal;
        asset.scene_eligible = eligibleEl.checked;
        asset.tags = updates.tags as string[];
        if (updates.scale) asset.scale = updates.scale as number;
        if (updates.yaw_deg) asset.yaw_deg = updates.yaw_deg as number;
        if (updates.canonical_front) asset.canonical_front = updates.canonical_front as string;
        if (updates.dimensions_m) asset.dimensions_m = updates.dimensions_m as { width?: number; height?: number; depth?: number };
      }
      renderGallery();
      showToast(root, "Metadata saved");
    } catch (err) {
      showToast(root, `Save failed: ${err}`, "error");
    }
  });

  /* ── Remove duplicates ─────────────────────────────────────────── */
  removeDupsBtn.addEventListener("click", () => {
    if (!previewCtx?.currentModel) return;

    const dupGroups = new Map<number, THREE.Mesh[]>();
    const meshes: THREE.Mesh[] = [];
    previewCtx.currentModel.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) {
        meshes.push(child as THREE.Mesh);
      }
    });

    // Find duplicate info for each mesh
    for (const mesh of meshes) {
      const childInfo = state.sceneChildren.find((c) => c.uuid === mesh.uuid);
      if (childInfo?.isDuplicate) {
        if (!dupGroups.has(childInfo.duplicateGroup)) dupGroups.set(childInfo.duplicateGroup, []);
        dupGroups.get(childInfo.duplicateGroup)!.push(mesh);
      }
    }

    // Keep first of each group, remove rest
    let removedCount = 0;
    for (const [, group] of dupGroups) {
      // Keep the first, remove others
      for (let i = 1; i < group.length; i++) {
        const mesh = group[i];
        if (mesh.parent) mesh.parent.remove(mesh);
        if (mesh.geometry) mesh.geometry.dispose();
        if (mesh.material) {
          if (Array.isArray(mesh.material)) mesh.material.forEach((m) => m.dispose());
          else mesh.material.dispose();
        }
        removedCount++;
      }
    }

    // Re-analyze
    if (previewCtx.currentModel) {
      state.sceneChildren = analyzeChildren(previewCtx.currentModel);
      renderObjectList();
      updateActionButtons();
    }
    showToast(root, `Removed ${removedCount} duplicate mesh(es)`);
  });

  /* ── Split selected ────────────────────────────────────────────── */
  splitBtn.addEventListener("click", async () => {
    if (!previewCtx?.currentModel || state.selectedObjects.size === 0) return;

    const meshes: THREE.Mesh[] = [];
    previewCtx.currentModel.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) meshes.push(child as THREE.Mesh);
    });

    const selectedMeshes = meshes.filter((m) => state.selectedObjects.has(m.uuid));
    if (selectedMeshes.length === 0) {
      showToast(root, "No valid meshes selected", "error");
      return;
    }

    let exported = 0;
    for (const mesh of selectedMeshes) {
      try {
        const newScene = new THREE.Scene();
        const cloned = mesh.clone();
        // Reset position to origin relative to bbox center
        const box = new THREE.Box3().setFromObject(mesh);
        const center = box.getCenter(new THREE.Vector3());
        cloned.position.copy(mesh.position).sub(center);
        newScene.add(cloned);

        const data = await exportGlb(newScene);
        const name = mesh.name || `mesh_${exported}`;
        triggerDownload(data, `${name}.glb`);
        exported++;
      } catch (err) {
        showToast(root, `Failed to split ${mesh.name}: ${err}`, "error");
      }
    }
    showToast(root, `Exported ${exported} mesh(es) as separate GLB files`);
  });

  /* ── Init ──────────────────────────────────────────────────────── */
  initManifests();

  /* ── Teardown ──────────────────────────────────────────────────── */
  return () => {
    destroyed = true;
    if (previewCtx) {
      cancelAnimationFrame(previewCtx.animId);
      previewCtx.renderer.dispose();
      previewCtx.controls.dispose();
      previewCtx.originalMaterials.clear();
      previewCtx = null;
    }
  };
}
