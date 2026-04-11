import { EVALUATION_WEIGHTS } from "./constants";
import type { KnowledgeSourceKey, DesignDraft, EvaluationScores, RadarChartData, BarChartData, WalkabilityIndicators } from "./types";
import { API_BASE, SUMMARY_OMIT_KEYS, VIEWER_BASE, FIELD_CONFIGS } from "./types";
import { EVALUATION_COLORS, SCHEME_COLORS } from "./constants";

// ── HTML Utilities ────────────────────────────────────────────────────────────

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ── Error Handling ─────────────────────────────────────────────────────────────

export function asErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

export function formatBootstrapError(error: unknown): string {
  const message = asErrorMessage(error).trim();
  if (!message) {
    return `无法连接 API：${API_BASE}`;
  }
  if (/failed to fetch|networkerror|load failed|fetch failed|couldn't connect|cannot connect/i.test(message)) {
    return `无法连接 API：${API_BASE}`;
  }
  return message;
}

// ── Knowledge Source Utilities ─────────────────────────────────────────────────

export function normalizeKnowledgeSourceKey(value: string): KnowledgeSourceKey {
  if (value === "pdf_rag" || value === "graph_rag") {
    return value;
  }
  return "hybrid";
}

export function formatKnowledgeSourceLabel(source: string): string {
  switch (source) {
    case "pdf_rag":
      return "PDF RAG";
    case "graph_rag":
      return "GraphRAG";
    case "hybrid":
      return "Hybrid";
    default:
      return source || "Unknown";
  }
}

export function formatParameterSourceLabel(source: string): string {
  switch (source) {
    case "rag":
      return "RAG evidence";
    case "llm_inferred":
      return "LLM inference";
    case "user_override":
      return "User override";
    case "system_default":
      return "System default";
    default:
      return "Unknown";
  }
}

// ── Time Utilities ─────────────────────────────────────────────────────────────

export function formatTimestamp(value: string): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

// ── Geometry Utilities ─────────────────────────────────────────────────────────

export function formatBbox(bbox: [number, number, number, number]): string {
  return `(${bbox.map((value) => value.toFixed(4)).join(", ")})`;
}

export function formatMetricValue(value: number, digits = 2): string {
  return Number(value)
    .toFixed(digits)
    .replace(/\.0+$/, "")
    .replace(/(\.\d*?[1-9])0+$/, "$1");
}

// ── Scene Summary Utilities ───────────────────────────────────────────────────

export function compactSceneSummary(summary: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(summary || {}).filter(([key]) => !SUMMARY_OMIT_KEYS.has(key)),
  );
}

export function formatUnknownBbox(value: unknown): string {
  if (!Array.isArray(value) || value.length !== 4 || value.some((item) => typeof item !== "number" || !Number.isFinite(item))) {
    return "";
  }
  return `(${value.map((item) => Number(item).toFixed(4)).join(", ")})`;
}

export function renderTagRow(items: string[]): string {
  if (!items.length) {
    return `<div class="field-note">none</div>`;
  }
  return `<div class="tag-row">${items.map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("")}</div>`;
}

// ── Viewer URL Utilities ──────────────────────────────────────────────────────

export function normalizeSceneLayoutPath(layoutPath: string): string {
  const trimmed = String(layoutPath || "").trim();
  if (!trimmed) {
    return "";
  }
  if (/scene_layout\.json$/i.test(trimmed)) {
    return trimmed;
  }
  return `${trimmed.replace(/\/+$/, "")}/scene_layout.json`;
}

export function buildFallbackViewerUrl(layoutPath: string): string {
  const normalizedLayoutPath = normalizeSceneLayoutPath(layoutPath);
  if (!normalizedLayoutPath) {
    return "";
  }
  return `${VIEWER_BASE}/?layout=${encodeURIComponent(normalizedLayoutPath)}`;
}

export function resolveViewerUrl(viewerUrl: string, layoutPath: string): string {
  return String(viewerUrl || "").trim() || buildFallbackViewerUrl(layoutPath);
}

// ── Draft Utilities ───────────────────────────────────────────────────────────

export function formatDraftSummary(draft: DesignDraft): string {
  return [
    draft.design_summary || "No summary returned.",
    draft.risk_notes.length ? `\nRisk Notes:\n- ${draft.risk_notes.join("\n- ")}` : "",
  ].join("");
}

