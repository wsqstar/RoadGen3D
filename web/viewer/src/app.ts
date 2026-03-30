import * as THREE from "three";
import { PointerLockControls } from "three/examples/jsm/controls/PointerLockControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

type SceneOption = {
  key: string;
  label: string;
  glbUrl: string;
};

type RecentLayout = {
  layout_path: string;
  label: string;
  relative_path?: string;
  updated_at?: string;
  mtime_ms?: number;
};

type RecentLayoutsPayload = {
  results?: RecentLayout[];
  error?: string;
};

type SceneBounds = {
  center: [number, number, number];
  size: [number, number, number];
  road_axis: [number, number, number];
};

type InstanceInfo = {
  instance_id: string;
  asset_id: string;
  category: string;
  placement_group?: string;
  theme_id?: string;
  selection_source?: string;
  position_xyz?: [number, number, number] | number[];
  bbox_xz?: [number, number, number, number] | number[];
  anchor_poi_type?: string;
  anchor_distance_m?: number | null;
  feasibility_score?: number | null;
  constraint_penalty?: number | null;
  dist_to_road_edge_m?: number | null;
  dist_to_nearest_junction_m?: number | null;
  dist_to_nearest_entrance_m?: number | null;
};

type AssetDescription = {
  asset_id: string;
  category: string;
  text_desc?: string;
  source?: string;
  asset_role?: string;
};

type StaticObjectDescription = {
  match: "exact" | "prefix";
  title: string;
  category: string;
  source?: string;
  intro?: string;
  design_note?: string;
};

type ViewerManifest = {
  layout_path: string;
  final_scene: {
    label: string;
    glb_url: string;
  };
  production_steps: Array<{
    step_id: string;
    title: string;
    glb_url: string;
  }>;
  default_selection: string;
  spawn_point?: [number, number, number];
  forward_vector?: [number, number, number];
  scene_bounds?: SceneBounds;
  instances?: Record<string, InstanceInfo>;
  asset_descriptions?: Record<string, AssetDescription>;
  static_object_descriptions?: Record<string, StaticObjectDescription>;
};

type MovementState = {
  forward: boolean;
  backward: boolean;
  left: boolean;
  right: boolean;
  sprint: boolean;
};

type CameraMode = "first_person" | "third_person";

type LightingPresetValues = {
  exposure: number;
  keyLightIntensity: number;
  fillLightIntensity: number;
  warmth: number;
  shadowStrength: number;
};

type LightingState = LightingPresetValues & {
  preset: string;
};

type MinimapBounds = {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  center: THREE.Vector3;
  extent: number;
};

type HitDescriptor =
  | {
      kind: "instance";
      nodeName: string;
      instanceId: string;
      instanceInfo: InstanceInfo;
      assetDescription?: AssetDescription;
    }
  | {
      kind: "static";
      nodeName: string;
      staticDescription: StaticObjectDescription;
    }
  | {
      kind: "generic";
      nodeName: string;
    };

const LIGHTING_PRESETS: Record<string, LightingPresetValues> = {
  neutral_studio: {
    exposure: 1.1,
    keyLightIntensity: 1.0,
    fillLightIntensity: 0.55,
    warmth: 0.0,
    shadowStrength: 0.45,
  },
  bright_day: {
    exposure: 1.3,
    keyLightIntensity: 1.2,
    fillLightIntensity: 0.8,
    warmth: -0.1,
    shadowStrength: 0.3,
  },
  overcast: {
    exposure: 1.05,
    keyLightIntensity: 0.75,
    fillLightIntensity: 0.95,
    warmth: -0.15,
    shadowStrength: 0.15,
  },
  golden_hour: {
    exposure: 1.18,
    keyLightIntensity: 1.05,
    fillLightIntensity: 0.48,
    warmth: 0.85,
    shadowStrength: 0.58,
  },
  night_presentation: {
    exposure: 0.82,
    keyLightIntensity: 0.62,
    fillLightIntensity: 0.24,
    warmth: 0.2,
    shadowStrength: 0.72,
  },
};

const LIGHTING_PRESET_LABELS: Record<string, string> = {
  neutral_studio: "Neutral Studio",
  bright_day: "Bright Day",
  overcast: "Overcast",
  golden_hour: "Golden Hour",
  night_presentation: "Night Presentation",
  custom: "Custom",
};

const DEFAULT_LIGHTING_STATE: LightingState = {
  preset: "custom",
  exposure: 1.8,
  keyLightIntensity: 1.7,
  fillLightIntensity: 1.2,
  warmth: 0.6,
  shadowStrength: 0.05,
};

const UP_AXIS = new THREE.Vector3(0, 1, 0);
const AVATAR_HEIGHT_M = 1.7;
const AVATAR_EYE_HEIGHT_M = 1.62;
const THIRD_PERSON_DISTANCE_M = 3.6;
const THIRD_PERSON_VERTICAL_OFFSET_M = 1.1;

const CATEGORY_LABELS: Record<string, string> = {
  bench: "座椅",
  lamp: "路灯",
  tree: "树木",
  trash: "垃圾桶",
  bollard: "隔离桩",
  mailbox: "邮箱",
  hydrant: "消防栓",
  bus_stop: "公交站",
  building: "建筑",
  road: "道路",
  roadway: "道路",
  sidewalk: "人行道",
  marking: "道路标线",
  crossing: "过街区",
  transit: "公交设施",
  landscape: "景观设施",
  scene_object: "场景对象",
};

const FALLBACK_CATEGORY_INTRO: Record<string, string> = {
  bench: "用于停留休憩，通常位于步行活动带。",
  lamp: "用于夜间照明，通常沿步行界面连续布置。",
  tree: "用于遮荫与界面塑造，通常位于路缘或家具带。",
  trash: "用于保持街道整洁，通常布置在停留节点附近。",
  bollard: "用于分隔交通与人行区域，强化安全边界。",
  mailbox: "用于邮政投递，通常靠近停留节点或出入口。",
  hydrant: "用于消防取水，通常靠近机动车或消防可达界面。",
  bus_stop: "用于公交停靠与候车，通常锚定在公交站点附近。",
  building: "用于塑造沿街界面和空间围合。",
};

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (!element) {
    throw new Error(`Missing required viewer element: ${selector}`);
  }
  return element;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function finiteOrNull(value: unknown): number | null {
  return Number.isFinite(value) ? Number(value) : null;
}

function asTriplet(value: unknown): [number, number, number] | null {
  if (!Array.isArray(value) || value.length !== 3) {
    return null;
  }
  const items = value.map((entry) => finiteOrNull(entry));
  if (items.some((entry) => entry === null)) {
    return null;
  }
  return [items[0] ?? 0, items[1] ?? 0, items[2] ?? 0];
}

function asQuad(value: unknown): [number, number, number, number] | null {
  if (!Array.isArray(value) || value.length !== 4) {
    return null;
  }
  const items = value.map((entry) => finiteOrNull(entry));
  if (items.some((entry) => entry === null)) {
    return null;
  }
  return [items[0] ?? 0, items[1] ?? 0, items[2] ?? 0, items[3] ?? 0];
}

function isFiniteTriplet(value: unknown): value is [number, number, number] {
  return Array.isArray(value) && value.length === 3 && value.every((item) => Number.isFinite(item));
}

function setError(element: HTMLElement, message: string): void {
  element.textContent = message;
  element.hidden = false;
}

function clearError(element: HTMLElement): void {
  element.textContent = "";
  element.hidden = true;
}

