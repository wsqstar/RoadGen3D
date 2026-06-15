/**
 * EvaluationPanel - 评估面板组件
 * 使用 Ant Design 组件重构
 */

import { Card, Statistic, Row, Col, Tag, Button, Space, Typography, Divider, Tabs } from "antd";
import {
  TrophyOutlined,
  SafetyOutlined,
  BulbOutlined,
  RocketOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  BarChartOutlined,
  ScanOutlined,
  AreaChartOutlined,
} from "@ant-design/icons";
import type { EvaluationResult } from "../lib/types";
import { RadarComparison } from "./RadarComparison";
import { ScatterPlotComparison } from "./ScatterPlotComparison";
import { COLORS } from "../theme";
import type { LlmStatusEntry } from "../lib/types";

const { Text } = Typography;
const { TabPane } = Tabs;

interface EvaluationPanelProps {
  evaluations: EvaluationResult[];
  selectedSchemeId: string | null;
  onOptimize?: (schemeId: string, patch: Record<string, any>) => void;
  isOptimizing?: boolean;
}

function renderLlmStatusTag(label: string, entry?: LlmStatusEntry) {
  const source = String(entry?.source || "unavailable").toLowerCase();
  const statusLabel =
    source === "llm" ? "Live" :
    source === "cache" ? "Cache" :
    source === "disabled" ? "Disabled" :
    "Unavailable";
  const color =
    source === "llm" ? "green" :
    source === "cache" ? "blue" :
    source === "disabled" ? "default" :
    "volcano";
  return (
    <Space key={label} size="small">
      <Text type="secondary">{label}</Text>
      <Tag color={color}>{statusLabel}</Tag>
    </Space>
  );
}

