import { useState, useRef, useEffect } from "react";
import type { DraftResponse, ChatMessage } from "../lib/api";

const MAX_TURNS = 3;

interface ParameterSource {
  key: string;
  value: string | number;
  source: "user" | "ai_inferred";
}

interface FreeTextInputProps {
  onDraftCreated: (draft: DraftResponse, sources: ParameterSource[]) => void;
  onCancel: () => void;
  onStatusChange: (message: string) => void;
}

export function FreeTextInput({ onDraftCreated, onCancel, onStatusChange }: FreeTextInputProps) {
  const [inputText, setInputText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [turnCount, setTurnCount] = useState(0);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [followUpQuestions, setFollowUpQuestions] = useState<string[]>([]);
  const [showForceButton, setShowForceButton] = useState(false);
  const [parameterSources, setParameterSources] = useState<ParameterSource[]>([]);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const addMessage = (role: "user" | "assistant", content: string) => {
    setMessages((prev) => [...prev, { role, content }]);
  };

  const handleSubmit = async () => {
    if (!inputText.trim()) {
      setError("请输入内容");
      return;
    }

    const userMessage = inputText.trim();
    setInputText("");
    setError(null);
    setIsLoading(true);

    const currentTurn = turnCount + 1;
    addMessage("user", userMessage);

    try {
      const { draftDesign } = await import("../lib/api");
      const response = await draftDesign({
        messages: [...messages, { role: "user", content: userMessage }],
        userInput: userMessage,
        force: false,
      });

      if (!response) {
        setError("无法连接到后端服务，请确保服务已启动");
        onStatusChange("连接失败");
        return;
      }

      // Handle clarification required
      if (response.stage === "clarification_required") {
        const questions = response.intent?.follow_up_questions || [];
        setTurnCount(currentTurn);
        onStatusChange(`还需要一些信息 (${currentTurn}/${MAX_TURNS})`);

        if (questions.length > 0) {
          const questionsText = questions.join("\n");
          addMessage("assistant", `需要更多信息：\n${questionsText}`);
          setFollowUpQuestions(questions);

          if (currentTurn >= MAX_TURNS) {
            setShowForceButton(true);
            addMessage("assistant", "已达最大问答次数。您可以点击「强制生成」让 AI 自动推断剩余参数，或继续补充信息。");
          }
        } else {
          addMessage("assistant", "您的描述过于简略，请提供更多细节。");
        }
        setIsLoading(false);
        return;
      }

      // Success - got a draft
      if (response.draft || response.compose_config_patch) {
        const actualDraft = response.draft || response;
        onStatusChange("设计草案已生成，正在准备场景参数...");

        // Track parameter sources (simplified - user input vs AI inference)
        const sources = inferParameterSources(actualDraft.compose_config_patch || {}, userMessage);
        setParameterSources(sources);

        addMessage("assistant", `已生成设计草案：${actualDraft.design_summary || "无描述"}`);
        onDraftCreated(actualDraft, sources);
        setIsLoading(false);
        return;
      }

      // Unexpected response
      setError("生成草案失败，请重试");
      onStatusChange("生成失败");
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "未知错误";
      setError(`网络错误: ${errorMessage}`);
      onStatusChange("生成失败");
    } finally {
      setIsLoading(false);
    }
  };

  const handleForceGenerate = async () => {
    if (!inputText.trim() && messages.length === 0) {
      setError("请先输入初始描述");
      return;
    }

    setIsLoading(true);
    setError(null);
    onStatusChange("正在强制生成草案（AI 将自动填充缺失参数）...");

    try {
      const { draftDesign } = await import("../lib/api");
      const lastUserMessage = messages.filter((m) => m.role === "user").pop()?.content || inputText;

      const response = await draftDesign({
        messages: [...messages, { role: "user", content: lastUserMessage }],
        userInput: lastUserMessage,
        force: true, // Force generation with AI-filled defaults
      });

      if (!response) {
        setError("无法连接到后端服务");
        onStatusChange("连接失败");
        return;
      }

      const actualDraft = response.draft || response;
      if (actualDraft.compose_config_patch) {
        onStatusChange("设计草案已生成（包含 AI 推断参数）...");
        const sources = inferParameterSources(actualDraft.compose_config_patch, lastUserMessage);
        setParameterSources(sources);
        addMessage("assistant", `已强制生成草案：${actualDraft.design_summary || "无描述"}`);
        onDraftCreated(actualDraft, sources);
      } else {
        setError("强制生成失败");
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
        <p className="section-desc">
          用自然语言描述你想要的街道场景，AI 将自动分析并生成专业的设计参数
          {turnCount > 0 && <span className="turn-indicator">（问答 {turnCount}/{MAX_TURNS}）</span>}
        </p>
      </div>

      <div className="chat-container">
        {/* Messages */}
        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="chat-placeholder">
              <p>请描述你想要的街道场景，例如：</p>
              <ul>
                <li>"我想建一条步行街，两边有树和长凳"</li>
                <li>"商业区街道，需要公交站和自行车道"</li>
                <li>"安静的住宅区街道，要求安全美观"</li>
              </ul>
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} className={`chat-message ${msg.role}`}>
              <div className="message-role">{msg.role === "user" ? "你" : "AI"}</div>
              <div className="message-content">{msg.content}</div>
            </div>
          ))}
        </div>

        {/* Input */}
        <div className="chat-input-area">
          <textarea
            ref={inputRef}
            className="free-text-textarea"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSubmit();
              }
            }}
            placeholder={followUpQuestions.length > 0 ? "回答上述问题..." : "输入你的街道描述..."}
            disabled={isLoading}
            rows={3}
          />

          {error && (
            <div className="input-error">
              <span className="error-icon">⚠️</span>
              {error}
            </div>
          )}

          <div className="chat-actions">
            {showForceButton && (
              <button
                className="btn force-btn"
                onClick={handleForceGenerate}
                disabled={isLoading}
              >
                强制生成（AI 推断参数）
              </button>
            )}
            <button
              className="btn secondary"
              onClick={onCancel}
              disabled={isLoading}
            >
              取消
            </button>
            <button
              className="btn primary"
              onClick={handleSubmit}
              disabled={isLoading || !inputText.trim()}
            >
              {isLoading ? "分析中..." : turnCount > 0 ? "继续" : "生成草案"}
            </button>
          </div>
        </div>
      </div>

      <style>{`
        .free-text-input {
          display: flex;
          flex-direction: column;
          gap: var(--space-lg);
          padding: var(--space-md);
        }

        .turn-indicator {
          color: var(--primary);
          font-weight: 500;
        }

        .chat-container {
          max-width: 600px;
          margin: 0 auto;
          width: 100%;
          display: flex;
          flex-direction: column;
          gap: var(--space-md);
        }

        .chat-messages {
          display: flex;
          flex-direction: column;
          gap: var(--space-md);
          max-height: 300px;
          overflow-y: auto;
          padding: var(--space-md);
          background: var(--bg-secondary);
          border-radius: var(--radius-md);
        }

        .chat-placeholder {
          color: var(--text-secondary);
          font-size: 0.875rem;
        }

        .chat-placeholder p {
          margin-bottom: var(--space-sm);
        }

        .chat-placeholder ul {
          margin: 0;
          padding-left: var(--space-lg);
        }

        .chat-placeholder li {
          margin-bottom: var(--space-xs);
          font-size: 0.8125rem;
        }

        .chat-message {
          display: flex;
          flex-direction: column;
          gap: var(--space-xs);
        }

        .chat-message.user {
          align-items: flex-end;
        }

        .chat-message.assistant {
          align-items: flex-start;
        }

        .message-role {
          font-size: 0.75rem;
          color: var(--text-muted);
        }

        .message-content {
          padding: var(--space-sm) var(--space-md);
          border-radius: var(--radius-md);
          font-size: 0.875rem;
          line-height: 1.5;
          white-space: pre-wrap;
        }

        .chat-message.user .message-content {
          background: var(--primary-light);
          color: var(--primary);
        }

        .chat-message.assistant .message-content {
          background: var(--bg-card);
          border: 1px solid var(--border);
        }

        .chat-input-area {
          display: flex;
          flex-direction: column;
          gap: var(--space-sm);
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

        .chat-actions {
          display: flex;
          justify-content: flex-end;
          gap: var(--space-sm);
        }

        .force-btn {
          background: #f59e0b;
          color: white;
        }

        .force-btn:hover:not(:disabled) {
          background: #d97706;
        }
      `}</style>
    </div>
  );
}