function makeSceneOptions(manifest: ViewerManifest): SceneOption[] {
  const options: SceneOption[] = [
    {
      key: "final_scene",
      label: manifest.final_scene.label,
      glbUrl: manifest.final_scene.glb_url,
    },
  ];
  for (const step of manifest.production_steps ?? []) {
    options.push({
      key: step.step_id,
      label: step.title,
      glbUrl: step.glb_url,
    });
  }
  return options;
}

function makeDirectLayoutLabel(layoutPath: string): string {
  const normalized = layoutPath.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  const tail = parts.slice(-2).join("/");
  return `Direct Layout · ${tail || normalized}`;
}

function compactUiLabel(label: string, maxLength = 54): string {
  if (label.length <= maxLength) {
    return label;
  }

  const normalized = label.replace(/\\/g, "/");
  if (normalized.includes("/")) {
    const parts = normalized.split("/").filter(Boolean);
    const tail = parts.slice(-2).join("/");
    const head = parts[0] ?? "";
    const compactPath = `${head}/.../${tail}`;
    if (compactPath.length <= maxLength) {
      return compactPath;
    }
    if (tail.length + 1 >= maxLength) {
      return `...${tail.slice(-(maxLength - 3))}`;
    }
  }

  const left = Math.max(8, Math.floor((maxLength - 1) / 2));
  const right = Math.max(8, maxLength - left - 1);
  return `${label.slice(0, left)}...${label.slice(-right)}`;
}

function disposeObject(root: THREE.Object3D): void {
  root.traverse((child: THREE.Object3D) => {
    const mesh = child as THREE.Mesh;
    if (!("geometry" in mesh) || !mesh.geometry) {
      return;
    }
    mesh.geometry.dispose();
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const material of materials) {
      if (!material) {
        continue;
      }
      for (const value of Object.values(material as unknown as Record<string, unknown>)) {
        if (value instanceof THREE.Texture) {
          value.dispose();
        }
      }
      material.dispose();
    }
  });
}

function inferSpawnFromBbox(
  bbox: THREE.Box3,
  manifest: ViewerManifest,
): { position: THREE.Vector3; forward: THREE.Vector3 } {
  if (isFiniteTriplet(manifest.spawn_point) && isFiniteTriplet(manifest.forward_vector)) {
    return {
      position: new THREE.Vector3(
        manifest.spawn_point[0],
        manifest.spawn_point[1],
        manifest.spawn_point[2],
      ),
      forward: new THREE.Vector3(
        manifest.forward_vector[0],
        manifest.forward_vector[1],
        manifest.forward_vector[2],
      ).normalize(),
    };
  }

  const center = bbox.getCenter(new THREE.Vector3());
  return {
    position: new THREE.Vector3(center.x, 1.65, center.z),
    forward: new THREE.Vector3(1, 0, 0),
  };
}

function parseQueryLayoutPath(): string | null {
  const search = new URLSearchParams(window.location.search);
  const layoutPath = search.get("layout") ?? "";
  return layoutPath.trim() || null;
}

async function loadManifest(layoutPath: string): Promise<ViewerManifest> {
  const response = await fetch(`./api/layout?path=${encodeURIComponent(layoutPath)}`);
  const payload = (await response.json()) as ViewerManifest | { error?: string };
  if (!response.ok) {
    throw new Error(
      payload && "error" in payload
        ? String(payload.error ?? "Failed to load scene layout.")
        : "Failed to load scene layout.",
    );
  }
  return payload as ViewerManifest;
}

async function loadRecentLayouts(limit = 20): Promise<RecentLayout[]> {
  const response = await fetch(`./api/recent-layouts?limit=${encodeURIComponent(String(limit))}`);
  const payload = (await response.json()) as RecentLayoutsPayload;
  if (!response.ok) {
    throw new Error(String(payload?.error ?? "Failed to discover recent scene layouts."));
  }
  return Array.isArray(payload?.results) ? payload.results : [];
}

function updateQueryLayout(layoutPath: string): void {
  const nextUrl = new URL(window.location.href);
  nextUrl.searchParams.set("layout", layoutPath);
  window.history.replaceState({}, "", nextUrl.toString());
}

function categoryLabel(category: string): string {
  const key = String(category || "").trim().toLowerCase();
  return CATEGORY_LABELS[key] ?? (key || "场景对象");
}

function prettifySource(source: string | undefined): string {
  const value = String(source || "").trim();
  if (!value) {
    return "系统生成";
  }
  return value.replace(/_/g, " ");
}

function formatMetric(value: number | null | undefined, unit: string, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "未记录";
  }
  return `${value.toFixed(digits)}${unit}`;
}

function collectInstanceMetrics(instanceInfo: InstanceInfo): Array<[string, string]> {
  const metrics: Array<[string, string]> = [
    ["asset_id", String(instanceInfo.asset_id || "").trim()],
    ["placement_group", String(instanceInfo.placement_group || "").trim()],
    ["theme_id", String(instanceInfo.theme_id || "").trim()],
    ["距道路边缘", formatMetric(finiteOrNull(instanceInfo.dist_to_road_edge_m), "m")],
    ["距最近路口", formatMetric(finiteOrNull(instanceInfo.dist_to_nearest_junction_m), "m")],
    ["距最近出入口", formatMetric(finiteOrNull(instanceInfo.dist_to_nearest_entrance_m), "m")],
    ["可行性", formatMetric(finiteOrNull(instanceInfo.feasibility_score), "", 2)],
    ["约束惩罚", formatMetric(finiteOrNull(instanceInfo.constraint_penalty), "", 3)],
  ];
  return metrics.filter((entry) => entry[1] && entry[1] !== "未记录");
}

function buildPlacementReason(instanceInfo: InstanceInfo, category: string): string {
  const anchorPoiType = String(instanceInfo.anchor_poi_type || "").trim();
  const anchorDistance = finiteOrNull(instanceInfo.anchor_distance_m);
  if (anchorPoiType) {
    return `该对象锚定在 ${anchorPoiType} 相关位置，当前距锚点 ${formatMetric(anchorDistance, "m")}。`;
  }
  const source = String(instanceInfo.selection_source || "").trim();
  if (source) {
    return `本对象由 ${prettifySource(source)} 选中，并按当前规则集落位。`;
  }
  return FALLBACK_CATEGORY_INTRO[category] ?? "该对象按当前街道规则自动布置。";
}

function composeInstanceInfoHtml(
  nodeName: string,
  instanceInfo: InstanceInfo,
  assetDescription?: AssetDescription,
): string {
  const category = String(instanceInfo.category || "").trim().toLowerCase();
  const title = categoryLabel(category);
  const subtitleParts = [
    category ? `类别：${categoryLabel(category)}` : "",
    assetDescription?.source ? `来源：${prettifySource(assetDescription.source)}` : "",
  ].filter(Boolean);
  const intro = String(assetDescription?.text_desc || "").trim()
    || FALLBACK_CATEGORY_INTRO[category]
    || "这是场景中的自动生成对象。";
  const metrics = collectInstanceMetrics(instanceInfo);

  return `
    <div class="viewer-card-title">${escapeHtml(title)}</div>
    <div class="viewer-card-subtitle">${escapeHtml(subtitleParts.join(" · ") || `节点：${nodeName}`)}</div>
    <div class="viewer-card-section">${escapeHtml(intro)}</div>
    <div class="viewer-card-section viewer-card-highlight">${escapeHtml(buildPlacementReason(instanceInfo, category))}</div>
    <dl class="viewer-card-metrics">
      ${metrics
        .map(
          ([label, value]) =>
            `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`,
        )
        .join("")}
    </dl>
  `;
}

