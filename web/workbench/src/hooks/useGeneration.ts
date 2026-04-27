import { useState, useCallback, useEffect } from "react";
import type { ScenePreset, GeneratedScheme } from "../lib/types";
import type { DraftResponse } from "../lib/api";
import { DEFAULT_GRAPH_TEMPLATE_ID, POLL_INTERVAL_MS, MAX_GENERATION_ATTEMPTS, FALLBACK_SCENE_PRESETS } from "../lib/types";
import { fetchPresets, fetchGraphTemplates, postJson, getJson, evaluateScene, resolveApiUrl } from "../lib/api";
import { resolveViewerUrl, sleep } from "../lib/utils";

// 方案变体定义：确保A/B/C有显著差异
const SCHEME_VARIANTS = {
  A: { seed: 42, densityMod: 1.0, widthMod: 1.0 },    // 基准方案
  B: { seed: 137, densityMod: 1.2, widthMod: 0.9 },    // 紧凑高密度方案
  C: { seed: 256, densityMod: 0.8, widthMod: 1.1 },    // 舒展低密度方案
};

export type GenerationState =
  | { type: "idle" }
  | { type: "loading_presets"; }
  | { type: "generating"; schemes: GeneratedScheme[] }
  | { type: "done"; schemes: GeneratedScheme[] }
  | { type: "error"; message: string };

// 进度阶段定义
type JobProgress = {
  stage: string;        // 当前阶段: "queued", "composing", "rendering", "exporting", "succeeded"
  progress: number;      // 0-100
  message: string;       // 友好的状态描述
};

export function useScenePresets() {
  const [presets, setPresets] = useState<ScenePreset[]>(FALLBACK_SCENE_PRESETS);
  const [templates, setTemplates] = useState<{ template_id: string; label: string }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([fetchPresets(), fetchGraphTemplates()]).then(([presets, templates]) => {
      if (presets.length > 0) {
        setPresets(presets);
      }
      setTemplates(templates);
      setLoading(false);
    }).catch(() => {
      setLoading(false);
    });
  }, []);

  return { presets, templates, loading };
}

