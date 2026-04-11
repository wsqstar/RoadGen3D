import { API_BASE, EvaluationScores } from "./types";

export function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (!element) {
    throw new Error(`Missing required element: ${selector}`);
  }
  return element;
}

export async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleJsonResponse<T>(response);
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  return handleJsonResponse<T>(response);
}

export function resolveApiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) {
    return path;
  }
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

async function handleJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with status ${response.status}`);
  }
  return (await response.json()) as T;
}

/**
 * Call unified evaluation API to get walkability/safety/beauty scores.
 */
export async function evaluateScene(layoutPath: string): Promise<EvaluationScores> {
  const response = await postJson<{
    walkability: number;
    safety: number;
    beauty: number;
    overall: number;
    evaluation: string;
    suggestions: string[];
  }>("/api/design/evaluate/unified", {
    layout_path: layoutPath,
    image_path: null,
  });

  return {
    walkability: response.walkability,
    safety: response.safety,
    beauty: response.beauty,
    overall: response.overall,
  };
}
