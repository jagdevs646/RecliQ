import { ArrowRight, Plus, X } from "lucide-react";
import type { DateFormat, MatchingPass, NormalizationSettings, SheetRuleDraft, TransformationStep } from "../types";
import { availableColumns, newPass } from "../lib/plan";

interface EditorProps {
  config: SheetRuleDraft;
  onChange: (config: SheetRuleDraft) => void;
  sourceLabel: string;
  destinationLabel: string;
}

function ColumnSelect({ columns, value, onSelect, label, optional = false }: { columns: string[]; value: string; onSelect: (column: string) => void; label: string; optional?: boolean }) {
  return <select value={value} onChange={(event) => onSelect(event.target.value)} aria-label={label} className={value ? "" : "is-empty"}>
    <option value="">{optional ? "Not used" : "Choose a column…"}</option>
    {columns.map((column) => <option key={column} value={column}>{column}</option>)}
  </select>;
}

// ── Extra matching passes ───────────────────────────────────────────────
export function MatchingPassesEditor({ config, onChange, sourceLabel, destinationLabel }: EditorProps) {
  const source = availableColumns(config, "source");
  const destination = availableColumns(config, "destination");
  const keyed = config.primaryKeySource.some(Boolean);
  const update = (index: number, change: Partial<MatchingPass>) => onChange({
    ...config,
    matchingPasses: config.matchingPasses.map((item, itemIndex) => itemIndex === index ? { ...item, ...change } : item),
  });
  const add = (type: MatchingPass["type"]) => onChange({ ...config, matchingPasses: [...config.matchingPasses, newPass(type, config)] });
  const remove = (index: number) => onChange({ ...config, matchingPasses: config.matchingPasses.filter((_, itemIndex) => itemIndex !== index) });

  return <div className="rule-section">
    <div className="rule-section-heading">
      <div>
        <h4>3. If the key is not found, also try <span className="optional-tag">optional</span></h4>
        <p>{keyed
          ? "Records still unmatched after the key are tried again, in order, on amount and date. A pair is only made when exactly one record qualifies on both sides; ties are listed for you to decide."
          : "No key chosen: records are matched on amount and date only. A pair is only made when exactly one record qualifies on both sides."}</p>
      </div>
      <div className="button-row">
        <button type="button" className="secondary" onClick={() => add("amount_date")}><Plus size={15} />Amount + date</button>
        <button type="button" className="secondary" onClick={() => add("amount_tolerance")}><Plus size={15} />Amount within tolerance</button>
      </div>
    </div>
    {config.matchingPasses.length === 0 && <p className="rule-empty">No extra passes: unmatched records are listed as not found.</p>}
    {config.matchingPasses.map((item, index) => <div className="pass-card" key={`pass-${index}`}>
      <div className="pass-card-heading">
        <strong>Pass {index + (keyed ? 2 : 1)} · {item.type === "amount_date" ? "Same amount, date within a window" : "Amount within a tolerance"}</strong>
        <button type="button" className="icon-button" onClick={() => remove(index)} title="Remove this pass" aria-label="Remove this pass"><X size={16} /></button>
      </div>
      <div className="key-pair-header"><span>{sourceLabel}</span><span /><span>{destinationLabel}</span><span /></div>
      <div className="key-pair-row">
        <ColumnSelect columns={source} value={item.amount_source} onSelect={(column) => update(index, { amount_source: column })} label="Source amount column" />
        <ArrowRight size={16} />
        <ColumnSelect columns={destination} value={item.amount_destination} onSelect={(column) => update(index, { amount_destination: column })} label="Destination amount column" />
        <span className="field-hint">Amount</span>
      </div>
      <div className="key-pair-row">
        <ColumnSelect columns={source} value={item.date_source ?? ""} onSelect={(column) => update(index, { date_source: column })} label="Source date column" optional={item.type !== "amount_date"} />
        <ArrowRight size={16} />
        <ColumnSelect columns={destination} value={item.date_destination ?? ""} onSelect={(column) => update(index, { date_destination: column })} label="Destination date column" optional={item.type !== "amount_date"} />
        <span className="field-hint">Date</span>
      </div>
      <div className="key-pair-row">
        <ColumnSelect columns={source} value={item.narrative_source ?? ""} onSelect={(column) => update(index, { narrative_source: column })} label="Source reference column" optional />
        <ArrowRight size={16} />
        <ColumnSelect columns={destination} value={item.narrative_destination ?? ""} onSelect={(column) => update(index, { narrative_destination: column })} label="Destination reference column" optional />
        <span className="field-hint">Reference</span>
      </div>
      <div className="advanced-grid">
        {item.date_source && <label><span>Date window (± days)</span><input type="number" min={0} max={366} value={item.date_window_days} onChange={(event) => update(index, { date_window_days: Math.max(0, Number(event.target.value)) })} /></label>}
        {item.type === "amount_tolerance" && <label><span>Amount tolerance (±)</span><input type="number" min={0} step="0.01" value={item.amount_tolerance} onChange={(event) => update(index, { amount_tolerance: Math.max(0, Number(event.target.value)) })} /></label>}
        {item.type === "amount_tolerance" && <label><span>or tolerance (± %)</span><input type="number" min={0} max={100} step="0.1" value={item.amount_tolerance_percent} onChange={(event) => update(index, { amount_tolerance_percent: Math.max(0, Number(event.target.value)) })} /></label>}
        {item.narrative_source && <label><span>Reference similarity (%)</span><input type="number" min={50} max={100} value={item.narrative_threshold} onChange={(event) => update(index, { narrative_threshold: Math.min(100, Math.max(50, Number(event.target.value))) })} /></label>}
      </div>
      {config.secondaryConditions.length > 0 && <label className="checkbox-line"><input type="checkbox" checked={item.respect_secondary_keys} onChange={(event) => update(index, { respect_secondary_keys: event.target.checked })} />The "must also match" columns must still agree in this pass</label>}
    </div>)}
  </div>;
}