export function EvaluationPanel({ evaluations, selectedSchemeId, onOptimize, isOptimizing }: EvaluationPanelProps) {
  const selectedEval = evaluations.find((e) => e.sceneId === selectedSchemeId);

  if (!selectedEval) {
    return (
      <Card>
        <Text type="secondary">选择一个方案查看详细评估</Text>
      </Card>
    );
  }

  const { scores, evaluation, suggestions, config_patch, llmStatus } = selectedEval;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {/* 综合评分卡片 */}
      <Card>
        <Row gutter={16}>
          <Col span={6}>
            <Statistic
              title="综合评分"
              value={scores.overall}
              suffix="/ 100"
              valueStyle={{ color: COLORS.overall, fontSize: 36 }}
              prefix={<TrophyOutlined />}
            />
          </Col>
          <Col span={6}>
            <Statistic
              title="步行性"
              value={scores.walkability}
              suffix="/ 100"
              valueStyle={{ color: COLORS.walkability }}
              prefix={<CheckCircleOutlined />}
            />
          </Col>
          <Col span={6}>
            <Statistic
              title="安全性"
              value={scores.safety}
              suffix="/ 100"
              valueStyle={{ color: COLORS.safety }}
              prefix={<SafetyOutlined />}
            />
          </Col>
          <Col span={6}>
            <Statistic
              title="美观度"
              value={scores.beauty}
              suffix="/ 100"
              valueStyle={{ color: COLORS.beauty }}
              prefix={<BulbOutlined />}
            />
          </Col>
        </Row>
        <Divider style={{ margin: "16px 0 0" }} />
        <Space wrap style={{ marginTop: 12 }}>
          <Text strong>LLM Status</Text>
          {renderLlmStatusTag("Safety", llmStatus?.safety)}
          {renderLlmStatusTag("Beauty", llmStatus?.beauty)}
        </Space>
      </Card>

      {/* 多维度对比 - Tab 切换 */}
      {evaluations.length > 1 && (
        <Card>
          <Tabs defaultActiveKey="scatter">
            <TabPane
              tab={
                <Space>
                  <AreaChartOutlined />
                  散点图分析
                </Space>
              }
              key="scatter"
            >
              <ScatterPlotComparison evaluations={evaluations} height={400} />
            </TabPane>
            <TabPane
              tab={
                <Space>
                  <ScanOutlined />
                  雷达图对比
                </Space>
              }
              key="radar"
            >
              <RadarComparison evaluations={evaluations} height={400} />
            </TabPane>
            <TabPane
              tab={
                <Space>
                  <BarChartOutlined />
                  柱状图对比
                </Space>
              }
              key="bar"
            >
          {evaluations.map((eval_) => (
            <div key={eval_.sceneId} style={{ marginBottom: 16 }}>
              <Text strong>方案 {eval_.sceneId}</Text>
              <Row gutter={8} style={{ marginTop: 8 }}>
                <Col span={8}>
                  <div style={{ background: "#f0f0f0", borderRadius: 4, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${eval_.scores.walkability}%`,
                        background: COLORS.walkability,
                        padding: "4px 8px",
                        color: "white",
                        fontSize: 12,
                        textAlign: "right",
                      }}
                    >
                      步行性 {eval_.scores.walkability}
                    </div>
                  </div>
                </Col>
                <Col span={8}>
                  <div style={{ background: "#f0f0f0", borderRadius: 4, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${eval_.scores.safety}%`,
                        background: COLORS.safety,
                        padding: "4px 8px",
                        color: "white",
                        fontSize: 12,
                        textAlign: "right",
                      }}
                    >
                      安全性 {eval_.scores.safety}
                    </div>
                  </div>
                </Col>
                <Col span={8}>
                  <div style={{ background: "#f0f0f0", borderRadius: 4, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${eval_.scores.beauty}%`,
                        background: COLORS.beauty,
                        padding: "4px 8px",
                        color: "white",
                        fontSize: 12,
                        textAlign: "right",
                      }}
                    >
                      美观度 {eval_.scores.beauty}
                    </div>
                  </div>
                </Col>
              </Row>
            </div>
          ))}
            </TabPane>
          </Tabs>
        </Card>
      )}

      {/* LLM 评价文本 */}
      {evaluation && (
        <Card title={<Space><RocketOutlined />AI 评价</Space>}>
          <Text>{evaluation}</Text>
        </Card>
      )}

      {/* 改进建议 */}
      {suggestions && suggestions.length > 0 && (
        <Card
          title={
            <Space>
              <WarningOutlined style={{ color: COLORS.warning }} />
              改进建议
            </Space>
          }
        >
          <ul style={{ paddingLeft: 20, marginBottom: 0 }}>
            {suggestions.map((s, i) => (
              <li key={i}>
                <Text>{s}</Text>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* 参数修改建议 + 一键优化按钮 */}
      {config_patch && Object.keys(config_patch).length > 0 && onOptimize && (
        <Card
          title="建议参数修改"
          extra={
            <Button
              type="primary"
              icon={<RocketOutlined />}
              onClick={() => onOptimize(selectedEval.sceneId, config_patch)}
              loading={isOptimizing}
            >
              ✨ 一键优化
            </Button>
          }
        >
          <Space wrap>
            {Object.entries(config_patch).map(([key, value]) => (
              <Tag key={key} color="blue">
                {key}: {String(value)}
              </Tag>
            ))}
          </Space>
        </Card>
      )}

      {/* 权重说明 */}
      <Card size="small" title="评分权重">
        <Row gutter={8}>
          <Col span={8}>
            <Tag color={COLORS.walkability}>步行性 45%</Tag>
          </Col>
          <Col span={8}>
            <Tag color={COLORS.safety}>安全性 35%</Tag>
          </Col>
          <Col span={8}>
            <Tag color={COLORS.beauty}>美观度 20%</Tag>
          </Col>
        </Row>
        <Divider style={{ margin: "8px 0" }} />
        <Text type="secondary" style={{ fontSize: 12, fontFamily: "monospace" }}>
          综合 = 0.45×W + 0.35×S + 0.20×B
        </Text>
      </Card>
    </Space>
  );
}
