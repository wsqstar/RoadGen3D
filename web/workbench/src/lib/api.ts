import { API_BASE, EvaluationScores, WalkabilityIndicators } from "./types";

const DEFAULT_TIMEOUT_MS = 30000;

export async function postJson<T>(path: string, payload: unknown, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    return handleJsonResponse<T>(response);
  } catch (error) {
    clearTimeout(timeoutId);
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error(`Request timed out after ${timeoutMs}ms`);
    }
    throw error;
  }
}

export async function getJson<T>(path: string, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    return handleJsonResponse<T>(response);
  } catch (error) {
    clearTimeout(timeoutId);
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error(`Request timed out after ${timeoutMs}ms`);
    }
    throw error;
  }
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

export interface EvaluationResponse {
  scores: EvaluationScores;
  indicators: WalkabilityIndicators | null;
  evaluation: string;
  suggestions: string[];
}

export async function evaluateScene(layoutPath: string): Promise<EvaluationResponse | null> {
  try {
    const response = await postJson<{
      walkability: number;
      safety: number;
      beauty: number;
      overall: number;
      evaluation: string;
      suggestions: string[];
      indicators: WalkabilityIndicators | null;
    }>("/api/design/evaluate/unified", {
      layout_path: layoutPath,
      image_path: null,
    }, 60000);

    return {
      scores: {
        walkability: response.walkability,
        safety: response.safety,
        beauty: response.beauty,
        overall: response.overall,
      },
      indicators: response.indicators,
      evaluation: response.evaluation,
      suggestions: response.suggestions,
    };
  } catch (error) {
    console.error("Evaluation API failed:", error);
    return null;
  }
}