// ── Preparing values before matching ────────────────────────────────────
const OPERATIONS: Array<{ value: TransformationStep["operation"]; label: string; kind: "text" | "number" | "derived" }> = [
  { value: "trim", label: "Trim extra spaces", kind: "text" },
  { value: "uppercase", label: "Make upper case", kind: "text" },
  { value: "remove_prefix", label: "Remove a prefix (e.g. VND-)", kind: "text" },
  { value: "remove_suffix", label: "Remove a suffix", kind: "text" },
  { value: "remove_characters", label: "Remove characters", kind: "text" },
  { value: "replace_text", label: "Replace text", kind: "text" },
  { value: "strip_leading_zeros", label: "Remove leading zeros", kind: "text" },
  { value: "keep_alphanumeric", label: "Keep letters and digits only", kind: "text" },
  { value: "invert_sign", label: "Flip the sign (+/−)", kind: "number" },
  { value: "absolute_value", label: "Ignore the sign", kind: "number" },
  { value: "multiply", label: "Multiply by", kind: "number" },
  { value: "round", label: "Round", kind: "number" },
  { value: "debit_credit_to_signed", label: "Debit/Credit → one signed amount", kind: "derived" },
];

export function TransformationsEditor({ config, onChange, sourceLabel, destinationLabel }: EditorProps) {
  const update = (index: number, change: Partial<TransformationStep>) => onChange({
    ...config,
    transformations: config.transformations.map((step, itemIndex) => itemIndex === index ? { ...step, ...change } : step),
  });
  const add = () => onChange({ ...config, transformations: [...config.transformations, { operation: "trim", side: "both", columns: [""], params: {} }] });
  const remove = (index: number) => onChange({ ...config, transformations: config.transformations.filter((_, itemIndex) => itemIndex !== index) });
  const columnsFor = (side: TransformationStep["side"]) => side === "source" ? config.file1Columns : side === "destination" ? config.file2Columns : config.file1Columns.filter((column) => config.file2Columns.includes(column));

  return <details className="rule-advanced" open={config.transformations.length > 0}>
    <summary>Prepare values before matching{config.transformations.length ? ` (${config.transformations.length})` : ""}</summary>
    <p>Clean or reshape values before they are compared, for example combine Debit and Credit into one signed amount, or remove a prefix from codes. The report shows the original value next to every changed one.</p>
    {config.transformations.map((step, index) => {
      const operation = OPERATIONS.find((item) => item.value === step.operation);
      const columns = columnsFor(step.side);
      return <div className="transform-row" key={`step-${index}`}>
        <select value={step.operation} aria-label="Preparation step" onChange={(event) => {
          const next = event.target.value as TransformationStep["operation"];
          update(index, next === "debit_credit_to_signed"
            ? { operation: next, side: step.side === "both" ? "source" : step.side, columns: [], params: { debit_column: "", credit_column: "", debit_positive: true }, output_column: "Signed Amount" }
            : { operation: next, columns: step.columns.length ? step.columns : [""], params: {}, output_column: null });
        }}>
          {OPERATIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <select value={step.side} aria-label="Which file" onChange={(event) => update(index, { side: event.target.value as TransformationStep["side"] })}>
          {operation?.kind !== "derived" && <option value="both">Both files</option>}
          <option value="source">{sourceLabel}</option>
          <option value="destination">{destinationLabel}</option>
        </select>
        {step.operation === "debit_credit_to_signed" ? <>
          <ColumnSelect columns={columns} value={String(step.params.debit_column ?? "")} onSelect={(column) => update(index, { params: { ...step.params, debit_column: column } })} label="Debit column" />
          <ColumnSelect columns={columns} value={String(step.params.credit_column ?? "")} onSelect={(column) => update(index, { params: { ...step.params, credit_column: column } })} label="Credit column" />
          <input value={step.output_column ?? ""} placeholder="New column name" aria-label="New column name" onChange={(event) => update(index, { output_column: event.target.value })} />
        </> : <>
          <ColumnSelect columns={columns} value={step.columns[0] ?? ""} onSelect={(column) => update(index, { columns: [column] })} label="Column" />
          {step.operation === "replace_text" && <>
            <input value={String(step.params.find ?? "")} placeholder="Find" aria-label="Text to find" onChange={(event) => update(index, { params: { ...step.params, find: event.target.value } })} />
            <input value={String(step.params.replace ?? "")} placeholder="Replace with" aria-label="Replacement" onChange={(event) => update(index, { params: { ...step.params, replace: event.target.value } })} />
          </>}
          {(step.operation === "remove_prefix" || step.operation === "remove_suffix") && <input value={String(step.params.text ?? "")} placeholder="Text to remove" aria-label="Text to remove" onChange={(event) => update(index, { params: { ...step.params, text: event.target.value } })} />}
          {step.operation === "remove_characters" && <input value={String(step.params.characters ?? "")} placeholder="Characters, e.g. -/#" aria-label="Characters to remove" onChange={(event) => update(index, { params: { ...step.params, characters: event.target.value } })} />}
          {step.operation === "multiply" && <input type="number" step="any" value={String(step.params.factor ?? "")} placeholder="Factor, e.g. 0.01" aria-label="Factor" onChange={(event) => update(index, { params: { ...step.params, factor: event.target.value === "" ? "" : Number(event.target.value) } })} />}
          {step.operation === "round" && <input type="number" min={0} max={6} value={Number(step.params.decimals ?? 2)} aria-label="Decimal places" onChange={(event) => update(index, { params: { ...step.params, decimals: Number(event.target.value) } })} />}
        </>}
        {step.operation === "debit_credit_to_signed" && <label className="checkbox-line"><input type="checkbox" checked={step.params.debit_positive !== false} onChange={(event) => update(index, { params: { ...step.params, debit_positive: event.target.checked } })} />Debits positive</label>}
        <button type="button" className="icon-button" onClick={() => remove(index)} title="Remove this step" aria-label="Remove this step"><X size={16} /></button>
      </div>;
    })}
    <button type="button" className="text-command" onClick={add}><Plus size={15} />Add a preparation step</button>
  </details>;
}

// ── Dates and names ─────────────────────────────────────────────────────
export function DatesAndNamesSettings({ config, onChange }: Omit<EditorProps, "sourceLabel" | "destinationLabel">) {
  const setNormalization = (change: Partial<NormalizationSettings>) => onChange({ ...config, normalization: { ...config.normalization, ...change } });
  const setDateFormat = (dateFormat: DateFormat) => onChange({ ...config, dateFormat });
  const toggles: Array<{ key: keyof NormalizationSettings; label: string }> = [
    { key: "legal_forms", label: "Pvt = Private, Ltd = Limited, Co = Company, Corp, Inc, LLP" },
    { key: "abbreviations", label: "Common abbreviations (Intl, Bros, Mfg, A/c, Pmt…)" },
    { key: "ignore_prefixes", label: "Ignore M/s, Messrs and The" },
    { key: "join_initials", label: "A.B.C. = ABC" },
    { key: "word_order", label: "Ignore word order in names" },
    { key: "use_saved_aliases", label: "Use your organization's saved aliases" },
  ];
  return <details className="rule-advanced">
    <summary>Dates and names</summary>
    <div className="advanced-grid">
      <label><span>Read dates like 03/04/2026 as</span>
        <select value={config.dateFormat} onChange={(event) => setDateFormat(event.target.value as DateFormat)}>
          <option value="day_first">Day first: 3 April 2026 (DD/MM/YYYY)</option>
          <option value="month_first">Month first: 4 March 2026 (MM/DD/YYYY)</option>
        </select>
      </label>
    </div>
    <p>Names that differ only in these ways are treated as the same name. Anything else still needs an exact match (or appears for review).</p>
    <div className="toggle-list">
      {toggles.map((toggle) => <label className="checkbox-line" key={toggle.key}>
        <input type="checkbox" checked={Boolean(config.normalization[toggle.key])} onChange={(event) => setNormalization({ [toggle.key]: event.target.checked } as Partial<NormalizationSettings>)} />
        {toggle.label}
      </label>)}
    </div>
  </details>;
}
