/**
 * ScatterPlotComparison - 散点图对比组件
 * 使用 Chart.js 展示多方案的评估指标散点图
 * 支持回归线、趋势分析和交互式 tooltip
 */

import { useRef, useEffect, useState } from "react";
import { Card, Select, Space, Typography, Row, Col, Statistic, Tag } from "antd";
import {
  InfoCircleOutlined,
  RiseOutlined,
  AreaChartOutlined,
} from "@ant-design/icons";
import { Chart, registerables, type ChartConfiguration } from "chart.js";
import type { EvaluationResult } from "../lib/types";
import { COLORS } from "../theme";

const { Text } = Typography;
const { Option } = Select;

// Register Chart.js plugins
Chart.register(...registerables);

// 可用的指标选项
interface MetricOption {
  value: string;
  label: string;
  category: string;
}

const METRIC_OPTIONS: MetricOption[] = [
  // 综合评分
  { value: "overall", label: "综合评分", category: "综合" },
  { value: "walkability", label: "步行性", category: "综合" },
  { value: "safety", label: "安全性", category: "综合" },
  { value: "beauty", label: "美观度", category: "综合" },
  // 支柱评分
  { value: "Protection", label: "Protection", category: "支柱" },
  { value: "Comfort", label: "Comfort", category: "支柱" },
  { value: "Delight", label: "Delight", category: "支柱" },
  // 步行性指标
  { value: "SID_CLR", label: "人行道净宽", category: "步行性" },
  { value: "CLEAR_CONT", label: "无障碍连续性", category: "步行性" },
  { value: "FURN_D", label: "设施密度", category: "步行性" },
  { value: "LIGHT_UNI", label: "照明均匀性", category: "步行性" },
  { value: "TREE_SHADE", label: "树冠遮荫", category: "步行性" },
  { value: "BUFFER_RATIO", label: "缓冲强度", category: "步行性" },
  { value: "TRANSIT_PROX", label: "公交可达性", category: "步行性" },
  { value: "CROSS_PROV", label: "横道供给", category: "步行性" },
  { value: "ENTR_DENS", label: "入口密度", category: "步行性" },
  { value: "POI_MIX", label: "POI多样性", category: "步行性" },
  { value: "MICRO_ENV", label: "微气候", category: "步行性" },
];

const CHART_COLORS = [
  "#1890ff", // 蓝色
  "#52c41a", // 绿色
  "#faad14", // 橙色
  "#f5222d", // 红色
  "#722ed1", // 紫色
  "#13c2c2", // 青色
];

interface ScatterPlotComparisonProps {
  evaluations: EvaluationResult[];
  height?: number;
}

