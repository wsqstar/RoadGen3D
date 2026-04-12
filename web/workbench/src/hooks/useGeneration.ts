import { useState, useCallback } from "react";
import type { ScenePreset, GeneratedScheme } from "../lib/types";
import { DEFAULT_GRAPH_TEMPLATE_ID, POLL_INTERVAL_MS, MAX_GENERATION_ATTEMPTS } from "../lib/types";
import { postJson, getJson, evaluateScene, resolveApiUrl } from "../lib/api";
import { resolveViewerUrl, sleep } from "../lib/utils";

export type GenerationState =
  | { type: "idle" }
  | { type: "generating"; schemes: GeneratedScheme[] }
  | { type: "done"; schemes: GeneratedScheme[] }
  | { type: "error"; message: string };

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
        for (let p = 0; p <= 100; p += 20) {
          scheme.progress = p;
          setGenerationState({ type: "generating", schemes: [...updatedSchemes] });
          await sleep(200);
        }

        const result = await createSceneJob(selectedPreset, scheme.id);
        scheme.layoutPath = result.scene_layout_path;
        scheme.viewerUrl = resolveViewerUrl(result.viewer_url, result.scene_layout_path);
        scheme.previewUrl = resolveApiUrl(result.scene_layout_path);

        try {
          onStatusChange(`正在评估方案 ${scheme.id}...`);
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

  async function createSceneJob(preset: ScenePreset, seedSuffix: string): Promise<{
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

    return await pollJobCompletion(response.job_id);
  }

  async function pollJobCompletion(jobId: string): Promise<{
    scene_layout_path: string;
    scene_glb_path: string;
    viewer_url: string;
  }> {
    for (let i = 0; i < MAX_GENERATION_ATTEMPTS; i++) {
      try {
        const status = await getJson<{
          job_id: string;
          status: string;
          result: {
            scene_layout_path: string;
            scene_glb_path: string;
            viewer_url: string;
          } | null;
        }>(`/api/scene/jobs/${jobId}`, 10000);

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

  return {
    generationState,
    generateSchemes,
  };
}
