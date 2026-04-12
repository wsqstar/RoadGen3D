// ── Type definitions for Simplified Workbench ──

// ── Core Types ────────────────────────────────────────────────────────────────

export type ChatMessage = {
  role: string;
  content: string;
};

export type KnowledgeSourceKey = "hybrid" | "pdf_rag" | "graph_rag";

export type DesignIntent = {
  user_goals: string[];
  style_preferences: string[];
  safety_priorities: string[];
  follow_up_questions: string[];
  rag_queries: string[];
};

export type RagEvidence = {
  chunk_id: string;
  doc_id: string;
  section_title: string;
  page_start: number;
  page_end: number;
  text: string;
  source_path: string;
  score: number;
  relevance_reason: string;
  knowledge_source?: string;
  parameter_hints?: Record<string, string>;
};

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

export type ChinaCity = {
  name_zh: string;
  name_en: string;
  province: string;
  bbox: [number, number, number, number];
};

export type ChinaCityResponse = {
  items: ChinaCity[];
};

export type ReferencePlan = {
  plan_id: string;
  label: string;
  description: string;
  image_path: string;
  image_url: string;
  block_sequence: string;
  seed: number;
  straight_length_m: number;
  intersection_span_m: number;
  branch_length_m: number;
  curve_radius_m: number;
  curve_angle_deg: number;
};

export type ReferencePlanResponse = {
  items: ReferencePlan[];
};

export type GraphTemplate = {
  template_id: string;
  label: string;
  description: string;
  annotation_path: string;
  image_path: string;
  image_url: string;
  source_format: string;
  centerline_count: number;
  junction_count: number;
};

export type GraphTemplateResponse = {
  items: GraphTemplate[];
};

export type DraftResponse = {
  stage: "clarification_required" | "draft_ready";
  intent: DesignIntent;
  evidence: RagEvidence[];
  draft: DesignDraft | null;
  warnings: string[];
  cache_hit?: boolean;
};

export type KnowledgeSourceStatus = {
  key: KnowledgeSourceKey;
  label: string;
  available: boolean;
  description: string;
  artifact_count?: number;
  item_count?: number;
  project_dir?: string;
  output_dir?: string;
  txt_dir?: string;
  input_dir?: string;
  cache_dir?: string;
  last_build_status?: string;
  runtime_error?: string;
  artifact_dir?: string;
  source_path?: string;
  error?: string;
};

export type KnowledgeSourceListResponse = {
  items: KnowledgeSourceStatus[];
};

export type KnowledgeSearchResponse = {
  knowledge_source: KnowledgeSourceKey;
  items: RagEvidence[];
};

export type GenerationResponse = {
  compose_config: Record<string, string | number>;
  summary: Record<string, unknown>;
  scene_layout_path: string;
  scene_glb_path: string;
  scene_ply_path: string;
  viewer_url: string;
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
  result: GenerationResponse | null;
};

export type SceneRecord = {
  job_id: string;
  status: string;
  created_at: string;
  finished_at: string;
  scene_layout_path: string;
  scene_glb_path: string;
  scene_ply_path: string;
  viewer_url: string;
  summary: Record<string, unknown>;
};

export type SceneJobListResponse = {
  items: SceneJobStatusResponse[];
};

export type SceneRecentResponse = {
  items: SceneRecord[];
};

export type FieldConfig = {
  key: string;
  label: string;
  type: "text" | "number" | "select";
  options?: string[];
};

// ── Constants ──

export const API_BASE = (import.meta.env.VITE_ROADGEN_API_BASE as string | undefined) || "http://127.0.0.1:8010";
export const VIEWER_BASE = (import.meta.env.VITE_ROADGEN_VIEWER_BASE as string | undefined) || "http://127.0.0.1:4173";
export const POLL_INTERVAL_MS = 1200;
export const TERMINAL_JOB_STATES = new Set(["succeeded", "failed"]);
export const DEFAULT_WORKBENCH_CITY = "guangzhou";
export const DEFAULT_REFERENCE_PLAN_ID = "hkust_gz_gate";
export const DEFAULT_GRAPH_TEMPLATE_ID = "hkust_gz_gate";
export const PEDESTRIAN_ALL_AGE_PRESET_PROMPT = "步行安全，全龄友好";
export const SUMMARY_OMIT_KEYS = new Set([
  "spatial_context",
  "poi_exclusion_zones",
  "poi_conflict_assets",
  "scene_graph_available_categories",
  "scene_graph_node_count",
  "scene_graph_edge_count",
  "scene_graph",
  "render_views",
  "theme_segments",
  "road_segment_graph_summary",
]);

