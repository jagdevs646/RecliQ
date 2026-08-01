import { CheckCircle2, Clock3, Loader2, XCircle } from "lucide-react";

interface Props {
  status: string;
}

export function StatusBadge({ status }: Props) {
  const value = status.toLowerCase();
  const Icon = value === "completed" ? CheckCircle2 : value === "failed" ? XCircle : value === "processing" ? Loader2 : Clock3;
  return (
    <span className={`status-badge status-${value}`}>
      <Icon size={14} />
      {status}
    </span>
  );
}

