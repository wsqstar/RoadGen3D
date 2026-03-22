import fs from "node:fs";
import path from "node:path";
import { URL, fileURLToPath } from "node:url";

import { defineConfig, type Plugin } from "vite";

const viewerRoot = fileURLToPath(new URL(".", import.meta.url));
const repoRoot = path.resolve(viewerRoot, "..", "..");

function resolveRepoPath(rawPath: string | null): string | null {
  if (!rawPath) {
    return null;
  }
  const candidate = rawPath.trim();
  if (!candidate) {
    return null;
  }
  const resolved = path.resolve(candidate);
  const relative = path.relative(repoRoot, resolved);
  if (relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative))) {
    return resolved;
  }
  return null;
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
        if (requestUrl.pathname === "/api/layout") {
          const rawLayoutPath = requestUrl.searchParams.get("path");
          const layoutPath = resolveRepoPath(rawLayoutPath);
          if (!layoutPath) {
            jsonResponse(res, 403, { error: "Layout path must stay inside repo root." });
            return;
          }
          if (!fs.existsSync(layoutPath)) {
            jsonResponse(res, 404, { error: `Layout file not found: ${layoutPath}` });
            return;
          }
          try {
            const layoutPayload = JSON.parse(fs.readFileSync(layoutPath, "utf-8"));
            const outputs = layoutPayload.outputs ?? {};
            const finalScenePath = resolveRepoPath(String(outputs.scene_glb ?? ""));
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
                    const glbPath = resolveRepoPath(String(step.glb_path ?? ""));
                    if (!glbPath || !fs.existsSync(glbPath)) {
                      return null;
                    }
                    return {
                      step_id: String(step.step_id ?? ""),
                      title: String(step.title ?? step.step_id ?? "Production Step"),
                      glb_url: `/api/file?path=${encodeURIComponent(glbPath)}`,
                    };
                  })
                  .filter(Boolean)
              : [];
            const spawnPayload = buildSpawnPayload(layoutPayload);
            jsonResponse(res, 200, {
              layout_path: layoutPath,
              final_scene: {
                label: "Final Scene",
                glb_url: `/api/file?path=${encodeURIComponent(finalScenePath)}`,
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

        if (requestUrl.pathname === "/api/file") {
          const rawFilePath = requestUrl.searchParams.get("path");
          const filePath = resolveRepoPath(rawFilePath);
          if (!filePath) {
            textResponse(res, 403, "Requested file must stay inside repo root.");
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
  base: "/web-viewer/",
  plugins: [viewerApiPlugin()],
});