function composeInstanceInfoText(
  nodeName: string,
  instanceInfo: InstanceInfo,
  assetDescription?: AssetDescription,
): string {
  const category = String(instanceInfo.category || "").trim().toLowerCase();
  const title = categoryLabel(category);
  const subtitleParts = [
    category ? `类别：${categoryLabel(category)}` : "",
    assetDescription?.source ? `来源：${prettifySource(assetDescription.source)}` : "",
  ].filter(Boolean);
  const subtitle = subtitleParts.join(" · ") || `节点：${nodeName}`;
  const intro = String(assetDescription?.text_desc || "").trim()
    || FALLBACK_CATEGORY_INTRO[category]
    || "这是场景中的自动生成对象。";
  const metrics = collectInstanceMetrics(instanceInfo);
  return [
    title,
    subtitle,
    intro,
    buildPlacementReason(instanceInfo, category),
    ...metrics.map(([label, value]) => `${label}: ${value}`),
  ].filter(Boolean).join("\n");
}

function composeStaticInfoHtml(nodeName: string, description: StaticObjectDescription): string {
  const subtitle = [
    `类别：${categoryLabel(description.category)}`,
    description.source ? `来源：${prettifySource(description.source)}` : "来源：系统构件",
  ].join(" · ");
  return `
    <div class="viewer-card-title">${escapeHtml(description.title)}</div>
    <div class="viewer-card-subtitle">${escapeHtml(subtitle)}</div>
    <div class="viewer-card-section">${escapeHtml(description.intro || "这是场景中的基础构件。")}</div>
    <div class="viewer-card-section viewer-card-highlight">${escapeHtml(description.design_note || "用于支撑街道空间组织与交通可读性。")}</div>
    <dl class="viewer-card-metrics">
      <div><dt>node</dt><dd>${escapeHtml(nodeName)}</dd></div>
    </dl>
  `;
}

function composeStaticInfoText(nodeName: string, description: StaticObjectDescription): string {
  const subtitle = [
    `类别：${categoryLabel(description.category)}`,
    description.source ? `来源：${prettifySource(description.source)}` : "来源：系统构件",
  ].join(" · ");
  return [
    description.title,
    subtitle,
    description.intro || "这是场景中的基础构件。",
    description.design_note || "用于支撑街道空间组织与交通可读性。",
    `node: ${nodeName}`,
  ].filter(Boolean).join("\n");
}

function composeGenericInfoHtml(nodeName: string): string {
  return `
    <div class="viewer-card-title">场景对象</div>
    <div class="viewer-card-subtitle">未命名规则对象</div>
    <div class="viewer-card-section">当前对象没有更详细的街道说明元数据。</div>
    <dl class="viewer-card-metrics">
      <div><dt>node</dt><dd>${escapeHtml(nodeName)}</dd></div>
    </dl>
  `;
}

function composeGenericInfoText(nodeName: string): string {
  return [
    "场景对象",
    "未命名规则对象",
    "当前对象没有更详细的街道说明元数据。",
    `node: ${nodeName}`,
  ].join("\n");
}

function buildHitDescriptorContent(descriptor: HitDescriptor): { html: string; text: string } {
  if (descriptor.kind === "instance") {
    return {
      html: composeInstanceInfoHtml(
        descriptor.nodeName,
        descriptor.instanceInfo,
        descriptor.assetDescription,
      ),
      text: composeInstanceInfoText(
        descriptor.nodeName,
        descriptor.instanceInfo,
        descriptor.assetDescription,
      ),
    };
  }
  if (descriptor.kind === "static") {
    return {
      html: composeStaticInfoHtml(descriptor.nodeName, descriptor.staticDescription),
      text: composeStaticInfoText(descriptor.nodeName, descriptor.staticDescription),
    };
  }
  return {
    html: composeGenericInfoHtml(descriptor.nodeName),
    text: composeGenericInfoText(descriptor.nodeName),
  };
}

async function writeTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!copied) {
    throw new Error("Clipboard copy is unavailable in this browser.");
  }
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.isContentEditable) {
    return true;
  }
  return target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement
    || target instanceof HTMLSelectElement;
}

function resolveInstanceIdFromName(name: string): string | null {
  const match = String(name || "").match(/(inst_\d{4})/i);
  return match ? match[1] : null;
}

function createAvatarFigure(): THREE.Group {
  const avatar = new THREE.Group();
  avatar.name = "viewer_avatar";
  avatar.userData.viewerHelper = true;

  const bodyMaterial = new THREE.MeshStandardMaterial({
    color: "#59708c",
    roughness: 0.82,
    metalness: 0.02,
  });
  const accentMaterial = new THREE.MeshStandardMaterial({
    color: "#d9a68c",
    roughness: 0.95,
    metalness: 0.0,
  });
  const legMaterial = new THREE.MeshStandardMaterial({
    color: "#374151",
    roughness: 0.88,
    metalness: 0.02,
  });

  const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.18, 0.58, 6, 12), bodyMaterial);
  torso.position.set(0, 1.0, 0);
  torso.castShadow = true;
  torso.receiveShadow = true;
  torso.userData.viewerHelper = true;
  avatar.add(torso);

  const head = new THREE.Mesh(new THREE.SphereGeometry(0.14, 16, 16), accentMaterial);
  head.position.set(0, 1.48, 0);
  head.castShadow = true;
  head.receiveShadow = true;
  head.userData.viewerHelper = true;
  avatar.add(head);

  const leftLeg = new THREE.Mesh(new THREE.CapsuleGeometry(0.07, 0.56, 4, 10), legMaterial);
  leftLeg.position.set(-0.07, 0.42, 0);
  leftLeg.castShadow = true;
  leftLeg.receiveShadow = true;
  leftLeg.userData.viewerHelper = true;
  avatar.add(leftLeg);

  const rightLeg = leftLeg.clone();
  rightLeg.position.x = 0.07;
  rightLeg.userData.viewerHelper = true;
  avatar.add(rightLeg);

  const leftArm = new THREE.Mesh(new THREE.CapsuleGeometry(0.05, 0.42, 4, 10), bodyMaterial);
  leftArm.position.set(-0.24, 1.03, 0);
  leftArm.rotation.z = Math.PI / 28;
  leftArm.castShadow = true;
  leftArm.receiveShadow = true;
  leftArm.userData.viewerHelper = true;
  avatar.add(leftArm);

  const rightArm = leftArm.clone();
  rightArm.position.x = 0.24;
  rightArm.rotation.z = -Math.PI / 28;
  rightArm.userData.viewerHelper = true;
  avatar.add(rightArm);

  return avatar;
}

function mountViewer(root: HTMLElement): Promise<() => void> {
  return mountViewerImpl(root);
}

