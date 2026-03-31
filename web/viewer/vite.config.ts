import fs from "node:fs";
import path from "node:path";
import { URL, fileURLToPath } from "node:url";

import { defineConfig, type Plugin } from "vite";

const viewerRoot = fileURLToPath(new URL(".", import.meta.url));
const repoRoot = path.resolve(viewerRoot, "..", "..");
const RECENT_LAYOUT_LIMIT = 20;
const ASSET_MANIFEST_PATH = path.resolve(repoRoot, "data", "real", "real_assets_manifest.jsonl");
const ASSET_MANIFESTS_DIR = path.resolve(repoRoot, "data", "real");
const EXTRA_ASSET_MANIFEST_DIRS = [
  path.resolve(repoRoot, "data", "street_furniture"),
];
const IGNORED_DISCOVERY_DIRS = new Set([
  ".git",
  ".venv",
  ".pytest_cache",
  "__pycache__",
  "node_modules",
  "dist",
]);

type JsonRecord = Record<string, unknown>;

type StaticObjectDescription = {
  match: "exact" | "prefix";
  title: string;
  category: string;
  source: string;
  intro: string;
  design_note: string;
};

let cachedAssetDescriptionIndex: Map<string, JsonRecord> | null = null;

function allowedRoots(): string[] {
  const roots = [repoRoot];
  // Add common external asset caches so the dev server can serve them
  const homeDir = process.env.HOME || process.env.USERPROFILE || "";
  const knownCaches = [
    homeDir && path.join(homeDir, ".objaverse"),
    homeDir && path.join(homeDir, ".cache"),
  ].filter(Boolean) as string[];
  for (const cache of knownCaches) {
    if (fs.existsSync(cache) && !roots.includes(cache)) {
      roots.push(cache);
    }
  }
  const extra = (process.env.ROADGEN_VIEWER_ALLOWED_ROOTS ?? "")
    .split(path.delimiter)
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => path.resolve(item));
  for (const root of extra) {
    if (!roots.includes(root)) {
      roots.push(root);
    }
  }
  return roots;
}

function resolveAllowedPath(rawPath: string | null): string | null {
  if (!rawPath) {
    return null;
  }
  const candidate = rawPath.trim();
  if (!candidate) {
    return null;
  }
  const resolved = path.resolve(candidate);
  for (const root of allowedRoots()) {
    const relative = path.relative(root, resolved);
    if (relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative))) {
      return resolved;
    }
  }
  return null;
}

function discoverSceneLayoutPaths(roots: string[]): string[] {
  const seen = new Set<string>();
  const results: string[] = [];
  for (const root of roots) {
    if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
      continue;
    }
    const stack = [root];
    while (stack.length > 0) {
      const current = stack.pop() ?? "";
      let entries: fs.Dirent[] = [];
      try {
        entries = fs.readdirSync(current, { withFileTypes: true });
      } catch {
        continue;
      }
      for (const entry of entries) {
        const fullPath = path.join(current, entry.name);
        if (entry.isDirectory()) {
          if (!IGNORED_DISCOVERY_DIRS.has(entry.name)) {
            stack.push(fullPath);
          }
          continue;
        }
        if (!entry.isFile() || entry.name !== "scene_layout.json") {
          continue;
        }
        const resolved = path.resolve(fullPath);
        if (seen.has(resolved)) {
          continue;
        }
        seen.add(resolved);
        results.push(resolved);
      }
    }
  }
  return results;
}

function displayPathFor(filePath: string, roots: string[]): string {
  for (const root of roots) {
    const relative = path.relative(root, filePath);
    if (relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative))) {
      return relative || path.basename(filePath);
    }
  }
  return path.basename(filePath);
}