export function ScatterPlotComparison({
  evaluations,
  height = 400,
}: ScatterPlotComparisonProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const [xMetric, setXMetric] = useState("walkability");
  const [yMetric, setYMetric] = useState("safety");

  // 计算统计数据
  const stats = useRef<{
    correlation?: number;
    slope?: number;
    intercept?: number;
    r2?: number;
  }>({});

  // 绘制散点图
  useEffect(() => {
    if (!canvasRef.current || evaluations.length === 0) return;

    // 销毁旧图表
    if (chartRef.current) {
      chartRef.current.destroy();
      chartRef.current = null;
    }

    const ctx = canvasRef.current.getContext("2d");
    if (!ctx) return;

    // 准备数据
    const points = evaluations.map((eval_, index) => {
      const xValue = extractMetric(eval_, xMetric);
      const yValue = extractMetric(eval_, yMetric);
      return {
        x: xValue,
        y: yValue,
        schemeId: eval_.sceneId,
        color: CHART_COLORS[index % CHART_COLORS.length],
      };
    });

    // 计算线性回归
    const regression = calculateLinearRegression(points);
    stats.current = regression;

    // 创建图表
    chartRef.current = new Chart(ctx, {
      type: "scatter",
      data: {
        datasets: [
          {
            label: "方案",
            data: points.map((p) => ({ x: p.x, y: p.y })),
            backgroundColor: points.map((p) => p.color),
            borderColor: points.map((p) => p.color),
            borderWidth: 2,
            pointRadius: 8,
            pointHoverRadius: 12,
            pointHoverBorderWidth: 3,
            pointHoverBackgroundColor: "#fff",
          },
          // 回归线
          {
            label: "趋势线",
            type: "line" as const,
            data: generateRegressionLine(points, regression),
            borderColor: "#ff4d4f",
            borderWidth: 2,
            borderDash: [5, 5],
            pointRadius: 0,
            fill: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            title: {
              display: true,
              text: getMetricLabel(xMetric),
              font: { size: 14, weight: "bold" },
            },
            grid: {
              color: "#f0f0f0",
            },
            min: 0,
            max: 100,
          },
          y: {
            title: {
              display: true,
              text: getMetricLabel(yMetric),
              font: { size: 14, weight: "bold" },
            },
            grid: {
              color: "#f0f0f0",
            },
            min: 0,
            max: 100,
          },
        },
        plugins: {
          legend: {
            display: false,
          },
          tooltip: {
            callbacks: {
              label: function (context) {
                const index = context.dataIndex;
                const point = points[index];
                if (point) {
                  return `方案 ${point.schemeId}: (${point.x.toFixed(
                    1
                  )}, ${point.y.toFixed(1)})`;
                }
                return "";
              },
              title: function (tooltipItems) {
                const index = tooltipItems[0]?.dataIndex;
                const point = points[index];
                return point ? `方案 ${point.schemeId}` : "";
              },
            },
            backgroundColor: "rgba(0, 0, 0, 0.8)",
            padding: 12,
            titleFont: { size: 14, weight: "bold" },
            bodyFont: { size: 13 },
            displayColors: true,
          },
        },
      },
    });

    return () => {
      if (chartRef.current) {
        chartRef.current.destroy();
        chartRef.current = null;
      }
    };
  }, [evaluations, xMetric, yMetric]);

  if (evaluations.length < 2) {
    return (
      <Card>
        <Text type="secondary">至少需要 2 个方案才能显示散点图</Text>
      </Card>
    );
  }

  return (
    <Card
      title={
        <Space>
          <AreaChartOutlined />
          散点图分析
        </Space>
      }
      extra={
        <Space>
          <Text>X 轴:</Text>
          <Select
            value={xMetric}
            onChange={setXMetric}
            style={{ width: 150 }}
            size="small"
          >
            {METRIC_OPTIONS.map((opt) => (
              <Option key={opt.value} value={opt.value}>
                {opt.label}
              </Option>
            ))}
          </Select>
          <Text>Y 轴:</Text>
          <Select
            value={yMetric}
            onChange={setYMetric}
            style={{ width: 150 }}
            size="small"
          >
            {METRIC_OPTIONS.map((opt) => (
              <Option key={opt.value} value={opt.value}>
                {opt.label}
              </Option>
            ))}
          </Select>
        </Space>
      }
    >
      <Row gutter={16}>
        <Col span={18}>
          <div style={{ height, position: "relative" }}>
            <canvas ref={canvasRef} />
          </div>
        </Col>
        <Col span={6}>
          <Card size="small" title="统计信息">
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <InfoCircleOutlined /> 相关系数
                </Text>
                <Statistic
                  value={stats.current.correlation?.toFixed(3) || "N/A"}
                  suffix={
                    stats.current.correlation !== undefined
                      ? Math.abs(stats.current.correlation) > 0.7
                        ? "强相关"
                        : "弱相关"
                      : ""
                  }
                  valueStyle={{
                    fontSize: 18,
                    color:
                      stats.current.correlation !== undefined &&
                      Math.abs(stats.current.correlation) > 0.7
                        ? COLORS.walkability
                        : COLORS.warning,
                  }}
                />
              </div>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <RiseOutlined /> R² 决定系数
                </Text>
                <Statistic
                  value={stats.current.r2?.toFixed(3) || "N/A"}
                  valueStyle={{ fontSize: 18 }}
                />
              </div>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  斜率
                </Text>
                <Statistic
                  value={stats.current.slope?.toFixed(3) || "N/A"}
                  valueStyle={{ fontSize: 18 }}
                />
              </div>
              <div style={{ marginTop: 16 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  方案数量
                </Text>
                <Statistic
                  value={evaluations.length}
                  valueStyle={{ fontSize: 18 }}
                />
              </div>
            </Space>
          </Card>

          <Card size="small" title="图例" style={{ marginTop: 16 }}>
            <Space direction="vertical" size="small">
              {evaluations.map((eval_, index) => (
                <Space key={eval_.sceneId}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 12,
                      height: 12,
                      borderRadius: "50%",
                      background: CHART_COLORS[index % CHART_COLORS.length],
                    }}
                  />
                  <Text>方案 {eval_.sceneId}</Text>
                </Space>
              ))}
            </Space>
          </Card>
        </Col>
      </Row>
    </Card>
  );
}