export function buildClarificationAssistantMessage(questions: string[]): string {
  if (!questions.length) {
    return "我还需要补充一些关键信息后，才能继续生成设计草案。";
  }
  return [
    "继续生成设计草案前，我还需要确认这些关键信息：",
    ...questions.map((question, index) => `${index + 1}. ${question}`),
  ].join("\n");
}

export function buildDraftFromForm(baseDraft: DesignDraft, parameterForm: HTMLDivElement): DesignDraft {
  const composeConfigPatch: Record<string, string | number> = {};
  const citationsByField: Record<string, string[]> = { ...baseDraft.citations_by_field };
  const parameterSourcesByField: Record<string, string> = { ...baseDraft.parameter_sources_by_field };
  FIELD_CONFIGS.forEach((field) => {
    const input = parameterForm.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-key="${field.key}"]`);
    if (!input) {
      return;
    }
    const raw = input.value.trim();
    if (!raw) {
      return;
    }
    const nextValue = field.type === "number" ? Number(raw) : raw;
    composeConfigPatch[field.key] = nextValue;
    const baseValue = baseDraft.compose_config_patch[field.key];
    if (String(baseValue ?? "") !== String(nextValue)) {
      parameterSourcesByField[field.key] = "user_override";
      delete citationsByField[field.key];
    }
  });
  return {
    ...baseDraft,
    normalized_scene_query: String(composeConfigPatch.query || baseDraft.normalized_scene_query),
    compose_config_patch: composeConfigPatch,
    citations_by_field: citationsByField,
    parameter_sources_by_field: parameterSourcesByField,
  };
}

// ── Scene Summary Highlight Utilities ─────────────────────────────────────────

export function renderSceneSummaryHighlights(summary: Record<string, unknown>): string {
  const rows: string[] = [];
  const layoutMode = String(summary.layout_mode || "");
  if (layoutMode) {
    rows.push(`<div><strong>layout_mode</strong>: ${escapeHtml(layoutMode)}</div>`);
  }
  if (summary.reference_plan_label) {
    rows.push(`<div><strong>reference_plan</strong>: ${escapeHtml(String(summary.reference_plan_label))}</div>`);
  } else if (summary.reference_plan_id) {
    rows.push(`<div><strong>reference_plan_id</strong>: ${escapeHtml(String(summary.reference_plan_id))}</div>`);
  }
  if (summary.graph_template_label) {
    rows.push(`<div><strong>graph_template</strong>: ${escapeHtml(String(summary.graph_template_label))}</div>`);
  } else if (summary.graph_template_id) {
    rows.push(`<div><strong>graph_template_id</strong>: ${escapeHtml(String(summary.graph_template_id))}</div>`);
  }
  if (summary.generation_stage) {
    rows.push(`<div><strong>generation_stage</strong>: ${escapeHtml(String(summary.generation_stage))}</div>`);
  }
  const requestedAoi = formatUnknownBbox(summary.requested_aoi_bbox);
  if (requestedAoi) {
    rows.push(`<div><strong>requested_aoi_bbox</strong>: ${escapeHtml(requestedAoi)}</div>`);
  }
  const effectiveAoi = formatUnknownBbox(summary.effective_aoi_bbox || summary.aoi_bbox);
  if (effectiveAoi) {
    rows.push(`<div><strong>effective_aoi_bbox</strong>: ${escapeHtml(effectiveAoi)}</div>`);
  }
  if (summary.selected_road_osm_id !== undefined && summary.selected_road_osm_id !== null) {
    rows.push(`<div><strong>selected_road_osm_id</strong>: ${escapeHtml(String(summary.selected_road_osm_id))}</div>`);
  }
  if (summary.selected_highway_type) {
    rows.push(`<div><strong>selected_highway_type</strong>: ${escapeHtml(String(summary.selected_highway_type))}</div>`);
  }
  if (summary.building_footprint_count !== undefined) {
    rows.push(`<div><strong>building_footprint_count</strong>: ${escapeHtml(String(summary.building_footprint_count))}</div>`);
  }
  if (summary.infill_footprint_count !== undefined) {
    rows.push(`<div><strong>infill_footprint_count</strong>: ${escapeHtml(String(summary.infill_footprint_count))}</div>`);
  }
  const buildingGenerationMode = String(summary.building_generation_mode_used || summary.building_generation_mode || "");
  if (buildingGenerationMode) {
    rows.push(`<div><strong>building_generation_mode</strong>: ${escapeHtml(buildingGenerationMode)}</div>`);
  }
  [
    { key: "total_network_length_m", label: "total_network_length_m", digits: 1 },
    { key: "junction_density_per_100m", label: "junction_density_per_100m", digits: 3 },
    { key: "connectivity_ratio", label: "connectivity_ratio", digits: 3 },
    { key: "network_width_m", label: "network_width_m", digits: 1 },
    { key: "network_height_m", label: "network_height_m", digits: 1 },
    { key: "branching_factor", label: "branching_factor", digits: 3 },
  ].forEach((item) => {
    const value = summary[item.key];
    if (typeof value === "number" && Number.isFinite(value)) {
      rows.push(`<div><strong>${escapeHtml(item.label)}</strong>: ${escapeHtml(formatMetricValue(value, item.digits))}</div>`);
    }
  });
  if (!rows.length) {
    return "";
  }
  return `<div class="summary-list">${rows.join("")}</div>`;
}

// ── Evaluation Utilities ────────────────────────────────────────────────────────

/**
 * Calculate overall score from individual dimension scores
 */
export function calculateOverallScore(scores: EvaluationScores): number {
  return Math.round(
    scores.walkability * EVALUATION_WEIGHTS.walkability +
    scores.safety * EVALUATION_WEIGHTS.safety +
    scores.beauty * EVALUATION_WEIGHTS.beauty
  );
}

/**
 * Get color for score value (red -> yellow -> green)
 */
export function getScoreColor(value: number): string {
  if (value >= 85) return "#10B981"; // Green
  if (value >= 70) return "#F59E0B"; // Amber
  if (value >= 50) return "#EF4444"; // Red
  return "#6B7280"; // Gray
}

/**
 * Get CSS class for score category
 */
export function getScoreCategory(value: number): "excellent" | "good" | "fair" | "poor" {
  if (value >= 85) return "excellent";
  if (value >= 70) return "good";
  if (value >= 50) return "fair";
  return "poor";
}

/**
 * Convert evaluation data to radar chart format
 */
export function toRadarChartData(schemes: { id: string; scores: EvaluationScores }[]): RadarChartData {
  const colors = [SCHEME_COLORS.A, SCHEME_COLORS.B, SCHEME_COLORS.C];
  return {
    labels: ["步行性", "安全性", "美观度"],
    datasets: schemes.map((scheme, index) => ({
      label: `方案 ${scheme.id}`,
      data: [scheme.scores.walkability, scheme.scores.safety, scheme.scores.beauty],
      color: colors[index % 3],
    })),
  };
}

/**
 * Convert evaluation data to bar chart format
 */
export function toBarChartData(schemes: { id: string; scores: EvaluationScores }[]): BarChartData {
  const colors = [SCHEME_COLORS.A, SCHEME_COLORS.B, SCHEME_COLORS.C];
  return {
    labels: ["步行性", "安全性", "美观度"],
    datasets: schemes.map((scheme, index) => ({
      label: `方案 ${scheme.id}`,
      data: [scheme.scores.walkability, scheme.scores.safety, scheme.scores.beauty],
      color: colors[index % 3],
    })),
  };
}

/**
 * Format percentage for display
 */
export function formatPercent(value: number, decimals = 0): string {
  return `${(value * 100).toFixed(decimals)}%`;
}

/**
 * Get dimension label with color indicator
 */
export function getDimensionLabel(dimension: "walkability" | "safety" | "beauty"): { label: string; color: string } {
  switch (dimension) {
    case "walkability":
      return { label: "步行性", color: EVALUATION_COLORS.walkability.primary };
    case "safety":
      return { label: "安全性", color: EVALUATION_COLORS.safety.primary };
    case "beauty":
      return { label: "美观度", color: EVALUATION_COLORS.beauty.primary };
  }
}

/**
 * Rank schemes by overall score
 */
export function rankSchemes(schemes: { id: string; scores: EvaluationScores }[]): { id: string; rank: number; scores: EvaluationScores }[] {
  const sorted = [...schemes].sort((a, b) => {
    const aOverall = calculateOverallScore(a.scores);
    const bOverall = calculateOverallScore(b.scores);
    return bOverall - aOverall;
  });

  let currentRank = 1;
  return sorted.map((scheme, index) => {
    if (index > 0) {
      const prevOverall = calculateOverallScore(sorted[index - 1].scores);
      const currentOverall = calculateOverallScore(scheme.scores);
      if (currentOverall < prevOverall) {
        currentRank = index + 1;
      }
    }
    return { ...scheme, rank: currentRank };
  });
}
