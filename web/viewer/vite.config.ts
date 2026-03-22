import fs from "node:fs";
import path from "node:path";
import { URL, fileURLToPath } from "node:url";

import { defineConfig, type Plugin } from "vite";

const viewerRoot = fileURLToPath(new URL(".", import.meta.url));
const repoRoot = path.resolve(viewerRoot, "..", "..");
const RECENT_LAYOUT_LIMIT = 20;
const IGNORED_DISCOVERY_DIRS = new Set([
  ".git",
  ".venv",
  ".pytest_cache",
  "__pycache__",
  "node_modules",
  "dist",
]);

function allowedRoots(): string[] {
  const roots = [repoRoot];
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

        next();
      });
    },
  };
}

export default defineConfig({
  base: "/",
  plugins: [viewerApiPlugin()],
});
