/**
 * SceneCompareModal - 3D 场景对比模态框
 * 在 Viewer 中打开两个方案进行 3D 对比
 */

import { Modal, Button, Space, Typography, Select } from "antd";
import { useState } from "react";
import { EyeOutlined } from "@ant-design/icons";
import type { CompareScheme } from "../lib/types";
import { VIEWER_BASE } from "../lib/types";

const { Text } = Typography;

interface SceneCompareModalProps {
  schemes: CompareScheme[];
  visible: boolean;
  onClose: () => void;
}

export function SceneCompareModal({ schemes, visible, onClose }: SceneCompareModalProps) {
  const [schemeAId, setSchemeAId] = useState<string>(schemes[0]?.id || "");
  const [schemeBId, setSchemeBId] = useState<string>(schemes[1]?.id || "");

  const schemeA = schemes.find((s) => s.id === schemeAId);
  const schemeB = schemes.find((s) => s.id === schemeBId);

  const handleOpenCompare = () => {
    if (!schemeA || !schemeB) return;

    // 打开 Viewer 并传入两个方案的 layout path
    const viewerUrl = `${VIEWER_BASE}/?compare=true&layoutA=${encodeURIComponent(
      schemeA.layoutPath
    )}&layoutB=${encodeURIComponent(schemeB.layoutPath)}`;

    window.open(viewerUrl, "_blank");
    onClose();
  };

  return (
    <Modal
      title={
        <Space>
          <EyeOutlined />
          3D 场景对比
        </Space>
      }
      open={visible}
      onCancel={onClose}
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            onClick={handleOpenCompare}
            disabled={!schemeA || !schemeB || schemeAId === schemeBId}
          >
            在 Viewer 中对比
          </Button>
        </Space>
      }
      width={600}
    >
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <div>
          <Text strong>选择方案 A:</Text>
          <Select
            value={schemeAId}
            onChange={setSchemeAId}
            style={{ width: "100%", marginTop: 8 }}
            options={schemes.map((s) => ({
              value: s.id,
              label: `${s.name} (步行性: ${s.evaluation.walkability}, 安全性: ${s.evaluation.safety}, 美观度: ${s.evaluation.beauty})`,
            }))}
          />
        </div>

        <div>
          <Text strong>选择方案 B:</Text>
          <Select
            value={schemeBId}
            onChange={setSchemeBId}
            style={{ width: "100%", marginTop: 8 }}
            options={schemes.map((s) => ({
              value: s.id,
              label: `${s.name} (步行性: ${s.evaluation.walkability}, 安全性: ${s.evaluation.safety}, 美观度: ${s.evaluation.beauty})`,
            }))}
          />
        </div>

        {schemeA && schemeB && schemeAId !== schemeBId && (
          <div style={{ padding: 16, background: "#f5f5f5", borderRadius: 8 }}>
            <Text type="secondary">
              将打开 Viewer 的 3D 对比模式，左右分屏显示两个方案，支持同步相机控制和指标对比。
            </Text>
          </div>
        )}
      </Space>
    </Modal>
  );
}
