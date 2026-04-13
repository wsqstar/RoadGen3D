import { useState, useCallback } from "react";
import type { ScenePreset, GeneratedScheme } from "../lib/types";
import type { DraftResponse } from "../lib/api";
import { DEFAULT_GRAPH_TEMPLATE_ID, POLL_INTERVAL_MS, MAX_GENERATION_ATTEMPTS } from "../lib/types";
import { postJson, getJson, evaluateScene, resolveApiUrl } from "../lib/api";
import { resolveViewerUrl, sleep } from "../lib/utils";

export type GenerationState =
  | { type: "idle" }
  | { type: "generating"; schemes: GeneratedScheme[] }
  | { type: "done"; schemes: GeneratedScheme[] }
  | { type: "error"; message: string };

// 进度阶段定义
type JobProgress = {
  stage: string;        // 当前阶段: "queued", "composing", "rendering", "exporting", "succeeded"
  progress: number;      // 0-100
  message: string;       // 友好的状态描述
};

export function useGeneration(
  onStatusChange: (message: string) => void
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
      status: "generating",
      progress: 0,
    }));

    setGenerationState({ type: "generating", schemes: initialSchemes });

    const updatedSchemes = [...initialSchemes];

    for (let i = 0; i < updatedSchemes.length; i++) {
      const scheme = updatedSchemes[i];
      try {
        // 进度更新回调 - 实时反映真实进度
        const updateProgress = (prog: JobProgress) => {
          scheme.progress = prog.progress;
          onStatusChange(`方案 ${scheme.id}: ${prog.message}`);
          setGenerationState({ type: "generating", schemes: [...updatedSchemes] });
        };

        const result = await createSceneJob(selectedPreset, scheme.id, updateProgress);
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
          } else {
            scheme.evaluation = { walkability: -1, safety: -1, beauty: -1, overall: -1 };
          }
        } catch (evalError) {
          console.error(`方案 ${scheme.id} 评估失败:`, evalError);
          scheme.evaluation = { walkability: -1, safety: -1, beauty: -1, overall: -1 };
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
    onProgress?: (prog: JobProgress) => void
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
            case "layout_generation":
              return { stage: "layout_generation", progress: 20, message: "正在生成布局..." };
            case "graph_parsing":
              return { stage: "graph_parsing", progress: 30, message: "正在解析图形..." };
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
            default:
              return { stage: stage || "processing", progress: 50, message: "正在处理中..." };
          }
        case "succeeded":
          return { stage: "succeeded", progress: 95, message: "生成完成!" };
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
          operations?: string[];
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
            const opName = (currentOp as { name?: string }).name || (currentOp as { status?: string }).status || baseProg.message;
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
    onProgress?: (prog: JobProgress) => void
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
        compose_config_patch: draft.compose_config_patch,
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
        graph_template_id: DEFAULT_GRAPH_TEMPLATE_ID,
      },
      patch_overrides: {},
      generation_options: { preset_id: "custom_draft" },
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
      status: "generating",
      progress: 0,
    }));

    setGenerationState({ type: "generating", schemes: initialSchemes });

    const updatedSchemes = [...initialSchemes];

    for (let i = 0; i < updatedSchemes.length; i++) {
      const scheme = updatedSchemes[i];
      try {
        const updateProgress = (prog: JobProgress) => {
          scheme.progress = prog.progress;
          onStatusChange(`方案 ${scheme.id}: ${prog.message}`);
          setGenerationState({ type: "generating", schemes: [...updatedSchemes] });
        };

        const result = await createSceneJobFromDraft(draft, scheme.id, updateProgress);
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
          } else {
            scheme.evaluation = { walkability: -1, safety: -1, beauty: -1, overall: -1 };
          }
        } catch (evalError) {
          console.error(`方案 ${scheme.id} 评估失败:`, evalError);
          scheme.evaluation = { walkability: -1, safety: -1, beauty: -1, overall: -1 };
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

  return {
    generationState,
    generateSchemes,
    generateFromDraft,
  };
}
