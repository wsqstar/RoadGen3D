// ── Type definitions for React Workbench ──

export type KnowledgeSourceKey = "hybrid" | "pdf_rag" | "graph_rag" | "scenario_parameters";

export type DesignDraft = {
  normalized_scene_query: string;
  compose_config_patch: Record<string, string | number>;
  citations_by_field: Record<string, string[]>;
  design_summary: string;
  risk_notes: string[];
  parameter_sources_by_field: Record<string, string>;
};

export type SceneContext = {
  layout_mode: "template" | "osm" | "metaurban" | "graph_template";
  aoi_bbox: [number, number, number, number] | null;
  city_name_en: string | null;
  reference_plan_id: string | null;
  graph_template_id: string | null;
};

export type SceneJobCreateResponse = {
  job_id: string;
  status: string;
  created_at: string;
};

export type SceneJobStatusResponse = {
  job_id: string;
  status: string;
  created_at: string;
  started_at: string;
  finished_at: string;
  error: string;
  stage: string;
  progress: number;
  operations: Array<{
    timestamp?: string;
    stage?: string;
    progress?: number;
    message?: string;
    name?: string;
    status?: string;
    detail?: Record<string, unknown>;
  }>;
  result: {
    scene_layout_path: string;
    scene_glb_path: string;
    scene_ply_path: string;
    viewer_url: string;
  } | null;
};

export type EvaluationScores = {
  walkability: number;
  safety: number;
  beauty: number;
  overall: number;
};

export type LlmStatusEntry = {
  enabled?: boolean;
  available?: boolean;
  source?: string;
  cached?: boolean;
  reasoning?: string;
};

export type LlmStatusMap = {
  safety?: LlmStatusEntry;
  beauty?: LlmStatusEntry;
};

export type WalkabilityIndicators = {
  SID_CLR: number;
  CLEAR_CONT: number;
  FURN_D: number;
  LIGHT_UNI: number;
  TREE_SHADE: number;
  BUFFER_RATIO: number;
  TRANSIT_PROX: number;
  CROSS_PROV: number;
  ENTR_DENS: number;
  POI_MIX: number;
  MICRO_ENV: number;
};

export type EvaluationResult = {
  sceneId: string;
  scores: EvaluationScores;
  indicators: WalkabilityIndicators;
  pillarScores: {
    Protection: number;
    Comfort: number;
    Delight: number;
  };
  evaluation?: string;
  suggestions?: string[];
  config_patch?: Record<string, any>;
  llmStatus?: LlmStatusMap | null;
  comparison?: {
    improved_areas: string[];
    regressed_areas: string[];
    unchanged_areas: string[];
    reasoning: string;
  };
};

export type ComparisonResult = {
  improved_areas: string[];
  regressed_areas: string[];
  unchanged_areas: string[];
  reasoning: string;
};

export type ImprovementResult = {
  config_patch: Record<string, any>;
  citations?: string[];
  reasoning?: string;
};

export type SchemeStatus = "idle" | "generating" | "ready" | "failed";

export interface GeneratedScheme {
  id: string;
  name: string;
  presetId: string;
  layoutPath: string;
  previewUrl: string;
  viewerUrl: string;
  evaluation: EvaluationScores;
  indicators: WalkabilityIndicators | null;
  evaluationText: string;
  suggestions: string[];
  llmStatus?: LlmStatusMap | null;
  status: SchemeStatus;
  progress: number;
}

export type ScenePreset = {
  id: string;
  name: string;
  nameEn: string;
  description: string;
  icon: string;
  color: string;
  prompt: string;
  configPatch: Record<string, string | number>;
};

export type WorkflowStep = 1 | 2 | 3;

export interface RadarChartData {
  labels: string[];
  datasets: {
    label: string;
    data: number[];
    color: string;
  }[];
}

export interface BarChartData {
  labels: string[];
  datasets: {
    label: string;
    data: number[];
    color: string;
  }[];
}

// Scene comparison types
export interface ConfigDiff {
  added: Record<string, any>;
  removed: Record<string, any>;
  changed: Record<string, { old: any; new: any }>;
}

export interface MetricDiff {
  key: string;
  old: number | null;
  new: number | null;
  delta: number;
  delta_pct: number | null;
}

export interface MetricsDiff {
  metrics: MetricDiff[];
}

export interface PlacementDiff {
  category: string;
  count_a: number;
  count_b: number;
  delta: number;
  matched: number;
  added: number;
  deleted: number;
  moved: number;
  mean_position_shift_m: number;
}

export interface PlacementsDiff {
  total_count_a: number;
  total_count_b: number;
  total_delta: number;
  category_stats: PlacementDiff[];
  added_instances: Array<{ category: string; position_xyz: number[] }>;
  deleted_instances: Array<{ category: string; position_xyz: number[] }>;
  moved_instances: Array<{ category: string; distance_m: number; a: any; b: any }>;
}

