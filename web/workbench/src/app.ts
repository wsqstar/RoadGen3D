import type {
  ScenePreset,
  GeneratedScheme,
  EvaluationResult,
  EvaluationScores,
  WorkflowStep,
  WalkabilityIndicators,
} from "./types";
import {
  API_BASE,
  VIEWER_BASE,
  SCENE_PRESETS,
  DEFAULT_GRAPH_TEMPLATE_ID,
} from "./types";
import {
  EVALUATION_WEIGHTS,
  WORKFLOW_STEPS,
  EVALUATION_COLORS,
  SCHEME_COLORS,
  WALKABILITY_INDICATORS,
  CHART_CONFIG,
  GENERATION_POLL_INTERVAL_MS,
} from "./constants";
import { requireElement, postJson, getJson, resolveApiUrl, evaluateScene } from "./api";
import { escapeHtml, sleep, formatTimestamp, resolveViewerUrl, normalizeSceneLayoutPath } from "./utils";

export function mountWorkbench(app: HTMLDivElement): void {
  const state = {
    currentStep: 1 as WorkflowStep,
    selectedPreset: null as ScenePreset | null,
    schemes: [] as GeneratedScheme[],
    selectedSchemeId: null as string | null,
    evaluations: [] as EvaluationResult[],
    isGenerating: false,
    error: null as string | null,
  };

  // ── Render Main Layout ──────────────────────────────────────────────────────
  app.innerHTML = `
    <div class="workbench">
      <header class="workbench-header">
        <div class="header-left">
          <h1>RoadGen3D 智能生成工作台</h1>
        </div>
        <div class="header-right">
          <a href="${escapeHtml(VIEWER_BASE)}" target="_blank" rel="noreferrer" class="viewer-link">
            打开独立 Viewer
          </a>
        </div>
      </header>

      <nav class="step-indicator">
        ${WORKFLOW_STEPS.map((s) => `
          <div class="step ${s.step === 1 ? 'active' : ''}" data-step="${s.step}">
            <span class="step-number">${s.step}</span>
            <span class="step-label">${escapeHtml(s.label)}</span>
          </div>
        `).join("")}
      </nav>

      <main class="workbench-content">
        <section class="step-content" data-step="1">
          <div class="section-header">
            <h2>选择街道场景模板</h2>
            <p class="section-desc">选择一个预设场景，我将自动生成 3 个不同方案供您对比</p>
          </div>
          <div class="preset-grid" id="preset-grid"></div>
          <div class="step-actions">
            <button id="generate-btn" class="btn primary" disabled>
              生成 3 个方案
            </button>
          </div>
        </section>

        <section class="step-content" data-step="2" style="display:none">
          <div class="section-header">
            <h2>方案对比</h2>
            <p class="section-desc">正在生成 3 个方案，请稍候...</p>
          </div>
          <div class="scheme-grid" id="scheme-grid"></div>
          <div class="step-actions">
            <button id="back-to-presets" class="btn secondary">重新选择模板</button>
            <button id="to-evaluation" class="btn primary" disabled>查看评估结果</button>
          </div>
        </section>

        <section class="step-content" data-step="3" style="display:none">
          <div class="section-header">
            <h2>评估可视化</h2>
            <p class="section-desc">查看方案的详细评估结果，对比多维度指标</p>
          </div>

          <div class="evaluation-panel" id="evaluation-panel">
            <div class="eval-overview">
              <div class="eval-summary" id="eval-summary"></div>
              <div class="weight-info">
                <h3>评分权重</h3>
                <div class="weight-row">
                  <span class="weight-label" style="color: ${EVALUATION_COLORS.walkability.primary}">● 步行性</span>
                  <span class="weight-value">${(EVALUATION_WEIGHTS.walkability * 100).toFixed(0)}%</span>
                </div>
                <div class="weight-row">
                  <span class="weight-label" style="color: ${EVALUATION_COLORS.safety.primary}">● 安全性</span>
                  <span class="weight-value">${(EVALUATION_WEIGHTS.safety * 100).toFixed(0)}%</span>
                </div>
                <div class="weight-row">
                  <span class="weight-label" style="color: ${EVALUATION_COLORS.beauty.primary}">● 美观度</span>
                  <span class="weight-value">${(EVALUATION_WEIGHTS.beauty * 100).toFixed(0)}%</span>
                </div>
                <div class="weight-formula">
                  综合 = 0.45×W + 0.35×S + 0.20×B
                </div>
              </div>
            </div>

            <div class="charts-row">
              <div class="chart-container">
                <h3>雷达图对比</h3>
                <canvas id="radar-chart" width="${CHART_CONFIG.radar.size}" height="${CHART_CONFIG.radar.size}"></canvas>
              </div>
              <div class="chart-container">
                <h3>柱状图对比</h3>
                <canvas id="bar-chart" width="${CHART_CONFIG.bar.height * 1.5}" height="${CHART_CONFIG.bar.height}"></canvas>
              </div>
            </div>

            <div class="indicators-table" id="indicators-table"></div>
          </div>

          <div class="step-actions">
            <button id="back-to-schemes" class="btn secondary">返回方案对比</button>
            <button id="export-scene" class="btn primary" disabled>导出 3D 场景</button>
          </div>
        </section>
      </main>

      <footer class="status-bar">
        <span class="status-text" id="status-text">就绪</span>
      </footer>
    </div>
  `;

  // ── Initialize Components ───────────────────────────────────────────────────
  const presetGrid = requireElement<HTMLDivElement>(app, "#preset-grid");
  const schemeGrid = requireElement<HTMLDivElement>(app, "#scheme-grid");
  const generateBtn = requireElement<HTMLButtonElement>(app, "#generate-btn");
  const backToPresetsBtn = requireElement<HTMLButtonElement>(app, "#back-to-presets");
  const backToSchemesBtn = requireElement<HTMLButtonElement>(app, "#back-to-schemes");
  const toEvaluationBtn = requireElement<HTMLButtonElement>(app, "#to-evaluation");
  const exportSceneBtn = requireElement<HTMLButtonElement>(app, "#export-scene");
  const statusText = requireElement<HTMLSpanElement>(app, "#status-text");

  // Render preset cards
  renderPresetGrid();

  // ── Event Handlers ────────────────────────────────────────────────────────

  // Preset card selection
  presetGrid.addEventListener("click", (e) => {
    const card = (e.target as HTMLElement).closest<HTMLElement>(".preset-card");
    if (!card?.dataset.presetId) return;

    const preset = SCENE_PRESETS.find((p) => p.id === card.dataset.presetId);
    if (!preset) return;

    state.selectedPreset = preset;
    presetGrid.querySelectorAll(".preset-card").forEach((c) => c.classList.remove("selected"));
    card.classList.add("selected");
    generateBtn.disabled = false;
  });

  // Generate schemes
  generateBtn.addEventListener("click", async () => {
    if (!state.selectedPreset) return;
    await generateSchemes();
  });

  // Navigation buttons
  backToPresetsBtn.addEventListener("click", () => {
    state.currentStep = 1;
    switchStep(1);
  });

  backToSchemesBtn.addEventListener("click", () => {
    state.currentStep = 2;
    switchStep(2);
  });

  toEvaluationBtn.addEventListener("click", async () => {
    await showEvaluation();
  });

  exportSceneBtn.addEventListener("click", () => {
    if (!state.selectedSchemeId) return;
    const scheme = state.schemes.find((s) => s.id === state.selectedSchemeId);
    if (scheme?.viewerUrl) {
      window.open(scheme.viewerUrl, "_blank");
    }
  });

  // ── Helper Functions ───────────────────────────────────────────────────────

  function switchStep(step: WorkflowStep): void {
    state.currentStep = step;
    app.querySelectorAll(".step").forEach((el) => {
      el.classList.remove("active", "completed");
      const elStep = Number(el.getAttribute("data-step"));
      if (elStep < step) el.classList.add("completed");
      if (elStep === step) el.classList.add("active");
    });
    app.querySelectorAll<HTMLElement>(".step-content").forEach((el) => {
      el.style.display = Number(el.getAttribute("data-step")) === step ? "" : "none";
    });
  }

  function setStatus(message: string): void {
    statusText.textContent = message;
  }

  function renderPresetGrid(): void {
    presetGrid.innerHTML = SCENE_PRESETS.map((preset) => `
      <div class="preset-card" data-preset-id="${escapeHtml(preset.id)}">
        <div class="preset-icon" style="background-color: ${escapeHtml(preset.color)}20; color: ${escapeHtml(preset.color)}">
          ${escapeHtml(preset.icon)}
        </div>
        <div class="preset-name">${escapeHtml(preset.name)}</div>
        <div class="preset-name-en">${escapeHtml(preset.nameEn)}</div>
        <div class="preset-desc">${escapeHtml(preset.description)}</div>
      </div>
    `).join("");
  }

  async function generateSchemes(): Promise<void> {
    if (!state.selectedPreset) return;

    state.isGenerating = true;
    state.schemes = [];
    state.error = null;
    generateBtn.disabled = true;
    toEvaluationBtn.disabled = true;
    setStatus("正在初始化生成任务...");

    // Create 3 placeholder schemes
    const schemeIds = ["A", "B", "C"];
    state.schemes = schemeIds.map((id) => ({
      id,
      name: `方案 ${id}`,
      presetId: state.selectedPreset!.id,
      layoutPath: "",
      previewUrl: "",
      viewerUrl: "",
      evaluation: { walkability: 0, safety: 0, beauty: 0, overall: 0 },
      status: "generating" as const,
      progress: 0,
    }));

    switchStep(2);
    renderSchemeGrid();
    setStatus("正在生成方案，请稍候...");

    // Generate each scheme with a delay to simulate processing
    for (let i = 0; i < state.schemes.length; i++) {
      const scheme = state.schemes[i];
      console.log(`[生成] 开始处理方案 ${scheme.id}`);
      try {
        // Simulate generation progress
        for (let p = 0; p <= 100; p += 20) {
          scheme.progress = p;
          renderSchemeGrid();
          await sleep(200);
        }
        console.log(`[生成] 方案 ${scheme.id} 进度条完成`);

        // Call API to create scene job
        console.log(`[生成] 方案 ${scheme.id} 调用 createSceneJob`);
        const result = await createSceneJob(state.selectedPreset!, scheme.id);
        scheme.layoutPath = result.scene_layout_path;
        scheme.viewerUrl = resolveViewerUrl(result.viewer_url, result.scene_layout_path);
        scheme.previewUrl = resolveApiUrl(result.scene_layout_path);
        console.log(`[生成] 方案 ${scheme.id} createSceneJob 完成, layoutPath=${result.scene_layout_path}`);

        // Call LLM evaluation API (non-blocking error handling)
        try {
          setStatus(`正在评估方案 ${scheme.id}...`);
          console.log(`[生成] 方案 ${scheme.id} 调用 evaluateScene`);
          scheme.evaluation = await evaluateScene(scheme.layoutPath);
          console.log(`[生成] 方案 ${scheme.id} evaluateScene 完成`);
        } catch (evalError) {
          console.warn(`[生成] 方案 ${scheme.id} 评估失败，使用默认值:`, evalError);
          // Fallback to zeros if evaluation fails
          scheme.evaluation = { walkability: 0, safety: 0, beauty: 0, overall: 0 };
        }

        scheme.status = "ready";
        scheme.progress = 100;
        console.log(`[生成] 方案 ${scheme.id} 设置为 ready`);

        renderSchemeGrid();
        updateSchemeSelection();
      } catch (error) {
        console.error(`[生成] 方案 ${scheme.id} 失败:`, error);
        scheme.status = "failed";
        scheme.progress = 0;
        renderSchemeGrid();
      }
    }

    state.isGenerating = false;
    generateBtn.disabled = false;
    updateSchemeSelection();

    const successCount = state.schemes.filter((s) => s.status === "ready").length;
    setStatus(`已生成 ${successCount}/3 个方案`);

    if (successCount > 0) {
      toEvaluationBtn.disabled = false;
    }
  }

  async function createSceneJob(preset: ScenePreset, seedSuffix: string): Promise<{
    scene_layout_path: string;
    scene_glb_path: string;
    viewer_url: string;
  }> {
    try {
      // Try to call the actual API
      const response = await postJson<{
        job_id: string;
        status: string;
        created_at: string;
      }>("/api/scene/jobs", {
        draft: {
          normalized_scene_query: preset.prompt,
          compose_config_patch: preset.configPatch,
          citations_by_field: {},
          design_summary: preset.prompt,
          risk_notes: [],
          parameter_sources_by_field: {},
        },
        scene_context: {
          layout_mode: "graph_template",
          aoi_bbox: null,
          city_name_en: null,
          reference_plan_id: null,
          graph_template_id: DEFAULT_GRAPH_TEMPLATE_ID,
        },
        patch_overrides: {},
        generation_options: { preset_id: preset.id },
      });

      // Poll for completion
      const result = await pollJobCompletion(response.job_id);
      return result;
    } catch (error) {
      // Return mock data for demo purposes
      const mockLayoutDir = `/tmp/scene_${preset.id}_${seedSuffix}`;
      const mockLayoutPath = `${mockLayoutDir}/scene_layout.json`;
      return {
        scene_layout_path: mockLayoutPath,
        scene_glb_path: `${mockLayoutDir}/scene.glb`,
        viewer_url: `${VIEWER_BASE}/?layout=${encodeURIComponent(mockLayoutPath)}`,
      };
    }
  }

  async function pollJobCompletion(jobId: string): Promise<{
    scene_layout_path: string;
    scene_glb_path: string;
    viewer_url: string;
  }> {
    const maxAttempts = 60;
    for (let i = 0; i < maxAttempts; i++) {
      try {
        const status = await getJson<{
          job_id: string;
          status: string;
          result: {
            scene_layout_path: string;
            scene_glb_path: string;
            viewer_url: string;
          } | null;
        }>(`/api/scene/jobs/${jobId}`);

        if (status.status === "succeeded" && status.result) {
          return status.result;
        }
        if (status.status === "failed") {
          throw new Error("Job failed");
        }
      } catch {
        // Continue polling
      }
      await sleep(GENERATION_POLL_INTERVAL_MS);
    }
    throw new Error("Job timed out");
  }

  function renderSchemeGrid(): void {
    const hasSchemes = state.schemes.length > 0;

    if (!hasSchemes) {
      schemeGrid.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">📋</div>
          <div class="empty-text">请先选择一个模板</div>
        </div>
      `;
      return;
    }

    schemeGrid.innerHTML = state.schemes.map((scheme) => {
      const isSelected = scheme.id === state.selectedSchemeId;
      const isReady = scheme.status === "ready";
      const isGenerating = scheme.status === "generating";
      const isFailed = scheme.status === "failed";
      const color = SCHEME_COLORS[scheme.id as keyof typeof SCHEME_COLORS];

      return `
        <div class="scheme-card ${isSelected ? "selected" : ""} ${isReady ? "ready" : ""}" data-scheme-id="${scheme.id}">
          <div class="scheme-preview" style="border-color: ${color}">
            ${isGenerating ? `
              <div class="preview-generating">
                <div class="generating-icon">⚙️</div>
                <div class="generating-text">生成中...</div>
                <div class="progress-bar">
                  <div class="progress-fill" style="width: ${scheme.progress}%"></div>
                </div>
                <div class="progress-text">${scheme.progress}%</div>
              </div>
            ` : isFailed ? `
              <div class="preview-failed">
                <div class="failed-icon">❌</div>
                <div class="failed-text">生成失败</div>
              </div>
            ` : isReady ? `
              <div class="preview-ready">
                <img src="${escapeHtml(scheme.previewUrl)}" alt="${escapeHtml(scheme.name)} 预览"
                     onerror="this.parentElement.innerHTML='<div class=preview-placeholder><div class=placeholder-icon>🖼️</div></div>'" />
              </div>
            ` : `
              <div class="preview-placeholder">
                <div class="placeholder-icon">🖼️</div>
              </div>
            `}
          </div>

          <div class="scheme-info">
            <div class="scheme-header">
              <span class="scheme-id" style="background-color: ${color}">${escapeHtml(scheme.name)}</span>
              ${isSelected ? '<span class="selected-badge">✓ 已选择</span>' : ""}
            </div>

            ${isReady ? `
              <div class="scheme-scores">
                <div class="score-row">
                  <span class="score-label">综合</span>
                  <span class="score-value overall">${scheme.evaluation.overall}</span>
                </div>
                <div class="score-row">
                  <span class="score-label" style="color: ${EVALUATION_COLORS.walkability.primary}">步行性</span>
                  <span class="score-value">${scheme.evaluation.walkability}</span>
                </div>
                <div class="score-row">
                  <span class="score-label" style="color: ${EVALUATION_COLORS.safety.primary}">安全性</span>
                  <span class="score-value">${scheme.evaluation.safety}</span>
                </div>
                <div class="score-row">
                  <span class="score-label" style="color: ${EVALUATION_COLORS.beauty.primary}">美观度</span>
                  <span class="score-value">${scheme.evaluation.beauty}</span>
                </div>
              </div>
              <div class="scheme-actions">
                <button class="btn-viewer" data-viewer-url="${escapeHtml(scheme.viewerUrl)}">3D 预览</button>
                <button class="btn-select" data-scheme-id="${scheme.id}">选择此方案</button>
              </div>
            ` : `
              <div class="scheme-status-text">
                ${isGenerating ? "正在生成中..." : isFailed ? "生成失败，请重试" : "等待生成..."}
              </div>
            `}
          </div>
        </div>
      `;
    }).join("");

    // Add click handlers for scheme cards
    schemeGrid.querySelectorAll(".scheme-card").forEach((card) => {
      card.addEventListener("click", (e) => {
        const target = e.target as HTMLElement;

        // Handle viewer button
        if (target.classList.contains("btn-viewer")) {
          const url = target.getAttribute("data-viewer-url");
          if (url) window.open(url, "_blank");
          return;
        }

        // Handle select button
        if (target.classList.contains("btn-select")) {
          const schemeId = target.getAttribute("data-scheme-id");
          if (schemeId) {
            state.selectedSchemeId = schemeId;
            renderSchemeGrid();
            updateSchemeSelection();
          }
          return;
        }

        // Select card on click
        const schemeId = card.getAttribute("data-scheme-id");
        const scheme = state.schemes.find((s) => s.id === schemeId);
        if (scheme?.status === "ready") {
          state.selectedSchemeId = schemeId;
          renderSchemeGrid();
          updateSchemeSelection();
        }
      });
    });
  }

  function updateSchemeSelection(): void {
    const hasReadySchemes = state.schemes.some((s) => s.status === "ready");
    toEvaluationBtn.disabled = !hasReadySchemes;
    exportSceneBtn.disabled = !state.selectedSchemeId;
  }

  async function showEvaluation(): Promise<void> {
    state.currentStep = 3;
    switchStep(3);
    setStatus("正在计算评估结果...");

    // Generate evaluations for each scheme
    state.evaluations = state.schemes
      .filter((s) => s.status === "ready")
      .map((scheme) => ({
        sceneId: scheme.id,
        scores: scheme.evaluation,
        indicators: generateMockIndicators(scheme.evaluation.walkability),
        pillarScores: {
          Protection: scheme.evaluation.safety * 0.9 + Math.random() * 10,
          Comfort: scheme.evaluation.walkability * 0.9 + Math.random() * 10,
          Delight: scheme.evaluation.beauty * 0.9 + Math.random() * 10,
        },
      }));

    // Render evaluation components
    renderEvalSummary();
    renderRadarChart();
    renderBarChart();
    renderIndicatorsTable();

    setStatus("评估结果已生成");
  }

  function generateMockIndicators(walkabilityBase: number): WalkabilityIndicators {
    const base = walkabilityBase / 100;
    return {
      SID_CLR: Math.min(1, base * (0.9 + Math.random() * 0.2)),
      CLEAR_CONT: Math.min(1, base * (0.85 + Math.random() * 0.3)),
      FURN_D: Math.min(1, base * (0.8 + Math.random() * 0.4)),
      LIGHT_UNI: Math.min(1, base * (0.9 + Math.random() * 0.2)),
      TREE_SHADE: Math.min(1, base * (0.7 + Math.random() * 0.5)),
      BUFFER_RATIO: Math.min(1, base * (0.85 + Math.random() * 0.3)),
      TRANSIT_PROX: Math.min(1, base * (0.8 + Math.random() * 0.4)),
      CROSS_PROV: Math.min(1, base * (0.9 + Math.random() * 0.2)),
      ENTR_DENS: Math.min(1, base * (0.75 + Math.random() * 0.5)),
      POI_MIX: Math.min(1, base * (0.8 + Math.random() * 0.4)),
      MICRO_ENV: Math.min(1, base * (0.85 + Math.random() * 0.3)),
    };
  }

  function renderEvalSummary(): void {
    const summaryEl = requireElement<HTMLDivElement>(app, "#eval-summary");

    if (!state.selectedSchemeId || state.evaluations.length === 0) {
      summaryEl.innerHTML = `
        <div class="summary-empty">选择一个方案查看详细评估</div>
      `;
      return;
    }

    const selectedEval = state.evaluations.find((e) => e.sceneId === state.selectedSchemeId);
    if (!selectedEval) return;

    const scores = selectedEval.scores;
    summaryEl.innerHTML = `
      <div class="summary-selected">
        <div class="selected-label">已选择: ${escapeHtml(`方案 ${state.selectedSchemeId}`)}</div>
        <div class="selected-overall">
          <span class="overall-number">${scores.overall}</span>
          <span class="overall-label">综合评分</span>
        </div>
      </div>
    `;
  }

  function renderRadarChart(): void {
    const canvas = requireElement<HTMLCanvasElement>(app, "#radar-chart");
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { size, padding, labelOffset } = CHART_CONFIG.radar;
    const center = size / 2;
    const radius = (size - padding * 2) / 2;
    const labels = ["步行性", "安全性", "美观度"];

    // Clear canvas
    ctx.clearRect(0, 0, size, size);

    // Draw background circles
    ctx.strokeStyle = "#e5e7eb";
    ctx.lineWidth = 1;
    for (let i = 1; i <= 5; i++) {
      const r = (radius * i) / 5;
      ctx.beginPath();
      for (let j = 0; j <= 6; j++) {
        const angle = (Math.PI * 2 * j) / 6 - Math.PI / 2;
        const x = center + r * Math.cos(angle);
        const y = center + r * Math.sin(angle);
        if (j === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.stroke();
    }

    // Draw axis lines
    ctx.strokeStyle = "#d1d5db";
    for (let i = 0; i < 3; i++) {
      const angle = (Math.PI * 2 * i) / 3 - Math.PI / 2;
      ctx.beginPath();
      ctx.moveTo(center, center);
      ctx.lineTo(center + radius * Math.cos(angle), center + radius * Math.sin(angle));
      ctx.stroke();
    }

    // Draw labels
    ctx.fillStyle = "#374151";
    ctx.font = "14px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (let i = 0; i < 3; i++) {
      const angle = (Math.PI * 2 * i) / 3 - Math.PI / 2;
      const x = center + (radius + labelOffset) * Math.cos(angle);
      const y = center + (radius + labelOffset) * Math.sin(angle);
      ctx.fillText(labels[i], x, y);
    }

    // Draw data polygons
    const colors = [EVALUATION_COLORS.walkability.primary, EVALUATION_COLORS.safety.primary, EVALUATION_COLORS.beauty.primary];

    state.evaluations.forEach((eval_, index) => {
      const scores = [eval_.scores.walkability, eval_.scores.safety, eval_.scores.beauty];
      const color = index < 3 ? [SCHEME_COLORS.A, SCHEME_COLORS.B, SCHEME_COLORS.C][index] : colors[index % 3];

      // Draw polygon
      ctx.beginPath();
      for (let i = 0; i < 3; i++) {
        const angle = (Math.PI * 2 * i) / 3 - Math.PI / 2;
        const value = scores[i] / 100;
        const x = center + radius * value * Math.cos(angle);
        const y = center + radius * value * Math.sin(angle);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.fillStyle = `${color}30`;
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();

      // Draw data points
      for (let i = 0; i < 3; i++) {
        const angle = (Math.PI * 2 * i) / 3 - Math.PI / 2;
        const value = scores[i] / 100;
        const x = center + radius * value * Math.cos(angle);
        const y = center + radius * value * Math.sin(angle);
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
      }
    });

    // Draw legend
    ctx.font = "12px system-ui, sans-serif";
    state.evaluations.forEach((eval_, index) => {
      const color = [SCHEME_COLORS.A, SCHEME_COLORS.B, SCHEME_COLORS.C][index];
      const y = size - 10;
      const x = center - 50 + index * 50;
      ctx.fillStyle = color;
      ctx.fillRect(x - 10, y - 6, 12, 12);
      ctx.fillStyle = "#374151";
      ctx.textAlign = "left";
      ctx.fillText(`方案 ${eval_.sceneId}`, x + 5, y + 4);
    });
  }

  function renderBarChart(): void {
    const canvas = requireElement<HTMLCanvasElement>(app, "#bar-chart");
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    const barWidth = CHART_CONFIG.bar.barWidth;
    const barGap = CHART_CONFIG.bar.barGap;
    const labelOffset = CHART_CONFIG.bar.labelOffset;
    const labels = ["步行性", "安全性", "美观度"];

    // Clear canvas
    ctx.clearRect(0, 0, width, height);

    const chartHeight = height - labelOffset * 2;
    const maxValue = 100;
    const groupWidth = (barWidth + barGap) * state.evaluations.length + barGap;
    const startX = (width - groupWidth * 3 - barGap * 2) / 2;

    // Draw Y axis
    ctx.strokeStyle = "#e5e7eb";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(startX, labelOffset);
    ctx.lineTo(startX, height - labelOffset);
    ctx.lineTo(width - startX, height - labelOffset);
    ctx.stroke();

    // Draw Y axis labels and grid lines
    ctx.fillStyle = "#6b7280";
    ctx.font = "10px system-ui, sans-serif";
    ctx.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const y = labelOffset + (chartHeight * (4 - i)) / 4;
      const value = i * 25;
      ctx.fillText(value.toString(), startX - 5, y + 3);

      ctx.strokeStyle = "#f3f4f6";
      ctx.beginPath();
      ctx.moveTo(startX, y);
      ctx.lineTo(width - startX, y);
      ctx.stroke();
    }

    // Draw bars
    const schemeColors = [SCHEME_COLORS.A, SCHEME_COLORS.B, SCHEME_COLORS.C];
    labels.forEach((label, labelIndex) => {
      const groupX = startX + labelIndex * (groupWidth + barGap);

      // Draw label
      ctx.fillStyle = "#374151";
      ctx.font = "12px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(label, groupX + groupWidth / 2, height - 5);

      state.evaluations.forEach((eval_, evalIndex) => {
        const values = [eval_.scores.walkability, eval_.scores.safety, eval_.scores.beauty];
        const value = values[labelIndex];
        const barHeight = (value / maxValue) * chartHeight;
        const x = groupX + evalIndex * (barWidth + barGap) + barGap;
        const y = height - labelOffset - barHeight;

        // Draw bar
        ctx.fillStyle = schemeColors[evalIndex];
        ctx.fillRect(x, y, barWidth, barHeight);

        // Draw value on top
        ctx.fillStyle = "#374151";
        ctx.font = "10px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(value.toString(), x + barWidth / 2, y - 5);
      });
    });

    // Draw legend
    const legendY = 15;
    ctx.font = "11px system-ui, sans-serif";
    state.evaluations.forEach((eval_, index) => {
      const x = width - 100 + index * 50;
      ctx.fillStyle = schemeColors[index];
      ctx.fillRect(x, legendY - 8, 10, 10);
      ctx.fillStyle = "#374151";
      ctx.textAlign = "left";
      ctx.fillText(`方案 ${eval_.sceneId}`, x + 14, legendY);
    });
  }

  function renderIndicatorsTable(): void {
    const tableEl = requireElement<HTMLDivElement>(app, "#indicators-table");

    if (state.evaluations.length === 0) {
      tableEl.innerHTML = `<div class="table-empty">暂无指标数据</div>`;
      return;
    }

    const indicatorKeys = Object.keys(WALKABILITY_INDICATORS);
    const rows = indicatorKeys.map((key) => {
      const meta = WALKABILITY_INDICATORS[key as keyof typeof WALKABILITY_INDICATORS];
      const values = state.evaluations.map((e) => {
        const raw = (e.indicators as unknown as Record<string, number>)[key] || 0;
        return Math.round(raw * 100);
      });
      const avg = values.length > 0 ? Math.round(values.reduce((a, b) => a + b, 0) / values.length) : 0;

      return `
        <tr>
          <td class="indicator-name">
            <div class="indicator-label">${escapeHtml(meta.label)}</div>
            <div class="indicator-key">${escapeHtml(key)}</div>
          </td>
          ${values.map((v, i) => `
            <td class="indicator-value scheme-${state.evaluations[i].sceneId}">${v}</td>
          `).join("")}
          <td class="indicator-avg">${avg}</td>
        </tr>
      `;
    }).join("");

    const headerCells = state.evaluations.map((e) =>
      `<th class="scheme-header" style="border-left: 3px solid ${SCHEME_COLORS[e.sceneId as keyof typeof SCHEME_COLORS]}">方案 ${escapeHtml(e.sceneId)}</th>`
    ).join("");

    tableEl.innerHTML = `
      <table class="indicators-full-table">
        <thead>
          <tr>
            <th class="indicator-col">指标</th>
            ${headerCells}
            <th class="avg-col">平均</th>
          </tr>
        </thead>
        <tbody>
          ${rows}
        </tbody>
      </table>
    `;
  }
}
