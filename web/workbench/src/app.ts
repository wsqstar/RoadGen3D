import type {
  ChatMessage,
  KnowledgeSourceKey,
  DesignIntent,
  RagEvidence,
  DesignDraft,
  SceneContext,
  ChinaCity,
  ChinaCityResponse,
  ReferencePlan,
  ReferencePlanResponse,
  GraphTemplate,
  GraphTemplateResponse,
  DraftResponse,
  KnowledgeSourceStatus,
  KnowledgeSourceListResponse,
  KnowledgeSearchResponse,
  GenerationResponse,
  SceneJobCreateResponse,
  SceneJobStatusResponse,
  SceneRecord,
  SceneJobListResponse,
  SceneRecentResponse,
} from "./types";
import {
  API_BASE,
  VIEWER_BASE,
  POLL_INTERVAL_MS,
  TERMINAL_JOB_STATES,
  DEFAULT_WORKBENCH_CITY,
  DEFAULT_REFERENCE_PLAN_ID,
  DEFAULT_GRAPH_TEMPLATE_ID,
  PEDESTRIAN_ALL_AGE_PRESET_PROMPT,
  FIELD_CONFIGS,
} from "./types";
import { requireElement, postJson, getJson, resolveApiUrl } from "./api";
import {
  escapeHtml,
  asErrorMessage,
  formatBootstrapError,
  normalizeKnowledgeSourceKey,
  formatKnowledgeSourceLabel,
  formatParameterSourceLabel,
  formatTimestamp,
  sleep,
  formatBbox,
  formatMetricValue,
  compactSceneSummary,
  renderTagRow,
  normalizeSceneLayoutPath,
  resolveViewerUrl,
  formatDraftSummary,
  buildClarificationAssistantMessage,
  buildDraftFromForm,
  renderSceneSummaryHighlights,
} from "./utils";

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
    previewVisible: false,
  };

  function computePipelineStep(): number {
    if (state.currentJob) return 4;
    if (state.lastDraft?.draft) return 3;
    if (state.lastDraft && state.lastDraft.evidence?.length > 0) return 2;
    return 1;
  }

  function renderHeroSteps(): void {
    const step = computePipelineStep();
    app.querySelectorAll<HTMLDivElement>(".hero-chip").forEach((chip) => {
      const chipStep = Number(chip.dataset.step);
      chip.classList.toggle("completed", chipStep < step);
      chip.classList.toggle("active", chipStep === step);
    });
  }

  app.innerHTML = `
    <div class="shell">
      <section class="hero">
        <h1>RoadGen3D Street Workbench</h1>
        <p>
          生成工作台和 3D viewer 分开运行。这里负责对话、RAG、参数确认和任务触发；空间浏览与漫游交给独立 viewer。
        </p>
        <div class="hero-grid">
          <div class="hero-chip" data-step="1">1. Intent Clarification</div>
          <div class="hero-chip" data-step="2">2. GraphRAG Evidence</div>
          <div class="hero-chip" data-step="3">3. Parameter Confirmation</div>
          <div class="hero-chip" data-step="4">4. Scene Job</div>
        </div>
        <div class="hero-actions">
          <a class="hero-link" href="${escapeHtml(VIEWER_BASE)}" target="_blank" rel="noreferrer">Open Standalone Viewer</a>
        </div>
      </section>

      <div class="config-bar">
        <div class="config-bar-group">
          <span class="config-bar-label">Layout</span>
          <select id="config-bar-layout" class="config-bar-select">
            <option value="graph_template" selected>graph_template</option>
            <option value="osm">osm</option>
            <option value="metaurban">metaurban</option>
            <option value="template">template</option>
          </select>
        </div>
        <div class="config-bar-group">
          <span class="config-bar-label">Knowledge</span>
          <select id="config-bar-knowledge" class="config-bar-select">
            <option value="graph_rag" selected>graph_rag</option>
            <option value="hybrid">hybrid</option>
            <option value="pdf_rag">pdf_rag</option>
          </select>
        </div>
        <div class="config-bar-actions">
          <button id="config-bar-draft-btn" class="btn primary btn-sm">Generate Draft</button>
          <button id="config-bar-generate-btn" class="btn secondary btn-sm" disabled>Create Scene Job</button>
          <a class="hero-link btn-sm" href="${escapeHtml(VIEWER_BASE)}" target="_blank" rel="noreferrer">Open Viewer</a>
        </div>
      </div>

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
  const configBarLayout = requireElement<HTMLSelectElement>(app, "#config-bar-layout");
  const configBarKnowledge = requireElement<HTMLSelectElement>(app, "#config-bar-knowledge");
  const configBarDraftBtn = requireElement<HTMLButtonElement>(app, "#config-bar-draft-btn");
  const configBarGenerateBtn = requireElement<HTMLButtonElement>(app, "#config-bar-generate-btn");

  renderTimeline();
  renderParameterForm({});
  renderSceneSetup();
  renderKnowledgeSourcePanel();
  renderJobPanel();
  renderHeroSteps();
  void bootstrap();

  knowledgeSource.addEventListener("change", () => {
    state.selectedKnowledgeSource = normalizeKnowledgeSourceKey(knowledgeSource.value);
    configBarKnowledge.value = knowledgeSource.value;
    renderKnowledgeSourcePanel();
  });

  configBarKnowledge.addEventListener("change", () => {
    state.selectedKnowledgeSource = normalizeKnowledgeSourceKey(configBarKnowledge.value);
    knowledgeSource.value = configBarKnowledge.value;
    renderKnowledgeSourcePanel();
  });

  sceneLayoutMode.addEventListener("change", () => {
    configBarLayout.value = sceneLayoutMode.value;
    renderSceneSetup();
  });

  configBarLayout.addEventListener("change", () => {
    sceneLayoutMode.value = configBarLayout.value;
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

  configBarDraftBtn.addEventListener("click", () => { draftBtn.click(); });
  configBarGenerateBtn.addEventListener("click", () => { generateBtn.click(); });

  function syncButtons(): void {
    configBarGenerateBtn.disabled = generateBtn.disabled;
    configBarDraftBtn.disabled = draftBtn.disabled;
  }

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
      syncButtons();
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
    syncButtons();
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
      syncButtons();
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
        renderHeroSteps();
        if (!TERMINAL_JOB_STATES.has(state.currentJob.status)) {
          generateBtn.disabled = true;
          syncButtons();
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
      renderHeroSteps();
      if (TERMINAL_JOB_STATES.has(payload.status)) {
        generateBtn.disabled = false;
        syncButtons();
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
      renderHeroSteps();
      return;
    }
    generateBtn.disabled = false;
    syncButtons();
    renderIntent(payload.intent);
    renderEvidence(payload.evidence, payload.draft.citations_by_field);
    renderParameterForm(
      payload.draft.compose_config_patch,
      payload.draft.citations_by_field,
      payload.draft.parameter_sources_by_field,
    );
    draftSummary.textContent = formatDraftSummary(payload.draft);
    renderHeroSteps();
  }

  function renderClarification(payload: DraftResponse): void {
    generateBtn.disabled = true;
    syncButtons();
    renderIntent(payload.intent);
    evidenceList.innerHTML = `<div class="field-note">澄清轮次暂不执行 RAG 检索，请先回答问题。</div>`;
    renderParameterForm({});
    draftSummary.textContent = [
      "需要先确认以下信息后，才能继续生成设计草案：",
      ...payload.intent.follow_up_questions.map((item, index) => `${index + 1}. ${item}`),
    ].join("\n");
    renderHeroSteps();
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
    const hasPatch = patch && Object.keys(patch).length > 0;
    if (!hasPatch) {
      parameterForm.innerHTML = `<div class="field-note" style="text-align:center;padding:12px 0;">等待生成设计草案后填充参数。</div>`;
      return;
    }
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

  function renderPhaseInfo(status: string, result: GenerationResponse | null): string {
    const statusPill = `<span class="status-pill ${escapeHtml(status)}">${escapeHtml(status)}</span>`;
    if (status === "queued") {
      return `<div class="phase-row">${statusPill}<span class="field-note">Waiting...</span></div>`;
    }
    if (status === "running") {
      return `<div class="phase-row">${statusPill}<span class="field-note">Generating scene...</span></div>`;
    }
    if (status === "failed") {
      return `<div class="phase-row">${statusPill}<span class="field-note" style="color:#982727;">Failed</span></div>`;
    }
    if (status === "succeeded" && result) {
      const normalizedLayoutPath = normalizeSceneLayoutPath(result.scene_layout_path);
      const viewerUrl = resolveViewerUrl(result.viewer_url, result.scene_layout_path);
      const layoutHtml = normalizedLayoutPath
        ? `<div class="mono field-note">layout: ${escapeHtml(normalizedLayoutPath)}</div>`
        : "";
      const glbHtml = result.scene_glb_path
        ? `<div class="mono field-note">glb: ${escapeHtml(result.scene_glb_path)}</div>`
        : "";
      const previewBtnHtml = viewerUrl
        ? `<button class="btn secondary btn-sm preview-btn" data-viewer-url="${escapeHtml(viewerUrl)}">Preview 3D</button>`
        : "";
      const viewerLinkHtml = viewerUrl
        ? `<a href="${escapeHtml(viewerUrl)}" target="_blank" rel="noreferrer">Open Viewer</a>`
        : "";
      return `
        <div class="phase-row">${statusPill}</div>
        ${layoutHtml}
        ${glbHtml}
        <div class="actions" style="margin-top:6px;">
          ${previewBtnHtml}
          ${viewerLinkHtml}
        </div>
      `;
    }
    return `<div class="phase-row">${statusPill}</div>`;
  }

  function toggleInlinePreview(viewerUrl: string): void {
    const container = generationResult.querySelector<HTMLDivElement>("#preview-container");
    if (!container) return;
    if (state.previewVisible) {
      container.innerHTML = "";
      container.style.display = "none";
      state.previewVisible = false;
      return;
    }
    container.innerHTML = `
      <div class="preview-header">
        <strong>3D Preview</strong>
        <button class="btn secondary btn-sm preview-close-btn">Close</button>
      </div>
      <iframe class="preview-iframe" src="${escapeHtml(viewerUrl)}" allowfullscreen></iframe>
    `;
    container.style.display = "";
    state.previewVisible = true;
    const closeBtn = container.querySelector<HTMLButtonElement>(".preview-close-btn");
    closeBtn?.addEventListener("click", () => {
      state.previewVisible = false;
      container.innerHTML = "";
      container.style.display = "none";
    });
  }

  function renderJobPanel(): void {
    const currentJob = state.currentJob;
    const currentJobHtml = currentJob
      ? `
          <div class="result-section">
            <div class="result-head">
              <strong>Current Job</strong>
            </div>
            ${renderPhaseInfo(currentJob.status, currentJob.result)}
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
      <div id="preview-container" style="display:none"></div>
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

    generationResult.querySelectorAll<HTMLButtonElement>(".preview-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const viewerUrl = btn.dataset.viewerUrl;
        if (viewerUrl) {
          toggleInlinePreview(viewerUrl);
        }
      });
    });
    renderHeroSteps();
  }

  function renderGenerationCard(result: GenerationResponse): string {
    const summary = compactSceneSummary(result.summary);
    const viewerUrl = resolveViewerUrl(result.viewer_url, result.scene_layout_path);
    const normalizedLayoutPath = normalizeSceneLayoutPath(result.scene_layout_path);
    const links: string[] = [];
    if (viewerUrl) {
      links.push(`<div><a href="${escapeHtml(viewerUrl)}" target="_blank" rel="noreferrer">Open Viewer</a></div>`);
      links.push(`<button class="btn secondary btn-sm preview-btn" data-viewer-url="${escapeHtml(viewerUrl)}">Preview 3D</button>`);
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

function setBboxInputs(bbox: [number, number, number, number]): void {
  requireElement<HTMLInputElement>(document, "#bbox-min-lon").value = String(bbox[0]);
  requireElement<HTMLInputElement>(document, "#bbox-min-lat").value = String(bbox[1]);
  requireElement<HTMLInputElement>(document, "#bbox-max-lon").value = String(bbox[2]);
  requireElement<HTMLInputElement>(document, "#bbox-max-lat").value = String(bbox[3]);
}