function buildRecentLayoutsPayload(limit: number): { results: Array<Record<string, unknown>> } {
  const roots = allowedRoots();
  const safeLimit = Math.max(1, Number.isFinite(limit) ? Math.trunc(limit) : RECENT_LAYOUT_LIMIT);
  const results = discoverSceneLayoutPaths(roots)
    .map((layoutPath) => {
      const stats = fs.statSync(layoutPath);
      const relativePath = displayPathFor(layoutPath, roots);
      return {
        layout_path: layoutPath,
        label: `${path.basename(path.dirname(layoutPath))} · ${relativePath}`,
        relative_path: relativePath,
        updated_at: new Date(stats.mtimeMs).toISOString(),
        mtime_ms: Math.trunc(stats.mtimeMs),
      };
    })
    .sort((left, right) => Number(right.mtime_ms) - Number(left.mtime_ms))
    .slice(0, safeLimit);
  return { results };
}

function asNumber(value: unknown, fallback: number): number {
  return Number.isFinite(value) ? Number(value) : fallback;
}

function asFiniteNumberOrNull(value: unknown): number | null {
  return Number.isFinite(value) ? Number(value) : null;
}

function asTriplet(value: unknown): [number, number, number] | null {
  if (!Array.isArray(value) || value.length !== 3) {
    return null;
  }
  const items = value.map((entry) => asFiniteNumberOrNull(entry));
  if (items.some((entry) => entry === null)) {
    return null;
  }
  return [items[0] ?? 0, items[1] ?? 0, items[2] ?? 0];
}

function asQuad(value: unknown): [number, number, number, number] | null {
  if (!Array.isArray(value) || value.length !== 4) {
    return null;
  }
  const items = value.map((entry) => asFiniteNumberOrNull(entry));
  if (items.some((entry) => entry === null)) {
    return null;
  }
  return [items[0] ?? 0, items[1] ?? 0, items[2] ?? 0, items[3] ?? 0];
}

function cleanForJson(value: unknown): unknown {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (Array.isArray(value)) {
    return value.map((entry) => cleanForJson(entry));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, entry]) => [key, cleanForJson(entry)]),
    );
  }
  return value;
}

function loadAssetDescriptionIndex(): Map<string, JsonRecord> {
  if (cachedAssetDescriptionIndex) {
    return cachedAssetDescriptionIndex;
  }
  const index = new Map<string, JsonRecord>();
  if (!fs.existsSync(ASSET_MANIFEST_PATH)) {
    cachedAssetDescriptionIndex = index;
    return index;
  }
  const lines = fs.readFileSync(ASSET_MANIFEST_PATH, "utf-8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }
    try {
      const parsed = JSON.parse(trimmed) as JsonRecord;
      const assetId = String(parsed.asset_id ?? "").trim();
      if (assetId) {
        index.set(assetId, parsed);
      }
    } catch {
      continue;
    }
  }
  cachedAssetDescriptionIndex = index;
  return index;
}

function fallbackAssetDescription(assetId: string, category: string): JsonRecord {
  return {
    asset_id: assetId,
    category,
    text_desc: `${category || "street_object"} · ${assetId}`,
    source: "scene_generated",
  };
}

function buildAssetDescriptions(layoutPayload: JsonRecord): Record<string, JsonRecord> {
  const placements = Array.isArray(layoutPayload.placements) ? layoutPayload.placements : [];
  const assetIds = new Set<string>();
  for (const placement of placements) {
    if (!placement || typeof placement !== "object") {
      continue;
    }
    const assetId = String((placement as JsonRecord).asset_id ?? "").trim();
    if (assetId) {
      assetIds.add(assetId);
    }
  }
  const index = loadAssetDescriptionIndex();
  const descriptions: Record<string, JsonRecord> = {};
  for (const assetId of assetIds) {
    const manifestRow = index.get(assetId);
    if (manifestRow) {
      descriptions[assetId] = cleanForJson({
        asset_id: assetId,
        category: String(manifestRow.category ?? "").trim(),
        text_desc: String(manifestRow.text_desc ?? "").trim(),
        source: String(manifestRow.source ?? "").trim(),
        asset_role: String(manifestRow.asset_role ?? "").trim(),
      }) as JsonRecord;
      continue;
    }
    const placement = placements.find(
      (entry) =>
        entry &&
        typeof entry === "object" &&
        String((entry as JsonRecord).asset_id ?? "").trim() === assetId,
    ) as JsonRecord | undefined;
    descriptions[assetId] = cleanForJson(
      fallbackAssetDescription(assetId, String(placement?.category ?? "").trim()),
    ) as JsonRecord;
  }
  return descriptions;
}