export const FIELD_CONFIGS: FieldConfig[] = [
  { key: "query", label: "Scene Query", type: "text" },
  {
    key: "design_rule_profile",
    label: "Rule Profile",
    type: "select",
    options: ["balanced_complete_street_v1", "pedestrian_priority_v1", "transit_priority_v1"],
  },
  { key: "target_street_type", label: "Street Type", type: "text" },
  { key: "objective_profile", label: "Objective", type: "select", options: ["balanced", "greening", "commerce", "transit"] },
  { key: "city_context", label: "City Context", type: "text" },
  { key: "length_m", label: "Length (m)", type: "number" },
  { key: "road_width_m", label: "Road Width (m)", type: "number" },
  { key: "sidewalk_width_m", label: "Sidewalk Width (m)", type: "number" },
  { key: "lane_count", label: "Lane Count", type: "number" },
  { key: "density", label: "Density", type: "number" },
  { key: "ped_demand_level", label: "Ped Demand", type: "select", options: ["low", "medium", "high"] },
  { key: "bike_demand_level", label: "Bike Demand", type: "select", options: ["low", "medium", "high"] },
  { key: "transit_demand_level", label: "Transit Demand", type: "select", options: ["low", "medium", "high"] },
  { key: "vehicle_demand_level", label: "Vehicle Demand", type: "select", options: ["low", "medium", "high"] },
];

// ── Simplified Workbench Types ────────────────────────────────────────────────

/**
 * Evaluation scores for a single dimension (0-100)
 */
export interface EvaluationScores {
  walkability: number;
  safety: number;
  beauty: number;
  overall: number;
}

/**
 * Detailed walkability indicators from eval_quality.py
 */
export interface WalkabilityIndicators {
  SID_CLR: number;      // 人行道净宽
  CLEAR_CONT: number;   // 净空连续性
  FURN_D: number;       // 街道家具密度
  LIGHT_UNI: number;    // 照明均匀度
  TREE_SHADE: number;   // 绿化遮荫率
  BUFFER_RATIO: number; // 缓冲带比例
  TRANSIT_PROX: number; // 公交站可达性
  CROSS_PROV: number;   // 过街设施
  ENTR_DENS: number;    // 入口密度
  POI_MIX: number;      // POI 混合度
  MICRO_ENV: number;    // 微气候环境
}

/**
 * Complete evaluation result for a scheme
 */
export interface EvaluationResult {
  sceneId: string;
  scores: EvaluationScores;
  indicators: WalkabilityIndicators;
  pillarScores: {
    Protection: number;
    Comfort: number;
    Delight: number;
  };
}

/**
 * Data structure for radar chart visualization
 */
export interface RadarChartData {
  labels: string[];
  datasets: {
    label: string;
    data: number[];
    color: string;
  }[];
}

/**
 * Data structure for bar chart visualization
 */
export interface BarChartData {
  labels: string[];
  datasets: {
    label: string;
    data: number[];
    color: string;
  }[];
}

/**
 * Generation status for a scheme
 */
export type SchemeStatus = "idle" | "generating" | "ready" | "failed";

/**
 * A single generated scheme in the comparison grid
 */
export interface GeneratedScheme {
  id: string;           // "A", "B", "C"
  name: string;         // "方案 A"
  presetId: string;     // 对应的预设ID
  layoutPath: string;    // 场景布局路径
  previewUrl: string;   // 预览图 URL
  viewerUrl: string;    // 3D Viewer URL
  evaluation: EvaluationScores;
  indicators: WalkabilityIndicators | null;  // 详细指标（来自 LLM）
  evaluationText: string;  // LLM 文字评价
  suggestions: string[];    // 改进建议
  status: SchemeStatus;
  progress: number;     // 0-100
}

/**
 * Simplified scene preset for template selection
 */
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

/**
 * Step in the 3-step workflow
 */
export type WorkflowStep = 1 | 2 | 3;

/**
 * Simplified scene presets for template selection
 */
export const SCENE_PRESETS: ScenePreset[] = [
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
      vehicle_demand_level: "low"
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
      vehicle_demand_level: "medium"
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
      vehicle_demand_level: "high"
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
      density: 0.2,
      ped_demand_level: "medium",
      bike_demand_level: "medium",
      transit_demand_level: "low",
      vehicle_demand_level: "low"
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
      density: 0.3,
      ped_demand_level: "low",
      bike_demand_level: "medium",
      transit_demand_level: "low",
      vehicle_demand_level: "low"
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
      vehicle_demand_level: "medium"
    },
  },
];
