import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Circle, CircleDot } from "lucide-react";
import { useEffect, useRef, type KeyboardEvent } from "react";

export type RuleSetupStatus = "not-configured" | "partial" | "configured" | "error";

export interface RuleNavigationItem {
  id: string;
  label: string;
  title: string;
  status: RuleSetupStatus;
  statusText?: string;
}

interface Props {
  items: RuleNavigationItem[];
  activeId: string;
  onSelect: (id: string) => void;
  label: string;
}

const STATUS_LABELS: Record<RuleSetupStatus, string> = {
  "not-configured": "Not configured",
  partial: "Partially configured",
  configured: "Configured",
  error: "Validation error",
};

function StatusIcon({ status }: { status: RuleSetupStatus }) {
  if (status === "configured") return <CheckCircle2 size={15} aria-hidden="true" />;
  if (status === "partial") return <CircleDot size={15} aria-hidden="true" />;
  if (status === "error") return <AlertTriangle size={15} aria-hidden="true" />;
  return <Circle size={15} aria-hidden="true" />;
}

export function RuleNavigator({ items, activeId, onSelect, label }: Props) {
  const tabs = useRef(new Map<string, HTMLButtonElement>());
  const selectedIndex = Math.max(0, items.findIndex((item) => item.id === activeId));
  const selected = items[selectedIndex];

  useEffect(() => {
    if (!selected) return;
    const tab = tabs.current.get(selected.id);
    if (!tab || typeof tab.scrollIntoView !== "function") return;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    tab.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest", inline: "nearest" });
  }, [selected?.id]);

  if (!selected) return null;

  function selectByKeyboard(event: KeyboardEvent<HTMLDivElement>) {
    let nextIndex: number | undefined;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (selectedIndex + 1) % items.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (selectedIndex - 1 + items.length) % items.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = items.length - 1;
    if (nextIndex === undefined) return;
    event.preventDefault();
    const next = items[nextIndex];
    onSelect(next.id);
    window.requestAnimationFrame(() => tabs.current.get(next.id)?.focus());
  }

  const selectRelative = (offset: number) => {
    const next = items[selectedIndex + offset];
    if (next) onSelect(next.id);
  };

  return <nav className="workflow-rule-navigator" aria-label={`${label} rule navigation`}>
    <button className="icon-button rule-nav-arrow" type="button" aria-label="Previous rule" title="Previous rule" onClick={() => selectRelative(-1)} disabled={selectedIndex === 0}>
      <ChevronLeft size={18} />
    </button>
    <div className="rule-tabs-wrap">
      <div className="rule-tabs" role="tablist" aria-label={label} onKeyDown={selectByKeyboard}>
        {items.map((item, index) => {
          const isActive = item.id === selected.id;
          const statusLabel = item.statusText ?? STATUS_LABELS[item.status];
          return <button
            key={item.id}
            ref={(element) => { if (element) tabs.current.set(item.id, element); else tabs.current.delete(item.id); }}
            id={`rule-tab-${index + 1}`}
            className={`rule-tab is-${item.status}${isActive ? " is-active" : ""}`}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-controls="active-rule-panel"
            aria-label={`Rule ${index + 1}: ${item.title}. ${statusLabel}`}
            tabIndex={isActive ? 0 : -1}
            title={`Rule ${index + 1} · ${item.title} · ${statusLabel}`}
            onClick={() => onSelect(item.id)}
          >
            <span className="rule-tab-topline"><strong>Rule {index + 1}</strong><span className="rule-tab-status" aria-label={statusLabel} title={statusLabel}><StatusIcon status={item.status} /></span></span>
            <span className="rule-tab-label">{item.label}</span>
            <span className="rule-tab-status-text">{statusLabel}</span>
          </button>;
        })}
      </div>
      <span className="rule-tab-count" aria-live="polite">{selectedIndex + 1} of {items.length}</span>
    </div>
    <button className="icon-button rule-nav-arrow" type="button" aria-label="Next rule" title="Next rule" onClick={() => selectRelative(1)} disabled={selectedIndex === items.length - 1}>
      <ChevronRight size={18} />
    </button>
  </nav>;
}
