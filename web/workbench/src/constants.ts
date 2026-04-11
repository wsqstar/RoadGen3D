// ── Workbench Simplified Constants ──

/**
 * Evaluation weights for the three main dimensions
 * Based on eval_quality.py analysis:
 * - Walkability (45%): 11 indicators (SID_CLR, CLEAR_CONT, FURN_D, etc.)
 * - Safety (35%): speed compliance, lighting, safety perception
 * - Beauty (20%): aesthetics, greening, street furniture
 */
export const EVALUATION_WEIGHTS = {
  walkability: 0.45,
  safety: 0.35,
  beauty: 0.20,
} as const;

/**
 * Score thresholds for visual indicators
 */
export const SCORE_THRESHOLDS = {
  excellent: 85,
  good: 70,
  fair: 50,
} as const;

/**
 * Workflow step definitions
 */
export const WORKFLOW_STEPS = [
  { step: 1, label: "选择模板", shortLabel: "模板" },
  { step: 2, label: "对比方案", shortLabel: "方案" },
  { step: 3, label: "评估可视化", shortLabel: "评估" },
] as const;

/**
 * Color palette for evaluation dimensions
 */
export const EVALUATION_COLORS = {
  walkability: {
    primary: "#3B82F6",   // Blue
    light: "#DBEAFE",
    dark: "#1E40AF",
  },
  safety: {
    primary: "#EF4444",   // Red
    light: "#FEE2E2",
    dark: "#991B1B",
  },
  beauty: {
    primary: "#10B981",   // Green
    light: "#D1FAE5",
    dark: "#065F46",
  },
  overall: {
    primary: "#8B5CF6",   // Purple
    light: "#EDE9FE",
    dark: "#5B21B6",
  },
} as const;

/**
 * Color palette for scheme cards
 */
export const SCHEME_COLORS = {
  A: "#3B82F6",  // Blue
  B: "#10B981",  // Green
  C: "#F59E0B",  // Amber
} as const;

/**
 * Walkability indicator metadata
 */
export const WALKABILITY_INDICATORS = {
  SID_CLR: { label: "人行道净宽", description: "SID_CLR - Sidewalk Clear Width", ideal: "≥ 3.0m" },
  CLEAR_CONT: { label: "净空连续性", description: "CLEAR_CONT - Clear Continuity", ideal: "≥ 0.8" },
  FURN_D: { label: "街道家具密度", description: "FURN_D - Furnishing Density", ideal: "0.1-0.2/m" },
  LIGHT_UNI: { label: "照明均匀度", description: "LIGHT_UNI - Lamp Uniformity", ideal: "≥ 0.8" },
  TREE_SHADE: { label: "绿化遮荫率", description: "TREE_SHADE - Tree Shade Fraction", ideal: "≥ 0.3" },
  BUFFER_RATIO: { label: "缓冲带比例", description: "BUFFER_RATIO - Buffer Ratio", ideal: "≥ 0.5" },
  TRANSIT_PROX: { label: "公交站可达性", description: "TRANSIT_PROX - Transit Proximity", ideal: "≤ 30m" },
  CROSS_PROV: { label: "过街设施", description: "CROSS_PROV - Crossing Provision", ideal: "≥ 1/80m" },
  ENTR_DENS: { label: "入口密度", description: "ENTR_DENS - Entrance Density", ideal: "≥ 4/100m" },
  POI_MIX: { label: "POI 混合度", description: "POI_MIX - POI Mix", ideal: "≥ 0.7" },
  MICRO_ENV: { label: "微气候环境", description: "MICRO_ENV - Micro Environment", ideal: "≥ 0.6" },
} as const;

/**
 * Pillar scores metadata for walkability
 */
export const WALKABILITY_PILLARS = {
  Protection: { label: "防护性", weight: 0.40 },
  Comfort: { label: "舒适度", weight: 0.35 },
  Delight: { label: "愉悦性", weight: 0.25 },
} as const;

/**
 * Chart dimensions
 */
export const CHART_CONFIG = {
  radar: {
    size: 280,
    padding: 40,
    labelOffset: 25,
  },
  bar: {
    height: 200,
    barWidth: 40,
    barGap: 20,
    labelOffset: 30,
  },
} as const;

/**
 * Animation durations (ms)
 */
export const ANIMATION = {
  stepTransition: 300,
  cardHover: 200,
  chartRender: 500,
  progressUpdate: 150,
} as const;

/**
 * Generation polling interval
 */
export const GENERATION_POLL_INTERVAL_MS = 1500;

/**
 * Maximum generation attempts before timeout
 */
export const MAX_GENERATION_TIMEOUT_MS = 300000; // 5 minutes
