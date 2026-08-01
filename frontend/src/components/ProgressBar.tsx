interface Props {
  value: number;
}

export function ProgressBar({ value }: Props) {
  const bounded = Math.max(0, Math.min(100, value));
  return (
    <div className="progress" aria-label={`Progress ${bounded}%`}>
      <div style={{ width: `${bounded}%` }} />
    </div>
  );
}