async function mountViewerImpl(root: HTMLElement): Promise<() => void> {
  root.innerHTML = `
    <div class="viewer-shell">
      <div class="viewer-topbar">
        <div class="viewer-controls-group">
          <div class="viewer-controls">
            <label class="viewer-label" for="layout-select">Recent Result</label>
            <select id="layout-select" class="viewer-select"></select>
          </div>
          <div class="viewer-controls">
            <label class="viewer-label" for="scene-select">Scene</label>
            <select id="scene-select" class="viewer-select"></select>
          </div>
        </div>
        <div class="viewer-actions">
          <div class="viewer-help">
            Click to capture mouse · WASD move · Shift sprint · Esc unlock · R reset · P panel · Ctrl/Cmd+C copy target
          </div>
          <button
            id="viewer-scene-graph-link"
            class="viewer-nav-button viewer-nav-button-secondary"
            type="button"
          >
            Annotation
          </button>
          <button
            id="viewer-asset-editor-link"
            class="viewer-nav-button viewer-nav-button-secondary"
            type="button"
          >
            Asset Editor
          </button>
          <button id="viewer-settings-toggle" class="viewer-settings-toggle" type="button" aria-expanded="false">
            Settings
          </button>
        </div>
      </div>
      <div id="viewer-canvas" class="viewer-canvas"></div>
      <div id="viewer-crosshair" class="viewer-crosshair" hidden></div>
      <div id="viewer-info-card" class="viewer-info-card" hidden></div>
      <div id="viewer-minimap" class="viewer-minimap">
        <div class="viewer-minimap-title">Scene Map</div>
        <div id="viewer-minimap-canvas" class="viewer-minimap-canvas"></div>
        <canvas id="viewer-minimap-overlay" class="viewer-minimap-overlay"></canvas>
      </div>
      <aside id="viewer-settings-panel" class="viewer-settings-panel" data-open="false">
        <div class="viewer-settings-header">
          <div>
            <div class="viewer-settings-title">Display Settings</div>
            <div class="viewer-settings-subtitle">Light presets, shadows, and laser pointer</div>
          </div>
          <button id="viewer-settings-close" class="viewer-settings-close" type="button" aria-label="Close settings">
            ×
          </button>
        </div>
        <div class="viewer-settings-section">
          <label class="viewer-settings-label" for="lighting-preset">Lighting Preset</label>
          <select id="lighting-preset" class="viewer-select viewer-select-compact"></select>
        </div>
        <div class="viewer-settings-section">
          <label class="viewer-range-label" for="lighting-exposure">
            <span>Exposure</span>
            <span id="lighting-exposure-value"></span>
          </label>
          <input id="lighting-exposure" class="viewer-range" type="range" min="0.5" max="2.0" step="0.05" />
        </div>
        <div class="viewer-settings-section">
          <label class="viewer-range-label" for="lighting-key">
            <span>Key Light Intensity</span>
            <span id="lighting-key-value"></span>
          </label>
          <input id="lighting-key" class="viewer-range" type="range" min="0.2" max="2.0" step="0.05" />
        </div>
        <div class="viewer-settings-section">
          <label class="viewer-range-label" for="lighting-fill">
            <span>Fill Light Intensity</span>
            <span id="lighting-fill-value"></span>
          </label>
          <input id="lighting-fill" class="viewer-range" type="range" min="0.1" max="1.6" step="0.05" />
        </div>
        <div class="viewer-settings-section">
          <label class="viewer-range-label" for="lighting-warmth">
            <span>Warmth</span>
            <span id="lighting-warmth-value"></span>
          </label>
          <input id="lighting-warmth" class="viewer-range" type="range" min="-1" max="1" step="0.05" />
        </div>
        <div class="viewer-settings-section">
          <label class="viewer-range-label" for="lighting-shadow">
            <span>Shadow Strength</span>
            <span id="lighting-shadow-value"></span>
          </label>
          <input id="lighting-shadow" class="viewer-range" type="range" min="0" max="1" step="0.05" />
        </div>
        <div class="viewer-settings-section viewer-settings-section-divider">
          <label class="viewer-toggle-row" for="third-person-enabled">
            <span>Third Person Camera</span>
            <input id="third-person-enabled" type="checkbox" />
          </label>
        </div>
        <div class="viewer-settings-section">
          <label class="viewer-toggle-row" for="laser-pointer-enabled">
            <span>Laser Pointer</span>
            <input id="laser-pointer-enabled" type="checkbox" />
          </label>
        </div>
      </aside>
      <div id="viewer-status" class="viewer-status">Loading viewer…</div>
      <div id="viewer-overlay" class="viewer-overlay">Click scene to capture mouse</div>
      <div id="viewer-error" class="viewer-error" hidden></div>
    </div>
  `;

  const canvasHost = requireElement<HTMLElement>(root, "#viewer-canvas");
  const statusEl = requireElement<HTMLElement>(root, "#viewer-status");
  const overlayEl = requireElement<HTMLElement>(root, "#viewer-overlay");
  const errorEl = requireElement<HTMLElement>(root, "#viewer-error");
  const layoutSelectEl = requireElement<HTMLSelectElement>(root, "#layout-select");
  const selectEl = requireElement<HTMLSelectElement>(root, "#scene-select");
  const sceneGraphLinkEl = requireElement<HTMLButtonElement>(root, "#viewer-scene-graph-link");
  const assetEditorLinkEl = requireElement<HTMLButtonElement>(root, "#viewer-asset-editor-link");
  const settingsToggleEl = requireElement<HTMLButtonElement>(root, "#viewer-settings-toggle");
  const settingsPanelEl = requireElement<HTMLElement>(root, "#viewer-settings-panel");
  const settingsCloseEl = requireElement<HTMLButtonElement>(root, "#viewer-settings-close");
  const infoCardEl = requireElement<HTMLElement>(root, "#viewer-info-card");
  const crosshairEl = requireElement<HTMLElement>(root, "#viewer-crosshair");
  const minimapHost = requireElement<HTMLElement>(root, "#viewer-minimap-canvas");
  const minimapOverlayEl = requireElement<HTMLCanvasElement>(root, "#viewer-minimap-overlay");
  const lightingPresetEl = requireElement<HTMLSelectElement>(root, "#lighting-preset");
  const exposureInput = requireElement<HTMLInputElement>(root, "#lighting-exposure");
  const keyInput = requireElement<HTMLInputElement>(root, "#lighting-key");
  const fillInput = requireElement<HTMLInputElement>(root, "#lighting-fill");
  const warmthInput = requireElement<HTMLInputElement>(root, "#lighting-warmth");
  const shadowInput = requireElement<HTMLInputElement>(root, "#lighting-shadow");
  const exposureValueEl = requireElement<HTMLElement>(root, "#lighting-exposure-value");
  const keyValueEl = requireElement<HTMLElement>(root, "#lighting-key-value");
  const fillValueEl = requireElement<HTMLElement>(root, "#lighting-fill-value");
  const warmthValueEl = requireElement<HTMLElement>(root, "#lighting-warmth-value");
  const shadowValueEl = requireElement<HTMLElement>(root, "#lighting-shadow-value");
  const thirdPersonToggleEl = requireElement<HTMLInputElement>(root, "#third-person-enabled");
  const laserToggleEl = requireElement<HTMLInputElement>(root, "#laser-pointer-enabled");

  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#f7f6f3");

  const camera = new THREE.PerspectiveCamera(70, 1, 0.05, 2000);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.setSize(canvasHost.clientWidth, canvasHost.clientHeight);
  canvasHost.appendChild(renderer.domElement);

  const minimapRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  minimapRenderer.outputColorSpace = THREE.SRGBColorSpace;
  minimapRenderer.setPixelRatio(1);
  minimapRenderer.shadowMap.enabled = false;
  minimapHost.appendChild(minimapRenderer.domElement);
  const minimapCamera = new THREE.OrthographicCamera(-20, 20, 20, -20, 0.1, 4000);
  minimapCamera.up.set(0, 0, -1);

  const hemiLight = new THREE.HemisphereLight(0xfafcff, 0xd6d5d0, 0.75);
  scene.add(hemiLight);

  const keyLight = new THREE.DirectionalLight(0xffffff, 1.0);
  keyLight.position.set(18, 30, 12);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(2048, 2048);
  keyLight.shadow.camera.near = 0.5;
  keyLight.shadow.camera.far = 220;
  keyLight.shadow.camera.left = -90;
  keyLight.shadow.camera.right = 90;
  keyLight.shadow.camera.top = 90;
  keyLight.shadow.camera.bottom = -90;
  keyLight.shadow.bias = -0.0002;
  keyLight.shadow.normalBias = 0.02;
  scene.add(keyLight);

  const fillLight = new THREE.DirectionalLight(0xdfe8ff, 0.45);
  fillLight.position.set(-18, 18, -18);
  scene.add(fillLight);

  const controls = new PointerLockControls(camera, renderer.domElement);
  scene.add(camera);

  const avatarFigure = createAvatarFigure();
  avatarFigure.visible = false;
  scene.add(avatarFigure);

  const loader = new GLTFLoader();
  const raycaster = new THREE.Raycaster();
  const clock = new THREE.Clock();
  const eventController = new AbortController();
  const { signal } = eventController;
  let animationFrameId = 0;
  let destroyed = false;
  const moveState: MovementState = {
    forward: false,
    backward: false,
    left: false,
    right: false,
    sprint: false,
  };

  const laserBeamGeometry = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(),
    new THREE.Vector3(),
  ]);
  const laserBeam = new THREE.Line(
    laserBeamGeometry,
    new THREE.LineBasicMaterial({ color: 0xff3b30, transparent: true, opacity: 0.95 }),
  );
  laserBeam.visible = false;
  laserBeam.userData.viewerHelper = true;
  scene.add(laserBeam);

  const laserHitDot = new THREE.Mesh(
    new THREE.SphereGeometry(0.12, 12, 12),
    new THREE.MeshBasicMaterial({ color: 0xff5a4f }),
  );
  laserHitDot.visible = false;
  laserHitDot.userData.viewerHelper = true;
  scene.add(laserHitDot);

  let currentRoot: THREE.Object3D | null = null;
  let currentManifest: ViewerManifest | null = null;
  let currentLayoutPath = "";
  let currentSpawn = new THREE.Vector3(0, 1.65, 0);
  let currentForward = new THREE.Vector3(1, 0, 0);
  let currentAvatarPosition = new THREE.Vector3(0, Math.max(0, 1.65 - AVATAR_EYE_HEIGHT_M), 0);
  let currentCameraMode: CameraMode = "first_person";
  let currentSceneBounds: MinimapBounds | null = null;
  let currentLaserHitPoint: THREE.Vector3 | null = null;
  let currentLaserCopyText = "";
  let settingsOpen = false;
  let resumeRoamAfterSettingsClose = false;
  let statusResetHandle: number | null = null;
  const optionsByKey = new Map<string, SceneOption>();
  const recentLayoutsByPath = new Map<string, RecentLayout>();

  const lightingState: LightingState = {
    ...DEFAULT_LIGHTING_STATE,
  };

  function setStatus(message: string): void {
    if (statusResetHandle !== null) {
      window.clearTimeout(statusResetHandle);
      statusResetHandle = null;
    }
    statusEl.textContent = message;
  }

  function flashStatus(message: string, durationMs = 1800): void {
    const restoreText = statusEl.textContent || "";
    if (statusResetHandle !== null) {
      window.clearTimeout(statusResetHandle);
    }
    statusEl.textContent = message;
    statusResetHandle = window.setTimeout(() => {
      statusEl.textContent = restoreText;
      statusResetHandle = null;
    }, durationMs);
  }

  function applyLightingState(): void {
    const warmthT = clamp((lightingState.warmth + 1) * 0.5, 0, 1);
    const coolKey = new THREE.Color("#f5fbff");
    const warmKey = new THREE.Color("#ffd8a8");
    const coolFill = new THREE.Color("#e7f0ff");
    const warmFill = new THREE.Color("#ffe9cd");
    const coolSky = new THREE.Color("#f8fbff");
    const warmSky = new THREE.Color("#fff1d9");
    const keyColor = new THREE.Color().lerpColors(coolKey, warmKey, warmthT);
    const fillColor = new THREE.Color().lerpColors(coolFill, warmFill, warmthT * 0.65);
    const skyColor = new THREE.Color().lerpColors(coolSky, warmSky, warmthT * 0.55);

    renderer.toneMappingExposure = lightingState.exposure;
    keyLight.color.copy(keyColor);
    fillLight.color.copy(fillColor);
    hemiLight.color.copy(skyColor);
    hemiLight.groundColor.set("#d5d0cb");

    keyLight.intensity = lightingState.keyLightIntensity * (0.85 + lightingState.shadowStrength * 0.45);
    fillLight.intensity = lightingState.fillLightIntensity * (1.0 - lightingState.shadowStrength * 0.25);
    hemiLight.intensity = 0.35 + lightingState.fillLightIntensity * (0.42 - lightingState.shadowStrength * 0.12);
    keyLight.shadow.radius = 2 + (1 - lightingState.shadowStrength) * 8;
    keyLight.shadow.normalBias = 0.01 + (1 - lightingState.shadowStrength) * 0.03;
  }

  function syncLightingUi(): void {
    lightingPresetEl.value = lightingState.preset;
    exposureInput.value = lightingState.exposure.toString();
    keyInput.value = lightingState.keyLightIntensity.toString();
    fillInput.value = lightingState.fillLightIntensity.toString();
    warmthInput.value = lightingState.warmth.toString();
    shadowInput.value = lightingState.shadowStrength.toString();
    exposureValueEl.textContent = lightingState.exposure.toFixed(2);
    keyValueEl.textContent = lightingState.keyLightIntensity.toFixed(2);
    fillValueEl.textContent = lightingState.fillLightIntensity.toFixed(2);
    warmthValueEl.textContent = lightingState.warmth.toFixed(2);
    shadowValueEl.textContent = lightingState.shadowStrength.toFixed(2);
    crosshairEl.hidden = !laserToggleEl.checked;
    applyLightingState();
  }

  function setSettingsOpen(nextOpen: boolean, restoreRoam = false): void {
    settingsOpen = nextOpen;
    settingsPanelEl.dataset.open = nextOpen ? "true" : "false";
    settingsToggleEl.setAttribute("aria-expanded", nextOpen ? "true" : "false");
    if (nextOpen) {
      if (controls.isLocked) {
        resumeRoamAfterSettingsClose = true;
        controls.unlock();
      }
      return;
    }
    const shouldRestoreRoam = restoreRoam || resumeRoamAfterSettingsClose;
    resumeRoamAfterSettingsClose = false;
    if (shouldRestoreRoam) {
      controls.lock();
    }
  }

  function toggleSettingsShortcut(): void {
    if (settingsOpen) {
      setSettingsOpen(false, true);
      return;
    }
    setSettingsOpen(true);
  }

  function resizeRenderer(): void {
    const width = Math.max(1, canvasHost.clientWidth);
    const height = Math.max(1, canvasHost.clientHeight);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height);

    const minimapWidth = Math.max(1, minimapHost.clientWidth);
    const minimapHeight = Math.max(1, minimapHost.clientHeight);
    minimapRenderer.setSize(minimapWidth, minimapHeight);
    const dpr = Math.min(window.devicePixelRatio, 2);
    minimapOverlayEl.width = Math.max(1, Math.round(minimapWidth * dpr));
    minimapOverlayEl.height = Math.max(1, Math.round(minimapHeight * dpr));
    minimapOverlayEl.style.width = `${minimapWidth}px`;
    minimapOverlayEl.style.height = `${minimapHeight}px`;
  }

  function cameraForwardHorizontal(): THREE.Vector3 {
    const forward = new THREE.Vector3();
    camera.getWorldDirection(forward);
    forward.y = 0;
    if (forward.lengthSq() < 1e-6) {
      return currentForward.clone().setY(0).normalize();
    }
    return forward.normalize();
  }

  function updateAvatarTransform(): void {
    avatarFigure.position.copy(currentAvatarPosition);
    avatarFigure.visible = currentCameraMode === "third_person";
    const forward = cameraForwardHorizontal();
    if (forward.lengthSq() > 1e-6) {
      avatarFigure.rotation.y = Math.atan2(forward.x, forward.z);
      currentForward.copy(forward);
    }
  }

  function syncCameraRig(): void {
    updateAvatarTransform();
    const headTarget = currentAvatarPosition.clone().add(new THREE.Vector3(0, AVATAR_EYE_HEIGHT_M, 0));
    const forward = cameraForwardHorizontal();
    if (currentCameraMode === "third_person") {
      camera.position
        .copy(headTarget)
        .add(new THREE.Vector3(0, THIRD_PERSON_VERTICAL_OFFSET_M, 0))
        .add(forward.multiplyScalar(-THIRD_PERSON_DISTANCE_M));
      return;
    }
    camera.position.copy(headTarget);
  }

  function resetView(): void {
    currentAvatarPosition.set(
      currentSpawn.x,
      Math.max(0, currentSpawn.y - AVATAR_EYE_HEIGHT_M),
      currentSpawn.z,
    );
    camera.position.copy(currentSpawn);
    const target = currentSpawn.clone().add(currentForward);
    camera.lookAt(target);
    syncCameraRig();
  }

  function updateOverlay(): void {
    overlayEl.hidden = controls.isLocked;
  }

  function clearInfoCard(): void {
    infoCardEl.innerHTML = "";
    infoCardEl.hidden = true;
    currentLaserCopyText = "";
  }

  function setInfoCardContent(htmlContent: string): void {
    infoCardEl.innerHTML = htmlContent;
    infoCardEl.hidden = false;
  }

  async function copyCurrentLaserTargetDetails(): Promise<void> {
    if (!laserToggleEl.checked) {
      flashStatus("Laser pointer is off.");
      return;
    }
    const text = currentLaserCopyText.trim();
    if (!text) {
      flashStatus("No laser target to copy.");
      return;
    }
    try {
      await writeTextToClipboard(text);
      flashStatus("Copied laser target details.");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Clipboard copy failed.";
      flashStatus(message);
    }
  }

  function handleKey(event: KeyboardEvent, active: boolean): void {
    if (
      active
      && !event.repeat
      && event.code === "KeyC"
      && (event.ctrlKey || event.metaKey)
      && !event.altKey
      && !isEditableTarget(event.target)
      && laserToggleEl.checked
    ) {
      event.preventDefault();
      void copyCurrentLaserTargetDetails();
      return;
    }
    switch (event.code) {
      case "KeyW":
        moveState.forward = active;
        break;
      case "KeyS":
        moveState.backward = active;
        break;
      case "KeyA":
        moveState.left = active;
        break;
      case "KeyD":
        moveState.right = active;
        break;
      case "ShiftLeft":
      case "ShiftRight":
        moveState.sprint = active;
        break;
      case "KeyR":
        if (active) {
          resetView();
        }
        break;
      case "KeyP":
        if (active && !event.repeat) {
          toggleSettingsShortcut();
        }
        break;
      default:
        return;
    }
    event.preventDefault();
  }

  function configureSceneObjectShadows(rootObject: THREE.Object3D): void {
    rootObject.traverse((child) => {
      const mesh = child as THREE.Mesh;
      if (!mesh.isMesh) {
        return;
      }
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      if (Array.isArray(mesh.material)) {
        for (const material of mesh.material) {
          if (material && "depthWrite" in material && material.transparent) {
            material.depthWrite = false;
          }
        }
      } else if (mesh.material && "depthWrite" in mesh.material && mesh.material.transparent) {
        mesh.material.depthWrite = false;
      }
    });
  }

  function sceneBoundsFromBox(box: THREE.Box3): MinimapBounds {
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const extent = Math.max(size.x, size.z) * 0.58 + 6;
    return {
      minX: center.x - extent,
      maxX: center.x + extent,
      minZ: center.z - extent,
      maxZ: center.z + extent,
      center,
      extent,
    };
  }

  function sceneBoundsFromManifest(box: THREE.Box3, manifest: ViewerManifest | null): MinimapBounds {
    const fallback = sceneBoundsFromBox(box);
    const bounds = manifest?.scene_bounds;
    const center = asTriplet(bounds?.center);
    const size = asTriplet(bounds?.size);
    if (!center || !size) {
      return fallback;
    }
    const extent = Math.max(size[0], size[2]) * 0.5;
    if (!(extent > 0)) {
      return fallback;
    }
    const paddedExtent = Math.max(extent + 4, fallback.extent);
    return {
      minX: center[0] - paddedExtent,
      maxX: center[0] + paddedExtent,
      minZ: center[2] - paddedExtent,
      maxZ: center[2] + paddedExtent,
      center: new THREE.Vector3(center[0], center[1], center[2]),
      extent: paddedExtent,
    };
  }

  function updateMinimapCamera(bounds: MinimapBounds, box: THREE.Box3): void {
    currentSceneBounds = bounds;
    minimapCamera.left = -bounds.extent;
    minimapCamera.right = bounds.extent;
    minimapCamera.top = bounds.extent;
    minimapCamera.bottom = -bounds.extent;
    minimapCamera.near = 0.1;
    minimapCamera.far = Math.max(500, box.max.y - box.min.y + bounds.extent * 8);
    minimapCamera.position.set(bounds.center.x, box.max.y + bounds.extent * 2.2 + 10, bounds.center.z);
    minimapCamera.lookAt(bounds.center.x, 0, bounds.center.z);
    minimapCamera.updateProjectionMatrix();
  }

  function worldToMinimap(x: number, z: number): { x: number; y: number } | null {
    if (!currentSceneBounds) {
      return null;
    }
    const width = minimapOverlayEl.clientWidth;
    const height = minimapOverlayEl.clientHeight;
    if (width <= 0 || height <= 0) {
      return null;
    }
    const u = clamp((x - currentSceneBounds.minX) / (currentSceneBounds.maxX - currentSceneBounds.minX), 0, 1);
    const v = clamp((z - currentSceneBounds.minZ) / (currentSceneBounds.maxZ - currentSceneBounds.minZ), 0, 1);
    return {
      x: u * width,
      y: v * height,
    };
  }

  function drawMinimapOverlay(): void {
    const ctx = minimapOverlayEl.getContext("2d");
    if (!ctx) {
      return;
    }
    const width = minimapOverlayEl.width;
    const height = minimapOverlayEl.height;
    const cssWidth = minimapOverlayEl.clientWidth;
    const cssHeight = minimapOverlayEl.clientHeight;
    ctx.clearRect(0, 0, width, height);
    if (!currentSceneBounds || cssWidth <= 0 || cssHeight <= 0) {
      return;
    }

    const dpr = width / Math.max(cssWidth, 1);
    ctx.save();
    ctx.scale(dpr, dpr);

    ctx.strokeStyle = "rgba(15, 23, 42, 0.12)";
    ctx.lineWidth = 1;
    ctx.strokeRect(0.5, 0.5, cssWidth - 1, cssHeight - 1);

    const camPos = worldToMinimap(currentAvatarPosition.x, currentAvatarPosition.z);
    if (camPos) {
      const arrowForward = cameraForwardHorizontal();
      const arrow = new THREE.Vector2(arrowForward.x, arrowForward.z);
      if (arrow.lengthSq() > 1e-6) {
        arrow.normalize();
      }
      const arrowLength = 18;
      const tipX = camPos.x + arrow.x * arrowLength;
      const tipY = camPos.y + arrow.y * arrowLength;
      ctx.fillStyle = "#1f4ed8";
      ctx.beginPath();
      ctx.arc(camPos.x, camPos.y, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#1f4ed8";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(camPos.x, camPos.y);
      ctx.lineTo(tipX, tipY);
      ctx.stroke();
      ctx.fillStyle = "#1f4ed8";
      ctx.beginPath();
      ctx.arc(tipX, tipY, 2.8, 0, Math.PI * 2);
      ctx.fill();
    }

    if (currentLaserHitPoint) {
      const hitPoint = worldToMinimap(currentLaserHitPoint.x, currentLaserHitPoint.z);
      if (hitPoint) {
        ctx.fillStyle = "#ff5a4f";
        ctx.strokeStyle = "rgba(255, 90, 79, 0.25)";
        ctx.lineWidth = 6;
        ctx.beginPath();
        ctx.arc(hitPoint.x, hitPoint.y, 5.5, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(hitPoint.x, hitPoint.y, 3.2, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    ctx.restore();
  }

  function renderMinimap(): void {
    if (!currentRoot || !currentSceneBounds) {
      return;
    }
    minimapRenderer.render(scene, minimapCamera);
    drawMinimapOverlay();
  }

  function staticDescriptionForNode(nodeName: string): StaticObjectDescription | null {
    const descriptions = currentManifest?.static_object_descriptions ?? {};
    for (const [pattern, description] of Object.entries(descriptions)) {
      if (!description) {
        continue;
      }
      if (description.match === "exact" && nodeName === pattern) {
        return description;
      }
      if (description.match === "prefix" && nodeName.startsWith(pattern)) {
        return description;
      }
    }
    return null;
  }

  function resolveHitDescriptor(object: THREE.Object3D): HitDescriptor | null {
    let cursor: THREE.Object3D | null = object;
    const names: string[] = [];
    while (cursor) {
      if (cursor.name) {
        names.push(cursor.name);
      }
      cursor = cursor.parent;
    }

    for (const nodeName of names) {
      const instanceId = resolveInstanceIdFromName(nodeName);
      if (!instanceId) {
        continue;
      }
      const instanceInfo = currentManifest?.instances?.[instanceId];
      if (instanceInfo) {
        return {
          kind: "instance",
          nodeName,
          instanceId,
          instanceInfo,
          assetDescription: currentManifest?.asset_descriptions?.[instanceInfo.asset_id],
        };
      }
      return { kind: "generic", nodeName };
    }

    for (const nodeName of names) {
      const description = staticDescriptionForNode(nodeName);
      if (description) {
        return {
          kind: "static",
          nodeName,
          staticDescription: description,
        };
      }
    }

    const nodeName = names[0];
    return nodeName ? { kind: "generic", nodeName } : null;
  }

  function updateLaserPointer(): void {
    if (!laserToggleEl.checked || !currentRoot) {
      laserBeam.visible = false;
      laserHitDot.visible = false;
      currentLaserHitPoint = null;
      clearInfoCard();
      return;
    }

    const origin = camera.position.clone();
    const direction = new THREE.Vector3();
    camera.getWorldDirection(direction);
    raycaster.set(origin, direction.normalize());
    raycaster.far = 220;

    const intersections = raycaster
      .intersectObject(currentRoot, true)
      .filter((hit) => !(hit.object.userData && hit.object.userData.viewerHelper));

    const hit = intersections[0];
    const beamEnd = hit ? hit.point.clone() : origin.clone().add(direction.multiplyScalar(120));
    const positions = (laserBeam.geometry as THREE.BufferGeometry).getAttribute("position");
    positions.setXYZ(0, origin.x, origin.y, origin.z);
    positions.setXYZ(1, beamEnd.x, beamEnd.y, beamEnd.z);
    positions.needsUpdate = true;
    laserBeam.visible = true;

    if (!hit) {
      laserHitDot.visible = false;
      currentLaserHitPoint = null;
      clearInfoCard();
      return;
    }

    currentLaserHitPoint = hit.point.clone();
    laserHitDot.visible = true;
    laserHitDot.position.copy(hit.point);

    const descriptor = resolveHitDescriptor(hit.object);
    if (!descriptor) {
      clearInfoCard();
      return;
    }
    const content = buildHitDescriptorContent(descriptor);
    currentLaserCopyText = content.text;
    setInfoCardContent(content.html);
  }

  async function loadScene(option: SceneOption): Promise<void> {
    clearError(errorEl);
    setStatus(`Loading ${option.label}…`);
    if (controls.isLocked) {
      controls.unlock();
    }

    if (currentRoot) {
      scene.remove(currentRoot);
      disposeObject(currentRoot);
      currentRoot = null;
    }
    clearInfoCard();
    currentLaserHitPoint = null;
    laserHitDot.visible = false;
    laserBeam.visible = false;

    const gltf = await loader.loadAsync(option.glbUrl);
    currentRoot = gltf.scene;
    configureSceneObjectShadows(currentRoot);
    scene.add(currentRoot);

    const bbox = new THREE.Box3().setFromObject(currentRoot);
    const spawn = inferSpawnFromBbox(bbox, currentManifest ?? {
      layout_path: "",
      final_scene: { label: "Final Scene", glb_url: option.glbUrl },
      production_steps: [],
      default_selection: "final_scene",
    });
    currentSpawn = spawn.position;
    currentForward = spawn.forward;
    updateMinimapCamera(sceneBoundsFromManifest(bbox, currentManifest), bbox);
    resetView();
    applyLightingState();
    setStatus(`Viewing ${option.label}`);
  }

  function populateRecentLayoutOptions(layouts: RecentLayout[], selectedPath: string): void {
    recentLayoutsByPath.clear();
    layoutSelectEl.innerHTML = "";
    for (const layout of layouts) {
      recentLayoutsByPath.set(layout.layout_path, layout);
      const optionEl = document.createElement("option");
      optionEl.value = layout.layout_path;
      optionEl.textContent = compactUiLabel(layout.label);
      optionEl.title = layout.label;
      layoutSelectEl.appendChild(optionEl);
    }
    if (selectedPath && !recentLayoutsByPath.has(selectedPath)) {
      const optionEl = document.createElement("option");
      optionEl.value = selectedPath;
      const directLabel = makeDirectLayoutLabel(selectedPath);
      optionEl.textContent = compactUiLabel(directLabel);
      optionEl.title = directLabel;
      layoutSelectEl.appendChild(optionEl);
    }
    layoutSelectEl.disabled = layoutSelectEl.options.length === 0;
    if (selectedPath) {
      layoutSelectEl.value = selectedPath;
      const selectedLayout = recentLayoutsByPath.get(selectedPath);
      layoutSelectEl.title = selectedLayout?.label ?? makeDirectLayoutLabel(selectedPath);
    }
  }

  function populateSceneOptions(manifest: ViewerManifest): SceneOption[] {
    optionsByKey.clear();
    selectEl.innerHTML = "";
    const options = makeSceneOptions(manifest);
    for (const option of options) {
      optionsByKey.set(option.key, option);
      const optionEl = document.createElement("option");
      optionEl.value = option.key;
      optionEl.textContent = compactUiLabel(option.label, 42);
      optionEl.title = option.label;
      selectEl.appendChild(optionEl);
    }
    selectEl.disabled = options.length === 0;
    const selectedOption = options.find((option) => option.key === selectEl.value) ?? options[0];
    selectEl.title = selectedOption?.label ?? "";
    return options;
  }

  async function loadLayoutSelection(layoutPath: string): Promise<void> {
    clearError(errorEl);
    setStatus("Loading scene set…");
    currentLayoutPath = layoutPath;
    currentManifest = await loadManifest(layoutPath);
    const options = populateSceneOptions(currentManifest);
    if (options.length === 0) {
      throw new Error("No viewable GLB entries were found in this scene layout.");
    }
    const defaultKey = optionsByKey.has(currentManifest.default_selection)
      ? currentManifest.default_selection
      : options[0]?.key ?? "";
    selectEl.value = defaultKey;
    selectEl.title = optionsByKey.get(defaultKey)?.label ?? "";
    updateQueryLayout(layoutPath);
    await loadScene(optionsByKey.get(defaultKey) ?? options[0]);
  }

  renderer.domElement.addEventListener(
    "click",
    () => {
      if (!settingsOpen && !controls.isLocked) {
        controls.lock();
      }
    },
    { signal },
  );

  sceneGraphLinkEl.addEventListener(
    "click",
    () => {
      window.location.hash = "#scene-graph";
    },
    { signal },
  );

  assetEditorLinkEl.addEventListener(
    "click",
    () => {
      window.location.hash = "#asset-editor";
    },
    { signal },
  );

  settingsToggleEl.addEventListener("click", () => setSettingsOpen(!settingsOpen), { signal });
  settingsCloseEl.addEventListener("click", () => setSettingsOpen(false), { signal });

  minimapOverlayEl.addEventListener(
    "click",
    (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (!currentSceneBounds) {
        return;
      }
      const rect = minimapOverlayEl.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        return;
      }
      const nx = clamp((event.clientX - rect.left) / rect.width, 0, 1);
      const nz = clamp((event.clientY - rect.top) / rect.height, 0, 1);
      const worldX = currentSceneBounds.minX + nx * (currentSceneBounds.maxX - currentSceneBounds.minX);
      const worldZ = currentSceneBounds.minZ + nz * (currentSceneBounds.maxZ - currentSceneBounds.minZ);
      currentAvatarPosition.set(worldX, currentAvatarPosition.y, worldZ);
      syncCameraRig();
    },
    { signal },
  );

  for (const [presetKey, presetLabel] of Object.entries(LIGHTING_PRESET_LABELS)) {
    const optionEl = document.createElement("option");
    optionEl.value = presetKey;
    optionEl.textContent = presetLabel;
    lightingPresetEl.appendChild(optionEl);
  }

  lightingPresetEl.addEventListener(
    "change",
    () => {
      const nextPreset = lightingPresetEl.value;
      const presetValues = LIGHTING_PRESETS[nextPreset];
      if (!presetValues) {
        return;
      }
      lightingState.preset = nextPreset;
      Object.assign(lightingState, presetValues);
      syncLightingUi();
    },
    { signal },
  );

  exposureInput.addEventListener(
    "input",
    () => {
      lightingState.preset = "custom";
      lightingState.exposure = Number(exposureInput.value);
      syncLightingUi();
    },
    { signal },
  );
  keyInput.addEventListener(
    "input",
    () => {
      lightingState.preset = "custom";
      lightingState.keyLightIntensity = Number(keyInput.value);
      syncLightingUi();
    },
    { signal },
  );
  fillInput.addEventListener(
    "input",
    () => {
      lightingState.preset = "custom";
      lightingState.fillLightIntensity = Number(fillInput.value);
      syncLightingUi();
    },
    { signal },
  );
  warmthInput.addEventListener(
    "input",
    () => {
      lightingState.preset = "custom";
      lightingState.warmth = Number(warmthInput.value);
      syncLightingUi();
    },
    { signal },
  );
  shadowInput.addEventListener(
    "input",
    () => {
      lightingState.preset = "custom";
      lightingState.shadowStrength = Number(shadowInput.value);
      syncLightingUi();
    },
    { signal },
  );
  thirdPersonToggleEl.addEventListener(
    "change",
    () => {
      currentCameraMode = thirdPersonToggleEl.checked ? "third_person" : "first_person";
      syncCameraRig();
    },
    { signal },
  );
  laserToggleEl.addEventListener(
    "change",
    () => {
      crosshairEl.hidden = !laserToggleEl.checked;
      if (!laserToggleEl.checked) {
        clearInfoCard();
        laserBeam.visible = false;
        laserHitDot.visible = false;
        currentLaserHitPoint = null;
      }
    },
    { signal },
  );

  const handleControlsLock = () => updateOverlay();
  const handleControlsUnlock = () => updateOverlay();
  controls.addEventListener("lock", handleControlsLock);
  controls.addEventListener("unlock", handleControlsUnlock);

  window.addEventListener("resize", resizeRenderer, { signal });
  window.addEventListener("keydown", (event) => handleKey(event, true), { signal });
  window.addEventListener("keyup", (event) => handleKey(event, false), { signal });
  layoutSelectEl.addEventListener(
    "change",
    async () => {
      const nextLayoutPath = layoutSelectEl.value.trim();
      if (!nextLayoutPath || nextLayoutPath === currentLayoutPath) {
        return;
      }
      try {
        await loadLayoutSelection(nextLayoutPath);
        layoutSelectEl.title = recentLayoutsByPath.get(nextLayoutPath)?.label ?? makeDirectLayoutLabel(nextLayoutPath);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to load scene layout.";
        setError(errorEl, message);
        setStatus("Scene layout load failed");
      }
    },
    { signal },
  );
  selectEl.addEventListener(
    "change",
    async () => {
      const nextOption = optionsByKey.get(selectEl.value);
      if (!nextOption) {
        return;
      }
    try {
      selectEl.title = nextOption.label;
      await loadScene(nextOption);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load GLB.";
      setError(errorEl, message);
        setStatus("Scene load failed");
      }
    },
    { signal },
  );

  function animate(): void {
    if (destroyed) {
      return;
    }
    const delta = clock.getDelta();
    if (controls.isLocked) {
      const moveSpeed = moveState.sprint ? 8.5 : 4.5;
      const forwardAxis = Number(moveState.forward) - Number(moveState.backward);
      const sideAxis = Number(moveState.right) - Number(moveState.left);
      const forward = cameraForwardHorizontal();
      const right = new THREE.Vector3().crossVectors(forward, UP_AXIS).normalize();
      if (forwardAxis !== 0) {
        currentAvatarPosition.addScaledVector(forward, forwardAxis * moveSpeed * delta);
      }
      if (sideAxis !== 0) {
        currentAvatarPosition.addScaledVector(right, sideAxis * moveSpeed * delta);
      }
      currentAvatarPosition.y = Math.max(0, currentSpawn.y - AVATAR_EYE_HEIGHT_M);
      syncCameraRig();
    }
    updateLaserPointer();
    renderer.render(scene, camera);
    renderMinimap();
    animationFrameId = requestAnimationFrame(animate);
  }

  try {
    syncLightingUi();
    const requestedLayoutPath = parseQueryLayoutPath();
    const recentLayouts = await loadRecentLayouts();
    const initialLayoutPath = requestedLayoutPath ?? recentLayouts[0]?.layout_path ?? "";
    if (!initialLayoutPath) {
      throw new Error(
        "No recent scene layouts were found. Generate a scene first or open the viewer with ?layout=/abs/path/to/scene_layout.json.",
      );
    }
    populateRecentLayoutOptions(recentLayouts, initialLayoutPath);
    resizeRenderer();
    await loadLayoutSelection(initialLayoutPath);
    animate();
    updateOverlay();
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to initialize viewer.";
    setError(errorEl, message);
    setStatus("Viewer unavailable");
  }

  return () => {
    destroyed = true;
    if (animationFrameId) {
      cancelAnimationFrame(animationFrameId);
    }
    eventController.abort();
    controls.removeEventListener("lock", handleControlsLock);
    controls.removeEventListener("unlock", handleControlsUnlock);
    if (controls.isLocked) {
      controls.unlock();
    }
    renderer.dispose();
    minimapRenderer.dispose();
  };
}

export { mountViewer };
