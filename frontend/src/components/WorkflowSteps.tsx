import { Check } from "lucide-react";

const steps = ["Upload files", "Matching key", "Map columns", "Report setup", "Run reconciliation"];

interface Props {
  current: number;
  completedThrough: number;
  onSelect: (step: number) => void;
}

export function WorkflowSteps({ current, completedThrough, onSelect }: Props) {
  return (
    <ol className="workflow-steps" aria-label="Reconciliation setup progress">
      {steps.map((label, index) => {
        const step = index + 1;
        const complete = step < current || step <= completedThrough;
        return (
          <li key={label} className={step === current ? "is-current" : complete ? "is-complete" : ""}>
            <button type="button" onClick={() => step <= completedThrough + 1 && onSelect(step)} disabled={step > completedThrough + 1}>
              <span className="step-number">{complete ? <Check size={15} strokeWidth={3} /> : step}</span>
              <span>{label}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