// Helper function to infer parameter sources
function inferParameterSources(
  composeConfigPatch: Record<string, string | number>,
  userInput: string
): ParameterSource[] {
  const sources: ParameterSource[] = [];
  const userInputLower = userInput.toLowerCase();

  // Keywords that indicate user-provided parameters
  const userKeywords: Record<string, string[]> = {
    design_rule_profile: ["步行", "商业", "居住", "公交", "公园"],
    objective_profile: ["平衡", "绿化", "商业", "公交", "安全", "美观"],
    density: ["密度", "紧凑", "稀疏"],
    ped_demand_level: ["步行", "行人"],
    bike_demand_level: ["自行车", "骑行"],
    transit_demand_level: ["公交", "巴士"],
    vehicle_demand_level: ["机动车", "车"],
    lane_count: ["车道", "路宽", "宽度"],
    sidewalk_width_m: ["人行道", "步道", "人行"],
  };

  for (const [key, value] of Object.entries(composeConfigPatch)) {
    let source: "user" | "ai_inferred" = "ai_inferred";
    const keywords = userKeywords[key as keyof typeof userKeywords];

    if (keywords) {
      for (const kw of keywords) {
        if (userInputLower.includes(kw)) {
          source = "user";
          break;
        }
      }
    }

    sources.push({ key, value, source });
  }

  return sources;
}
