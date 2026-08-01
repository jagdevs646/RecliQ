import { ArrowRight, Check, CircleHelp, Download, Plus, Redo2, Save, Sparkles, Trash2, Undo2, Upload } from "lucide-react";
import { useMemo, useState } from "react";
import type { RuleMapping } from "../types";

interface Props {
  file1Columns: string[];
  file2Columns: string[];
  rules: RuleMapping[];
  onRulesChange: (rules: RuleMapping[]) => void;
  primaryFile1?: string;
  primaryFile2?: string;
  file1Name?: string;
  file2Name?: string;
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

export function MappingBuilder({ file1Columns, file2Columns, rules, onRulesChange, primaryFile1, primaryFile2, file1Name, file2Name }: Props) {
  const [mode, setMode] = useState<MappingMode>("drag");
  const [left, setLeft] = useState<string[]>([]);
  const [right, setRight] = useState<string[]>([]);
  const [past, setPast] = useState<RuleMapping[][]>([]);
  const [future, setFuture] = useState<RuleMapping[][]>([]);
  const [templateName, setTemplateName] = useState("");
  const [autoMatches, setAutoMatches] = useState<AutoMatch[]>([]);
  const availableFile1 = useMemo(() => file1Columns.filter((column) => column !== primaryFile1), [file1Columns, primaryFile1]);
  const availableFile2 = useMemo(() => file2Columns.filter((column) => column !== primaryFile2), [file2Columns, primaryFile2]);
  const mappedSource = new Set(rules.flatMap((rule) => rule.file_1_fields));
  const mappedDestination = new Set(rules.flatMap((rule) => rule.file_2_fields));

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
    <div className="mapping-list">{rules.length === 0 ? <div className="mapping-empty"><CircleHelp size={20} /><p>No mapped fields yet. Drag a source field to its destination, select field groups, or use Auto map columns.</p></div> : rules.map((rule, index) => <div className="mapping-row" key={`${rule.file_1_fields.join(",")}-${rule.file_2_fields.join(",")}-${index}`}><span>{rule.file_1_fields.join(" + ")}</span><ArrowRight size={16} /><span>{rule.file_2_fields.join(" + ")}</span><button type="button" className="icon-button" onClick={() => commit(rules.filter((_, itemIndex) => itemIndex !== index))} title="Delete mapping"><Trash2 size={16} /></button></div>)}</div>
    {autoMatches.length > 0 && <p className="success-text"><Check size={16} />{autoMatches.length} columns mapped automatically. Confidence: {autoMatches.map((match) => `${match.source} to ${match.destination} (${match.confidence}%)`).join(", ")}.</p>}
    <div className="template-toolbar"><div className="template-save"><input value={templateName} onChange={(event) => setTemplateName(event.target.value)} placeholder="Template name" /><button type="button" className="secondary" onClick={saveTemplate} disabled={!templateName.trim() || !rules.length}><Save size={16} />Save mapping</button></div><select defaultValue="" onChange={loadTemplate} aria-label="Load mapping template"><option value="">Load a saved mapping</option>{templateNames.map((name) => <option key={name}>{name}</option>)}</select><button type="button" className="secondary" onClick={loadLastMapping} title="Duplicate the most recently changed mapping"><Download size={16} />Duplicate previous</button><button type="button" className="icon-button" title="Importing templates is planned for a future release" disabled><Upload size={16} /></button></div>
  </section>;
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
  return <div className="column-board"><h3>{title}</h3><div>{columns.map((column) => <button key={column} type="button" draggable={draggable && !mapped.has(column)} className={`column-chip ${selected.includes(column) ? "is-selected" : ""} ${mapped.has(column) ? "is-mapped" : ""}`} onClick={() => onToggle(column)} onDragStart={(event) => event.dataTransfer.setData("text/plain", column)} onDragOver={(event) => { if (droppable && !mapped.has(column)) event.preventDefault(); }} onDrop={(event) => { if (droppable && !mapped.has(column)) onDropColumn?.(column, event.dataTransfer.getData("text/plain")); }}><span>{column}</span>{mapped.has(column) && <Check size={14} />}</button>)}</div></div>;
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
  return <section className="row-mapping-editor">
    <div className="row-mapping-selection">
      <div className="row-mapping-selection-header"><div><h3>{sourceTitle}</h3><p>Choose one or more numeric fields to combine.</p></div><strong>{sourceSelection.length} selected</strong></div>
      <div className="row-mapping-options">{sourceColumns.map((column) => <label key={column} className="row-mapping-option"><input type="checkbox" checked={sourceSelection.includes(column)} onChange={() => onSourceToggle(column)} /><span>{column}</span></label>)}</div>
      <SelectedFields fields={sourceSelection} />
    </div>
    <div className="row-mapping-arrow"><ArrowRight size={22} /><span>compare</span></div>
    <div className="row-mapping-selection">
      <div className="row-mapping-selection-header"><div><h3>{destTitle}</h3><p>Select every numeric component to add together.</p></div><strong>{destinationSelection.length} selected</strong></div>
      <div className="row-mapping-options">{destinationColumns.map((column) => <label key={column} className="row-mapping-option"><input type="checkbox" checked={destinationSelection.includes(column)} onChange={() => onDestinationToggle(column)} /><span>{column}</span></label>)}</div>
      <SelectedFields fields={destinationSelection} />
    </div>
    <div className="row-mapping-actions"><button type="button" className="secondary" onClick={onClear} disabled={!sourceSelection.length && !destinationSelection.length}>Clear selection</button><button type="button" className="primary" onClick={onAdd} disabled={!sourceSelection.length || !destinationSelection.length}><Plus size={16} />Add mapping</button></div>
  </section>;
}

function SelectedFields({ fields }: { fields: string[] }) {
  return <div className="selected-fields-preview">{fields.length ? fields.map((field) => <span key={field}>{field}</span>) : <small>No fields selected</small>}</div>;
}
