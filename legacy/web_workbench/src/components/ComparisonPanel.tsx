/**
 * ComparisonPanel - PNG + JSON 场景对比面板
 * 支持两个方案的详细对比，包括配置、指标、放置差异和2D差异图
 */

import { useState, useEffect } from "react";
import { Card, Tabs, Table, Tag, Space, Typography, Spin, Row, Col, Image, Empty, Button } from "antd";
import {
  ArrowUpOutlined,
  ArrowDownOutlined,
  MinusOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  SwapOutlined,
  PictureOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import type { SceneDiffResult, CompareScheme } from "../lib/types";
import { compareScenes, getDiffImageUrl } from "../lib/api";
import { COLORS } from "../theme";

const { Text, Title } = Typography;
const { TabPane } = Tabs;

interface ComparisonPanelProps {
  schemeA: CompareScheme | null;
  schemeB: CompareScheme | null;
  onBack: () => void;
}

export function ComparisonPanel({ schemeA, schemeB, onBack }: ComparisonPanelProps) {
  const [loading, setLoading] = useState(false);
  const [diffResult, setDiffResult] = useState<SceneDiffResult | null>(null);
  const [diffImageMode, setDiffImageMode] = useState<"overlay" | "delta">("overlay");

  useEffect(() => {
    if (!schemeA || !schemeB) return;

    const loadDiff = async () => {
      setLoading(true);
      try {
        const result = await compareScenes(schemeA.layoutPath, schemeB.layoutPath);
        setDiffResult(result);
      } catch (error) {
        console.error("Failed to load scene diff:", error);
      } finally {
        setLoading(false);
      }
    };

    loadDiff();
  }, [schemeA, schemeB]);

  if (!schemeA || !schemeB) {
    return (
      <Empty description="请选择两个方案进行对比">
        <Button type="primary" onClick={onBack}>
          返回方案列表
        </Button>
      </Empty>
    );
  }

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: "60px 0" }}>
        <Spin size="large" tip="正在加载对比数据..." />
      </div>
    );
  }

  const diffImageAUrl = schemeA && schemeB ? getDiffImageUrl(schemeA.layoutPath, schemeB.layoutPath, diffImageMode) : "";

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {/* 对比方案信息 */}
      <Card>
        <Row gutter={16}>
          <Col span={12}>
            <Card size="small" style={{ borderColor: COLORS.walkability }}>
              <Title level={4} style={{ color: COLORS.walkability, margin: 0 }}>
                方案 A: {schemeA.name}
              </Title>
              <div style={{ marginTop: 12 }}>
                <img
                  src={schemeA.previewUrl}
                  alt={schemeA.name}
                  style={{ width: "100%", borderRadius: 8 }}
                />
              </div>
              <div style={{ marginTop: 12 }}>
                <Space>
                  <Tag color={COLORS.walkability}>步行性 {schemeA.evaluation.walkability}</Tag>
                  <Tag color={COLORS.safety}>安全性 {schemeA.evaluation.safety}</Tag>
                  <Tag color={COLORS.beauty}>美观度 {schemeA.evaluation.beauty}</Tag>
                </Space>
              </div>
            </Card>
          </Col>
          <Col span={12}>
            <Card size="small" style={{ borderColor: COLORS.safety }}>
              <Title level={4} style={{ color: COLORS.safety, margin: 0 }}>
                方案 B: {schemeB.name}
              </Title>
              <div style={{ marginTop: 12 }}>
                <img
                  src={schemeB.previewUrl}
                  alt={schemeB.name}
                  style={{ width: "100%", borderRadius: 8 }}
                />
              </div>
              <div style={{ marginTop: 12 }}>
                <Space>
                  <Tag color={COLORS.walkability}>步行性 {schemeB.evaluation.walkability}</Tag>
                  <Tag color={COLORS.safety}>安全性 {schemeB.evaluation.safety}</Tag>
                  <Tag color={COLORS.beauty}>美观度 {schemeB.evaluation.beauty}</Tag>
                </Space>
              </div>
            </Card>
          </Col>
        </Row>
      </Card>

      {/* 对比详情 Tabs */}
      <Card>
        <Tabs defaultActiveKey="metrics">
          <TabPane
            tab={
              <Space>
                <FileTextOutlined />
                指标对比
              </Space>
            }
            key="metrics"
          >
            {diffResult && <MetricsDiffTable diff={diffResult} />}
          </TabPane>

          <TabPane
            tab={
              <Space>
                <EditOutlined />
                配置对比
              </Space>
            }
            key="config"
          >
            {diffResult && <ConfigDiffTable diff={diffResult} />}
          </TabPane>

          <TabPane
            tab={
              <Space>
                <SwapOutlined />
                放置对比
              </Space>
            }
            key="placements"
          >
            {diffResult && <PlacementsDiffTable diff={diffResult} />}
          </TabPane>

          <TabPane
            tab={
              <Space>
                <PictureOutlined />
                2D 差异图
              </Space>
            }
            key="diff-image"
          >
            <div>
              <div style={{ marginBottom: 16 }}>
                <Space>
                  <Text>模式:</Text>
                  <Button
                    type={diffImageMode === "overlay" ? "primary" : "default"}
                    size="small"
                    onClick={() => setDiffImageMode("overlay")}
                  >
                    叠加对比
                  </Button>
                  <Button
                    type={diffImageMode === "delta" ? "primary" : "default"}
                    size="small"
                    onClick={() => setDiffImageMode("delta")}
                  >
                    矢量箭头
                  </Button>
                </Space>
              </div>
              {diffImageAUrl && (
                <Image
                  src={diffImageAUrl}
                  alt="2D 差异图"
                  style={{ width: "100%", borderRadius: 8 }}
                />
              )}
            </div>
          </TabPane>
        </Tabs>
      </Card>

      {/* 返回按钮 */}
      <div>
        <Button onClick={onBack}>返回方案列表</Button>
      </div>
    </Space>
  );
}

