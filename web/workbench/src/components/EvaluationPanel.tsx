import { useEffect, useRef } from "react";
import { EVALUATION_COLORS, SCHEME_COLORS, WALKABILITY_INDICATORS, CHART_CONFIG } from "../lib/constants";
import type { EvaluationResult } from "../lib/types";
import { toRadarChartData, toBarChartData } from "../lib/utils";

interface EvaluationPanelProps {
  evaluations: EvaluationResult[];
  selectedSchemeId: string | null;
  onOptimize?: (schemeId: string, patch: Record<string, any>) => void;
  isOptimizing?: boolean;
}

export function EvaluationPanel({ evaluations, selectedSchemeId, onOptimize, isOptimizing }: EvaluationPanelProps) {
  const radarRef = useRef<HTMLCanvasElement>(null);
  const barRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (evaluations.length > 0 && radarRef.current) {
      drawRadarChart(radarRef.current, evaluations);
    }
  }, [evaluations]);

  useEffect(() => {
    if (evaluations.length > 0 && barRef.current) {
      drawBarChart(barRef.current, evaluations);
    }
  }, [evaluations]);

  const selectedEval = evaluations.find((e) => e.sceneId === selectedSchemeId);

  return (
    <div className="evaluation-panel">
      <div className="eval-overview">
        <div className="eval-summary">
          {!selectedSchemeId || evaluations.length === 0 ? (
            <div className="summary-empty">选择一个方案查看详细评估</div>
          ) : selectedEval ? (
            <div className="summary-selected">
              <div className="selected-label">已选择: 方案 {selectedSchemeId}</div>
              <div className="selected-overall">
                <span className="overall-number">{selectedEval.scores.overall}</span>
                <span className="overall-label">综合评分</span>
              </div>
            </div>
          ) : null}
        </div>
        <div className="weight-info">
          <h3>评分权重</h3>
          <div className="weight-row">
            <span className="weight-label" style={{ color: EVALUATION_COLORS.walkability.primary }}>
              ● 步行性
            </span>
            <span className="weight-value">{(0.45 * 100).toFixed(0)}%</span>
          </div>
          <div className="weight-row">
            <span className="weight-label" style={{ color: EVALUATION_COLORS.safety.primary }}>
              ● 安全性
            </span>
            <span className="weight-value">{(0.35 * 100).toFixed(0)}%</span>
          </div>
          <div className="weight-row">
            <span className="weight-label" style={{ color: EVALUATION_COLORS.beauty.primary }}>
              ● 美观度
            </span>
            <span className="weight-value">{(0.2 * 100).toFixed(0)}%</span>
          </div>
          <div className="weight-formula">综合 = 0.45×W + 0.35×S + 0.20×B</div>
        </div>
      </div>

      <div className="charts-row">
        <div className="chart-container">
          <h3>雷达图对比</h3>
          <canvas ref={radarRef} width={CHART_CONFIG.radar.size} height={CHART_CONFIG.radar.size} />
        </div>
        <div className="chart-container">
          <h3>柱状图对比</h3>
          <canvas ref={barRef} width={CHART_CONFIG.bar.height * 1.5} height={CHART_CONFIG.bar.height} />
        </div>
      </div>

      <div className="indicators-table">
        {evaluations.length === 0 ? (
          <div className="table-empty">暂无指标数据</div>
        ) : (
          <IndicatorsTable evaluations={evaluations} />
        )}
      </div>

      {/* 优化建议区块 */}
      {selectedEval && (selectedEval.suggestions?.length || selectedEval.config_patch) && (
        <div className="optimization-section">
          <h3>🚀 优化建议</h3>
          
          {selectedEval.suggestions && selectedEval.suggestions.length > 0 && (
            <div className="suggestions-list">
              <h4>改进建议:</h4>
              <ul>
                {selectedEval.suggestions.map((suggestion, idx) => (
                  <li key={idx}>{suggestion}</li>
                ))}
              </ul>
            </div>
          )}

          {selectedEval.config_patch && Object.keys(selectedEval.config_patch).length > 0 && (
            <div className="config-patch">
              <h4>建议参数修改:</h4>
              <div className="patch-preview">
                {Object.entries(selectedEval.config_patch).map(([key, value]) => (
                  <div key={key} className="patch-item">
                    <span className="patch-key">{key}:</span>
                    <span className="patch-value">{String(value)}</span>
                  </div>
                ))}
              </div>
              
              {onOptimize && (
                <button
                  className="btn optimize-btn"
                  onClick={() => onOptimize(selectedEval.sceneId, selectedEval.config_patch!)}
                  disabled={isOptimizing}
                >
                  {isOptimizing ? "优化中..." : "✨ 一键优化"}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function IndicatorsTable({ evaluations }: { evaluations: EvaluationResult[] }) {
  const indicatorKeys = Object.keys(WALKABILITY_INDICATORS) as Array<keyof typeof WALKABILITY_INDICATORS>;

  return (
    <table className="indicators-full-table">
      <thead>
        <tr>
          <th className="indicator-col">指标</th>
          {evaluations.map((e) => (
            <th
              key={e.sceneId}
              className="scheme-header"
              style={{ borderLeft: `3px solid ${SCHEME_COLORS[e.sceneId as keyof typeof SCHEME_COLORS]}` }}
            >
              方案 {e.sceneId}
            </th>
          ))}
          <th className="avg-col">平均</th>
        </tr>
      </thead>
      <tbody>
        {indicatorKeys.map((key) => {
          const meta = WALKABILITY_INDICATORS[key];
          const values = evaluations.map((e) => Math.round(((e.indicators as Record<string, number>)[key] || 0) * 100));
          const avg = values.length > 0 ? Math.round(values.reduce((a, b) => a + b, 0) / values.length) : 0;

          return (
            <tr key={key}>
              <td className="indicator-name">
                <div className="indicator-label">{meta.label}</div>
                <div className="indicator-key">{key}</div>
              </td>
              {values.map((v, i) => (
                <td key={evaluations[i].sceneId} className={`indicator-value scheme-${evaluations[i].sceneId}`}>
                  {v}
                </td>
              ))}
              <td className="indicator-avg">{avg}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function drawRadarChart(canvas: HTMLCanvasElement, evaluations: EvaluationResult[]) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const { size, padding, labelOffset } = CHART_CONFIG.radar;
  const center = size / 2;
  const radius = (size - padding * 2) / 2;
  const labels = ["步行性", "安全性", "美观度"];

  ctx.clearRect(0, 0, size, size);

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

  ctx.strokeStyle = "#d1d5db";
  for (let i = 0; i < 3; i++) {
    const angle = (Math.PI * 2 * i) / 3 - Math.PI / 2;
    ctx.beginPath();
    ctx.moveTo(center, center);
    ctx.lineTo(center + radius * Math.cos(angle), center + radius * Math.sin(angle));
    ctx.stroke();
  }

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

  const colors = [EVALUATION_COLORS.walkability.primary, EVALUATION_COLORS.safety.primary, EVALUATION_COLORS.beauty.primary];
  const schemeColors = [SCHEME_COLORS.A, SCHEME_COLORS.B, SCHEME_COLORS.C];

  evaluations.forEach((eval_, index) => {
    const scores = [eval_.scores.walkability, eval_.scores.safety, eval_.scores.beauty];
    const color = schemeColors[index] || colors[index % 3];

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

  ctx.font = "12px system-ui, sans-serif";
  evaluations.forEach((eval_, index) => {
    const color = schemeColors[index];
    const y = size - 10;
    const x = center - 50 + index * 50;
    ctx.fillStyle = color;
    ctx.fillRect(x - 10, y - 6, 12, 12);
    ctx.fillStyle = "#374151";
    ctx.textAlign = "left";
    ctx.fillText(`方案 ${eval_.sceneId}`, x + 5, y + 4);
  });
}

function drawBarChart(canvas: HTMLCanvasElement, evaluations: EvaluationResult[]) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const width = canvas.width;
  const height = canvas.height;
  const barWidth = CHART_CONFIG.bar.barWidth;
  const barGap = CHART_CONFIG.bar.barGap;
  const labelOffset = CHART_CONFIG.bar.labelOffset;
  const labels = ["步行性", "安全性", "美观度"];

  ctx.clearRect(0, 0, width, height);

  const chartHeight = height - labelOffset * 2;
  const maxValue = 100;
  const groupWidth = (barWidth + barGap) * evaluations.length + barGap;
  const startX = (width - groupWidth * 3 - barGap * 2) / 2;

  ctx.strokeStyle = "#e5e7eb";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(startX, labelOffset);
  ctx.lineTo(startX, height - labelOffset);
  ctx.lineTo(width - startX, height - labelOffset);
  ctx.stroke();

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

  const schemeColors = [SCHEME_COLORS.A, SCHEME_COLORS.B, SCHEME_COLORS.C];
  labels.forEach((label, labelIndex) => {
    const groupX = startX + labelIndex * (groupWidth + barGap);

    ctx.fillStyle = "#374151";
    ctx.font = "12px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(label, groupX + groupWidth / 2, height - 5);

    evaluations.forEach((eval_, evalIndex) => {
      const values = [eval_.scores.walkability, eval_.scores.safety, eval_.scores.beauty];
      const value = values[labelIndex];
      const barHeight = (value / maxValue) * chartHeight;
      const x = groupX + evalIndex * (barWidth + barGap) + barGap;
      const y = height - labelOffset - barHeight;

      ctx.fillStyle = schemeColors[evalIndex];
      ctx.fillRect(x, y, barWidth, barHeight);

      ctx.fillStyle = "#374151";
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(value.toString(), x + barWidth / 2, y - 5);
    });
  });

  const legendY = 15;
  ctx.font = "11px system-ui, sans-serif";
  evaluations.forEach((eval_, index) => {
    const x = width - 100 + index * 50;
    ctx.fillStyle = schemeColors[index];
    ctx.fillRect(x, legendY - 8, 10, 10);
    ctx.fillStyle = "#374151";
    ctx.textAlign = "left";
    ctx.fillText(`方案 ${eval_.sceneId}`, x + 14, legendY);
  });
}
