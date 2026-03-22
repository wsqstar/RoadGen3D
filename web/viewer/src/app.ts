import * as THREE from "three";
import { PointerLockControls } from "three/examples/jsm/controls/PointerLockControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

type SceneOption = {
  key: string;
  label: string;
  glbUrl: string;
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
};

type MovementState = {
  forward: boolean;
  backward: boolean;
  left: boolean;
  right: boolean;
  sprint: boolean;
};

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (!element) {
    throw new Error(`Missing required viewer element: ${selector}`);
  }
  return element;
}

function isFiniteTriplet(value: unknown): value is [number, number, number] {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every((item) => Number.isFinite(item))
  );
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

  const size = bbox.getSize(new THREE.Vector3());
  const center = bbox.getCenter(new THREE.Vector3());
  if (size.x >= size.z) {
    return {
      position: new THREE.Vector3(center.x - size.x * 0.35, 1.65, center.z),
      forward: new THREE.Vector3(1, 0, 0),
    };
  }
  return {
    position: new THREE.Vector3(center.x, 1.65, center.z - size.z * 0.35),
    forward: new THREE.Vector3(0, 0, 1),
  };
}

function parseQueryLayoutPath(): string {
  const search = new URLSearchParams(window.location.search);
  const layoutPath = search.get("layout") ?? "";
  if (!layoutPath.trim()) {
    throw new Error("Missing ?layout=/abs/path/to/scene_layout.json query parameter.");
  }
  return layoutPath;
}

async function loadManifest(layoutPath: string): Promise<ViewerManifest> {
  const response = await fetch(`./api/layout?path=${encodeURIComponent(layoutPath)}`);
  const payload = (await response.json()) as ViewerManifest | { error?: string };
  if (!response.ok) {
    throw new Error(payload && "error" in payload ? String(payload.error ?? "Failed to load scene layout.") : "Failed to load scene layout.");
  }
  return payload as ViewerManifest;
}

export async function mountViewer(root: HTMLElement): Promise<void> {
  root.innerHTML = `
    <div class="viewer-shell">
      <div class="viewer-topbar">
        <div class="viewer-controls">
          <label class="viewer-label" for="scene-select">Scene</label>
          <select id="scene-select" class="viewer-select"></select>
        </div>
        <div class="viewer-help">
          Click to capture mouse · WASD move · Shift sprint · Esc unlock · R reset
        </div>
      </div>
      <div id="viewer-canvas" class="viewer-canvas"></div>
      <div id="viewer-status" class="viewer-status">Loading viewer…</div>
      <div id="viewer-overlay" class="viewer-overlay">Click scene to capture mouse</div>
      <div id="viewer-error" class="viewer-error" hidden></div>
    </div>
  `;

  const canvasHost = requireElement<HTMLElement>(root, "#viewer-canvas");
  const statusEl = requireElement<HTMLElement>(root, "#viewer-status");
  const overlayEl = requireElement<HTMLElement>(root, "#viewer-overlay");
  const errorEl = requireElement<HTMLElement>(root, "#viewer-error");
  const selectEl = requireElement<HTMLSelectElement>(root, "#scene-select");

  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#f7f6f3");

  const camera = new THREE.PerspectiveCamera(70, 1, 0.05, 2000);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(canvasHost.clientWidth, canvasHost.clientHeight);
  canvasHost.appendChild(renderer.domElement);

  const hemiLight = new THREE.HemisphereLight(0xfafcff, 0xd9d9d2, 1.15);
  scene.add(hemiLight);

  const dirLight = new THREE.DirectionalLight(0xffffff, 0.95);
  dirLight.position.set(18, 28, 12);
  scene.add(dirLight);

  const controls = new PointerLockControls(camera, renderer.domElement);
  scene.add(camera);

  const loader = new GLTFLoader();
  const clock = new THREE.Clock();
  const moveState: MovementState = {
    forward: false,
    backward: false,
    left: false,
    right: false,
    sprint: false,
  };

  let currentRoot: THREE.Object3D | null = null;
  let currentManifest: ViewerManifest | null = null;
  let currentSpawn = new THREE.Vector3(0, 1.65, 0);
  let currentForward = new THREE.Vector3(1, 0, 0);
  const optionsByKey = new Map<string, SceneOption>();

  function resizeRenderer(): void {
    const width = Math.max(1, canvasHost.clientWidth);
    const height = Math.max(1, canvasHost.clientHeight);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height);
  }

  function resetView(): void {
    camera.position.copy(currentSpawn);
    const target = currentSpawn.clone().add(currentForward);
    camera.lookAt(target);
  }

  function updateOverlay(): void {
    overlayEl.hidden = controls.isLocked;
  }

  function handleKey(event: KeyboardEvent, active: boolean): void {
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
      default:
        return;
    }
    event.preventDefault();
  }

  async function loadScene(option: SceneOption): Promise<void> {
    clearError(errorEl);
    statusEl.textContent = `Loading ${option.label}…`;
    if (controls.isLocked) {
      controls.unlock();
    }

    if (currentRoot) {
      scene.remove(currentRoot);
      disposeObject(currentRoot);
      currentRoot = null;
    }

    const gltf = await loader.loadAsync(option.glbUrl);
    currentRoot = gltf.scene;
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
    resetView();
    statusEl.textContent = `Viewing ${option.label}`;
  }

  renderer.domElement.addEventListener("click", () => {
    if (!controls.isLocked) {
      controls.lock();
    }
  });

  controls.addEventListener("lock", updateOverlay);
  controls.addEventListener("unlock", updateOverlay);
  window.addEventListener("resize", resizeRenderer);
  window.addEventListener("keydown", (event) => handleKey(event, true));
  window.addEventListener("keyup", (event) => handleKey(event, false));

  function animate(): void {
    const delta = clock.getDelta();
    if (controls.isLocked) {
      const moveSpeed = moveState.sprint ? 8.5 : 4.5;
      const forwardAxis = Number(moveState.forward) - Number(moveState.backward);
      const sideAxis = Number(moveState.right) - Number(moveState.left);
      if (forwardAxis !== 0) {
        controls.moveForward(forwardAxis * moveSpeed * delta);
      }
      if (sideAxis !== 0) {
        controls.moveRight(sideAxis * moveSpeed * delta);
      }
      camera.position.y = currentSpawn.y;
    }
    renderer.render(scene, camera);
    requestAnimationFrame(animate);
  }

  try {
    const layoutPath = parseQueryLayoutPath();
    const manifest = await loadManifest(layoutPath);
    currentManifest = manifest;
    const options = makeSceneOptions(manifest);
    for (const option of options) {
      optionsByKey.set(option.key, option);
      const optionEl = document.createElement("option");
      optionEl.value = option.key;
      optionEl.textContent = option.label;
      selectEl.appendChild(optionEl);
    }
    const defaultKey = optionsByKey.has(manifest.default_selection)
      ? manifest.default_selection
      : options[0]?.key ?? "";
    selectEl.value = defaultKey;
    selectEl.addEventListener("change", async () => {
      const nextOption = optionsByKey.get(selectEl.value);
      if (!nextOption) {
        return;
      }
      try {
        await loadScene(nextOption);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to load GLB.";
        setError(errorEl, message);
        statusEl.textContent = "Scene load failed";
      }
    });
    resizeRenderer();
    if (options.length === 0) {
      throw new Error("No viewable GLB entries were found in this scene layout.");
    }
    await loadScene(optionsByKey.get(defaultKey) ?? options[0]);
    animate();
    updateOverlay();
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to initialize viewer.";
    setError(errorEl, message);
    statusEl.textContent = "Viewer unavailable";
  }
}
