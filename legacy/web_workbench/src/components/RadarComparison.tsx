/**
 * RadarComparison - 多方案雷达图对比组件
 * 使用 Canvas 绘制多维度评分雷达图
 */

import { useRef, useEffect } from "react";
import { Card, Typography, Space } from "antd";
import type { EvaluationResult } from "../lib/types";

const { Text } = Typography;

interface RadarComparisonProps {
  evaluations: EvaluationResult[];
  height?: number;
}

const COLORS = ["#1890ff", "#52c41a", "#faad14", "#f5222d", "#722ed1", "#13c2c2"];

export function RadarComparison({ evaluations, height = 400 }: RadarComparisonProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!canvasRef.current || evaluations.length === 0) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Set canvas size
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.offsetWidth;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    // Clear canvas
    ctx.clearRect(0, 0, width, height);

    // Draw radar chart
    const centerX = width / 2;
    const centerY = height / 2;
    const radius = Math.min(width, height) / 2 - 80;

    // Data dimensions
    const dimensions = [
      { key: "walkability", label: "步行性" },
      { key: "safety", label: "安全性" },
      { key: "beauty", label: "美观度" },
    ];

    const n = dimensions.length;
    const angleStep = (2 * Math.PI) / n;

    // Draw grid
    for (let level = 1; level <= 5; level++) {
      const r = (radius * level) / 5;
      ctx.beginPath();
      for (let i = 0; i <= n; i++) {
        const angle = i * angleStep - Math.PI / 2;
        const x = centerX + r * Math.cos(angle);
        const y = centerY + r * Math.sin(angle);
        if (i === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.closePath();
      ctx.strokeStyle = "#e8e8e8";
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // Draw axes
    for (let i = 0; i < n; i++) {
      const angle = i * angleStep - Math.PI / 2;
      const x = centerX + radius * Math.cos(angle);
      const y = centerY + radius * Math.sin(angle);

      ctx.beginPath();
      ctx.moveTo(centerX, centerY);
      ctx.lineTo(x, y);
      ctx.strokeStyle = "#d9d9d9";
      ctx.lineWidth = 1;
      ctx.stroke();

      // Draw label
      const labelX = centerX + (radius + 30) * Math.cos(angle);
      const labelY = centerY + (radius + 30) * Math.sin(angle);
      ctx.fillStyle = "#595959";
      ctx.font = "14px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(dimensions[i].label, labelX, labelY);
    }

    // Draw data for each evaluation
    evaluations.forEach((eval_, evalIndex) => {
      const color = COLORS[evalIndex % COLORS.length];
      const data = dimensions.map((dim) => (eval_.scores as any)[dim.key] / 100);

      // Draw polygon
      ctx.beginPath();
      for (let i = 0; i <= n; i++) {
        const idx = i % n;
        const angle = idx * angleStep - Math.PI / 2;
        const r = radius * data[idx];
        const x = centerX + r * Math.cos(angle);
        const y = centerY + r * Math.sin(angle);
        if (i === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.closePath();
      ctx.fillStyle = color + "33"; // Add transparency
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();

      // Draw points
      for (let i = 0; i < n; i++) {
        const angle = i * angleStep - Math.PI / 2;
        const r = radius * data[i];
        const x = centerX + r * Math.cos(angle);
        const y = centerY + r * Math.sin(angle);

        ctx.beginPath();
        ctx.arc(x, y, 4, 0, 2 * Math.PI);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    });
  }, [evaluations, height]);

  if (evaluations.length === 0) {
    return null;
  }

  return (
    <Card title="多维度评分对比 (雷达图)">
      <canvas ref={canvasRef} style={{ width: "100%", height }} />
      <div style={{ marginTop: 16 }}>
        <Space wrap>
          {evaluations.map((eval_, index) => (
            <span key={eval_.sceneId}>
              <span
                style={{
                  display: "inline-block",
                  width: 12,
                  height: 12,
                  borderRadius: "50%",
                  background: COLORS[index % COLORS.length],
                  marginRight: 8,
                }}
              />
              <Text>方案 {eval_.sceneId}</Text>
            </span>
          ))}
        </Space>
      </div>
    </Card>
  );
}