function buildInstancePayloads(layoutPayload: JsonRecord): Record<string, JsonRecord> {
  const placements = Array.isArray(layoutPayload.placements) ? layoutPayload.placements : [];
  const instances: Record<string, JsonRecord> = {};
  for (const placement of placements) {
    if (!placement || typeof placement !== "object") {
      continue;
    }
    const row = placement as JsonRecord;
    const instanceId = String(row.instance_id ?? "").trim();
    if (!instanceId) {
      continue;
    }
    const positionXyz = asTriplet(row.position_xyz);
    const bboxXz = asQuad(row.bbox_xz);
    instances[instanceId] = cleanForJson({
      instance_id: instanceId,
      asset_id: String(row.asset_id ?? "").trim(),
      category: String(row.category ?? "").trim(),
      placement_group: String(row.placement_group ?? "").trim(),
      theme_id: String(row.theme_id ?? "").trim(),
      selection_source: String(row.selection_source ?? "").trim(),
      position_xyz: positionXyz,
      bbox_xz: bboxXz,
      anchor_poi_type: String(row.anchor_poi_type ?? "").trim(),
      anchor_distance_m: asFiniteNumberOrNull(row.anchor_distance_m),
      feasibility_score: asFiniteNumberOrNull(row.feasibility_score),
      constraint_penalty: asFiniteNumberOrNull(row.constraint_penalty),
      dist_to_road_edge_m: asFiniteNumberOrNull(row.dist_to_road_edge_m),
      dist_to_nearest_junction_m: asFiniteNumberOrNull(row.dist_to_nearest_junction_m),
      dist_to_nearest_entrance_m: asFiniteNumberOrNull(row.dist_to_nearest_entrance_m),
    }) as JsonRecord;
  }
  return instances;
}

function buildStaticObjectDescriptions(): Record<string, StaticObjectDescription> {
  return {
    road_slab: {
      match: "exact",
      title: "机动车道",
      category: "roadway",
      source: "system",
      intro: "这是街道中的机动车道铺装面。",
      design_note: "承担机动车连续通行，并作为道路中心线与车道组织的依附基底。",
    },
    sidewalk_: {
      match: "prefix",
      title: "人行道铺装",
      category: "sidewalk",
      source: "system",
      intro: "这是街道的人行活动界面。",
      design_note: "为步行、停留和沿街活动提供连续可达的基础空间。",
    },
    curb_: {
      match: "prefix",
      title: "路缘石",
      category: "landscape",
      source: "system",
      intro: "这是车行与步行空间之间的边界构件。",
      design_note: "用于强化空间边界、组织排水，并提升行人与车辆分隔的可读性。",
    },
    centerline_mark_: {
      match: "prefix",
      title: "道路中心线",
      category: "marking",
      source: "system",
      intro: "这是机动车道的中心虚线标记。",
      design_note: "用于组织双向行驶秩序并强化道路方向识别。",
    },
    lane_mark_: {
      match: "prefix",
      title: "车道标线",
      category: "marking",
      source: "system",
      intro: "这是机动车道内的辅助标线。",
      design_note: "用于强化车道组织与行驶边界，提升整体交通可读性。",
    },
    crossing_patch_: {
      match: "prefix",
      title: "过街区",
      category: "crossing",
      source: "system",
      intro: "这是街道中的过街铺装区。",
      design_note: "用于提示行人过街位置，并在交叉口或重点界面提升可达性。",
    },
    tree_pit_: {
      match: "prefix",
      title: "树池",
      category: "landscape",
      source: "system",
      intro: "这是街树的种植基底。",
      design_note: "为树木生长提供透水与根系空间，同时构成街道绿化节奏。",
    },
    transit_pad_: {
      match: "prefix",
      title: "公交停靠面",
      category: "transit",
      source: "system",
      intro: "这是公交候车或停靠相关的铺装面。",
      design_note: "用于组织公交换乘与停靠，保障候车与上下车的空间清晰度。",
    },
    zoning_proxy_: {
      match: "prefix",
      title: "用地界面体块",
      category: "scene_object",
      source: "system",
      intro: "这是用于表达沿街用地和建筑界面的代理体块。",
      design_note: "用于在设计预览中快速表现街墙连续性和空间围合关系。",
    },
  };
}

