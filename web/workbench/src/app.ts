type ChatMessage = {
  role: string;
  content: string;
};

type KnowledgeSourceKey = "hybrid" | "pdf_rag" | "graph_rag";

type DesignIntent = {
  user_goals: string[];
  style_preferences: string[];
  safety_priorities: string[];
  follow_up_questions: string[];
  rag_queries: string[];
};

type RagEvidence = {
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

type DesignDraft = {
  normalized_scene_query: string;
  compose_config_patch: Record<string, string | number>;
  citations_by_field: Record<string, string[]>;
  design_summary: string;
  risk_notes: string[];
  parameter_sources_by_field: Record<string, string>;
};

type SceneContext = {
  layout_mode: "template" | "osm" | "metaurban" | "graph_template";
  aoi_bbox: [number, number, number, number] | null;
  city_name_en: string | null;
  reference_plan_id: string | null;
  graph_template_id: string | null;
};

type ChinaCity = {
  name_zh: string;
  name_en: string;
  province: string;
  bbox: [number, number, number, number];
};

type ChinaCityResponse = {
  items: ChinaCity[];
};

type ReferencePlan = {
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

type ReferencePlanResponse = {
  items: ReferencePlan[];
};

type GraphTemplate = {
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

type GraphTemplateResponse = {
  items: GraphTemplate[];
};

type DraftResponse = {
  stage: "clarification_required" | "draft_ready";
  intent: DesignIntent;
  evidence: RagEvidence[];
  draft: DesignDraft | null;
  warnings: string[];
  cache_hit?: boolean;
};

type KnowledgeSourceStatus = {
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

type KnowledgeSourceListResponse = {
  items: KnowledgeSourceStatus[];
};

type KnowledgeSearchResponse = {
  knowledge_source: KnowledgeSourceKey;
  items: RagEvidence[];
};

type GenerationResponse = {
  compose_config: Record<string, string | number>;
  summary: Record<string, unknown>;
  scene_layout_path: string;
  scene_glb_path: string;
  scene_ply_path: string;
  viewer_url: string;
};

type SceneJobCreateResponse = {
  job_id: string;
  status: string;
  created_at: string;
};

type SceneJobStatusResponse = {
  job_id: string;
  status: string;
  created_at: string;
  started_at: string;
  finished_at: string;
  error: string;
  result: GenerationResponse | null;
};

type SceneRecord = {
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

type SceneJobListResponse = {
  items: SceneJobStatusResponse[];
};

type SceneRecentResponse = {
  items: SceneRecord[];
};

type FieldConfig = {
  key: string;
  label: string;
  type: "text" | "number" | "select";
  options?: string[];
};

const API_BASE = (import.meta.env.VITE_ROADGEN_API_BASE as string | undefined) || "http://127.0.0.1:8010";
const VIEWER_BASE = (import.meta.env.VITE_ROADGEN_VIEWER_BASE as string | undefined) || "http://127.0.0.1:4173";
const POLL_INTERVAL_MS = 1200;
const TERMINAL_JOB_STATES = new Set(["succeeded", "failed"]);
const DEFAULT_WORKBENCH_CITY = "guangzhou";
const DEFAULT_REFERENCE_PLAN_ID = "hkust_gz_gate";
const DEFAULT_GRAPH_TEMPLATE_ID = "hkust_gz_gate";
const PEDESTRIAN_ALL_AGE_PRESET_PROMPT = "步行安全，全龄友好";
const SUMMARY_OMIT_KEYS = new Set([
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

const FIELD_CONFIGS: FieldConfig[] = [
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

export function mountWorkbench(app: HTMLDivElement): void {
  const state = {
    messages: [] as ChatMessage[],
    lastDraft: null as DraftResponse | null,
    lastGeneration: null as GenerationResponse | null,
    currentJob: null as SceneJobStatusResponse | null,
    recentScenes: [] as SceneRecord[],
    cities: [] as ChinaCity[],
    referencePlans: [] as ReferencePlan[],
    graphTemplates: [] as GraphTemplate[],
    knowledgeSources: [] as KnowledgeSourceStatus[],
    selectedKnowledgeSource: "graph_rag" as KnowledgeSourceKey,
    manualKnowledgeResults: [] as RagEvidence[],
    knowledgeSourceLoadError: null as string | null,
    cityLoadError: null as string | null,
    graphTemplateLoadError: null as string | null,
    referencePlanLoadError: null as string | null,
    sceneContext: {
      layout_mode: "graph_template",
      aoi_bbox: null,
      city_name_en: DEFAULT_WORKBENCH_CITY,
      reference_plan_id: DEFAULT_REFERENCE_PLAN_ID,
      graph_template_id: DEFAULT_GRAPH_TEMPLATE_ID,
    } as SceneContext,
    bboxDirty: false,
  };

  app.innerHTML = `
    <div class="shell">
      <section class="hero">
        <h1>RoadGen3D Street Workbench</h1>
        <p>
          生成工作台和 3D viewer 分开运行。这里负责对话、RAG、参数确认和任务触发；空间浏览与漫游交给独立 viewer。
        </p>
        <div class="hero-grid">
          <div class="hero-chip">1. Intent Clarification</div>
          <div class="hero-chip">2. GraphRAG Evidence</div>
          <div class="hero-chip">3. Parameter Confirmation</div>
          <div class="hero-chip">4. Scene Job</div>
        </div>
        <div class="hero-actions">
          <a class="hero-link" href="${escapeHtml(VIEWER_BASE)}" target="_blank" rel="noreferrer">Open Standalone Viewer</a>
        </div>
      </section>

	      <div class="layout">
	        <div class="stack">
	          <section class="panel">
            <div class="panel-head">
              <h2>Conversation</h2>
              <div class="intent-row">
                <span class="tag">API: ${escapeHtml(API_BASE)}</span>
                <span class="tag">Viewer: ${escapeHtml(VIEWER_BASE)}</span>
              </div>
            </div>
            <div class="panel-body">
              <div id="timeline" class="timeline"></div>
              <div class="composer">
                <textarea id="prompt-input" placeholder="例如：我想做一条步行安全、全龄友好的完整街道，公交可达性要好，机动车不要太强势。"></textarea>
                <div class="scene-setup-grid">
                  <div class="field">
                    <label for="knowledge-source">Knowledge Mode</label>
                    <select id="knowledge-source">
                      <option value="graph_rag" selected>graph_rag</option>
                      <option value="hybrid">hybrid</option>
                      <option value="pdf_rag">pdf_rag</option>
                    </select>
                  </div>
                </div>
                <div class="actions">
                  <button id="draft-btn" class="btn primary">生成设计建议</button>
                  <button id="preset-pedestrian-btn" class="btn secondary" type="button">步行安全，全龄友好</button>
                  <button id="rebuild-btn" class="btn secondary">重建 PDF 知识库</button>
                </div>
                <div id="status-box" class="status-box">等待输入。</div>
              </div>
	            </div>
	          </section>

	          <section class="panel">
	            <div class="panel-head">
	              <h2>Scene Setup</h2>
	            </div>
	            <div class="panel-body">
	              <div class="scene-setup-grid">
	                <div class="field">
	                  <label for="scene-layout-mode">Layout Mode</label>
	                  <select id="scene-layout-mode">
	                    <option value="graph_template" selected>graph_template</option>
	                    <option value="osm">osm</option>
	                    <option value="metaurban">metaurban</option>
	                    <option value="template">template</option>
	                  </select>
	                </div>
	                <div id="scene-city-field" class="field">
	                  <label for="scene-city">City</label>
	                  <select id="scene-city">
	                    <option value="">Loading cities...</option>
	                  </select>
	                </div>
	                <div id="scene-graph-template-field" class="field" style="display:none;">
	                  <label for="scene-graph-template">Graph Template</label>
	                  <select id="scene-graph-template">
	                    <option value="">Loading graph templates...</option>
	                  </select>
	                </div>
	                <div id="scene-reference-field" class="field" style="display:none;">
	                  <label for="scene-reference-plan">Reference Plan</label>
	                  <select id="scene-reference-plan">
	                    <option value="">Loading reference plans...</option>
	                  </select>
	                </div>
	              </div>
	              <div id="osm-scene-fields" class="scene-setup-stack">
	                <div class="scene-setup-grid bbox-grid">
	                  <div class="field">
	                    <label for="bbox-min-lon">AOI Min Lon</label>
	                    <input id="bbox-min-lon" type="number" step="0.0001" />
	                  </div>
	                  <div class="field">
	                    <label for="bbox-min-lat">AOI Min Lat</label>
	                    <input id="bbox-min-lat" type="number" step="0.0001" />
	                  </div>
	                  <div class="field">
	                    <label for="bbox-max-lon">AOI Max Lon</label>
	                    <input id="bbox-max-lon" type="number" step="0.0001" />
	                  </div>
	                  <div class="field">
	                    <label for="bbox-max-lat">AOI Max Lat</label>
	                    <input id="bbox-max-lat" type="number" step="0.0001" />
	                  </div>
	                </div>
	              </div>
	              <div id="scene-template-preview" class="reference-plan-preview" hidden></div>
	              <div id="scene-setup-summary" class="summary-box">Graph Template 模式会默认加载港科广门口街道 graph，并直接走 3D 场景导出链。</div>
	            </div>
	          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>Knowledge Search</h2>
            </div>
            <div class="panel-body">
              <div id="knowledge-source-summary" class="summary-box">正在检查知识源状态...</div>
              <div class="composer knowledge-composer">
                <textarea id="knowledge-query" placeholder="例如：minimum sidewalk width near transit stops，或者 输入中文问题来手动核对 GraphRAG / PDF 证据。"></textarea>
                <div class="actions">
                  <button id="knowledge-search-btn" class="btn secondary" type="button">查询知识</button>
                </div>
              </div>
              <div id="knowledge-search-results" class="evidence-list"></div>
            </div>
          </section>

	          <section class="panel">
	            <div class="panel-head">
              <h2>Evidence</h2>
            </div>
            <div class="panel-body">
              <div id="intent-box" class="summary-box">尚未生成设计意图。</div>
              <div id="evidence-list" class="evidence-list"></div>
            </div>
          </section>
        </div>

        <div class="stack">
          <section class="panel">
            <div class="panel-head">
              <h2>Design Draft</h2>
            </div>
            <div class="panel-body">
              <div id="draft-summary" class="summary-box">等待设计草案。</div>
              <div id="parameter-form" class="form-grid"></div>
              <div class="actions">
                <button id="generate-btn" class="btn primary" disabled>确认参数并创建生成任务</button>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>Scene Jobs</h2>
            </div>
            <div class="panel-body">
              <div id="generation-result" class="result-box">尚未触发生成任务。</div>
            </div>
          </section>
        </div>
      </div>
    </div>
  `;

  const timelineEl = requireElement<HTMLDivElement>(app, "#timeline");
  const promptInput = requireElement<HTMLTextAreaElement>(app, "#prompt-input");
  const draftBtn = requireElement<HTMLButtonElement>(app, "#draft-btn");
  const presetPedestrianBtn = requireElement<HTMLButtonElement>(app, "#preset-pedestrian-btn");
  const rebuildBtn = requireElement<HTMLButtonElement>(app, "#rebuild-btn");
  const statusBox = requireElement<HTMLDivElement>(app, "#status-box");
  const knowledgeSource = requireElement<HTMLSelectElement>(app, "#knowledge-source");
  const knowledgeSourceSummary = requireElement<HTMLDivElement>(app, "#knowledge-source-summary");
  const knowledgeQuery = requireElement<HTMLTextAreaElement>(app, "#knowledge-query");
  const knowledgeSearchBtn = requireElement<HTMLButtonElement>(app, "#knowledge-search-btn");
  const knowledgeSearchResults = requireElement<HTMLDivElement>(app, "#knowledge-search-results");
  const sceneLayoutMode = requireElement<HTMLSelectElement>(app, "#scene-layout-mode");
  const sceneCity = requireElement<HTMLSelectElement>(app, "#scene-city");
  const sceneCityField = requireElement<HTMLDivElement>(app, "#scene-city-field");
  const sceneGraphTemplateField = requireElement<HTMLDivElement>(app, "#scene-graph-template-field");
  const sceneGraphTemplate = requireElement<HTMLSelectElement>(app, "#scene-graph-template");
  const sceneReferenceField = requireElement<HTMLDivElement>(app, "#scene-reference-field");
  const sceneReferencePlan = requireElement<HTMLSelectElement>(app, "#scene-reference-plan");
  const osmSceneFields = requireElement<HTMLDivElement>(app, "#osm-scene-fields");
  const sceneTemplatePreview = requireElement<HTMLDivElement>(app, "#scene-template-preview");
  const bboxMinLon = requireElement<HTMLInputElement>(app, "#bbox-min-lon");
  const bboxMinLat = requireElement<HTMLInputElement>(app, "#bbox-min-lat");
  const bboxMaxLon = requireElement<HTMLInputElement>(app, "#bbox-max-lon");
  const bboxMaxLat = requireElement<HTMLInputElement>(app, "#bbox-max-lat");
  const sceneSetupSummary = requireElement<HTMLDivElement>(app, "#scene-setup-summary");
  const intentBox = requireElement<HTMLDivElement>(app, "#intent-box");
  const evidenceList = requireElement<HTMLDivElement>(app, "#evidence-list");
  const draftSummary = requireElement<HTMLDivElement>(app, "#draft-summary");
  const parameterForm = requireElement<HTMLDivElement>(app, "#parameter-form");
  const generateBtn = requireElement<HTMLButtonElement>(app, "#generate-btn");
  const generationResult = requireElement<HTMLDivElement>(app, "#generation-result");

  renderTimeline();
  renderParameterForm({});
  renderSceneSetup();
  renderKnowledgeSourcePanel();
  renderJobPanel();
  void bootstrap();

  knowledgeSource.addEventListener("change", () => {
    state.selectedKnowledgeSource = normalizeKnowledgeSourceKey(knowledgeSource.value);
    renderKnowledgeSourcePanel();
  });

  sceneLayoutMode.addEventListener("change", () => {
    renderSceneSetup();
  });

  sceneReferencePlan.addEventListener("change", () => {
    renderSceneSetup();
  });

  sceneGraphTemplate.addEventListener("change", () => {
    renderSceneSetup();
  });

  sceneCity.addEventListener("change", () => {
    const city = state.cities.find((item) => item.name_en === sceneCity.value);
    if (city && !state.bboxDirty) {
      setBboxInputs(city.bbox);
    }
    renderSceneSetup();
  });

  [bboxMinLon, bboxMinLat, bboxMaxLon, bboxMaxLat].forEach((input) => {
    input.addEventListener("input", () => {
      state.bboxDirty = true;
      renderSceneSetup();
    });
  });

  draftBtn.addEventListener("click", async () => {
    const prompt = promptInput.value.trim();
    if (!prompt) {
      setStatus("请输入街道目标。");
      return;
    }
    try {
      await requestDesignDraft({
        prompt,
        currentPatch: state.lastDraft?.draft?.compose_config_patch || {},
        knowledgeSource: state.selectedKnowledgeSource,
        autoGenerate: false,
      });
    } catch (error) {
      setStatus(asErrorMessage(error));
    }
  });

  presetPedestrianBtn.addEventListener("click", async () => {
    state.selectedKnowledgeSource = "graph_rag";
    knowledgeSource.value = "graph_rag";
    renderKnowledgeSourcePanel();
    promptInput.value = PEDESTRIAN_ALL_AGE_PRESET_PROMPT;
    try {
      await requestDesignDraft({
        prompt: PEDESTRIAN_ALL_AGE_PRESET_PROMPT,
        currentPatch: {},
        knowledgeSource: "graph_rag",
        autoGenerate: true,
      });
    } catch (error) {
      setStatus(asErrorMessage(error));
    }
  });

  rebuildBtn.addEventListener("click", async () => {
    rebuildBtn.disabled = true;
    setStatus("正在重建设计文档知识库...");
    try {
      const payload = await postJson<Record<string, unknown>>("/api/knowledge/rebuild", {});
      setStatus(`知识库已重建：${String(payload.output_dir || "")}`);
    } catch (error) {
      setStatus(asErrorMessage(error));
    } finally {
      rebuildBtn.disabled = false;
    }
  });

  knowledgeSearchBtn.addEventListener("click", async () => {
    const query = knowledgeQuery.value.trim();
    if (!query) {
      setStatus("请输入要查询的知识问题。");
      return;
    }
    knowledgeSearchBtn.disabled = true;
    setStatus(`正在查询 ${formatKnowledgeSourceLabel(state.selectedKnowledgeSource)}...`);
    try {
      const payload = await postJson<KnowledgeSearchResponse>("/api/knowledge/search", {
        query,
        topk: 6,
        knowledge_source: state.selectedKnowledgeSource,
      });
      state.manualKnowledgeResults = payload.items;
      renderKnowledgeSourcePanel();
      setStatus(
        payload.items.length
          ? `已返回 ${payload.items.length} 条知识证据。`
          : `没有在 ${formatKnowledgeSourceLabel(state.selectedKnowledgeSource)} 中检索到匹配证据。`,
      );
    } catch (error) {
      setStatus(asErrorMessage(error));
    } finally {
      knowledgeSearchBtn.disabled = false;
    }
  });

  generateBtn.addEventListener("click", async () => {
    if (!state.lastDraft || !state.lastDraft.draft) {
      setStatus("请先生成设计草案。");
      return;
    }
    try {
      const draft = buildDraftFromForm(state.lastDraft.draft, parameterForm);
      await createSceneJobFromDraft(draft, {
        queuedStatusMessage: "任务已入队，正在轮询生成状态...",
      });
    } catch (error) {
      setStatus(asErrorMessage(error));
    }
  });

  async function requestDesignDraft(options: {
    prompt: string;
    currentPatch: Record<string, string | number>;
    knowledgeSource: KnowledgeSourceKey;
    autoGenerate: boolean;
  }): Promise<void> {
    const {
      prompt,
      currentPatch,
      knowledgeSource: requestedKnowledgeSource,
      autoGenerate,
    } = options;
    draftBtn.disabled = true;
    presetPedestrianBtn.disabled = true;
    setStatus(
      autoGenerate
        ? "正在尝试命中“步行安全，全龄友好”的缓存草案；若未命中，仍需先完成设计分析，然后再创建 2D / 3D 生成任务..."
        : "正在尝试加载缓存；若没有命中，再执行新的 LLM / GraphRAG 分析...",
    );
    try {
      const payload = await postJson<DraftResponse>("/api/design/draft", {
        messages: state.messages,
        user_input: prompt,
        current_patch: currentPatch,
        topk: 6,
        knowledge_source: requestedKnowledgeSource,
      });
      const cacheMessage = payload.cache_hit
        ? "已命中缓存，跳过新的 LLM / GraphRAG 分析。"
        : "未命中缓存，已完成新的 LLM / GraphRAG 分析。";
      state.messages.push({ role: "user", content: prompt });
      if (payload.stage === "clarification_required") {
        const followUpMessage = buildClarificationAssistantMessage(payload.intent.follow_up_questions);
        state.messages.push({ role: "assistant", content: followUpMessage });
        state.lastDraft = null;
      } else {
        state.messages.push({ role: "assistant", content: payload.draft?.design_summary || "设计草案已生成。" });
        state.lastDraft = payload;
      }
      state.lastGeneration = null;
      state.currentJob = null;
      promptInput.value = autoGenerate ? prompt : "";
      renderTimeline();
      renderDraftResponse(payload);
      renderJobPanel();
      if (payload.stage === "clarification_required" || !payload.draft) {
        setStatus(
          payload.warnings.length
            ? `${cacheMessage}\n${payload.warnings.join("\n")}`
            : `${cacheMessage}\n请先回答澄清问题，然后我再继续生成设计草案。`,
        );
        return;
      }
      if (autoGenerate) {
        setStatus(`${cacheMessage}\n正在直接创建 2D / 3D 生成任务...`);
        await createSceneJobFromDraft(payload.draft, {
          queuedStatusMessage: `${cacheMessage}\n任务已入队，正在轮询 2D / 3D 生成状态...`,
        });
        return;
      }
      setStatus(
        payload.warnings.length
          ? `${cacheMessage}\n${payload.warnings.join("\n")}`
          : `${cacheMessage}\n设计草案已生成，请确认参数后创建生成任务。`,
      );
    } finally {
      generateBtn.disabled = false;
      draftBtn.disabled = false;
      presetPedestrianBtn.disabled = false;
    }
  }

  async function createSceneJobFromDraft(
    draft: DesignDraft,
    options: {
      queuedStatusMessage: string;
    },
  ): Promise<void> {
    generateBtn.disabled = true;
    presetPedestrianBtn.disabled = true;
    draftBtn.disabled = true;
    setStatus("正在创建场景生成任务...");
    try {
      const sceneContext = buildSceneContextFromForm();
      if (sceneContext.layout_mode === "osm" && !sceneContext.aoi_bbox) {
        throw new Error("OSM 模式需要有效的 AOI bbox。");
      }
      const created = await postJson<SceneJobCreateResponse>("/api/scene/jobs", {
        draft,
        scene_context: sceneContext,
        patch_overrides: {},
        generation_options: {},
      });
      state.currentJob = {
        job_id: created.job_id,
        status: created.status,
        created_at: created.created_at,
        started_at: "",
        finished_at: "",
        error: "",
        result: null,
      };
      renderJobPanel();
      setStatus(options.queuedStatusMessage);
      await pollSceneJob(created.job_id);
    } finally {
      generateBtn.disabled = false;
      draftBtn.disabled = false;
      presetPedestrianBtn.disabled = false;
    }
  }

  async function bootstrap(): Promise<void> {
    const startupErrors: string[] = [];
    const collectBootstrapError = (label: string, error: unknown): void => {
      startupErrors.push(`${label}: ${formatBootstrapError(error)}`);
    };
    try {
      try {
        await loadKnowledgeSources();
      } catch (error) {
        collectBootstrapError("知识源", error);
      }
      try {
        await loadChinaCities();
      } catch (error) {
        collectBootstrapError("城市列表", error);
      }
      try {
        await loadGraphTemplates();
      } catch (error) {
        collectBootstrapError("Graph Template", error);
      }
      try {
        await loadReferencePlans();
      } catch (error) {
        collectBootstrapError("MetaUrban Reference Plan", error);
      }
      try {
        await refreshRecentScenes();
      } catch (error) {
        collectBootstrapError("最近场景", error);
      }
      const jobs = await getJson<SceneJobListResponse>("/api/scene/jobs");
      if (jobs.items.length) {
        state.currentJob = jobs.items[0];
        if (state.currentJob.result) {
          state.lastGeneration = state.currentJob.result;
        }
        renderJobPanel();
        if (!TERMINAL_JOB_STATES.has(state.currentJob.status)) {
          generateBtn.disabled = true;
          setStatus("检测到未完成任务，继续同步状态...");
          await pollSceneJob(state.currentJob.job_id);
          return;
        }
      }
    } catch (error) {
      collectBootstrapError("场景任务列表", error);
    }
    if (startupErrors.length) {
      setStatus(
        [
          "Workbench 已加载，但后端 API 目前不可用或尚未完全启动。",
          ...startupErrors,
          `请检查 ${API_BASE}，或在仓库根目录运行 make workbench-api。`,
        ].join("\n"),
      );
    }
  }

  async function loadKnowledgeSources(): Promise<void> {
    try {
      const payload = await getJson<KnowledgeSourceListResponse>("/api/knowledge/sources");
      state.knowledgeSources = payload.items;
      state.knowledgeSourceLoadError = null;
      const optionsHtml = payload.items
        .map((item) => `<option value="${escapeHtml(item.key)}" ${item.available ? "" : "disabled"}>${escapeHtml(item.label)}</option>`)
        .join("");
      if (optionsHtml) {
        knowledgeSource.innerHTML = optionsHtml;
      }
      const preferredSource =
        payload.items.find((item) => item.key === "graph_rag" && item.available)
        || payload.items.find((item) => item.key === "hybrid" && item.available)
        || payload.items.find((item) => item.available)
        || payload.items.find((item) => item.key === state.selectedKnowledgeSource)
        || payload.items[0];
      if (preferredSource) {
        state.selectedKnowledgeSource = preferredSource.key;
        knowledgeSource.value = preferredSource.key;
      }
      renderKnowledgeSourcePanel();
    } catch (error) {
      state.knowledgeSources = [];
      state.knowledgeSourceLoadError = formatBootstrapError(error);
      knowledgeSource.innerHTML = `<option value="graph_rag">graph_rag</option>`;
      renderKnowledgeSourcePanel();
      throw error;
    }
  }

  async function loadChinaCities(): Promise<void> {
    try {
      const payload = await getJson<ChinaCityResponse>("/api/geo/china-cities");
      state.cities = payload.items;
      state.cityLoadError = null;
      sceneCity.innerHTML = state.cities
        .map((item) => `<option value="${escapeHtml(item.name_en)}">${escapeHtml(`${item.name_zh} ${item.name_en}`)}</option>`)
        .join("");
      const preferredCity = state.cities.find((item) => item.name_en === state.sceneContext.city_name_en)
        || state.cities.find((item) => item.name_en === DEFAULT_WORKBENCH_CITY)
        || state.cities[0];
      if (preferredCity) {
        sceneCity.value = preferredCity.name_en;
        if (!state.bboxDirty) {
          setBboxInputs(preferredCity.bbox);
        }
        state.sceneContext = {
          ...state.sceneContext,
          city_name_en: preferredCity.name_en,
          aoi_bbox: state.sceneContext.layout_mode === "osm" ? preferredCity.bbox : state.sceneContext.aoi_bbox,
        };
        state.bboxDirty = false;
      }
      renderSceneSetup();
    } catch (error) {
      state.cities = [];
      state.cityLoadError = formatBootstrapError(error);
      sceneCity.innerHTML = `<option value="">Cities unavailable</option>`;
      renderSceneSetup();
      throw error;
    }
  }

  async function loadGraphTemplates(): Promise<void> {
    try {
      const payload = await getJson<GraphTemplateResponse>("/api/graph-templates");
      state.graphTemplates = payload.items;
      state.graphTemplateLoadError = null;
      sceneGraphTemplate.innerHTML = state.graphTemplates.length
        ? state.graphTemplates
            .map((item) => `<option value="${escapeHtml(item.template_id)}">${escapeHtml(item.label)}</option>`)
            .join("")
        : `<option value="">No graph templates</option>`;
      const preferredTemplate = state.graphTemplates.find((item) => item.template_id === state.sceneContext.graph_template_id)
        || state.graphTemplates.find((item) => item.template_id === DEFAULT_GRAPH_TEMPLATE_ID)
        || state.graphTemplates[0]
        || null;
      if (preferredTemplate) {
        sceneGraphTemplate.value = preferredTemplate.template_id;
        state.sceneContext.graph_template_id = preferredTemplate.template_id;
      } else {
        state.sceneContext.graph_template_id = null;
      }
      renderSceneSetup();
    } catch (error) {
      state.graphTemplates = [];
      state.graphTemplateLoadError = formatBootstrapError(error);
      state.sceneContext.graph_template_id = null;
      sceneGraphTemplate.innerHTML = `<option value="">Graph templates unavailable</option>`;
      renderSceneSetup();
      throw error;
    }
  }

  async function loadReferencePlans(): Promise<void> {
    try {
      const payload = await getJson<ReferencePlanResponse>("/api/reference-plans");
      state.referencePlans = payload.items;
      state.referencePlanLoadError = null;
      sceneReferencePlan.innerHTML = state.referencePlans.length
        ? state.referencePlans
            .map((item) => `<option value="${escapeHtml(item.plan_id)}">${escapeHtml(item.label)}</option>`)
            .join("")
        : `<option value="">No reference plans</option>`;
      const preferredPlan = state.referencePlans.find((item) => item.plan_id === state.sceneContext.reference_plan_id)
        || state.referencePlans.find((item) => item.plan_id === DEFAULT_REFERENCE_PLAN_ID)
        || state.referencePlans[0]
        || null;
      if (preferredPlan) {
        sceneReferencePlan.value = preferredPlan.plan_id;
        state.sceneContext.reference_plan_id = preferredPlan.plan_id;
      } else {
        state.sceneContext.reference_plan_id = null;
      }
      renderSceneSetup();
    } catch (error) {
      state.referencePlans = [];
      state.referencePlanLoadError = formatBootstrapError(error);
      state.sceneContext.reference_plan_id = null;
      sceneReferencePlan.innerHTML = `<option value="">Reference plans unavailable</option>`;
      renderSceneSetup();
      throw error;
    }
  }

  async function pollSceneJob(jobId: string): Promise<void> {
    while (true) {
      const payload = await getJson<SceneJobStatusResponse>(`/api/scene/jobs/${jobId}`);
      state.currentJob = payload;
      if (payload.result) {
        state.lastGeneration = payload.result;
      }
      renderJobPanel();
      if (TERMINAL_JOB_STATES.has(payload.status)) {
        generateBtn.disabled = false;
        if (payload.status === "succeeded" && payload.result) {
          await refreshRecentScenes();
          setStatus("生成完成，可以在独立 viewer 中查看结果。");
        } else {
          setStatus(payload.error || "生成任务失败。");
        }
        return;
      }
      await sleep(POLL_INTERVAL_MS);
    }
  }

  async function refreshRecentScenes(): Promise<void> {
    const payload = await getJson<SceneRecentResponse>("/api/scenes/recent");
    state.recentScenes = payload.items;
    renderJobPanel();
  }

  function renderKnowledgeSourcePanel(): void {
    const rows = state.knowledgeSources.length
      ? state.knowledgeSources
          .map((item) => {
            const countLabel = item.available
              ? `${String(item.item_count || 0)} items`
              : item.error || "unavailable";
            return `<span class="tag">${escapeHtml(`${item.label}: ${countLabel}`)}</span>`;
          })
          .join("")
      : `<span class="tag">Hybrid: API not loaded</span>`;
    const selected = state.knowledgeSources.find((item) => item.key === state.selectedKnowledgeSource);
    const selectedDescription = selected
      ? [
          `${selected.label}: ${selected.description}`,
          selected.available ? `items: ${String(selected.item_count || 0)}` : `status: ${selected.error || "unavailable"}`,
          selected.last_build_status ? `runtime build: ${selected.last_build_status}` : "",
          selected.runtime_error ? `runtime error: ${selected.runtime_error}` : "",
        ].join("\n")
      : `${formatKnowledgeSourceLabel(state.selectedKnowledgeSource)}: API not loaded yet.`;
    knowledgeSourceSummary.innerHTML = `
      <div class="tag-row">${rows}</div>
      <div class="field-note">当前草案生成与手动查询都会使用：${escapeHtml(formatKnowledgeSourceLabel(state.selectedKnowledgeSource))}</div>
      <div class="field-note">${escapeHtml(selectedDescription)}</div>
    `;
    knowledgeSearchResults.innerHTML = state.manualKnowledgeResults.length
      ? buildEvidenceCards(state.manualKnowledgeResults)
      : `<div class="field-note">这里会显示手动查询到的 PDF / GraphRAG 证据。</div>`;
  }

  function renderDraftResponse(payload: DraftResponse): void {
    if (payload.stage === "clarification_required" || !payload.draft) {
      renderClarification(payload);
      return;
    }
    generateBtn.disabled = false;
    renderIntent(payload.intent);
    renderEvidence(payload.evidence, payload.draft.citations_by_field);
    renderParameterForm(
      payload.draft.compose_config_patch,
      payload.draft.citations_by_field,
      payload.draft.parameter_sources_by_field,
    );
    draftSummary.textContent = formatDraftSummary(payload.draft);
  }

  function renderClarification(payload: DraftResponse): void {
    generateBtn.disabled = true;
    renderIntent(payload.intent);
    evidenceList.innerHTML = `<div class="field-note">澄清轮次暂不执行 RAG 检索，请先回答问题。</div>`;
    renderParameterForm({});
    draftSummary.textContent = [
      "需要先确认以下信息后，才能继续生成设计草案：",
      ...payload.intent.follow_up_questions.map((item, index) => `${index + 1}. ${item}`),
    ].join("\n");
  }

  function renderIntent(intent: DesignIntent): void {
    intentBox.innerHTML = `
      <div><strong>Goals</strong></div>
      ${renderTagRow(intent.user_goals)}
      <div><strong>Style</strong></div>
      ${renderTagRow(intent.style_preferences)}
      <div><strong>Safety Priorities</strong></div>
      ${renderTagRow(intent.safety_priorities)}
      <div><strong>RAG Queries</strong></div>
      ${renderTagRow(intent.rag_queries)}
      <div><strong>Follow-up</strong></div>
      ${renderTagRow(intent.follow_up_questions)}
    `;
  }

  function renderEvidence(evidence: RagEvidence[], citationsByField: Record<string, string[]>): void {
    evidenceList.innerHTML = buildEvidenceCards(evidence, citationsByField);
  }

  function buildEvidenceCards(evidence: RagEvidence[], citationsByField: Record<string, string[]> = {}): string {
    const citedMap = new Map<string, string[]>();
    Object.entries(citationsByField).forEach(([field, ids]) => {
      ids.forEach((id) => {
        const list = citedMap.get(id) || [];
        list.push(field);
        citedMap.set(id, list);
      });
    });
    return evidence
      .map((item) => {
        const citedFields = citedMap.get(item.chunk_id) || [];
        const hints = Object.entries(item.parameter_hints || {})
          .map(([key, value]) => `<span class="hint">${escapeHtml(key)}: ${escapeHtml(value)}</span>`)
          .join("");
        const pageLabel = item.page_start > 0 && item.page_end > 0 ? `<span>pp. ${item.page_start}-${item.page_end}</span>` : "";
        const sourceLabel = `<span class="tag">${escapeHtml(formatKnowledgeSourceLabel(item.knowledge_source || "pdf_rag"))}</span>`;
        return `
          <article class="evidence-card">
            <div class="evidence-meta">
              <span><strong>${escapeHtml(item.section_title || item.chunk_id)}</strong></span>
              <span>${escapeHtml(item.doc_id)}</span>
              ${pageLabel}
              <span>score ${item.score.toFixed(3)}</span>
              ${sourceLabel}
            </div>
            <div class="field-note">${escapeHtml(item.relevance_reason)}</div>
            <p class="evidence-text">${escapeHtml(item.text)}</p>
            <div class="hint-row">${hints || ""}</div>
            <div class="tag-row">${citedFields.map((field) => `<span class="tag">Used by ${escapeHtml(field)}</span>`).join("")}</div>
          </article>
        `;
      })
      .join("");
  }

  function renderParameterForm(
    patch: Record<string, string | number>,
    citationsByField: Record<string, string[]> = {},
    parameterSourcesByField: Record<string, string> = {},
  ): void {
    parameterForm.innerHTML = FIELD_CONFIGS.map((field) => {
      const value = patch[field.key] ?? "";
      const citations = (citationsByField[field.key] || []).join(", ");
      const source = parameterSourcesByField[field.key] || "unknown";
      const sourceLabel = formatParameterSourceLabel(source);
      if (field.type === "select") {
        const options = (field.options || [])
          .map((option) => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`)
          .join("");
        return `
          <div class="field">
            <label for="field-${field.key}">${escapeHtml(field.label)}</label>
            <select id="field-${field.key}" data-key="${escapeHtml(field.key)}">${options}</select>
            <div class="tag-row">
              <span class="tag source-tag ${escapeHtml(source)}">source: ${escapeHtml(sourceLabel)}</span>
            </div>
            <div class="field-note">citations: ${escapeHtml(citations || "none")}</div>
          </div>
        `;
      }
      return `
        <div class="field">
          <label for="field-${field.key}">${escapeHtml(field.label)}</label>
          <input id="field-${field.key}" data-key="${escapeHtml(field.key)}" type="${field.type}" value="${escapeHtml(String(value))}" />
          <div class="tag-row">
            <span class="tag source-tag ${escapeHtml(source)}">source: ${escapeHtml(sourceLabel)}</span>
          </div>
          <div class="field-note">citations: ${escapeHtml(citations || "none")}</div>
        </div>
      `;
    }).join("");
  }

  function renderSceneSetup(): void {
    const sceneContext = buildSceneContextFromForm();
    state.sceneContext = sceneContext;
    const isOsm = sceneContext.layout_mode === "osm";
    const isMetaUrban = sceneContext.layout_mode === "metaurban";
    const isGraphTemplate = sceneContext.layout_mode === "graph_template";
    sceneCityField.style.display = isOsm ? "" : "none";
    sceneGraphTemplateField.style.display = isGraphTemplate ? "" : "none";
    sceneReferenceField.style.display = isMetaUrban ? "" : "none";
    osmSceneFields.style.display = isOsm ? "" : "none";
    sceneTemplatePreview.hidden = !(isMetaUrban || isGraphTemplate);
    if (isGraphTemplate) {
      const template = state.graphTemplates.find((item) => item.template_id === sceneContext.graph_template_id) || null;
      renderGraphTemplatePreview(template);
      if (!template) {
        sceneSetupSummary.textContent = state.graphTemplateLoadError
          ? `Graph Template 模式加载失败。\n${state.graphTemplateLoadError}`
          : "Graph Template 模式需要一个可用的内置 street graph，目前还没有加载到模板。";
        return;
      }
      sceneSetupSummary.textContent =
        "Graph Template 模式会直接基于内置 street graph 生成 3D street scene。"
        + `\nTemplate: ${template.label}`
        + `\nGraph: ${template.centerline_count} centerlines · ${template.junction_count} junctions`
        + "\n会复用现有 compose/export/viewer 链路，不进入 Annotator，也不进入 MetaUrban 仿真 runtime。";
      return;
    }
    if (isMetaUrban) {
      const plan = state.referencePlans.find((item) => item.plan_id === sceneContext.reference_plan_id) || null;
      renderReferencePlanPreview(plan);
      if (!plan) {
        sceneSetupSummary.textContent = state.referencePlanLoadError
          ? `MetaUrban 模式加载失败。\n${state.referencePlanLoadError}`
          : "MetaUrban 模式需要一个可用的参考平面，目前还没有加载到预置。";
        return;
      }
      sceneSetupSummary.textContent =
        `MetaUrban 模式会基于参考平面和 block grammar 直接生成可导出的 3D corridor scene。`
        + `\nReference: ${plan.label}`
        + `\nBlock sequence: ${plan.block_sequence}`
        + "\n会复用 corridor 几何、周边建筑、scene export 和 viewer 链路，不再停在平面 layout。";
      return;
    }
    sceneTemplatePreview.innerHTML = "";
    if (!isOsm) {
      sceneSetupSummary.textContent =
        "Template 模式会生成参数化直街模板，不会启用 OSM 走廊、自动选路或周边建筑链路。";
      return;
    }
    const city = state.cities.find((item) => item.name_en === sceneContext.city_name_en);
    const cityLabel = city ? `${city.name_zh} ${city.name_en}` : sceneContext.city_name_en || "manual";
    const bboxLabel = sceneContext.aoi_bbox ? formatBbox(sceneContext.aoi_bbox) : "invalid bbox";
    sceneSetupSummary.textContent =
      `OSM 模式将在 ${cityLabel} 的 AOI 中自动挑一条普通可步行街道。`
      + `\nAOI: ${bboxLabel}`
      + `\nBBox source: ${state.bboxDirty ? "manual override" : "city preset"}`
      + "\nPOI 只用于选路与道路两侧用地/规则推断，不作为 workbench 主显示对象。";
  }

  function renderReferencePlanPreview(plan: ReferencePlan | null): void {
    if (!plan) {
      sceneTemplatePreview.innerHTML = `<div class="field-note">${
        escapeHtml(state.referencePlanLoadError || "未加载到 MetaUrban reference plan。")
      }</div>`;
      return;
    }
    sceneTemplatePreview.innerHTML = `
      <article class="reference-plan-card">
        <div class="reference-plan-meta">
          <div class="result-head">
            <strong>${escapeHtml(plan.label)}</strong>
            <span class="tag">seed ${escapeHtml(String(plan.seed))}</span>
          </div>
          <div class="field-note">${escapeHtml(plan.description)}</div>
          <div class="tag-row">
            <span class="tag">sequence ${escapeHtml(plan.block_sequence)}</span>
            <span class="tag">straight ${escapeHtml(formatMetricValue(plan.straight_length_m, 1))}m</span>
            <span class="tag">roundabout radius ${escapeHtml(formatMetricValue(plan.curve_radius_m, 1))}m</span>
          </div>
        </div>
        <img
          class="reference-plan-image"
          src="${escapeHtml(resolveApiUrl(plan.image_url))}"
          alt="${escapeHtml(plan.label)} reference plan"
        />
      </article>
    `;
  }

  function renderGraphTemplatePreview(template: GraphTemplate | null): void {
    if (!template) {
      sceneTemplatePreview.innerHTML = `<div class="field-note">${
        escapeHtml(state.graphTemplateLoadError || "未加载到 graph template。")
      }</div>`;
      return;
    }
    sceneTemplatePreview.innerHTML = `
      <article class="reference-plan-card">
        <div class="reference-plan-meta">
          <div class="result-head">
            <strong>${escapeHtml(template.label)}</strong>
            <span class="tag">${escapeHtml(template.source_format)}</span>
          </div>
          <div class="field-note">${escapeHtml(template.description)}</div>
          <div class="tag-row">
            <span class="tag">${escapeHtml(`${template.centerline_count} centerlines`)}</span>
            <span class="tag">${escapeHtml(`${template.junction_count} junctions`)}</span>
          </div>
        </div>
        <img
          class="reference-plan-image"
          src="${escapeHtml(resolveApiUrl(template.image_url))}"
          alt="${escapeHtml(template.label)} graph template"
        />
      </article>
    `;
  }

  function renderJobPanel(): void {
    const currentJob = state.currentJob;
    const currentJobHtml = currentJob
      ? `
          <div class="result-section">
            <div class="result-head">
              <strong>Current Job</strong>
              <span class="status-pill ${escapeHtml(currentJob.status)}">${escapeHtml(currentJob.status)}</span>
            </div>
            <div class="field-note">job_id: <span class="mono">${escapeHtml(currentJob.job_id)}</span></div>
            <div class="field-note">created: ${escapeHtml(formatTimestamp(currentJob.created_at))}</div>
            ${currentJob.started_at ? `<div class="field-note">started: ${escapeHtml(formatTimestamp(currentJob.started_at))}</div>` : ""}
            ${currentJob.finished_at ? `<div class="field-note">finished: ${escapeHtml(formatTimestamp(currentJob.finished_at))}</div>` : ""}
            ${currentJob.error ? `<div class="summary-box">Error: ${escapeHtml(currentJob.error)}</div>` : ""}
            ${currentJob.result ? renderGenerationCard(currentJob.result) : `<div class="field-note">等待生成结果...</div>`}
          </div>
        `
      : `<div class="result-section"><strong>Current Job</strong><div class="field-note">尚未创建生成任务。</div></div>`;

    const recentScenesHtml = state.recentScenes.length
        ? state.recentScenes
          .map((item) => {
            const summary = JSON.stringify(compactSceneSummary(item.summary || {}), null, 2);
            const viewerUrl = resolveViewerUrl(item.viewer_url, item.scene_layout_path);
            const normalizedLayoutPath = normalizeSceneLayoutPath(item.scene_layout_path);
            const viewerLinkHtml = viewerUrl
              ? `<a class="hero-link scene-link" href="${escapeHtml(viewerUrl)}" target="_blank" rel="noreferrer">Open Viewer</a>`
              : `<div class="field-note">Viewer 链接暂不可用，但 scene/export 结果仍可直接检查。</div>`;
            return `
              <article class="scene-card">
                <div class="result-head">
                  <strong>${escapeHtml(item.job_id.slice(0, 10))}</strong>
                  <span class="status-pill ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>
                </div>
                <div class="field-note">finished: ${escapeHtml(formatTimestamp(item.finished_at || item.created_at))}</div>
                <div class="actions">
                  ${viewerLinkHtml}
                </div>
                ${renderSceneSummaryHighlights(item.summary || {})}
                ${normalizedLayoutPath ? `<div class="mono">layout: ${escapeHtml(normalizedLayoutPath)}</div>` : ""}
                <pre class="mono scene-summary">${escapeHtml(summary)}</pre>
              </article>
            `;
          })
          .join("")
      : `<div class="field-note">还没有成功生成的场景。</div>`;

    generationResult.innerHTML = `
      ${currentJobHtml}
      <div class="result-section">
        <div class="result-head">
          <strong>Recent Scenes</strong>
          <button id="refresh-scenes-btn" class="btn secondary" type="button">刷新</button>
        </div>
        <div class="scene-list">${recentScenesHtml}</div>
      </div>
    `;

    const refreshBtn = generationResult.querySelector<HTMLButtonElement>("#refresh-scenes-btn");
    refreshBtn?.addEventListener("click", async () => {
      refreshBtn.disabled = true;
      try {
        await refreshRecentScenes();
        setStatus("最近场景列表已刷新。");
      } catch (error) {
        setStatus(asErrorMessage(error));
      } finally {
        refreshBtn.disabled = false;
      }
    });
  }

  function renderGenerationCard(result: GenerationResponse): string {
    const summary = compactSceneSummary(result.summary);
    const viewerUrl = resolveViewerUrl(result.viewer_url, result.scene_layout_path);
    const normalizedLayoutPath = normalizeSceneLayoutPath(result.scene_layout_path);
    const links: string[] = [];
    if (viewerUrl) {
      links.push(`<div><a href="${escapeHtml(viewerUrl)}" target="_blank" rel="noreferrer">Open Viewer</a></div>`);
    } else {
      links.push(`<div class="field-note">Viewer 链接暂不可用，请直接检查导出的 scene 文件。</div>`);
    }
    if (normalizedLayoutPath) {
      links.push(`<div class="mono">layout: ${escapeHtml(normalizedLayoutPath)}</div>`);
    }
    if (result.scene_glb_path) {
      links.push(`<div class="mono">glb: ${escapeHtml(result.scene_glb_path)}</div>`);
    }
    return `
      <div class="summary-box">
        <div><strong>Summary</strong></div>
        ${renderSceneSummaryHighlights(result.summary)}
        <pre class="mono scene-summary">${escapeHtml(JSON.stringify(summary, null, 2))}</pre>
        ${links.join("")}
      </div>
    `;
  }

  function renderTimeline(): void {
    timelineEl.innerHTML = state.messages.length
      ? state.messages
          .map(
            (item) => `
              <div class="message ${escapeHtml(item.role)}">
                <span class="message-label">${escapeHtml(item.role)}</span>
                ${escapeHtml(item.content)}
              </div>
            `,
          )
          .join("")
      : `<div class="message assistant"><span class="message-label">assistant</span>告诉我你想做什么街道，我会先从完整街道设计指南中抽取证据，再给出可编辑参数草案。</div>`;
  }

  function setStatus(message: string): void {
    statusBox.textContent = message;
  }
}

function buildDraftFromForm(baseDraft: DesignDraft, parameterForm: HTMLDivElement): DesignDraft {
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

function buildSceneContextFromForm(): SceneContext {
  const layoutModeEl = requireElement<HTMLSelectElement>(document, "#scene-layout-mode");
  const cityEl = requireElement<HTMLSelectElement>(document, "#scene-city");
  const graphTemplateEl = requireElement<HTMLSelectElement>(document, "#scene-graph-template");
  const referencePlanEl = requireElement<HTMLSelectElement>(document, "#scene-reference-plan");
  const bboxFields = [
    requireElement<HTMLInputElement>(document, "#bbox-min-lon"),
    requireElement<HTMLInputElement>(document, "#bbox-min-lat"),
    requireElement<HTMLInputElement>(document, "#bbox-max-lon"),
    requireElement<HTMLInputElement>(document, "#bbox-max-lat"),
  ];
  const layoutMode = layoutModeEl.value === "template"
    ? "template"
    : layoutModeEl.value === "metaurban"
      ? "metaurban"
      : layoutModeEl.value === "graph_template"
        ? "graph_template"
        : "osm";
  if (layoutMode === "graph_template") {
    return {
      layout_mode: "graph_template",
      aoi_bbox: null,
      city_name_en: cityEl.value || null,
      reference_plan_id: null,
      graph_template_id: graphTemplateEl.value || null,
    };
  }
  if (layoutMode === "metaurban") {
    return {
      layout_mode: "metaurban",
      aoi_bbox: null,
      city_name_en: cityEl.value || null,
      reference_plan_id: referencePlanEl.value || null,
      graph_template_id: null,
    };
  }
  if (layoutMode === "template") {
    return {
      layout_mode: "template",
      aoi_bbox: null,
      city_name_en: cityEl.value || null,
      reference_plan_id: null,
      graph_template_id: null,
    };
  }
  const bbox = bboxFields.map((field) => Number(field.value.trim()));
  const isValid =
    bbox.every((value) => Number.isFinite(value))
    && bbox[0] < bbox[2]
    && bbox[1] < bbox[3];
  return {
    layout_mode: "osm",
    aoi_bbox: isValid ? (bbox as [number, number, number, number]) : null,
    city_name_en: cityEl.value || null,
    reference_plan_id: null,
    graph_template_id: null,
  };
}

function formatDraftSummary(draft: DesignDraft): string {
  return [
    draft.design_summary || "No summary returned.",
    draft.risk_notes.length ? `\nRisk Notes:\n- ${draft.risk_notes.join("\n- ")}` : "",
  ].join("");
}

function buildClarificationAssistantMessage(questions: string[]): string {
  if (!questions.length) {
    return "我还需要补充一些关键信息后，才能继续生成设计草案。";
  }
  return [
    "继续生成设计草案前，我还需要确认这些关键信息：",
    ...questions.map((question, index) => `${index + 1}. ${question}`),
  ].join("\n");
}

function setBboxInputs(bbox: [number, number, number, number]): void {
  requireElement<HTMLInputElement>(document, "#bbox-min-lon").value = String(bbox[0]);
  requireElement<HTMLInputElement>(document, "#bbox-min-lat").value = String(bbox[1]);
  requireElement<HTMLInputElement>(document, "#bbox-max-lon").value = String(bbox[2]);
  requireElement<HTMLInputElement>(document, "#bbox-max-lat").value = String(bbox[3]);
}

function formatBbox(bbox: [number, number, number, number]): string {
  return `(${bbox.map((value) => value.toFixed(4)).join(", ")})`;
}

function compactSceneSummary(summary: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(summary || {}).filter(([key]) => !SUMMARY_OMIT_KEYS.has(key)),
  );
}

function renderSceneSummaryHighlights(summary: Record<string, unknown>): string {
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

function formatUnknownBbox(value: unknown): string {
  if (!Array.isArray(value) || value.length !== 4 || value.some((item) => typeof item !== "number" || !Number.isFinite(item))) {
    return "";
  }
  return `(${value.map((item) => Number(item).toFixed(4)).join(", ")})`;
}

function formatMetricValue(value: number, digits = 2): string {
  return Number(value)
    .toFixed(digits)
    .replace(/\.0+$/, "")
    .replace(/(\.\d*?[1-9])0+$/, "$1");
}

function normalizeSceneLayoutPath(layoutPath: string): string {
  const trimmed = String(layoutPath || "").trim();
  if (!trimmed) {
    return "";
  }
  if (/scene_layout\.json$/i.test(trimmed)) {
    return trimmed;
  }
  return `${trimmed.replace(/\/+$/, "")}/scene_layout.json`;
}

function buildFallbackViewerUrl(layoutPath: string): string {
  const normalizedLayoutPath = normalizeSceneLayoutPath(layoutPath);
  if (!normalizedLayoutPath) {
    return "";
  }
  return `${VIEWER_BASE}/?layout=${encodeURIComponent(normalizedLayoutPath)}`;
}

function resolveViewerUrl(viewerUrl: string, layoutPath: string): string {
  return String(viewerUrl || "").trim() || buildFallbackViewerUrl(layoutPath);
}

function renderTagRow(items: string[]): string {
  if (!items.length) {
    return `<div class="field-note">none</div>`;
  }
  return `<div class="tag-row">${items.map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("")}</div>`;
}

function normalizeKnowledgeSourceKey(value: string): KnowledgeSourceKey {
  if (value === "pdf_rag" || value === "graph_rag") {
    return value;
  }
  return "hybrid";
}

function formatKnowledgeSourceLabel(source: string): string {
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

function formatParameterSourceLabel(source: string): string {
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

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (!element) {
    throw new Error(`Missing required element: ${selector}`);
  }
  return element;
}

async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleJsonResponse<T>(response);
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  return handleJsonResponse<T>(response);
}

function resolveApiUrl(path: string): string {
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

function formatTimestamp(value: string): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function asErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function formatBootstrapError(error: unknown): string {
  const message = asErrorMessage(error).trim();
  if (!message) {
    return `无法连接 API：${API_BASE}`;
  }
  if (/failed to fetch|networkerror|load failed|fetch failed|couldn't connect|cannot connect/i.test(message)) {
    return `无法连接 API：${API_BASE}`;
  }
  return message;
}
