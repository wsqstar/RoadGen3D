import { VIEWER_BASE } from "./types";
import { SCHEME_COLORS, EVALUATION_WEIGHTS } from "./constants";
import type { EvaluationScores, RadarChartData, BarChartData } from "./types";

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function normalizeSceneLayoutPath(layoutPath: string): string {
  const trimmed = String(layoutPath || "").trim();
  if (!trimmed) return "";
  if (/scene_layout\.json$/i.test(trimmed)) return trimmed;
  return `${trimmed.replace(/\/+$/, "")}/scene_layout.json`;
}

export function buildFallbackViewerUrl(layoutPath: string): string {
  const normalized = normalizeSceneLayoutPath(layoutPath);
  if (!normalized) return "";
  return `${VIEWER_BASE}/?layout=${encodeURIComponent(normalized)}`;
}

export function resolveViewerUrl(viewerUrl: string, layoutPath: string): string {
  return String(viewerUrl || "").trim() || buildFallbackViewerUrl(layoutPath);
}

export function calculateOverallScore(scores: EvaluationScores): number {
  return Math.round(
    scores.walkability * EVALUATION_WEIGHTS.walkability +
    scores.safety * EVALUATION_WEIGHTS.safety +
    scores.beauty * EVALUATION_WEIGHTS.beauty
  );
}

export function getScoreColor(value: number): string {
  if (value >= 85) return "#10B981";
  if (value >= 70) return "#F59E0B";
  if (value >= 50) return "#EF4444";
  return "#6B7280";
}

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