// 指标对比表格
function MetricsDiffTable({ diff }: { diff: SceneDiffResult }) {
  const columns = [
    {
      title: "指标",
      dataIndex: "key",
      key: "key",
      render: (text: string) => <Text strong>{text}</Text>,
    },
    {
      title: "方案 A",
      dataIndex: "old",
      key: "old",
      render: (val: number | null) => (val !== null ? val.toFixed(2) : "-"),
    },
    {
      title: "方案 B",
      dataIndex: "new",
      key: "new",
      render: (val: number | null) => (val !== null ? val.toFixed(2) : "-"),
    },
    {
      title: "变化",
      dataIndex: "delta",
      key: "delta",
      render: (delta: number, record: any) => {
        if (Math.abs(delta) < 0.01) {
          return (
            <Tag color="default">
              <MinusOutlined /> 无变化
            </Tag>
          );
        }
        return delta > 0 ? (
          <Tag color="success">
            <ArrowUpOutlined /> +{delta.toFixed(2)}
          </Tag>
        ) : (
          <Tag color="error">
            <ArrowDownOutlined /> {delta.toFixed(2)}
          </Tag>
        );
      },
    },
    {
      title: "变化率",
      dataIndex: "delta_pct",
      key: "delta_pct",
      render: (pct: number | null) => {
        if (pct === null || !isFinite(pct)) return "-";
        return `${pct > 0 ? "+" : ""}${(pct * 100).toFixed(1)}%`;
      },
    },
  ];

  return (
    <Table
      columns={columns}
      dataSource={diff.metrics_diff.metrics}
      rowKey="key"
      size="small"
      pagination={false}
    />
  );
}