export interface SceneDiffResult {
  config_diff: ConfigDiff;
  metrics_diff: MetricsDiff;
  placements_diff: PlacementsDiff;
}

export interface CompareScheme {
  id: string;
  name: string;
  layoutPath: string;
  previewUrl: string;
  viewerUrl: string;
  evaluation: EvaluationScores;
  indicators: WalkabilityIndicators | null;
}

export const API_BASE = (import.meta.env.VITE_ROADGEN_API_BASE as string | undefined) || "http://127.0.0.1:8010";
export const VIEWER_BASE = (import.meta.env.VITE_ROADGEN_VIEWER_BASE as string | undefined) || "http://127.0.0.1:4173";
export const DEFAULT_GRAPH_TEMPLATE_ID = "hkust_gz_gate";
export const POLL_INTERVAL_MS = 1500;
export const MAX_GENERATION_ATTEMPTS = 120;

// Fallback presets when API is not available (kept for offline/demo mode)
// SCENE_PRESETS are now fetched from /api/presets endpoint
export const FALLBACK_SCENE_PRESETS: ScenePreset[] = [
  {
    id: "pedestrian_friendly",
    name: "步行友好",
    nameEn: "Pedestrian Friendly",
    description: "行人优先，安全舒适",
    icon: "🚶",
    color: "#4CAF50",
    prompt: "步行安全，全龄友好的完整街道，安静、安全、舒适",
    configPatch: {
      design_rule_profile: "pedestrian_priority_v1",
      objective_profile: "balanced",
      density: 0.5,
      ped_demand_level: "high",
      bike_demand_level: "medium",
      transit_demand_level: "medium",
      vehicle_demand_level: "low",
    },
  },
  {
    id: "commercial_vitality",
    name: "商业活力",
    nameEn: "Commercial Vitality",
    description: "商业活跃，人流密集",
    icon: "🛍️",
    color: "#FF9800",
    prompt: "商业活跃的街道，商业设施密集，人流穿梭",
    configPatch: {
      design_rule_profile: "balanced_complete_street_v1",
      objective_profile: "commerce",
      density: 0.9,
      ped_demand_level: "high",
      bike_demand_level: "medium",
      transit_demand_level: "high",
      vehicle_demand_level: "medium",
    },
  },
  {
    id: "transit_priority",
    name: "公交优先",
    nameEn: "Transit Priority",
    description: "公交导向，换乘便利",
    icon: "🚌",
    color: "#2196F3",
    prompt: "公交优先的街道，公交可达性高，换乘便利",
    configPatch: {
      design_rule_profile: "transit_priority_v1",
      objective_profile: "transit",
      density: 0.85,
      ped_demand_level: "high",
      bike_demand_level: "medium",
      transit_demand_level: "high",
      vehicle_demand_level: "high",
    },
  },
  {
    id: "park_landscape",
    name: "公园景观",
    nameEn: "Park Landscape",
    description: "绿化为主，休闲舒适",
    icon: "🌳",
    color: "#8BC34A",
    prompt: "公园景观街道，绿化丰富，自然生态，休闲舒适",
    configPatch: {
      design_rule_profile: "pedestrian_priority_v1",
      objective_profile: "greening",
      density: 0.25,
      ped_demand_level: "medium",
      bike_demand_level: "medium",
      transit_demand_level: "low",
      vehicle_demand_level: "low",
    },
  },
  {
    id: "quiet_residential",
    name: "安静居住",
    nameEn: "Quiet Residential",
    description: "住宅区安静，绿树成荫",
    icon: "🏠",
    color: "#9C27B0",
    prompt: "安静居住街道，绿树成荫，步行安全，适合全龄",
    configPatch: {
      design_rule_profile: "pedestrian_priority_v1",
      objective_profile: "greening",
      density: 0.35,
      ped_demand_level: "high",
      bike_demand_level: "medium",
      transit_demand_level: "low",
      vehicle_demand_level: "low",
    },
  },
  {
    id: "balanced_complete",
    name: "平衡街道",
    nameEn: "Balanced Complete",
    description: "各类使用者平衡",
    icon: "⚖️",
    color: "#607D8B",
    prompt: "各类使用者平衡的完整街道，行人、自行车、公交、机动车和谐共处",
    configPatch: {
      design_rule_profile: "balanced_complete_street_v1",
      objective_profile: "balanced",
      density: 0.6,
      ped_demand_level: "medium",
      bike_demand_level: "medium",
      transit_demand_level: "medium",
      vehicle_demand_level: "medium",
    },
  },
];
