import { VIEWER_BASE } from "../lib/types";
import { WORKFLOW_STEPS } from "../lib/constants";
import type { WorkflowStep } from "../lib/types";

interface HeaderProps {
  currentStep: WorkflowStep;
}

export function Header({ currentStep }: HeaderProps) {
  return (
    <header className="workbench-header">
      <div className="header-left">
        <h1>RoadGen3D 智能生成工作台</h1>
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
