// ── Type definitions ──

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

export type ScenePreset = {
  id: string;
  name: string;
  description: string;
  prompt: string;
  configPatch: Record<string, string | number>;
};

export const SCENE_PRESETS: ScenePreset[] = [
  {
    id: "urban_downtown",
    name: "Urban Downtown",
    description: "Dense urban core with mixed-use streetscape, heavy pedestrian flow",
    prompt: "高密度城市核心区，混合功能街道，行人流量大，公交可达性高，完整街道设计，行人优先、商业活跃、街道家具齐全",
    configPatch: { design_rule_profile: "balanced_complete_street_v1", objective_profile: "commerce", density: 0.9, ped_demand_level: "high", bike_demand_level: "high", transit_demand_level: "high", vehicle_demand_level: "medium" },
  },
  {
    id: "residential_quiet",
    name: "Quiet Residential",
    description: "Low-density residential street with trees and minimal furniture",
    prompt: "低密度住宅区安静街道，行人和自行车友好的居住区道路，以绿化为主，街道家具简约，步行安全和全龄友好",
    configPatch: { design_rule_profile: "pedestrian_priority_v1", objective_profile: "greening", density: 0.3, ped_demand_level: "low", bike_demand_level: "medium", transit_demand_level: "low", vehicle_demand_level: "low" },
  },
  {
    id: "waterfront_promenade",
    name: "Waterfront Promenade",
    description: "Scenic waterfront walkway with benches, lamps, and landscape",
    prompt: "滨水步道，景观休闲为主，配备座椅、路灯和绿化景观，行人优先，宽阔人行道和观景空间，自行车道分离",
    configPatch: { design_rule_profile: "pedestrian_priority_v1", objective_profile: "greening", density: 0.5, ped_demand_level: "medium", bike_demand_level: "medium", transit_demand_level: "low", vehicle_demand_level: "low" },
  },
  {
    id: "commercial_strip",
    name: "Commercial Strip",
    description: "Busy commercial street with bus stops, signage, and heavy furniture",
    prompt: "繁忙商业街，公交站点、标志牌和街道家具齐全，商业活跃度高，平衡机动车通行和行人购物体验，宽阔人行道",
    configPatch: { design_rule_profile: "balanced_complete_street_v1", objective_profile: "commerce", density: 0.8, ped_demand_level: "high", bike_demand_level: "medium", transit_demand_level: "high", vehicle_demand_level: "medium" },
  },
  {
    id: "park_pathway",
    name: "Park Pathway",
    description: "Green park pathway with scattered trees and landscape elements",
    prompt: "公园绿道，自然景观为主，散布树木和绿化元素，步行和自行车友好，无机动车，强调生态和休闲功能",
    configPatch: { design_rule_profile: "pedestrian_priority_v1", objective_profile: "greening", density: 0.2, ped_demand_level: "medium", bike_demand_level: "medium", transit_demand_level: "low", vehicle_demand_level: "low" },
  },
  {
    id: "transit_corridor",
    name: "Transit Corridor",
    description: "Transit-oriented corridor with bus stops, shelters, and wide sidewalks",
    prompt: "公交导向走廊，配备公交站、候车亭和宽阔人行道，公交专用道并行，高密度开发，公交可达性和换乘便利",
    configPatch: { design_rule_profile: "transit_priority_v1", objective_profile: "transit", density: 0.85, ped_demand_level: "high", bike_demand_level: "medium", transit_demand_level: "high", vehicle_demand_level: "high" },
  },
];