export function useGeneration(
  onStatusChange: (message: string) => void,
  graphTemplateId: string = DEFAULT_GRAPH_TEMPLATE_ID,
  presets: ScenePreset[] = FALLBACK_SCENE_PRESETS
) {
  const [generationState, setGenerationState] = useState<GenerationState>({ type: "idle" });

  const generateSchemes = useCallback(async (selectedPreset: ScenePreset): Promise<GeneratedScheme[]> => {
    onStatusChange("正在初始化生成任务...");

    const schemeIds = ["A", "B", "C"];
    const initialSchemes: GeneratedScheme[] = schemeIds.map((id) => ({
      id,
      name: `方案 ${id}`,
      presetId: selectedPreset.id,
      layoutPath: "",
      previewUrl: "",
      viewerUrl: "",
      evaluation: { walkability: 0, safety: 0, beauty: 0, overall: 0 },
      indicators: null,
      evaluationText: "",
      suggestions: [],
      llmStatus: null,
      status: "generating",
      progress: 0,
    }));

    setGenerationState({ type: "generating", schemes: initialSchemes });

    const updatedSchemes = [...initialSchemes];

    for (let i = 0; i < updatedSchemes.length; i++) {
      const scheme = updatedSchemes[i];
      
      // 获取当前方案的变体配置（种子 + 参数微扰）
      const variant = SCHEME_VARIANTS[scheme.id as keyof typeof SCHEME_VARIANTS];
      const baseConfig = selectedPreset.configPatch;
      
      // 计算微扰后的参数（限制在合理范围内）
      const perturbedConfig = {
        ...baseConfig,
        density: Math.max(0.1, Math.min(1.5, Number(baseConfig.density || 0.6) * variant.densityMod)),
        road_width_m: Math.max(5.0, Math.min(30.0, Number(baseConfig.road_width_m || 13.5) * variant.widthMod)),
      };

      try {
        // 进度更新回调 - 实时反映真实进度
        const updateProgress = (prog: JobProgress) => {
          scheme.progress = prog.progress;
          onStatusChange(`方案 ${scheme.id}: ${prog.message}`);
          setGenerationState({ type: "generating", schemes: [...updatedSchemes] });
        };

        // 传入变体参数：种子和微扰配置
        const result = await createSceneJob(
          selectedPreset, 
          scheme.id, 
          updateProgress, 
          variant.seed, 
          perturbedConfig
        );
        scheme.layoutPath = result.scene_layout_path;
        scheme.viewerUrl = resolveViewerUrl(result.viewer_url, result.scene_layout_path);
        scheme.previewUrl = resolveApiUrl(result.scene_layout_path);

        // 评估阶段
        updateProgress({ stage: "evaluating", progress: 90, message: "正在评估..." });
        try {
          const evalResult = await evaluateScene(scheme.layoutPath);
          if (evalResult) {
            scheme.evaluation = evalResult.scores;
            scheme.indicators = evalResult.indicators || scheme.indicators;
            scheme.evaluationText = evalResult.evaluation;
            scheme.suggestions = evalResult.suggestions;
            scheme.llmStatus = evalResult.llm_status || null;
          } else {
            scheme.evaluation = { walkability: -1, safety: -1, beauty: -1, overall: -1 };
            scheme.llmStatus = null;
          }
        } catch (evalError) {
          console.error(`方案 ${scheme.id} 评估失败:`, evalError);
          scheme.evaluation = { walkability: -1, safety: -1, beauty: -1, overall: -1 };
          scheme.llmStatus = null;
        }

        scheme.status = "ready";
        scheme.progress = 100;
        setGenerationState({ type: "generating", schemes: [...updatedSchemes] });
      } catch (error) {
        console.error(`方案 ${scheme.id} 生成失败:`, error);
        scheme.status = "failed";
        scheme.progress = 0;
        setGenerationState({ type: "generating", schemes: [...updatedSchemes] });
      }
    }

    const successCount = updatedSchemes.filter((s) => s.status === "ready").length;
    onStatusChange(`已生成 ${successCount}/3 个方案`);
    setGenerationState({ type: "done", schemes: [...updatedSchemes] });
    return updatedSchemes;
  }, [onStatusChange]);

  async function createSceneJob(
    preset: ScenePreset,
    seedSuffix: string,
    onProgress?: (prog: JobProgress) => void,
    randomSeed?: number,
    perturbedConfig?: Record<string, any>
  ): Promise<{
    scene_layout_path: string;
    scene_glb_path: string;
    viewer_url: string;
  }> {
    const response = await postJson<{
      job_id: string;
      status: string;
      created_at: string;
    }>("/api/scene/jobs", {
      draft: {
        normalized_scene_query: preset.prompt,
        compose_config_patch: perturbedConfig || preset.configPatch,
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
        graph_template_id: graphTemplateId,
      },
      patch_overrides: {},
      generation_options: { preset_id: preset.id, random_seed: randomSeed },
    }, 60000);

    return await pollJobCompletion(response.job_id, onProgress);
  }

  async function pollJobCompletion(
    jobId: string,
    onProgress?: (prog: JobProgress) => void
  ): Promise<{
    scene_layout_path: string;
    scene_glb_path: string;
    viewer_url: string;
  }> {
    // 阶段到进度的映射
    const stageToProgress = (stage: string, status: string): JobProgress => {
      switch (status) {
        case "queued":
          return { stage: "queued", progress: 5, message: "任务排队中..." };
        case "running":
        case "processing":
          switch (stage) {
            case "context_resolving":
              return { stage: "context_resolving", progress: 15, message: "正在解析场景上下文..." };
            case "asset_loading":
              return { stage: "asset_loading", progress: 25, message: "正在加载资产..." };
            case "layout_generation":
              return { stage: "layout_generation", progress: 40, message: "正在生成布局..." };
            case "constraint_solving":
              return { stage: "constraint_solving", progress: 45, message: "正在求解约束..." };
            case "asset_composition":
              return { stage: "asset_composition", progress: 55, message: "正在组合资产..." };
            case "mesh_generation":
              return { stage: "mesh_generation", progress: 65, message: "正在生成网格..." };
            case "scene_rendering":
              return { stage: "scene_rendering", progress: 75, message: "正在渲染场景..." };
            case "glb_export":
              return { stage: "glb_export", progress: 85, message: "正在导出 GLB..." };
            case "finalizing":
              return { stage: "finalizing", progress: 98, message: "正在整理结果..." };
            default:
              return { stage: stage || "processing", progress: 50, message: "正在处理中..." };
          }
        case "succeeded":
          return { stage: "succeeded", progress: 100, message: "生成完成!" };
        case "failed":
          return { stage: "failed", progress: 0, message: "生成失败" };
        default:
          return { stage: status, progress: 10, message: "等待中..." };
      }
    };

    for (let i = 0; i < MAX_GENERATION_ATTEMPTS; i++) {
      try {
        const status = await getJson<{
          job_id: string;
          status: string;
          stage?: string;
          progress?: number;
          operations?: Array<string | { name?: string; status?: string; message?: string }>;
          result: {
            scene_layout_path: string;
            scene_glb_path: string;
            viewer_url: string;
          } | null;
        }>(`/api/scene/jobs/${jobId}`, 10000);

        // 计算真实进度
        const baseProg = stageToProgress(status.stage || "", status.status);
        let progress = baseProg.progress;

        // 如果 API 返回了 progress 字段，直接使用
        if (status.progress !== undefined && status.progress > 0) {
          progress = Math.round(status.progress);
        }

        // 获取当前操作信息
        let message = baseProg.message;
        if (status.operations && status.operations.length > 0) {
          const currentOp = status.operations[status.operations.length - 1];
          if (typeof currentOp === "string") {
            message = currentOp;
          } else if (typeof currentOp === "object" && currentOp !== null) {
            const opName = (currentOp as { message?: string }).message || (currentOp as { name?: string }).name || (currentOp as { status?: string }).status || baseProg.message;
            message = opName;
          }
        }

        // 回调更新进度
        if (onProgress) {
          onProgress({ stage: baseProg.stage, progress, message });
        }

        if (status.status === "succeeded" && status.result) {
          return status.result;
        }
        if (status.status === "failed") {
          throw new Error("Job failed");
        }
      } catch {
        // Continue polling on error
      }
      await sleep(POLL_INTERVAL_MS);
    }
    throw new Error("Job timed out");
  }

  async function createSceneJobFromDraft(
    draft: DraftResponse,
    seedSuffix: string,
    onProgress?: (prog: JobProgress) => void,
    randomSeed?: number,
    perturbedConfig?: Record<string, any>
  ): Promise<{
    scene_layout_path: string;
    scene_glb_path: string;
    viewer_url: string;
  }> {
    const response = await postJson<{
      job_id: string;
      status: string;
      created_at: string;
    }>("/api/scene/jobs", {
      draft: {
        normalized_scene_query: draft.normalized_scene_query,
        compose_config_patch: perturbedConfig || draft.compose_config_patch,
        citations_by_field: draft.citations_by_field || {},
        design_summary: draft.design_summary,
        risk_notes: draft.risk_notes || [],
        parameter_sources_by_field: {},
      },
      scene_context: {
        layout_mode: "graph_template",
        aoi_bbox: null,
        city_name_en: null,
        reference_plan_id: null,
        graph_template_id: graphTemplateId,
      },
      patch_overrides: {},
      generation_options: { preset_id: "custom_draft", random_seed: randomSeed },
    }, 60000);

    return await pollJobCompletion(response.job_id, onProgress);
  }

  const generateFromDraft = useCallback(async (draft: DraftResponse): Promise<GeneratedScheme[]> => {
    onStatusChange("正在初始化生成任务...");

    const schemeIds = ["A", "B", "C"];
    const initialSchemes: GeneratedScheme[] = schemeIds.map((id) => ({
      id,
      name: `方案 ${id}`,
      presetId: "custom_draft",
      layoutPath: "",
      previewUrl: "",
      viewerUrl: "",
      evaluation: { walkability: 0, safety: 0, beauty: 0, overall: 0 },
      indicators: null,
      evaluationText: "",
      suggestions: [],
      llmStatus: null,
      status: "generating",
      progress: 0,
    }));

    setGenerationState({ type: "generating", schemes: initialSchemes });

    const updatedSchemes = [...initialSchemes];

    for (let i = 0; i < updatedSchemes.length; i++) {
      const scheme = updatedSchemes[i];
      
      // 获取方案变体 (与预设模式保持一致的差异性)
      const variant = SCHEME_VARIANTS[scheme.id as keyof typeof SCHEME_VARIANTS];
      const baseConfig = draft.compose_config_patch || {};
      
      // 计算微扰配置
      const perturbedConfig = {
        ...baseConfig,
        density: Math.max(0.1, Math.min(2.0, (Number(baseConfig.density) || 0.6) * variant.densityMod)),
        road_width_m: Math.max(5.0, Math.min(30.0, (Number(baseConfig.road_width_m) || 13.5) * variant.widthMod)),
      };

      try {
        const updateProgress = (prog: JobProgress) => {
          scheme.progress = prog.progress;
          onStatusChange(`方案 ${scheme.id}: ${prog.message}`);
          setGenerationState({ type: "generating", schemes: [...updatedSchemes] });
        };

        // 传入变体参数
        const result = await createSceneJobFromDraft(
          draft, 
          scheme.id, 
          updateProgress, 
          variant.seed, 
          perturbedConfig
        );
        scheme.layoutPath = result.scene_layout_path;
        scheme.viewerUrl = resolveViewerUrl(result.viewer_url, result.scene_layout_path);
        scheme.previewUrl = resolveApiUrl(result.scene_layout_path);

        updateProgress({ stage: "evaluating", progress: 90, message: "正在评估..." });
        try {
          const evalResult = await evaluateScene(scheme.layoutPath);
          if (evalResult) {
            scheme.evaluation = evalResult.scores;
            scheme.indicators = evalResult.indicators || scheme.indicators;
            scheme.evaluationText = evalResult.evaluation;
            scheme.suggestions = evalResult.suggestions;
            scheme.llmStatus = evalResult.llm_status || null;
          } else {
            scheme.evaluation = { walkability: -1, safety: -1, beauty: -1, overall: -1 };
            scheme.llmStatus = null;
          }
        } catch (evalError) {
          console.error(`方案 ${scheme.id} 评估失败:`, evalError);
          scheme.evaluation = { walkability: -1, safety: -1, beauty: -1, overall: -1 };
          scheme.llmStatus = null;
        }

        scheme.status = "ready";
        scheme.progress = 100;
        setGenerationState({ type: "generating", schemes: [...updatedSchemes] });
      } catch (error) {
        console.error(`方案 ${scheme.id} 生成失败:`, error);
        scheme.status = "failed";
        scheme.progress = 0;
        setGenerationState({ type: "generating", schemes: [...updatedSchemes] });
      }
    }

    const successCount = updatedSchemes.filter((s) => s.status === "ready").length;
    onStatusChange(`已生成 ${successCount}/3 个方案`);
    setGenerationState({ type: "done", schemes: [...updatedSchemes] });
    return updatedSchemes;
  }, [onStatusChange]);

  /**
   * Apply a config patch and regenerate the scene
   * This is the "一键优化" function
   */
  const applyAndRegenerate = useCallback(async (
    patch: Record<string, any>,
    targetScheme: GeneratedScheme,
    currentSchemes: GeneratedScheme[]
  ): Promise<GeneratedScheme[]> => {
    onStatusChange(`正在应用优化建议...`);

    // Create a copy of schemes with the target scheme in "generating" state
    const updatedSchemes = currentSchemes.map(s => 
      s.id === targetScheme.id 
        ? { ...s, status: "generating" as const, progress: 10, evaluation: { walkability: 0, safety: 0, beauty: 0, overall: 0 } }
        : s
    );
    setGenerationState({ type: "generating", schemes: updatedSchemes });

    try {
      // Get the current config patch from the preset or draft
      // For now, we'll use the target scheme's preset to get the base config
      const preset = presets.find(p => p.id === targetScheme.presetId);
      const baseConfig = preset?.configPatch || {};

      // Merge the patch with the base config
      const newConfig = { ...baseConfig, ...patch };

      // Create a new job with the patched config
      const result = await createSceneJobFromPatch(
        targetScheme.presetId,
        targetScheme.id,
        newConfig,
        (prog) => {
          const scheme = updatedSchemes.find(s => s.id === targetScheme.id);
          if (scheme) {
            scheme.progress = prog.progress;
            onStatusChange(`方案 ${targetScheme.id}: ${prog.message}`);
            setGenerationState({ type: "generating", schemes: [...updatedSchemes] });
          }
        }
      );

      // Update the scheme with the new result
      const schemeIndex = updatedSchemes.findIndex(s => s.id === targetScheme.id);
      if (schemeIndex !== -1) {
        const scheme = updatedSchemes[schemeIndex];
        scheme.layoutPath = result.scene_layout_path;
        scheme.viewerUrl = resolveViewerUrl(result.viewer_url, result.scene_layout_path);
        scheme.previewUrl = resolveApiUrl(result.scene_layout_path);

        // Evaluate the new scene
        onStatusChange(`正在评估优化后的方案...`);
        try {
          const evalResult = await evaluateScene(scheme.layoutPath);
          if (evalResult) {
            scheme.evaluation = evalResult.scores;
            scheme.indicators = evalResult.indicators || scheme.indicators;
            scheme.evaluationText = evalResult.evaluation;
            scheme.suggestions = evalResult.suggestions;
            scheme.llmStatus = evalResult.llm_status || null;
          }
        } catch (evalError) {
          console.error(`方案 ${targetScheme.id} 评估失败:`, evalError);
          scheme.llmStatus = null;
        }

        scheme.status = "ready";
        scheme.progress = 100;
      }

      setGenerationState({ type: "done", schemes: updatedSchemes });
      onStatusChange(`优化完成!`);
      return updatedSchemes;
    } catch (error) {
      console.error(`方案 ${targetScheme.id} 优化失败:`, error);
      const schemeIndex = updatedSchemes.findIndex(s => s.id === targetScheme.id);
      if (schemeIndex !== -1) {
        updatedSchemes[schemeIndex].status = "failed";
        updatedSchemes[schemeIndex].progress = 0;
      }
      setGenerationState({ type: "done", schemes: updatedSchemes });
      onStatusChange(`优化失败: ${error instanceof Error ? error.message : String(error)}`);
      return updatedSchemes;
    }
  }, []);

  /**
   * Create a scene job with a specific config patch
   */
  async function createSceneJobFromPatch(
    presetId: string,
    seedSuffix: string,
    configPatch: Record<string, any>,
    onProgress?: (prog: JobProgress) => void
  ): Promise<{
    scene_layout_path: string;
    scene_glb_path: string;
    viewer_url: string;
  }> {
    const preset = presets.find(p => p.id === presetId);
    const prompt = preset?.prompt || "优化后的街道设计";

    const response = await postJson<{
      job_id: string;
      status: string;
      created_at: string;
    }>("/api/scene/jobs", {
      draft: {
        normalized_scene_query: prompt,
        compose_config_patch: configPatch,
        citations_by_field: {},
        design_summary: prompt,
        risk_notes: [],
        parameter_sources_by_field: {},
      },
      scene_context: {
        layout_mode: "graph_template",
        aoi_bbox: null,
        city_name_en: null,
        reference_plan_id: null,
        graph_template_id: graphTemplateId,
      },
      patch_overrides: {},
      generation_options: { preset_id: presetId },
    }, 60000);

    return await pollJobCompletion(response.job_id, onProgress);
  }

  return {
    generationState,
    generateSchemes,
    generateFromDraft,
    applyAndRegenerate,
  };
}
