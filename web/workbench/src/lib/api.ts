import type { EvaluationScores, WalkabilityIndicators, ComparisonResult, ImprovementResult, LlmStatusMap } from "./types";
import type { ScenePreset, SceneDiffResult } from "./types";
import { API_BASE } from "./types";

const DEFAULT_TIMEOUT_MS = 30000;

export interface GraphTemplate {
  template_id: string;
  label: string;
  description: string;
  image_url: string;
}

export async function fetchPresets(): Promise<ScenePreset[]> {
  try {
    const response = await getJson<{ items: ScenePreset[] }>("/api/presets");
    return response.items;
  } catch (error) {
    console.error("Failed to fetch presets:", error);
    return [];
  }
}

export async function fetchGraphTemplates(): Promise<GraphTemplate[]> {
  try {
    const response = await getJson<{ items: GraphTemplate[] }>("/api/graph-templates");
    return response.items;
  } catch (error) {
    console.error("Failed to fetch graph templates:", error);
    return [];
  }
}

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

export interface DesignIntent {
  user_goals: string[];
  style_preferences: string[];
  safety_priorities: string[];
  follow_up_questions: string[];
  rag_queries: string[];
}

export interface DraftResponse {
  stage?: string;
  intent?: DesignIntent;
  draft?: {
    normalized_scene_query: string;
    compose_config_patch: Record<string, string | number>;
    citations_by_field: Record<string, string[]>;
    design_summary: string;
    risk_notes: string[];
  } | null;
  warnings?: string[];
  evidence?: unknown[];
  // Direct draft fields (backward compatibility)
  normalized_scene_query?: string;
  compose_config_patch?: Record<string, string | number>;
  citations_by_field?: Record<string, string[]>;
  design_summary?: string;
  risk_notes?: string[];
}

export interface DraftDesignOptions {
  messages: ChatMessage[];
  userInput: string;
  currentPatch?: Record<string, string | number>;
  topk?: number;
  knowledgeSource?: string;
  force?: boolean;  // Skip clarification, force draft generation
}

export interface EvaluationResponse {
  scores: EvaluationScores;
  indicators: WalkabilityIndicators | null;
  evaluation: string;
  suggestions: string[];
  config_patch?: Record<string, any>;
  llm_status?: LlmStatusMap | null;
  comparison?: ComparisonResult;
}

export async function evaluateScene(layoutPath: string): Promise<EvaluationResponse | null> {
  try {
    const response = await postJson<{
      walkability: number;
      safety: number | null;
      beauty: number | null;
      overall: number | null;
      evaluation: string;
      suggestions: string[];
      indicators: WalkabilityIndicators | null;
      config_patch?: Record<string, any>;
      llm_status?: LlmStatusMap | null;
    }>("/api/design/evaluate/unified", {
      layout_path: layoutPath,
      image_path: null,
    }, 60000);

    if (response.safety === null || response.beauty === null || response.overall === null) {
      return null;
    }

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
      config_patch: response.config_patch,
      llm_status: response.llm_status,
    };
  } catch (error) {
    console.error("Evaluation API failed:", error);
    return null;
  }
}

export async function evaluateSceneWithHistory(
  currentLayoutPath: string,
  previousLayoutPath: string,
  previousScore?: number,
  previousEvaluation?: string
): Promise<EvaluationResponse | null> {
  try {
    const response = await postJson<{
      walkability: number;
      safety: number;
      beauty: number;
      overall: number;
      evaluation: string;
      suggestions: string[];
      indicators: WalkabilityIndicators | null;
      llm_status?: LlmStatusMap | null;
      comparison: ComparisonResult;
    }>("/api/design/evaluate/compare", {
      current_layout_path: currentLayoutPath,
      previous_layout_path: previousLayoutPath,
      previous_score: previousScore,
      previous_evaluation: previousEvaluation,
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
      llm_status: response.llm_status,
      comparison: response.comparison,
    };
  } catch (error) {
    console.error("Evaluation compare API failed:", error);
    return null;
  }
}

export async function proposeImprovement(
  currentEvaluation: string,
  currentPatch: Record<string, any>,
  comparison?: ComparisonResult,
  weaknessQueries?: string[]
): Promise<ImprovementResult | null> {
  try {
    const response = await postJson<{
      config_patch: Record<string, any>;
      citations?: string[];
      reasoning?: string;
    }>("/api/design/improve", {
      current_evaluation: currentEvaluation,
      comparison: comparison || {},
      current_patch: currentPatch,
      weakness_queries: weaknessQueries || [],
    }, 60000);

    return {
      config_patch: response.config_patch,
      citations: response.citations,
      reasoning: response.reasoning,
    };
  } catch (error) {
    console.error("Improve API failed:", error);
    return null;
  }
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export async function draftDesign(options: DraftDesignOptions): Promise<DraftResponse | null> {
  const {
    messages = [],
    userInput,
    currentPatch = {},
    topk = 6,
    knowledgeSource = "graph_rag",
    force = false,
  } = options;

  try {
    const response = await postJson<DraftResponse>("/api/design/draft", {
      messages,
      user_input: userInput,
      current_patch: currentPatch,
      topk,
      knowledge_source: knowledgeSource,
      force,
    }, 60000);

    return response;
  } catch (error) {
    console.error("Draft design API failed:", error);
    return null;
  }
}

export async function compareScenes(
  layoutA: string,
  layoutB: string
): Promise<SceneDiffResult | null> {
  try {
    const response = await postJson<SceneDiffResult>("/api/scenes/diff", {
      layout_a: layoutA,
      layout_b: layoutB,
    }, 30000);

    return response;
  } catch (error) {
    console.error("Scene compare API failed:", error);
    return null;
  }
}

export function getDiffImageUrl(
  layoutA: string,
  layoutB: string,
  mode: "overlay" | "delta" = "overlay"
): string {
  return `${API_BASE}/api/scenes/diff/image?layout_a=${encodeURIComponent(layoutA)}&layout_b=${encodeURIComponent(layoutB)}&mode=${mode}`;
}
