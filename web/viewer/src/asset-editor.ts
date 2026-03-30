import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { GLTFExporter } from "three/examples/jsm/exporters/GLTFExporter.js";

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

async function fetchManifestAssets(name: string): Promise<AssetRecord[]> {
  const res = await fetch(`/api/asset-manifest?name=${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`Failed to fetch manifest: ${res.status}`);
  const data = await res.json();
  return data.assets ?? [];
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
};

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

  const wireframeMaterial = new THREE.MeshBasicMaterial({
    color: 0x88ccff,
    wireframe: true,
  });

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
  };

  function animate() {
    ctx.animId = requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();

  const onResize = () => {
    const w = container.clientWidth || 600;
    const h = container.clientHeight || 400;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
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
    showEmptyState();
    try {
      state.assets = await fetchManifestAssets(name);
      updateCategoryFilter();
      applyFilters();
    } catch (err) {
      showToast(root, `Failed to load manifest: ${err}`, "error");
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
    galleryStats.textContent = `${state.filteredAssets.length} of ${state.assets.length} assets`;

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
    state.scaleValue = 1;
    scaleInput.value = "1";

    // Update gallery selection
    galleryGrid.querySelectorAll(".ae-asset-card").forEach((el) => {
      el.classList.toggle("active", (el as HTMLElement).dataset.assetId === assetId);
    });

    const asset = state.assets.find((a) => a.asset_id === assetId);
    if (!asset) return;

    emptyState.style.display = "none";
    detailContent.style.display = "";
    saveBtn.disabled = false;

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
      } catch (err) {
        showToast(root, `Failed to load GLB: ${err}`, "error");
      }
    }
  }

  function showEmptyState() {
    emptyState.style.display = "";
    detailContent.style.display = "none";
    saveBtn.disabled = true;
  }

  /* ── Info panel ────────────────────────────────────────────────── */
  function renderInfoPanel(asset: AssetRecord) {
    const fCount = asset.face_count ?? asset.mesh_face_count ?? 0;
    const vCount = asset.vertex_count ?? asset.quality_metrics?.vertex_count ?? 0;

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

    try {
      await saveAssetMetadata(state.manifestName, state.selectedAssetId, updates);
      // Update local state
      const asset = state.assets.find((a) => a.asset_id === state.selectedAssetId);
      if (asset) {
        if (tierVal !== undefined) asset.quality_tier = tierVal;
        asset.scene_eligible = eligibleEl.checked;
        asset.tags = updates.tags as string[];
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
