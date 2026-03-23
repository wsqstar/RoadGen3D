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

type FieldConfig = {
  key: string;
  label: string;
  type: "text" | "number" | "select";
  options?: string[];
};

const API_BASE = (import.meta.env.VITE_ROADGEN_API_BASE as string | undefined) || "http://127.0.0.1:8010";
const VIEWER_BASE = (import.meta.env.VITE_ROADGEN_VIEWER_BASE as string | undefined) || "http://127.0.0.1:4173";
const FIELD_CONFIGS: FieldConfig[] = [
  { key: "query", label: "Scene Query", type: "text" },
  { key: "design_rule_profile", label: "Rule Profile", type: "select", options: ["balanced_complete_street_v1", "pedestrian_priority_v1", "transit_priority_v1"] },
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
  };

  app.innerHTML = `
    <div class="shell">
      <section class="hero">
        <h1>RoadGen3D Street Workbench</h1>
        <p>
          生成工作台和 3D viewer 分开运行。这里负责对话、RAG、参数确认和生成触发；空间浏览与漫游交给独立 viewer。
        </p>
        <div class="hero-grid">
          <div class="hero-chip">1. Intent Clarification</div>
          <div class="hero-chip">2. PDF RAG Evidence</div>
          <div class="hero-chip">3. Parameter Confirmation</div>
          <div class="hero-chip">4. Generate</div>
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
                <button id="generate-btn" class="btn primary" disabled>确认参数并生成街道</button>
              </div>
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>Generation Result</h2>
            </div>
            <div class="panel-body">
              <div id="generation-result" class="result-box">尚未触发生成。</div>
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
  const intentBox = requireElement<HTMLDivElement>(app, "#intent-box");
  const evidenceList = requireElement<HTMLDivElement>(app, "#evidence-list");
  const draftSummary = requireElement<HTMLDivElement>(app, "#draft-summary");
  const parameterForm = requireElement<HTMLDivElement>(app, "#parameter-form");
  const generateBtn = requireElement<HTMLButtonElement>(app, "#generate-btn");
  const generationResult = requireElement<HTMLDivElement>(app, "#generation-result");

  renderTimeline();
  renderParameterForm({});

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
      promptInput.value = "";
      renderTimeline();
      renderDraft(payload);
      setStatus(payload.warnings.length ? payload.warnings.join("\n") : "设计草案已生成，请确认参数后再触发生成。");
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
    setStatus("正在调用现有场景生成链路...");
    try {
      const draft = buildDraftFromForm(state.lastDraft.draft, parameterForm);
      const payload = await postJson<GenerationResponse>("/api/design/generate", {
        draft,
        patch_overrides: {},
        generation_options: {},
      });
      state.lastGeneration = payload;
      renderGeneration(payload);
      setStatus("生成完成，可以在独立 viewer 中查看结果。");
    } catch (error) {
      setStatus(asErrorMessage(error));
    } finally {
      generateBtn.disabled = false;
    }
  });

  function renderDraft(payload: DraftResponse): void {
    generateBtn.disabled = false;
    renderIntent(payload.intent);
    renderEvidence(payload.evidence, payload.draft.citations_by_field);
    renderParameterForm(payload.draft.compose_config_patch, payload.draft.citations_by_field);
    draftSummary.textContent = formatDraftSummary(payload.draft);
    generationResult.textContent = "尚未触发生成。";
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
  ): void {
    parameterForm.innerHTML = FIELD_CONFIGS.map((field) => {
      const value = patch[field.key] ?? "";
      const citations = (citationsByField[field.key] || []).join(", ");
      if (field.type === "select") {
        const options = (field.options || [])
          .map((option) => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`)
          .join("");
        return `
          <div class="field">
            <label for="field-${field.key}">${escapeHtml(field.label)}</label>
            <select id="field-${field.key}" data-key="${escapeHtml(field.key)}">${options}</select>
            <div class="field-note">citations: ${escapeHtml(citations || "none")}</div>
          </div>
        `;
      }
      return `
        <div class="field">
          <label for="field-${field.key}">${escapeHtml(field.label)}</label>
          <input id="field-${field.key}" data-key="${escapeHtml(field.key)}" type="${field.type}" value="${escapeHtml(String(value))}" />
          <div class="field-note">citations: ${escapeHtml(citations || "none")}</div>
        </div>
      `;
    }).join("");
  }

  function renderGeneration(result: GenerationResponse): void {
    const viewerHref = result.viewer_url || VIEWER_BASE;
    const links = [
      `<div><a href="${escapeHtml(viewerHref)}" target="_blank" rel="noreferrer">Open Viewer</a></div>`,
      result.scene_layout_path ? `<div class="mono">layout: ${escapeHtml(result.scene_layout_path)}</div>` : "",
      result.scene_glb_path ? `<div class="mono">glb: ${escapeHtml(result.scene_glb_path)}</div>` : "",
    ]
      .filter(Boolean)
      .join("");
    generationResult.innerHTML = `
      <div><strong>Summary</strong></div>
      <pre class="mono">${escapeHtml(JSON.stringify(result.summary, null, 2))}</pre>
      ${links}
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
  FIELD_CONFIGS.forEach((field) => {
    const input = parameterForm.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-key="${field.key}"]`);
    if (!input) {
      return;
    }
    const raw = input.value.trim();
    if (!raw) {
      return;
    }
    composeConfigPatch[field.key] = field.type === "number" ? Number(raw) : raw;
  });
  return {
    ...baseDraft,
    normalized_scene_query: String(composeConfigPatch.query || baseDraft.normalized_scene_query),
    compose_config_patch: composeConfigPatch,
  };
}

function formatDraftSummary(draft: DesignDraft): string {
  return [
    draft.design_summary || "No summary returned.",
    draft.risk_notes.length ? `\nRisk Notes:\n- ${draft.risk_notes.join("\n- ")}` : "",
  ].join("");
}

function renderTagRow(items: string[]): string {
  if (!items.length) {
    return `<div class="field-note">none</div>`;
  }
  return `<div class="tag-row">${items.map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("")}</div>`;
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
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with status ${response.status}`);
  }
  return (await response.json()) as T;
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
