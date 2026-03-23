type ChatMessage = {
  role: string;
  content: string;
};

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
  layout_mode: "template" | "osm";
  aoi_bbox: [number, number, number, number] | null;
  city_name_en: string | null;
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

type DraftResponse = {
  intent: DesignIntent;
  evidence: RagEvidence[];
  draft: DesignDraft;
  warnings: string[];
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
    sceneContext: {
      layout_mode: "osm",
      aoi_bbox: null,
      city_name_en: null,
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
          <div class="hero-chip">2. PDF RAG Evidence</div>
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
                <div class="actions">
                  <button id="draft-btn" class="btn primary">生成设计建议</button>
                  <button id="rebuild-btn" class="btn secondary">重建知识库</button>
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
	                    <option value="osm" selected>osm</option>
	                    <option value="template">template</option>
	                  </select>
	                </div>
	                <div id="scene-city-field" class="field">
	                  <label for="scene-city">City</label>
	                  <select id="scene-city">
	                    <option value="">Loading cities...</option>
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
	              <div id="scene-setup-summary" class="summary-box">OSM 模式会在 AOI 中自动挑一条普通可步行街道，并启用周边建筑链路。</div>
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
  const rebuildBtn = requireElement<HTMLButtonElement>(app, "#rebuild-btn");
  const statusBox = requireElement<HTMLDivElement>(app, "#status-box");
  const sceneLayoutMode = requireElement<HTMLSelectElement>(app, "#scene-layout-mode");
  const sceneCity = requireElement<HTMLSelectElement>(app, "#scene-city");
  const sceneCityField = requireElement<HTMLDivElement>(app, "#scene-city-field");
  const osmSceneFields = requireElement<HTMLDivElement>(app, "#osm-scene-fields");
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
  renderJobPanel();
  void bootstrap();

  sceneLayoutMode.addEventListener("change", () => {
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
    draftBtn.disabled = true;
    setStatus("正在让 LLM 解析意图并检索设计指南...");
    try {
      const payload = await postJson<DraftResponse>("/api/design/draft", {
        messages: state.messages,
        user_input: prompt,
        current_patch: state.lastDraft?.draft.compose_config_patch || {},
        topk: 6,
      });
      state.messages.push({ role: "user", content: prompt });
      state.messages.push({ role: "assistant", content: payload.draft.design_summary });
      state.lastDraft = payload;
      state.lastGeneration = null;
      state.currentJob = null;
      promptInput.value = "";
      renderTimeline();
      renderDraft(payload);
      renderJobPanel();
      setStatus(payload.warnings.length ? payload.warnings.join("\n") : "设计草案已生成，请确认参数后创建生成任务。");
    } catch (error) {
      setStatus(asErrorMessage(error));
    } finally {
      draftBtn.disabled = false;
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

  generateBtn.addEventListener("click", async () => {
    if (!state.lastDraft) {
      setStatus("请先生成设计草案。");
      return;
    }
    generateBtn.disabled = true;
    setStatus("正在创建场景生成任务...");
    try {
      const draft = buildDraftFromForm(state.lastDraft.draft, parameterForm);
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
      setStatus("任务已入队，正在轮询生成状态...");
      await pollSceneJob(created.job_id);
    } catch (error) {
      setStatus(asErrorMessage(error));
      generateBtn.disabled = false;
    }
  });

  async function bootstrap(): Promise<void> {
    try {
      await loadChinaCities();
      await refreshRecentScenes();
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
    } catch (_error) {
      // Workbench should still render even if the API is not up yet.
    }
  }

  async function loadChinaCities(): Promise<void> {
    const payload = await getJson<ChinaCityResponse>("/api/geo/china-cities");
    state.cities = payload.items;
    sceneCity.innerHTML = state.cities
      .map((item) => `<option value="${escapeHtml(item.name_en)}">${escapeHtml(`${item.name_zh} ${item.name_en}`)}</option>`)
      .join("");
    const defaultCity = state.cities.find((item) => item.name_en === DEFAULT_WORKBENCH_CITY) || state.cities[0];
    if (defaultCity) {
      sceneCity.value = defaultCity.name_en;
      setBboxInputs(defaultCity.bbox);
      state.bboxDirty = false;
      state.sceneContext = {
        layout_mode: "osm",
        aoi_bbox: defaultCity.bbox,
        city_name_en: defaultCity.name_en,
      };
    }
    renderSceneSetup();
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

  function renderDraft(payload: DraftResponse): void {
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
    const citedMap = new Map<string, string[]>();
    Object.entries(citationsByField).forEach(([field, ids]) => {
      ids.forEach((id) => {
        const list = citedMap.get(id) || [];
        list.push(field);
        citedMap.set(id, list);
      });
    });
    evidenceList.innerHTML = evidence
      .map((item) => {
        const citedFields = citedMap.get(item.chunk_id) || [];
        const hints = Object.entries(item.parameter_hints || {})
          .map(([key, value]) => `<span class="hint">${escapeHtml(key)}: ${escapeHtml(value)}</span>`)
          .join("");
        return `
          <article class="evidence-card">
            <div class="evidence-meta">
              <span><strong>${escapeHtml(item.section_title || item.chunk_id)}</strong></span>
              <span>${escapeHtml(item.doc_id)}</span>
              <span>pp. ${item.page_start}-${item.page_end}</span>
              <span>score ${item.score.toFixed(3)}</span>
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
    sceneCityField.style.display = isOsm ? "" : "none";
    osmSceneFields.style.display = isOsm ? "" : "none";
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
            const viewerHref = item.viewer_url || VIEWER_BASE;
            const summary = JSON.stringify(compactSceneSummary(item.summary || {}), null, 2);
            return `
              <article class="scene-card">
                <div class="result-head">
                  <strong>${escapeHtml(item.job_id.slice(0, 10))}</strong>
                  <span class="status-pill ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>
                </div>
                <div class="field-note">finished: ${escapeHtml(formatTimestamp(item.finished_at || item.created_at))}</div>
                <div class="actions">
                  <a class="hero-link scene-link" href="${escapeHtml(viewerHref)}" target="_blank" rel="noreferrer">Open Viewer</a>
                </div>
                ${renderSceneSummaryHighlights(item.summary || {})}
                ${item.scene_layout_path ? `<div class="mono">layout: ${escapeHtml(item.scene_layout_path)}</div>` : ""}
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
    const viewerHref = result.viewer_url || VIEWER_BASE;
    const summary = compactSceneSummary(result.summary);
    const links = [
      `<div><a href="${escapeHtml(viewerHref)}" target="_blank" rel="noreferrer">Open Viewer</a></div>`,
      result.scene_layout_path ? `<div class="mono">layout: ${escapeHtml(result.scene_layout_path)}</div>` : "",
      result.scene_glb_path ? `<div class="mono">glb: ${escapeHtml(result.scene_glb_path)}</div>` : "",
    ]
      .filter(Boolean)
      .join("");
    return `
      <div class="summary-box">
        <div><strong>Summary</strong></div>
        ${renderSceneSummaryHighlights(result.summary)}
        <pre class="mono scene-summary">${escapeHtml(JSON.stringify(summary, null, 2))}</pre>
        ${links}
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
  const bboxFields = [
    requireElement<HTMLInputElement>(document, "#bbox-min-lon"),
    requireElement<HTMLInputElement>(document, "#bbox-min-lat"),
    requireElement<HTMLInputElement>(document, "#bbox-max-lon"),
    requireElement<HTMLInputElement>(document, "#bbox-max-lat"),
  ];
  const layoutMode = layoutModeEl.value === "template" ? "template" : "osm";
  if (layoutMode === "template") {
    return {
      layout_mode: "template",
      aoi_bbox: null,
      city_name_en: cityEl.value || null,
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
  };
}

function formatDraftSummary(draft: DesignDraft): string {
  return [
    draft.design_summary || "No summary returned.",
    draft.risk_notes.length ? `\nRisk Notes:\n- ${draft.risk_notes.join("\n- ")}` : "",
  ].join("");
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

function renderTagRow(items: string[]): string {
  if (!items.length) {
    return `<div class="field-note">none</div>`;
  }
  return `<div class="tag-row">${items.map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("")}</div>`;
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
