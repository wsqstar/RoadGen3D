import { EVALUATION_COLORS, SCHEME_COLORS } from "../lib/constants";
import type { GeneratedScheme } from "../lib/types";

interface SchemeGridProps {
  schemes: GeneratedScheme[];
  selectedSchemeId: string | null;
  onSelectScheme: (id: string) => void;
}

export function SchemeGrid({ schemes, selectedSchemeId, onSelectScheme }: SchemeGridProps) {
  if (schemes.length === 0) {
    return (
      <div className="scheme-grid">
        <div className="empty-state" style={{ gridColumn: "1 / -1" }}>
          <div className="empty-icon">📋</div>
          <div className="empty-text">请先选择一个模板</div>
        </div>
      </div>
    );
  }

  return (
    <div className="scheme-grid">
      {schemes.map((scheme) => {
        const isSelected = scheme.id === selectedSchemeId;
        const isReady = scheme.status === "ready";
        const isGenerating = scheme.status === "generating";
        const isFailed = scheme.status === "failed";
        const color = SCHEME_COLORS[scheme.id as keyof typeof SCHEME_COLORS];

        return (
          <div
            key={scheme.id}
            className={`scheme-card ${isSelected ? "selected" : ""} ${isReady ? "ready" : ""}`}
            onClick={(e) => {
              const target = e.target as HTMLElement;
              if (target.classList.contains("btn-viewer") || target.classList.contains("btn-select")) {
                return;
              }
              if (isReady) onSelectScheme(scheme.id);
            }}
          >
            <div className="scheme-preview" style={{ borderColor: color }}>
              {isGenerating ? (
                <div className="preview-generating">
                  <div className="generating-icon">⚙️</div>
                  <div className="generating-text">生成中...</div>
                  <div className="progress-bar">
                    <div className="progress-fill" style={{ width: `${scheme.progress}%` }} />
                  </div>
                  <div className="progress-text">{scheme.progress}%</div>
                </div>
              ) : isFailed ? (
                <div className="preview-failed">
                  <div className="failed-icon">❌</div>
                  <div className="failed-text">生成失败</div>
                </div>
              ) : isReady ? (
                <div className="preview-ready">
                  <img
                    src={scheme.previewUrl}
                    alt={`${scheme.name} 预览`}
                    onError={(e) => {
                      const img = e.currentTarget;
                      img.parentElement!.innerHTML =
                        '<div class="preview-placeholder"><div class="placeholder-icon">🖼️</div></div>';
                    }}
                  />
                </div>
              ) : (
                <div className="preview-placeholder">
                  <div className="placeholder-icon">🖼️</div>
                </div>
              )}
            </div>

            <div className="scheme-info">
              <div className="scheme-header">
                <span className="scheme-id" style={{ backgroundColor: color }}>
                  {scheme.name}
                </span>
                {isSelected ? <span className="selected-badge">✓ 已选择</span> : null}
              </div>

              {isReady ? (
                <>
                  {scheme.evaluation.overall >= 0 ? (
                    <div className="scheme-scores">
                      <div className="score-row">
                        <span className="score-label">综合</span>
                        <span className="score-value overall">{scheme.evaluation.overall}</span>
                      </div>
                      <div className="score-row">
                        <span className="score-label" style={{ color: EVALUATION_COLORS.walkability.primary }}>
                          步行性
                        </span>
                        <span className="score-value">{scheme.evaluation.walkability}</span>
                      </div>
                      <div className="score-row">
                        <span className="score-label" style={{ color: EVALUATION_COLORS.safety.primary }}>
                          安全性
                        </span>
                        <span className="score-value">{scheme.evaluation.safety}</span>
                      </div>
                      <div className="score-row">
                        <span className="score-label" style={{ color: EVALUATION_COLORS.beauty.primary }}>
                          美观度
                        </span>
                        <span className="score-value">{scheme.evaluation.beauty}</span>
                      </div>
                    </div>
                  ) : (
                    <div className="scheme-status-text" style={{ color: "#f44336" }}>
                      评估服务不可用
                    </div>
                  )}
                  <div className="scheme-actions">
                    <button
                      className="btn-viewer"
                      onClick={() => scheme.viewerUrl && window.open(scheme.viewerUrl, "_blank")}
                    >
                      3D 预览
                    </button>
                    <button className="btn-select" onClick={() => onSelectScheme(scheme.id)}>
                      选择此方案
                    </button>
                  </div>
                </>
              ) : (
                <div className="scheme-status-text">
                  {isGenerating ? "正在生成中..." : isFailed ? "生成失败，请重试" : "等待生成..."}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
