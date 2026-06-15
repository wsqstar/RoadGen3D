export const EVALUATION_WEIGHTS = {
  walkability: 0.45,
  safety: 0.35,
  beauty: 0.20,
} as const;

export const WORKFLOW_STEPS = [
  { step: 1, label: "选择模板", shortLabel: "模板" },
  { step: 2, label: "对比方案", shortLabel: "方案" },
  { step: 3, label: "评估可视化", shortLabel: "评估" },
] as const;

export const EVALUATION_COLORS = {
  walkability: {
    primary: "#3B82F6",
    light: "#DBEAFE",
    dark: "#1E40AF",
  },
  safety: {
    primary: "#EF4444",
    light: "#FEE2E2",
    dark: "#991B1B",
  },
  beauty: {
    primary: "#10B981",
    light: "#D1FAE5",
    dark: "#065F46",
  },
  overall: {
    primary: "#8B5CF6",
    light: "#EDE9FE",
    dark: "#5B21B6",
  },
} as const;

export const SCHEME_COLORS = {
  A: "#3B82F6",
  B: "#10B981",
  C: "#F59E0B",
} as const;

export const WALKABILITY_INDICATORS = {
  SID_CLR: { label: "人行道净宽", description: "Sidewalk Clear Width", ideal: "≥ 3.0m" },
  CLEAR_CONT: { label: "净空连续性", description: "Clear Continuity", ideal: "≥ 0.8" },
  FURN_D: { label: "街道家具密度", description: "Furnishing Density", ideal: "0.1-0.2/m" },
  LIGHT_UNI: { label: "照明均匀度", description: "Lamp Uniformity", ideal: "≥ 0.8" },
  TREE_SHADE: { label: "绿化遮荫率", description: "Tree Shade Fraction", ideal: "≥ 0.3" },
  BUFFER_RATIO: { label: "缓冲带比例", description: "Buffer Ratio", ideal: "≥ 0.5" },
  TRANSIT_PROX: { label: "公交站可达性", description: "Transit Proximity", ideal: "≤ 30m" },
  CROSS_PROV: { label: "过街设施", description: "Crossing Provision", ideal: "≥ 1/80m" },
  ENTR_DENS: { label: "入口密度", description: "Entrance Density", ideal: "≥ 4/100m" },
  POI_MIX: { label: "POI 混合度", description: "POI Mix", ideal: "≥ 0.7" },
  MICRO_ENV: { label: "微气候环境", description: "Micro Environment", ideal: "≥ 0.6" },
} as const;

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
