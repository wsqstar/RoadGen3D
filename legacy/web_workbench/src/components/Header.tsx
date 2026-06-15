import { Select } from "antd";
import { VIEWER_BASE, DEFAULT_GRAPH_TEMPLATE_ID } from "../lib/types";
import { WORKFLOW_STEPS } from "../lib/constants";
import type { WorkflowStep } from "../lib/types";

interface GraphTemplate {
  template_id: string;
  label: string;
}

interface HeaderProps {
  currentStep: WorkflowStep;
  templates: GraphTemplate[];
  selectedTemplateId: string;
  onTemplateChange: (templateId: string) => void;
}

export function Header({ currentStep, templates, selectedTemplateId, onTemplateChange }: HeaderProps) {
  return (
    <header className="workbench-header">
      <div className="header-left">
        <div className="header-title-row">
          <h1>RoadGen3D 智能生成工作台</h1>
          <div className="header-template-selector">
            <label>图底模板:</label>
            <Select
              value={selectedTemplateId}
              onChange={onTemplateChange}
              style={{ width: 200 }}
              size="small"
              placeholder="选择模板"
            >
              {templates.map((t) => (
                <Select.Option key={t.template_id} value={t.template_id}>
                  {t.label}
                </Select.Option>
              ))}
              {templates.length === 0 && (
                <Select.Option value={DEFAULT_GRAPH_TEMPLATE_ID}>
                  {DEFAULT_GRAPH_TEMPLATE_ID} (默认)
                </Select.Option>
              )}
            </Select>
          </div>
        </div>
        <nav className="step-indicator">
          {WORKFLOW_STEPS.map((s) => {
            const isActive = s.step === currentStep;
            const isCompleted = s.step < currentStep;
            return (
              <div key={s.step} className={`step ${isActive ? "active" : ""} ${isCompleted ? "completed" : ""}`}>
                <span className="step-number">{s.step}</span>
                <span className="step-label">{s.label}</span>
              </div>
            );
          })}
        </nav>
      </div>
      <div className="header-right">
        <a href={VIEWER_BASE} target="_blank" rel="noreferrer" className="viewer-link">
          打开独立 Viewer
        </a>
      </div>
    </header>
  );
}
