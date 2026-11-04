import { useState, type ReactNode } from "react";
import { CUSTOM_LEVEL, type LevelOption } from "../lib/matchLevels";

interface Props<V> {
  options: LevelOption<V>[];
  /** The level id of the current value: an option's id, "" for the empty choice, or "custom". */
  currentId: string;
  onSelect: (value: V | null) => void;
  ariaLabel: string;
  /** A first choice that sets no value, e.g. "No tolerance" or "Automatic". */
  emptyLabel?: string;
  /** The number boxes shown under "Custom…", for values that are not a level. */
  customEditor?: ReactNode;
}

/**
 * A named-level picker (docs/adr/0002). Choosing a level sets its number(s);
 * "Custom…" reveals the original inputs. A saved number that matches no level
 * opens as Custom, so nothing a user saved is lost or silently changed.
 */
export function LevelSelect<V>({ options, currentId, onSelect, ariaLabel, emptyLabel, customEditor }: Props<V>) {
  const [customOpen, setCustomOpen] = useState(currentId === CUSTOM_LEVEL);
  const selected = customOpen || currentId === CUSTOM_LEVEL ? CUSTOM_LEVEL : currentId;
  const level = options.find((option) => option.id === selected);
  return <span className="level-select">
    <select
      value={selected}
      aria-label={ariaLabel}
      title={level?.hint}
      onChange={(event) => {
        const id = event.target.value;
        if (id === CUSTOM_LEVEL) {
          setCustomOpen(true);
          return;
        }
        setCustomOpen(false);
        const option = options.find((item) => item.id === id);
        onSelect(option ? option.value : null);
      }}
    >
      {emptyLabel !== undefined && <option value="">{emptyLabel}</option>}
      {options.map((option) => <option key={option.id} value={option.id}>{option.hint && option.hint !== option.label ? `${option.label} (${option.hint.toLowerCase()})` : option.label}</option>)}
      {customEditor !== undefined && <option value={CUSTOM_LEVEL}>Custom…</option>}
    </select>
    {selected === CUSTOM_LEVEL && customEditor}
    {level?.warning && <small className="level-warning">{level.warning}</small>}
  </span>;
}