function buildSceneBounds(layoutPayload: JsonRecord): JsonRecord {
  const placements = Array.isArray(layoutPayload.placements) ? layoutPayload.placements : [];
  const summary = (layoutPayload.summary ?? {}) as JsonRecord;
  const spatialContext = (summary.spatial_context ?? {}) as JsonRecord;

  let minX = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let minZ = Number.POSITIVE_INFINITY;
  let maxZ = Number.NEGATIVE_INFINITY;
  let maxY = 0;

  const includeXz = (x: number, z: number, padding = 0) => {
    minX = Math.min(minX, x - padding);
    maxX = Math.max(maxX, x + padding);
    minZ = Math.min(minZ, z - padding);
    maxZ = Math.max(maxZ, z + padding);
  };

  for (const placement of placements) {
    if (!placement || typeof placement !== "object") {
      continue;
    }
    const row = placement as JsonRecord;
    const bbox = asQuad(row.bbox_xz);
    if (bbox) {
      minX = Math.min(minX, bbox[0]);
      minZ = Math.min(minZ, bbox[1]);
      maxX = Math.max(maxX, bbox[2]);
      maxZ = Math.max(maxZ, bbox[3]);
    } else {
      const position = asTriplet(row.position_xyz);
      if (position) {
        includeXz(position[0], position[2], 0.75);
        maxY = Math.max(maxY, position[1]);
      }
    }
    const scaleY = asTriplet(row.scale_xyz)?.[1];
    if (scaleY !== null && scaleY !== undefined) {
      maxY = Math.max(maxY, scaleY);
    }
  }

  const roadHalfWidth = Math.max(3, asNumber(spatialContext.road_half_width_m, 6));
  const lengthM = Math.max(24, asNumber(spatialContext.length_m, asNumber(summary.length_m, 80)));
  if (!Number.isFinite(minX) || !Number.isFinite(maxX) || !Number.isFinite(minZ) || !Number.isFinite(maxZ)) {
    minX = -lengthM * 0.5;
    maxX = lengthM * 0.5;
    minZ = -roadHalfWidth * 3.5;
    maxZ = roadHalfWidth * 3.5;
  }

  const sizeX = Math.max(1, maxX - minX);
  const sizeZ = Math.max(1, maxZ - minZ);
  const sizeY = Math.max(12, maxY + 10);
  const roadAxis: [number, number, number] = sizeX >= sizeZ ? [1, 0, 0] : [0, 0, 1];

  return cleanForJson({
    center: [(minX + maxX) * 0.5, sizeY * 0.5, (minZ + maxZ) * 0.5],
    size: [sizeX, sizeY, sizeZ],
    road_axis: roadAxis,
  }) as JsonRecord;
}

function buildSpawnPayload(layoutPayload: Record<string, any>): {
  spawn_point: [number, number, number];
  forward_vector: [number, number, number];
} {
  const summary = layoutPayload.summary ?? {};
  const lengthM = Math.max(24, asNumber(summary.length_m, 80));
  return {
    spawn_point: [-(lengthM * 0.35), 1.65, 0],
    forward_vector: [1, 0, 0],
  };
}

function jsonResponse(res: any, statusCode: number, payload: Record<string, unknown>): void {
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(payload, null, 2));
}

function textResponse(res: any, statusCode: number, message: string): void {
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "text/plain; charset=utf-8");
  res.end(message);
}

