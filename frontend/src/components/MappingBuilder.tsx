import { ArrowRight, Check, CircleHelp, Download, Plus, Redo2, Save, Search, Sparkles, Trash2, Undo2, Upload } from "lucide-react";
import { useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { filterColumns, matchRanges, searchTerms } from "../lib/columnSearch";
import { TOLERANCE_KINDS, TOLERANCE_LIMITS, clearTolerance, comparedFieldLabel, guessToleranceKind, setToleranceLimit, toleranceFor, toleranceIsComplete, toleranceKind, toleranceLimit, type ToleranceKind } from "../lib/plan";
import type { AnalysisResponse, RuleMapping, ToleranceBand } from "../types";
import { ToleranceHelp } from "./RuleExtras";

interface Props {
  file1Columns: string[];
  file2Columns: string[];
  rules: RuleMapping[];
  onRulesChange: (rules: RuleMapping[]) => void;
  primaryFile1?: string | string[];
  primaryFile2?: string | string[];
  file1Name?: string;
  file2Name?: string;
  /** Column pairs RecliQ thinks belong together; offered, never auto-applied. */
  suggestions?: Array<{ source: string; target: string }>;
  /** Accepted differences, set per mapped field next to its mapping. */
  tolerances?: ToleranceBand[];
  onTolerancesChange?: (tolerances: ToleranceBand[]) => void;
  /** Shown above the mapped fields, e.g. tolerances on offer from the matching step. */
  toleranceNotice?: ReactNode;
  /** What RecliQ learned about the columns, to pick each field's type of data. */
  analysis?: AnalysisResponse | null;
}

type MappingMode = "drag" | "rows";
type AutoMatch = { source: string; destination: string; confidence: number };

const templateKey = "recliq.mapping.templates";
const lastMappingKey = "recliq.mapping.last";

function normalise(value: string) {
  const aliases: Record<string, string> = { emp: "employee", empl: "employee", dept: "department", gross: "salary", amt: "amount", no: "number", num: "number" };
  return value.toLowerCase().replace(/[_\-/.]+/g, " ").split(/\s+/).filter(Boolean).map((word) => aliases[word] ?? word);
}

function matchConfidence(source: string, destination: string) {
  const left = normalise(source);
  const right = normalise(destination);
  if (left.join("") === right.join("")) return 100;
  const common = left.filter((word) => right.includes(word));
  if (!common.length) return 0;
  if (common.length === Math.min(left.length, right.length)) return 94;
  if (common.some((word) => ["id", "name", "salary", "amount", "date", "department"].includes(word))) return 88;
  return Math.round((common.length / Math.max(left.length, right.length)) * 90);
}

export function MappingBuilder({ file1Columns, file2Columns, rules, onRulesChange, primaryFile1, primaryFile2, file1Name, file2Name, suggestions = [], tolerances = [], onTolerancesChange: editTolerances, toleranceNotice, analysis }: Props) {
  // A type of data the user picked over the detected one, by compared field.
  const [pickedKinds, setPickedKinds] = useState<Record<string, ToleranceKind>>({});
  const [mode, setMode] = useState<MappingMode>("drag");
  const [left, setLeft] = useState<string[]>([]);
  const [right, setRight] = useState<string[]>([]);
  const [past, setPast] = useState<RuleMapping[][]>([]);
  const [future, setFuture] = useState<RuleMapping[][]>([]);
  const [templateName, setTemplateName] = useState("");
  const [autoMatches, setAutoMatches] = useState<AutoMatch[]>([]);
  const primaryFile1Columns = Array.isArray(primaryFile1) ? primaryFile1 : primaryFile1 ? [primaryFile1] : [];
  const primaryFile2Columns = Array.isArray(primaryFile2) ? primaryFile2 : primaryFile2 ? [primaryFile2] : [];
  const availableFile1 = useMemo(() => file1Columns.filter((column) => !primaryFile1Columns.includes(column)), [file1Columns, primaryFile1Columns]);
  const availableFile2 = useMemo(() => file2Columns.filter((column) => !primaryFile2Columns.includes(column)), [file2Columns, primaryFile2Columns]);
  const mappedSource = new Set(rules.flatMap((rule) => rule.file_1_fields));
  const mappedDestination = new Set(rules.flatMap((rule) => rule.file_2_fields));
  const openSuggestions = suggestions.filter((suggestion) => (
    availableFile1.includes(suggestion.source) && availableFile2.includes(suggestion.target)
    && !mappedSource.has(suggestion.source) && !mappedDestination.has(suggestion.target)
  ));

  const sourceTitle = file1Name ? `${file1Name} columns` : "Source columns";
  const destTitle = file2Name ? `${file2Name} columns` : "Destination columns";
  const sourceFieldTitle = file1Name ? `${file1Name} fields` : "Source fields";
  const destFieldTitle = file2Name ? `${file2Name} fields` : "Destination fields";

  function commit(next: RuleMapping[]) {
    setPast((items) => [...items, rules]);
    setFuture([]);
    onRulesChange(next);
    localStorage.setItem(lastMappingKey, JSON.stringify(next));
  }

  function addRule(source = left, destination = right) {
    if (!source.length || !destination.length) return;
    commit([...rules, { file_1_fields: source, file_2_fields: destination }]);
    setLeft([]);
    setRight([]);
  }

  function toggle(values: string[], value: string, setter: (value: string[]) => void) {
    setter(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);
  }

  function dropOn(destination: string, source: string) {
    if (mappedSource.has(source) || mappedDestination.has(destination)) return;
    addRule([source], [destination]);
  }

  function autoMap() {
    const candidates: AutoMatch[] = [];
    const usedDestinations = new Set(mappedDestination);
    for (const source of availableFile1.filter((column) => !mappedSource.has(column))) {
      const best = availableFile2.filter((column) => !usedDestinations.has(column)).map((destination) => ({ source, destination, confidence: matchConfidence(source, destination) })).sort((a, b) => b.confidence - a.confidence)[0];
      if (best && best.confidence >= 85) {
        candidates.push(best);
        usedDestinations.add(best.destination);
      }
    }
    if (candidates.length) commit([...rules, ...candidates.map(({ source, destination }) => ({ file_1_fields: [source], file_2_fields: [destination] }))]);
    setAutoMatches(candidates);
  }

  function undo() {
    const previous = past[past.length - 1];
    if (!previous) return;
    setPast((items) => items.slice(0, -1));
    setFuture((items) => [rules, ...items]);
    onRulesChange(previous);
  }

  function redo() {
    const next = future[0];
    if (!next) return;
    setFuture((items) => items.slice(1));
    setPast((items) => [...items, rules]);
    onRulesChange(next);
  }

  function saveTemplate() {
    const name = templateName.trim();
    if (!name || !rules.length) return;
    const existing = JSON.parse(localStorage.getItem(templateKey) ?? "{}") as Record<string, RuleMapping[]>;
    localStorage.setItem(templateKey, JSON.stringify({ ...existing, [name]: rules }));
    setTemplateName("");
  }

  function loadTemplate(event: React.ChangeEvent<HTMLSelectElement>) {
    const name = event.target.value;
    const templates = JSON.parse(localStorage.getItem(templateKey) ?? "{}") as Record<string, RuleMapping[]>;
    if (name && templates[name]) commit(templates[name]);
    event.target.value = "";
  }

  function loadLastMapping() {
    const previous = localStorage.getItem(lastMappingKey);
    if (previous) commit(JSON.parse(previous) as RuleMapping[]);
  }

  const templateNames = Object.keys(JSON.parse(localStorage.getItem(templateKey) ?? "{}") as Record<string, RuleMapping[]>);
  return <section className="mapping-workspace">
    <div className="section-heading">
      <div><h2>Column mapping</h2><p>Map fields for comparison. Your matching key is already excluded.</p></div>
      <span className="mapping-count">{rules.length} / {Math.max(availableFile1.length, availableFile2.length)} mapped</span>
    </div>
    <div className="mapping-toolbar">
      <div className="segmented-control" aria-label="Mapping mode"><button type="button" className={mode === "drag" ? "is-active" : ""} onClick={() => setMode("drag")}>Drag & drop</button><button type="button" className={mode === "rows" ? "is-active" : ""} onClick={() => setMode("rows")}>Row mapping</button></div>
      <button type="button" className="primary" onClick={autoMap} title="Map compatible unmapped columns without changing your manual mappings"><Sparkles size={16} />Auto map columns</button>
      <div className="icon-actions"><button type="button" className="icon-button" onClick={undo} disabled={!past.length} title="Undo mapping"><Undo2 size={16} /></button><button type="button" className="icon-button" onClick={redo} disabled={!future.length} title="Redo mapping"><Redo2 size={16} /></button><button type="button" className="icon-button" onClick={() => commit([])} disabled={!rules.length} title="Reset all mappings"><Trash2 size={16} /></button></div>
    </div>
    {openSuggestions.length > 0 && <div className="suggestion-strip">
      <span>Suggested pairs</span>
      {openSuggestions.map((suggestion) => <button type="button" key={`${suggestion.source}-${suggestion.target}`} className="suggestion-chip" onClick={() => commit([...rules, { file_1_fields: [suggestion.source], file_2_fields: [suggestion.target] }])} title="Add this mapping">
        <Plus size={14} />{suggestion.source} <ArrowRight size={12} /> {suggestion.target}
      </button>)}
      {openSuggestions.length > 1 && <button type="button" className="text-command" onClick={() => commit([...rules, ...openSuggestions.map((suggestion) => ({ file_1_fields: [suggestion.source], file_2_fields: [suggestion.target] }))])}>Add all</button>}
    </div>}
    {mode === "drag" ? <div className="mapping-boards">
      <ColumnBoard title={sourceTitle} columns={availableFile1} selected={left} mapped={mappedSource} onToggle={(column) => toggle(left, column, setLeft)} draggable onDropColumn={dropOn} />
      <div className="mapping-bridge"><ArrowRight size={24} /><button className="secondary" type="button" onClick={() => addRule()} disabled={!left.length || !right.length}><Plus size={16} />Map selected</button><small>Select one or more fields on each side to create a combined rule.</small></div>
      <ColumnBoard title={destTitle} columns={availableFile2} selected={right} mapped={mappedDestination} onToggle={(column) => toggle(right, column, setRight)} droppable />
    </div> : <RowMappingEditor
      sourceColumns={availableFile1.filter((column) => !mappedSource.has(column))}
      destinationColumns={availableFile2.filter((column) => !mappedDestination.has(column))}
      sourceSelection={left}
      destinationSelection={right}
      sourceTitle={sourceFieldTitle}
      destTitle={destFieldTitle}
      onSourceToggle={(column) => toggle(left, column, setLeft)}
      onDestinationToggle={(column) => toggle(right, column, setRight)}
      onAdd={() => addRule()}
      onClear={() => { setLeft([]); setRight([]); }}
    />}
    {toleranceNotice}
    {editTolerances && rules.length > 0 && <div className="mapping-list-heading"><span>Mapped fields</span><span>Tolerance (optional) <ToleranceHelp destinationLabel={file2Name ?? "the destination file"} /></span></div>}
    <div className="mapping-list">{rules.length === 0 ? <div className="mapping-empty"><CircleHelp size={20} /><p>No mapped fields yet. Drag a source field to its destination, select field groups, or use Auto map columns.</p></div> : rules.map((rule, index) => <div className={`mapping-row${editTolerances ? " has-tolerance" : ""}`} key={`${rule.file_1_fields.join(",")}-${rule.file_2_fields.join(",")}-${index}`}><span>{rule.file_1_fields.join(" + ")}</span><ArrowRight size={16} /><span>{rule.file_2_fields.join(" + ")}</span>{editTolerances && <RuleTolerance field={comparedFieldLabel(rule)} guessed={guessToleranceKind(rule, analysis)} kind={pickedKinds[comparedFieldLabel(rule)] ?? toleranceKind(toleranceFor(tolerances, comparedFieldLabel(rule))) ?? guessToleranceKind(rule, analysis)} tolerances={tolerances} onKind={(kind) => setPickedKinds((current) => ({ ...current, [comparedFieldLabel(rule)]: kind }))} onChange={editTolerances} />}<button type="button" className="icon-button" onClick={() => commit(rules.filter((_, itemIndex) => itemIndex !== index))} title="Delete mapping"><Trash2 size={16} /></button></div>)}</div>
    {editTolerances && tolerances.some((band) => !toleranceIsComplete(band, { rules })) && <p className="field-hint is-warning">A tolerance of 0 accepts nothing: enter a value above 0, or clear the box for no tolerance.</p>}
    {autoMatches.length > 0 && <p className="success-text"><Check size={16} />{autoMatches.length} columns mapped automatically: {autoMatches.map((match) => `${match.source} → ${match.destination}`).join(", ")}. Remove any that are wrong.</p>}
    <div className="template-toolbar"><div className="template-save"><input value={templateName} onChange={(event) => setTemplateName(event.target.value)} placeholder="Template name" /><button type="button" className="secondary" onClick={saveTemplate} disabled={!templateName.trim() || !rules.length}><Save size={16} />Save mapping</button></div><select defaultValue="" onChange={loadTemplate} aria-label="Load mapping template"><option value="">Load a saved mapping</option>{templateNames.map((name) => <option key={name}>{name}</option>)}</select><button type="button" className="secondary" onClick={loadLastMapping} title="Duplicate the most recently changed mapping"><Download size={16} />Duplicate previous</button><button type="button" className="icon-button" title="Importing templates is planned for a future release" disabled><Upload size={16} /></button></div>
  </section>;
}

/** One mapped field's tolerance: the type of data, then the limit boxes that type takes. Empty boxes mean no tolerance. */
function RuleTolerance({ field, kind, guessed, tolerances, onKind, onChange }: { field: string; kind: ToleranceKind; guessed: ToleranceKind; tolerances: ToleranceBand[]; onKind: (kind: ToleranceKind) => void; onChange: (tolerances: ToleranceBand[]) => void }) {
  const band = toleranceFor(tolerances, field);
  const limits = TOLERANCE_KINDS.find((item) => item.value === kind)?.limits ?? [];
  return <div className="rule-tolerance">
    <select value={kind} aria-label={`Type of data in ${field}`} title={kind === guessed ? "Detected from the column. Change it if it is wrong." : "Type of data in this field"} onChange={(event) => { onKind(event.target.value as ToleranceKind); if (band) onChange(clearTolerance(tolerances, field)); }}>
      {TOLERANCE_KINDS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
    </select>
    {limits.map((limit) => {
      const settings = TOLERANCE_LIMITS[limit];
      return <input key={limit} type="number" min={0} max={settings.max} step={settings.step} inputMode={settings.whole ? "numeric" : "decimal"} value={band?.[limit] ?? ""} placeholder={settings.placeholder} title={settings.label} aria-label={`${settings.label} for ${field}`} onChange={(event) => onChange(setToleranceLimit(tolerances, field, kind, limit, toleranceLimit(limit, event.target.value)))} />;
    })}
  </div>;
}

interface BoardProps {
  title: string;
  columns: string[];
  selected: string[];
  mapped: Set<string>;
  onToggle: (column: string) => void;
  draggable?: boolean;
  droppable?: boolean;
  onDropColumn?: (destination: string, source: string) => void;
}

function ColumnBoard({ title, columns, selected, mapped, onToggle, draggable, droppable, onDropColumn }: BoardProps) {
  const [query, setQuery] = useState("");
  const shown = filterColumns(columns, query);
  return <div className="column-board"><h3>{title}</h3><ColumnSearch label={`Search ${title}`} query={query} total={columns.length} shown={shown.length} onChange={setQuery} onPickFirst={() => shown[0] && onToggle(shown[0])} /><div>{shown.map((column) => <button key={column} type="button" draggable={draggable && !mapped.has(column)} className={`column-chip ${selected.includes(column) ? "is-selected" : ""} ${mapped.has(column) ? "is-mapped" : ""}`} onClick={() => onToggle(column)} onDragStart={(event) => event.dataTransfer.setData("text/plain", column)} onDragOver={(event) => { if (droppable && !mapped.has(column)) event.preventDefault(); }} onDrop={(event) => { if (droppable && !mapped.has(column)) onDropColumn?.(column, event.dataTransfer.getData("text/plain")); }}><span><Highlighted text={column} query={query} /></span>{mapped.has(column) && <Check size={14} />}</button>)}</div><NoMatches query={query} shown={shown.length} onClear={() => setQuery("")} /></div>;
}

interface RowMappingEditorProps {
  sourceColumns: string[];
  destinationColumns: string[];
  sourceSelection: string[];
  destinationSelection: string[];
  sourceTitle?: string;
  destTitle?: string;
  onSourceToggle: (column: string) => void;
  onDestinationToggle: (column: string) => void;
  onAdd: () => void;
  onClear: () => void;
}

function RowMappingEditor({
  sourceColumns,
  destinationColumns,
  sourceSelection,
  destinationSelection,
  sourceTitle = "Source fields",
  destTitle = "Destination fields",
  onSourceToggle,
  onDestinationToggle,
  onAdd,
  onClear,
}: RowMappingEditorProps) {
  const [sourceQuery, setSourceQuery] = useState("");
  const [destinationQuery, setDestinationQuery] = useState("");
  const shownSource = filterColumns(sourceColumns, sourceQuery);
  const shownDestination = filterColumns(destinationColumns, destinationQuery);
  const clearSearch = () => { setSourceQuery(""); setDestinationQuery(""); };
  const options = (columns: string[], selection: string[], query: string, onToggle: (column: string) => void) => <div className="row-mapping-options">{columns.map((column) => <label key={column} className="row-mapping-option"><input type="checkbox" checked={selection.includes(column)} onChange={() => onToggle(column)} /><span><Highlighted text={column} query={query} /></span></label>)}</div>;

  return <section className="row-mapping-editor">
    <div className="row-mapping-selection">
      <div className="row-mapping-selection-header"><div><h3>{sourceTitle}</h3><p>Choose one or more numeric fields to combine.</p></div><strong>{sourceSelection.length} selected</strong></div>
      <ColumnSearch label={`Search ${sourceTitle}`} query={sourceQuery} total={sourceColumns.length} shown={shownSource.length} onChange={setSourceQuery} onPickFirst={() => shownSource[0] && onSourceToggle(shownSource[0])} />
      {options(shownSource, sourceSelection, sourceQuery, onSourceToggle)}
      <NoMatches query={sourceQuery} shown={shownSource.length} onClear={() => setSourceQuery("")} />
      <SelectedFields fields={sourceSelection} />
    </div>
    <div className="row-mapping-arrow"><ArrowRight size={22} /><span>compare</span></div>
    <div className="row-mapping-selection">
      <div className="row-mapping-selection-header"><div><h3>{destTitle}</h3><p>Select every numeric component to add together.</p></div><strong>{destinationSelection.length} selected</strong></div>
      <ColumnSearch label={`Search ${destTitle}`} query={destinationQuery} total={destinationColumns.length} shown={shownDestination.length} onChange={setDestinationQuery} onPickFirst={() => shownDestination[0] && onDestinationToggle(shownDestination[0])} />
      {options(shownDestination, destinationSelection, destinationQuery, onDestinationToggle)}
      <NoMatches query={destinationQuery} shown={shownDestination.length} onClear={() => setDestinationQuery("")} />
      <SelectedFields fields={destinationSelection} />
    </div>
    <div className="row-mapping-actions"><button type="button" className="secondary" onClick={() => { onClear(); clearSearch(); }} disabled={!sourceSelection.length && !destinationSelection.length}>Clear selection</button><button type="button" className="primary" onClick={() => { onAdd(); clearSearch(); }} disabled={!sourceSelection.length || !destinationSelection.length}><Plus size={16} />Add mapping</button></div>
  </section>;
}

interface ColumnSearchProps { label: string; query: string; total: number; shown: number; onChange: (query: string) => void; onPickFirst: () => void }

/** Narrows a long header list. Enter selects the first match and clears the box for the next search; Escape clears it. */
function ColumnSearch({ label, query, total, shown, onChange, onPickFirst }: ColumnSearchProps) {
  // Short lists are easier to scan than to search.
  if (total <= 8 && !query) return null;
  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      if (query.trim() && shown) { onPickFirst(); onChange(""); }
    } else if (event.key === "Escape" && query) {
      event.preventDefault();
      onChange("");
    }
  }
  return <label className="search-field column-search">
    <Search size={15} aria-hidden="true" />
    <input type="search" value={query} onChange={(event) => onChange(event.target.value)} onKeyDown={onKeyDown} placeholder={`Search ${total} columns`} aria-label={label} title="Enter selects the first match" />
    {query.trim() && <span className="column-search-count" aria-live="polite">{shown} of {total}</span>}
  </label>;
}

function NoMatches({ query, shown, onClear }: { query: string; shown: number; onClear: () => void }) {
  if (shown || !query.trim()) return null;
  return <p className="column-search-empty">No columns match "{query.trim()}". <button type="button" className="text-command" onClick={onClear}>Clear search</button></p>;
}

/** The column name with the searched words marked. */
function Highlighted({ text, query }: { text: string; query: string }) {
  const terms = searchTerms(query);
  const ranges = terms.length ? matchRanges(text, terms) : null;
  if (!ranges) return <>{text}</>;
  const parts: ReactNode[] = [];
  let at = 0;
  for (const [start, end] of ranges) {
    if (start > at) parts.push(text.slice(at, start));
    parts.push(<mark key={start}>{text.slice(start, end)}</mark>);
    at = end;
  }
  parts.push(text.slice(at));
  return <>{parts}</>;
}

function SelectedFields({ fields }: { fields: string[] }) {
  return <div className="selected-fields-preview">{fields.length ? fields.map((field) => <span key={field}>{field}</span>) : <small>No fields selected</small>}</div>;
}