// 从评估结果中提取指标值
function extractMetric(eval_: EvaluationResult, metric: string): number {
  // 综合评分
  if (metric === "overall") return eval_.scores.overall;
  if (metric === "walkability") return eval_.scores.walkability;
  if (metric === "safety") return eval_.scores.safety;
  if (metric === "beauty") return eval_.scores.beauty;

  // 支柱评分
  if (eval_.pillarScores && metric in eval_.pillarScores) {
    return (eval_.pillarScores as any)[metric];
  }

  // 步行性指标
  if (eval_.indicators && metric in eval_.indicators) {
    return (eval_.indicators as any)[metric] * 100; // 转换为 0-100
  }

  return 0;
}

// 获取指标的中文标签
function getMetricLabel(metric: string): string {
  const found = METRIC_OPTIONS.find((opt) => opt.value === metric);
  return found?.label || metric;
}

// 计算线性回归
function calculateLinearRegression(
  points: Array<{ x: number; y: number }>
): { correlation: number; slope: number; intercept: number; r2: number } {
  const n = points.length;
  if (n < 2) {
    return { correlation: 0, slope: 0, intercept: 0, r2: 0 };
  }

  let sumX = 0,
    sumY = 0,
    sumXY = 0,
    sumX2 = 0,
    sumY2 = 0;

  for (const p of points) {
    sumX += p.x;
    sumY += p.y;
    sumXY += p.x * p.y;
    sumX2 += p.x * p.x;
    sumY2 += p.y * p.y;
  }

  const denominator = n * sumX2 - sumX * sumX;
  if (denominator === 0) {
    return { correlation: 0, slope: 0, intercept: 0, r2: 0 };
  }

  const slope = (n * sumXY - sumX * sumY) / denominator;
  const intercept = (sumY - slope * sumX) / n;

  // 相关系数
  const numerator = n * sumXY - sumX * sumY;
  const denomCorr = Math.sqrt(
    (n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY)
  );
  const correlation = denomCorr === 0 ? 0 : numerator / denomCorr;

  // R² 决定系数
  const r2 = correlation * correlation;

  return { correlation, slope, intercept, r2 };
}

// 生成回归线数据点
function generateRegressionLine(
  points: Array<{ x: number; y: number }>,
  regression: { slope: number; intercept: number }
): Array<{ x: number; y: number }> {
  const minX = Math.max(0, Math.min(...points.map((p) => p.x)) - 10);
  const maxX = Math.min(100, Math.max(...points.map((p) => p.x)) + 10);

  return [
    { x: minX, y: regression.slope * minX + regression.intercept },
    { x: maxX, y: regression.slope * maxX + regression.intercept },
  ];
}