function contentTypeFor(filePath: string): string {
  const suffix = path.extname(filePath).toLowerCase();
  switch (suffix) {
    case ".glb":
      return "model/gltf-binary";
    case ".gltf":
      return "model/gltf+json";
    case ".json":
      return "application/json; charset=utf-8";
    case ".png":
      return "image/png";
    case ".jpg":
    case ".jpeg":
      return "image/jpeg";
    case ".webp":
      return "image/webp";
    default:
      return "application/octet-stream";
  }
}

function viewerApiPlugin(): Plugin {
  return {
    name: "roadgen3d-viewer-api",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (!req.url) {
          next();
          return;
        }

        const requestUrl = new URL(req.url, "http://127.0.0.1:4173");
        const isLayoutRoute =
          requestUrl.pathname === "/api/layout" ||
          requestUrl.pathname === "/web-viewer/api/layout";
        const isRecentLayoutsRoute =
          requestUrl.pathname === "/api/recent-layouts" ||
          requestUrl.pathname === "/web-viewer/api/recent-layouts";
        const isFileRoute =
          requestUrl.pathname === "/api/file" ||
          requestUrl.pathname === "/web-viewer/api/file";
        const apiPrefix = requestUrl.pathname.startsWith("/web-viewer/")
          ? "/web-viewer/api"
          : "/api";

        if (isLayoutRoute) {
          const rawLayoutPath = requestUrl.searchParams.get("path");
          const layoutPath = resolveAllowedPath(rawLayoutPath);
          if (!layoutPath) {
            jsonResponse(res, 403, { error: "Layout path must stay inside an allowed root." });
            return;
          }
          if (!fs.existsSync(layoutPath)) {
            jsonResponse(res, 404, { error: `Layout file not found: ${layoutPath}` });
            return;
          }
          try {
            const layoutPayload = JSON.parse(fs.readFileSync(layoutPath, "utf-8"));
            const outputs = layoutPayload.outputs ?? {};
            const finalScenePath = resolveAllowedPath(String(outputs.scene_glb ?? ""));
            if (!finalScenePath || !fs.existsSync(finalScenePath)) {
              jsonResponse(res, 400, {
                error: "scene_layout.json does not point to a valid final scene GLB.",
                layout_path: layoutPath,
              });
              return;
            }
            const productionSteps = Array.isArray(layoutPayload.production_steps)
              ? layoutPayload.production_steps
                  .map((step: Record<string, any>) => {
                    const glbPath = resolveAllowedPath(String(step.glb_path ?? ""));
                    if (!glbPath || !fs.existsSync(glbPath)) {
                      return null;
                    }
                    return {
                      step_id: String(step.step_id ?? ""),
                      title: String(step.title ?? step.step_id ?? "Production Step"),
                      glb_url: `${apiPrefix}/file?path=${encodeURIComponent(glbPath)}`,
                    };
                  })
                  .filter(Boolean)
              : [];
            const spawnPayload = buildSpawnPayload(layoutPayload);
            const sceneBounds = buildSceneBounds(layoutPayload);
            const instances = buildInstancePayloads(layoutPayload);
            const assetDescriptions = buildAssetDescriptions(layoutPayload);
            const staticObjectDescriptions = buildStaticObjectDescriptions();
            jsonResponse(res, 200, {
              layout_path: layoutPath,
              final_scene: {
                label: "Final Scene",
                glb_url: `${apiPrefix}/file?path=${encodeURIComponent(finalScenePath)}`,
              },
              production_steps: productionSteps,
              default_selection: "final_scene",
              spawn_point: spawnPayload.spawn_point,
              forward_vector: spawnPayload.forward_vector,
              scene_bounds: sceneBounds,
              instances,
              asset_descriptions: assetDescriptions,
              static_object_descriptions: staticObjectDescriptions,
            });
            return;
          } catch (error) {
            jsonResponse(res, 500, {
              error: error instanceof Error ? error.message : "Failed to parse scene layout.",
            });
            return;
          }
        }

        if (isRecentLayoutsRoute) {
          const requestedLimit = Number.parseInt(requestUrl.searchParams.get("limit") ?? "", 10);
          jsonResponse(res, 200, buildRecentLayoutsPayload(requestedLimit));
          return;
        }

        if (isFileRoute) {
          const rawFilePath = requestUrl.searchParams.get("path");
          const filePath = resolveAllowedPath(rawFilePath);
          if (!filePath) {
            textResponse(res, 403, "Requested file must stay inside an allowed root.");
            return;
          }
          if (!fs.existsSync(filePath)) {
            textResponse(res, 404, `File not found: ${filePath}`);
            return;
          }
          const stats = fs.statSync(filePath);
          if (!stats.isFile()) {
            textResponse(res, 400, `Not a regular file: ${filePath}`);
            return;
          }
          res.statusCode = 200;
          res.setHeader("Content-Type", contentTypeFor(filePath));
          res.setHeader("Content-Length", String(stats.size));
          fs.createReadStream(filePath).pipe(res);
          return;
        }

        /* ── Asset Manifest API ─────────────────────────────────────── */

        const isAssetManifestsRoute =
          requestUrl.pathname === "/api/asset-manifests" ||
          requestUrl.pathname === "/web-viewer/api/asset-manifests";
        const isAssetManifestDataRoute =
          requestUrl.pathname === "/api/asset-manifest" ||
          requestUrl.pathname === "/web-viewer/api/asset-manifest";
        const isAssetManifestSaveRoute =
          requestUrl.pathname === "/api/asset-manifest/save" ||
          requestUrl.pathname === "/web-viewer/api/asset-manifest/save";

        if (isAssetManifestsRoute) {
          const manifests: Array<{ name: string; label: string; count: number }> = [];
          
          // Helper function to scan a directory for manifests
          const scanManifestDir = (dirPath: string, prefix: string = "") => {
            if (!fs.existsSync(dirPath)) return;
            const entries = fs.readdirSync(dirPath);
            for (const entry of entries) {
              if (!entry.endsWith(".jsonl")) continue;
              const fullPath = path.join(dirPath, entry);
              if (!fs.statSync(fullPath).isFile()) continue;
              const lines = fs.readFileSync(fullPath, "utf-8").split(/\r?\n/);
              let count = 0;
              for (const line of lines) {
                if (line.trim()) count++;
              }
              const baseName = entry.replace(/\.jsonl$/, "").replace(/[_-]/g, " ");
              const label = baseName.charAt(0).toUpperCase() + baseName.slice(1);
              // Use prefix in name to distinguish manifests from different directories
              const name = prefix ? `${prefix}/${entry}` : entry;
              manifests.push({ name, label: prefix ? `[${prefix}] ${label}` : label, count });
            }
          };
          
          // Scan main directory
          scanManifestDir(ASSET_MANIFESTS_DIR);
          
          // Scan extra directories with prefix
          for (const extraDir of EXTRA_ASSET_MANIFEST_DIRS) {
            const dirName = path.basename(extraDir);
            scanManifestDir(extraDir, dirName);
          }
          
          jsonResponse(res, 200, { manifests });
          return;
        }

        if (isAssetManifestDataRoute) {
          const manifestName = requestUrl.searchParams.get("name") ?? "";
          if (!manifestName) {
            jsonResponse(res, 400, { error: "Missing 'name' query parameter." });
            return;
          }
          
          // Resolve manifest path - check if it has a prefix (e.g., "street_furniture/file.jsonl")
          let manifestPath: string | null = null;
          
          if (manifestName.includes("/")) {
            // Has prefix - look in extra directories
            const [prefix, fileName] = manifestName.split("/", 2);
            const extraDir = EXTRA_ASSET_MANIFEST_DIRS.find(
              (dir) => path.basename(dir) === prefix
            );
            if (extraDir) {
              const candidate = path.join(extraDir, fileName);
              // Ensure path is within the extra directory
              const relative = path.relative(extraDir, candidate);
              if (!relative.startsWith("..") && !path.isAbsolute(relative)) {
                manifestPath = candidate;
              }
            }
          } else {
            // No prefix - look in main directory
            const candidate = path.resolve(ASSET_MANIFESTS_DIR, manifestName);
            const relative = path.relative(ASSET_MANIFESTS_DIR, candidate);
            if (!relative.startsWith("..") && !path.isAbsolute(relative)) {
              manifestPath = candidate;
            }
          }
          
          if (!manifestPath) {
            jsonResponse(res, 403, { error: "Invalid manifest name." });
            return;
          }
          if (!fs.existsSync(manifestPath)) {
            jsonResponse(res, 404, { error: `Manifest not found: ${manifestName}` });
            return;
          }
          const lines = fs.readFileSync(manifestPath, "utf-8").split(/\r?\n/);
          const assets: JsonRecord[] = [];
          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) continue;
            try {
              assets.push(JSON.parse(trimmed) as JsonRecord);
            } catch {
              continue;
            }
          }
          jsonResponse(res, 200, { assets });
          return;
        }

        if (isAssetManifestSaveRoute) {
          if (req.method !== "POST") {
            jsonResponse(res, 405, { error: "Method not allowed. Use POST." });
            return;
          }
          const body = await readRequestBody(req);
          let parsed: { manifest_name?: string; asset_id?: string; updates?: JsonRecord };
          try {
            parsed = JSON.parse(body) as typeof parsed;
          } catch {
            jsonResponse(res, 400, { error: "Invalid JSON body." });
            return;
          }
          const { manifest_name: mName, asset_id: aId, updates } = parsed;
          if (!mName || !aId) {
            jsonResponse(res, 400, { error: "Missing manifest_name or asset_id." });
            return;
          }
          
          // Resolve manifest path - check if it has a prefix
          let manifestPath: string | null = null;
          
          if (mName.includes("/")) {
            // Has prefix - look in extra directories
            const [prefix, fileName] = mName.split("/", 2);
            const extraDir = EXTRA_ASSET_MANIFEST_DIRS.find(
              (dir) => path.basename(dir) === prefix
            );
            if (extraDir) {
              const candidate = path.join(extraDir, fileName);
              const relative = path.relative(extraDir, candidate);
              if (!relative.startsWith("..") && !path.isAbsolute(relative)) {
                manifestPath = candidate;
              }
            }
          } else {
            // No prefix - look in main directory
            const candidate = path.resolve(ASSET_MANIFESTS_DIR, mName);
            const relative = path.relative(ASSET_MANIFESTS_DIR, candidate);
            if (!relative.startsWith("..") && !path.isAbsolute(relative)) {
              manifestPath = candidate;
            }
          }
          
          if (!manifestPath) {
            jsonResponse(res, 403, { error: "Invalid manifest name." });
            return;
          }
          if (!fs.existsSync(manifestPath)) {
            jsonResponse(res, 404, { error: `Manifest not found: ${mName}` });
            return;
          }
          const rawLines = fs.readFileSync(manifestPath, "utf-8").split(/\r?\n/);
          const newLines: string[] = [];
          let found = false;
          for (const line of rawLines) {
            const trimmed = line.trim();
            if (!trimmed) {
              newLines.push(line);
              continue;
            }
            try {
              const record = JSON.parse(trimmed) as JsonRecord;
              if (String(record.asset_id ?? "") === aId) {
                const merged = { ...record, ...(updates ?? {}) };
                newLines.push(JSON.stringify(merged));
                found = true;
              } else {
                newLines.push(trimmed);
              }
            } catch {
              newLines.push(line);
            }
          }
          if (!found) {
            jsonResponse(res, 404, { error: `Asset not found: ${aId}` });
            return;
          }
          fs.writeFileSync(manifestPath, newLines.join("\n"), "utf-8");
          cachedAssetDescriptionIndex = null;
          jsonResponse(res, 200, { ok: true });
          return;
        }

        next();
      });
    },
  };
}

function readRequestBody(req: any): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", reject);
  });
}

export default defineConfig({
  base: "/",
  plugins: [viewerApiPlugin()],
});
