import { useState, useMemo, useCallback } from "react";
import type { ScenePreset, GeneratedScheme, EvaluationResult, WorkflowStep } from "./lib/types";
import { Header } from "./components/Header";
import { PresetGrid } from "./components/PresetGrid";
import { SchemeGrid } from "./components/SchemeGrid";
import { EvaluationPanel } from "./components/EvaluationPanel";
import { StatusBar } from "./components/StatusBar";
import { useGeneration } from "./hooks/useGeneration";
import "./App.css";

function App() {
  const [currentStep, setCurrentStep] = useState<WorkflowStep>(1);
  const [selectedPreset, setSelectedPreset] = useState<ScenePreset | null>(null);
  const [schemes, setSchemes] = useState<GeneratedScheme[]>([]);
  const [selectedSchemeId, setSelectedSchemeId] = useState<string | null>(null);
  const [evaluations, setEvaluations] = useState<EvaluationResult[]>([]);
  const [status, setStatus] = useState<string>("就绪");

  const { generationState, generateSchemes } = useGeneration(setStatus);

  const isGenerating = generationState.type === "generating";

  const displaySchemes = useMemo(() => {
    if (generationState.type === "generating" || generationState.type === "done") {
      return generationState.schemes;
    }
    return schemes;
  }, [generationState, schemes]);

  const hasReadySchemes = displaySchemes.some((s) => s.status === "ready");

  const handleSelectPreset = useCallback((preset: ScenePreset) => {
    setSelectedPreset(preset);
  }, []);

  const handleGenerate = useCallback(async () => {
    if (!selectedPreset) return;
    const result = await generateSchemes(selectedPreset);
    setSchemes(result);
  }, [selectedPreset, generateSchemes]);

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
    <div className="workbench">
      <Header currentStep={currentStep} />

      <main className="workbench-content">
        {currentStep === 1 && (
          <section className="step-content">
            <div className="section-header">
              <h2>选择街道场景模板</h2>
              <p className="section-desc">选择一个预设场景，我将自动生成 3 个不同方案供您对比</p>
            </div>
            <PresetGrid selectedPreset={selectedPreset} onSelect={handleSelectPreset} />
            <div className="step-actions">
              <button
                className="btn primary"
                onClick={handleGenerate}
                disabled={!selectedPreset || isGenerating}
              >
                生成 3 个方案
              </button>
            </div>
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
            <EvaluationPanel evaluations={evaluations} selectedSchemeId={selectedSchemeId} />
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
  );
}

export default App;
