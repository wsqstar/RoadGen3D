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
