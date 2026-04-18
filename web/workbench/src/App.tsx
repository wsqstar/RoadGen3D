import { useState, useMemo, useCallback } from "react";
import { ConfigProvider } from "antd";
import type { ScenePreset, GeneratedScheme, EvaluationResult, WorkflowStep } from "./lib/types";
import type { DraftResponse } from "./lib/api";
import { Header } from "./components/Header";
import { PresetGrid } from "./components/PresetGrid";
import { SchemeGrid } from "./components/SchemeGrid";
import { EvaluationPanel } from "./components/EvaluationPanel";
import { StatusBar } from "./components/StatusBar";
import { FreeTextInput } from "./components/FreeTextInput";
import { useGeneration, useScenePresets } from "./hooks/useGeneration";
import { antdTheme } from "./theme";
import { DEFAULT_GRAPH_TEMPLATE_ID } from "./lib/types";
import "./App.css";

type InputMode = "preset" | "free_text";

interface ParameterSource {
  key: string;
  value: string | number;
  source: "user" | "ai_inferred";
}

function App() {
  const [currentStep, setCurrentStep] = useState<WorkflowStep>(1);
  const [inputMode, setInputMode] = useState<InputMode>("preset");
  const [selectedPreset, setSelectedPreset] = useState<ScenePreset | null>(null);
  const [customDraft, setCustomDraft] = useState<DraftResponse | null>(null);
  const [parameterSources, setParameterSources] = useState<ParameterSource[]>([]);
  const [schemes, setSchemes] = useState<GeneratedScheme[]>([]);
  const [selectedSchemeId, setSelectedSchemeId] = useState<string | null>(null);
  const [evaluations, setEvaluations] = useState<EvaluationResult[]>([]);
  const [status, setStatus] = useState<string>("就绪");

  // Load presets and templates from API
  const { presets, templates, loading: loadingPresets } = useScenePresets();
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>(DEFAULT_GRAPH_TEMPLATE_ID);

  const { generationState, generateSchemes, generateFromDraft, applyAndRegenerate } = useGeneration(
    setStatus,
    selectedTemplateId,
    presets
  );

  const [isOptimizing, setIsOptimizing] = useState(false);

  const isGenerating = generationState.type === "generating";

  const displaySchemes = useMemo(() => {
    if (generationState.type === "generating" || generationState.type === "done") {
      return generationState.schemes;
    }
    return schemes;
  }, [generationState, schemes]);

  const handleOptimizeScheme = useCallback(async (schemeId: string, patch: Record<string, any>) => {
    const scheme = displaySchemes.find(s => s.id === schemeId);
    if (!scheme) return;

    setIsOptimizing(true);
    try {
      const newSchemes = await applyAndRegenerate(patch, scheme, displaySchemes);
      setSchemes(newSchemes);
      setEvaluations(newSchemes.filter(s => s.status === "ready").map(s => ({
        sceneId: s.id,
        scores: s.evaluation,
        indicators: s.indicators!,
        pillarScores: { Protection: 0, Comfort: 0, Delight: 0 },
      })));
    } catch (error) {
      console.error("Optimization failed:", error);
    } finally {
      setIsOptimizing(false);
    }
  }, [applyAndRegenerate, displaySchemes]);

  const hasReadySchemes = displaySchemes.some((s) => s.status === "ready");

  const handleSelectPreset = useCallback((preset: ScenePreset) => {
    setSelectedPreset(preset);
    setCustomDraft(null);
    setParameterSources([]);
  }, []);

  const handleDraftCreated = useCallback((draft: DraftResponse, sources: ParameterSource[]) => {
    setCustomDraft(draft);
    setSelectedPreset(null);
    setParameterSources(sources);
  }, []);

  const handleGenerate = useCallback(async () => {
    if (inputMode === "free_text" && customDraft) {
      const result = await generateFromDraft(customDraft);
      setSchemes(result);
    } else if (selectedPreset) {
      const result = await generateSchemes(selectedPreset);
      setSchemes(result);
    }
  }, [inputMode, customDraft, selectedPreset, generateSchemes, generateFromDraft]);

  const handleSwitchToFreeText = useCallback(() => {
    setInputMode("free_text");
    setSelectedPreset(null);
    setCustomDraft(null);
  }, []);

  const handleSwitchToPreset = useCallback(() => {
    setInputMode("preset");
    setCustomDraft(null);
  }, []);

  const handleShowEvaluation = useCallback(() => {
    const readySchemes = displaySchemes.filter((s) => s.status === "ready");
    const newEvaluations: EvaluationResult[] = readySchemes.map((scheme) => ({
      sceneId: scheme.id,
      scores: scheme.evaluation,
      indicators: scheme.indicators || {
        SID_CLR: 0,
        CLEAR_CONT: 0,
        FURN_D: 0,
        LIGHT_UNI: 0,
        TREE_SHADE: 0,
        BUFFER_RATIO: 0,
        TRANSIT_PROX: 0,
        CROSS_PROV: 0,
        ENTR_DENS: 0,
        POI_MIX: 0,
        MICRO_ENV: 0,
      },
      pillarScores: {
        Protection: scheme.evaluation.safety,
        Comfort: scheme.evaluation.walkability,
        Delight: scheme.evaluation.beauty,
      },
    }));
    setEvaluations(newEvaluations);
    setCurrentStep(3);
    setStatus("评估结果已生成");
  }, [displaySchemes]);

  const handleExportScene = useCallback(() => {
    if (!selectedSchemeId) return;
    const scheme = displaySchemes.find((s) => s.id === selectedSchemeId);
    if (scheme?.viewerUrl) {
      window.open(scheme.viewerUrl, "_blank");
    }
  }, [selectedSchemeId, displaySchemes]);

  return (
    <ConfigProvider theme={antdTheme}>
      <div className="workbench">
        <Header
          currentStep={currentStep}
          templates={templates}
          selectedTemplateId={selectedTemplateId}
          onTemplateChange={setSelectedTemplateId}
        />

        <main className="workbench-content">
        {currentStep === 1 && (
          <section className="step-content">
            <div className="section-header">
              <h2>选择输入方式</h2>
              <p className="section-desc">使用预设模板快速生成，或用自然语言描述你的需求</p>
            </div>

            <div className="input-mode-toggle">
              <button
                className={`mode-btn ${inputMode === "preset" ? "active" : ""}`}
                onClick={handleSwitchToPreset}
              >
                📋 预设模板
              </button>
              <button
                className={`mode-btn ${inputMode === "free_text" ? "active" : ""}`}
                onClick={handleSwitchToFreeText}
              >
                ✏️ 自由描述
              </button>
            </div>

            {inputMode === "preset" ? (
              <>
                {loadingPresets ? (
                  <div className="loading-presets">加载预设模板...</div>
                ) : (
                  <PresetGrid
                    selectedPreset={selectedPreset}
                    onSelect={handleSelectPreset}
                    presets={presets}
                  />
                )}
                <div className="step-actions">
                  <button
                    className="btn primary"
                    onClick={handleGenerate}
                    disabled={!selectedPreset || isGenerating}
                  >
                    生成 3 个方案
                  </button>
                </div>
              </>
            ) : (
              <>
                {customDraft ? (
                  <div className="draft-preview">
                    <div className="draft-header">
                      <span className="draft-badge">✓ 草案已生成</span>
                      <button className="btn secondary" onClick={handleSwitchToFreeText}>
                        重新描述
                      </button>
                    </div>
                    <div className="draft-content">
                      <h4>设计摘要</h4>
                      <p>{customDraft.design_summary || "无"}</p>
                      <h4>参数来源</h4>
                      <div className="draft-params">
                        {parameterSources.map(({ key, value, source }) => (
                          <span key={key} className={`param-tag ${source === "user" ? "param-user" : "param-ai"}`}>
                            {key}: {String(value)}
                            {source === "user" ? " (用户)" : " (AI推断)"}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div className="step-actions">
                      <button
                        className="btn primary"
                        onClick={handleGenerate}
                        disabled={isGenerating}
                      >
                        生成 3 个方案
                      </button>
                    </div>
                  </div>
                ) : (
                  <FreeTextInput
                    onDraftCreated={handleDraftCreated}
                    onCancel={handleSwitchToPreset}
                    onStatusChange={setStatus}
                  />
                )}
              </>
            )}
          </section>
        )}

        {currentStep === 2 && (
          <section className="step-content">
            <div className="section-header">
              <h2>方案对比</h2>
              <p className="section-desc">
                {isGenerating ? "正在生成 3 个方案，请稍候..." : "生成完成，点击卡片选择方案"}
              </p>
            </div>
            <SchemeGrid
              schemes={displaySchemes}
              selectedSchemeId={selectedSchemeId}
              onSelectScheme={setSelectedSchemeId}
            />
            <div className="step-actions">
              <button className="btn secondary" onClick={() => setCurrentStep(1)}>
                重新选择模板
              </button>
              <button
                className="btn primary"
                onClick={handleShowEvaluation}
                disabled={!hasReadySchemes}
              >
                查看评估结果
              </button>
            </div>
          </section>
        )}

        {currentStep === 3 && (
          <section className="step-content">
            <div className="section-header">
              <h2>评估可视化</h2>
              <p className="section-desc">查看方案的详细评估结果，对比多维度指标</p>
            </div>
            <EvaluationPanel
              evaluations={evaluations}
              selectedSchemeId={selectedSchemeId}
              onOptimize={handleOptimizeScheme}
              isOptimizing={isOptimizing}
            />
            <div className="step-actions">
              <button className="btn secondary" onClick={() => setCurrentStep(2)}>
                返回方案对比
              </button>
              <button
                className="btn primary"
                onClick={handleExportScene}
                disabled={!selectedSchemeId}
              >
                导出 3D 场景
              </button>
            </div>
          </section>
        )}
      </main>

      <StatusBar message={status} />
      </div>
    </ConfigProvider>
  );
}

export default App;