// 配置对比表格
function ConfigDiffTable({ diff }: { diff: SceneDiffResult }) {
  const { added, removed, changed } = diff.config_diff;

  const addedData = Object.entries(added).map(([key, value]) => ({
    key,
    field: key,
    value: String(value),
    type: "added",
  }));

  const removedData = Object.entries(removed).map(([key, value]) => ({
    key,
    field: key,
    value: String(value),
    type: "removed",
  }));

  const changedData = Object.entries(changed).map(([key, val]: [string, any]) => ({
    key,
    field: key,
    oldValue: String(val.old),
    newValue: String(val.new),
    type: "changed",
  }));

  const columns = [
    {
      title: "字段",
      dataIndex: "field",
      key: "field",
      render: (text: string) => <Text code>{text}</Text>,
    },
    {
      title: "方案 A",
      dataIndex: "oldValue",
      key: "oldValue",
      render: (val: string, record: any) => {
        if (record.type === "added") return <Tag color="success">新增</Tag>;
        if (record.type === "removed") return val ? <Text>{val}</Text> : "-";
        return val ? <Text>{val}</Text> : "-";
      },
    },
    {
      title: "方案 B",
      dataIndex: "newValue",
      key: "newValue",
      render: (val: string, record: any) => {
        if (record.type === "added") return val ? <Text code>{val}</Text> : "-";
        if (record.type === "removed") return <Tag color="error">删除</Tag>;
        return val ? <Text code>{val}</Text> : "-";
      },
    },
    {
      title: "状态",
      dataIndex: "type",
      key: "type",
      render: (type: string) => {
        if (type === "added")
          return (
            <Tag color="success">
              <PlusOutlined /> 新增
            </Tag>
          );
        if (type === "removed")
          return (
            <Tag color="error">
              <DeleteOutlined /> 删除
            </Tag>
          );
        return (
          <Tag color="warning">
            <EditOutlined /> 修改
          </Tag>
        );
      },
    },
  ];

  const allData = [...addedData, ...removedData, ...changedData];

  if (allData.length === 0) {
    return <Empty description="配置无差异" />;
  }

  return (
    <Table
      columns={columns}
      dataSource={allData}
      rowKey="key"
      size="small"
      pagination={false}
    />
  );
}

// 放置对比表格
function PlacementsDiffTable({ diff }: { diff: SceneDiffResult }) {
  const columns = [
    {
      title: "类别",
      dataIndex: "category",
      key: "category",
      render: (text: string) => <Text strong>{text}</Text>,
    },
    {
      title: "方案 A",
      dataIndex: "count_a",
      key: "count_a",
    },
    {
      title: "方案 B",
      dataIndex: "count_b",
      key: "count_b",
    },
    {
      title: "差异",
      dataIndex: "delta",
      key: "delta",
      render: (delta: number) => {
        if (delta === 0) return <Tag color="default">0</Tag>;
        return delta > 0 ? (
          <Tag color="success">+{delta}</Tag>
        ) : (
          <Tag color="error">{delta}</Tag>
        );
      },
    },
    {
      title: "匹配",
      dataIndex: "matched",
      key: "matched",
    },
    {
      title: "新增",
      dataIndex: "added",
      key: "added",
      render: (val: number) => (val > 0 ? <Tag color="success">{val}</Tag> : "0"),
    },
    {
      title: "删除",
      dataIndex: "deleted",
      key: "deleted",
      render: (val: number) => (val > 0 ? <Tag color="error">{val}</Tag> : "0"),
    },
    {
      title: "移动",
      dataIndex: "moved",
      key: "moved",
      render: (val: number) => (val > 0 ? <Tag color="warning">{val}</Tag> : "0"),
    },
    {
      title: "平均移动距离 (m)",
      dataIndex: "mean_position_shift_m",
      key: "mean_position_shift_m",
      render: (val: number) => val.toFixed(2),
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Space>
          <Text strong>总计:</Text>
          <Tag>方案 A: {diff.placements_diff.total_count_a}</Tag>
          <Tag>方案 B: {diff.placements_diff.total_count_b}</Tag>
          <Tag color={diff.placements_diff.total_delta > 0 ? "success" : "error"}>
            差异: {diff.placements_diff.total_delta > 0 ? "+" : ""}
            {diff.placements_diff.total_delta}
          </Tag>
        </Space>
      </div>
      <Table
        columns={columns}
        dataSource={diff.placements_diff.category_stats}
        rowKey="category"
        size="small"
        pagination={false}
      />
    </div>
  );
}
