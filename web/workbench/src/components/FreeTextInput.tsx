import { useState } from "react";
import type { DraftResponse } from "../lib/api";

interface FreeTextInputProps {
  onDraftCreated: (draft: DraftResponse) => void;
  onCancel: () => void;
  onStatusChange: (message: string) => void;
}

export function FreeTextInput({ onDraftCreated, onCancel, onStatusChange }: FreeTextInputProps) {
  const [inputText, setInputText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (!inputText.trim()) {
      setError("请输入街道描述");
      return;
    }

    setIsLoading(true);
    setError(null);
    onStatusChange("正在分析描述并检索设计知识...");

    try {
      const { draftDesign } = await import("../lib/api");
      const response = await draftDesign(inputText.trim());

      if (!response) {
        setError("无法连接到后端服务，请确保服务已启动");
        onStatusChange("连接失败");
        return;
      }

      // Handle clarification required case - draft is nested inside response.draft
      if (response.stage === "clarification_required") {
        const questions = response.intent?.follow_up_questions || [];
        if (questions.length > 0) {
          setError(`需要更多信息：${questions.join(" ")}`);
        } else {
          setError("您的描述过于简略，请提供更多细节（例如：街道宽度、需要的设施等）");
        }
        onStatusChange("需要更多描述信息");
        return;
      }

      // Extract the actual draft - either from nested draft or direct fields
      const actualDraft = response.draft || response;
      if (actualDraft.compose_config_patch && Object.keys(actualDraft.compose_config_patch).length > 0) {
        onStatusChange("设计草案已生成，正在准备场景参数...");
        onDraftCreated(actualDraft);
      } else {
        setError("生成设计草案失败，请确保后端服务已启动且 LLM API 已配置");
        onStatusChange("生成失败");
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "未知错误";
      setError(`网络错误: ${errorMessage}`);
      onStatusChange("生成失败");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="free-text-input">
      <div className="section-header">
        <h2>自由描述模式</h2>
        <p className="section-desc">用自然语言描述你想要的街道场景，AI 将自动分析并生成专业的设计参数</p>
      </div>

      <div className="input-area">
        <textarea
          className="free-text-textarea"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          placeholder="例如：我想建一条步行友好的街道，有人行道、树、长凳和路灯，要有安全感，景色美观..."
          disabled={isLoading}
          rows={5}
        />

        {error && (
          <div className="input-error">
            <span className="error-icon">⚠️</span>
            {error}
          </div>
        )}

        <div className="input-hints">
          <h4>💡 描述建议</h4>
          <ul>
            <li>说明街道的主要用途（步行、商业、居住等）</li>
            <li>提及需要的设施（树、长凳、路灯、公交站等）</li>
            <li>描述期望的风格或氛围（安全、美观、安静等）</li>
          </ul>
        </div>
      </div>

      <div className="step-actions">
        <button className="btn secondary" onClick={onCancel} disabled={isLoading}>
          取消
        </button>
        <button className="btn primary" onClick={handleSubmit} disabled={isLoading || !inputText.trim()}>
          {isLoading ? "分析中..." : "生成设计草案"}
        </button>
      </div>

      <style>{`
        .free-text-input {
          display: flex;
          flex-direction: column;
          gap: var(--space-lg);
          padding: var(--space-md);
        }

        .input-area {
          display: flex;
          flex-direction: column;
          gap: var(--space-md);
          max-width: 600px;
          margin: 0 auto;
          width: 100%;
        }

        .free-text-textarea {
          width: 100%;
          padding: var(--space-md);
          border: 2px solid var(--border);
          border-radius: var(--radius-md);
          font-family: var(--font-sans);
          font-size: 0.875rem;
          line-height: 1.6;
          resize: vertical;
          transition: border-color 0.2s;
        }

        .free-text-textarea:focus {
          outline: none;
          border-color: var(--primary);
        }

        .free-text-textarea:disabled {
          background: var(--bg-secondary);
          cursor: not-allowed;
        }

        .input-error {
          display: flex;
          align-items: center;
          gap: var(--space-sm);
          padding: var(--space-sm) var(--space-md);
          background: #fef2f2;
          border: 1px solid #fecaca;
          border-radius: var(--radius-sm);
          color: #dc2626;
          font-size: 0.875rem;
        }

        .error-icon {
          font-size: 1rem;
        }

        .input-hints {
          padding: var(--space-md);
          background: var(--bg-secondary);
          border-radius: var(--radius-md);
          font-size: 0.8125rem;
        }

        .input-hints h4 {
          margin-bottom: var(--space-sm);
          font-size: 0.875rem;
          color: var(--text-primary);
        }

        .input-hints ul {
          margin: 0;
          padding-left: var(--space-lg);
          color: var(--text-secondary);
        }

        .input-hints li {
          margin-bottom: var(--space-xs);
        }
      `}</style>
    </div>
  );
}
