import { AlertTriangle, Check } from "lucide-react";

const steps = ["Upload", "Pair sheets", "Matching key", "Map columns", "Report setup", "Run"];

export type WorkflowStepStatus = "complete" | "current" | "upcoming" | "error";

interface Props {
  current: number;
  maxVisitedStep: number;
  statuses: WorkflowStepStatus[];
  onSelect: (step: number) => void;
}

export function WorkflowSteps({ current, maxVisitedStep, statuses, onSelect }: Props) {
  return (
    <ol className="workflow-steps" aria-label="Reconciliation setup progress">
      {steps.map((label, index) => {
        const step = index + 1;
        const status = statuses[index] ?? "upcoming";
        const complete = status === "complete";
        const error = status === "error";
        const currentStep = step === current;
        const disabled = step > maxVisitedStep;
        return (
          <li key={label} className={`${currentStep ? "is-current" : ""}${complete ? " is-complete" : ""}${error ? " has-error" : ""}`}>
            <button type="button" onClick={() => onSelect(step)} disabled={disabled} aria-current={currentStep ? "step" : undefined} aria-label={`Step ${step}: ${label}${currentStep ? ", current step" : complete ? ", complete" : error ? ", needs attention" : ", upcoming"}`}>
              <span className="step-number">{complete ? <Check size={15} strokeWidth={3} /> : error ? <AlertTriangle size={15} /> : step}</span>
              <span className="workflow-step-label">{label}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
