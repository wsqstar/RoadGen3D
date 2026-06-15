import { WORKFLOW_STEPS } from "../lib/constants";
import type { WorkflowStep } from "../lib/types";

export function StepIndicator({ currentStep }: { currentStep: WorkflowStep }) {
  return (
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
  );
}
