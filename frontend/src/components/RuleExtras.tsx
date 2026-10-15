import { ArrowRight, Info, Plus, X } from "lucide-react";
import type { DateAmountSettings, DateFormat, MatchingPass, NormalizationSettings, SheetRuleDraft, ToleranceBand, TransformationStep } from "../types";
import { ALL_FIELDS, availableColumns, describeTolerance, limitsFromPasses, newPass } from "../lib/plan";

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

function DateAmountSideFields({ side, label, columns, settings, onChange }: {
  side: "source" | "destination";
  label: string;
  columns: string[];
  settings: DateAmountSettings;
  onChange: (change: Partial<DateAmountSettings>) => void;
}) {
  const source = side === "source";
  const dateColumn = source ? settings.dateSource : settings.dateDestination;
  const dateFormat = source ? settings.dateFormatSource : settings.dateFormatDestination;
  const amountMode = source ? settings.amountModeSource : settings.amountModeDestination;
  const amountColumn = source ? settings.amountSource : settings.amountDestination;
  const debitColumn = source ? settings.debitSource : settings.debitDestination;
  const creditColumn = source ? settings.creditSource : settings.creditDestination;
  const positiveIs = source ? settings.positiveIsSource : settings.positiveIsDestination;
  const reverseDirection = source ? settings.reverseDirectionSource : settings.reverseDirectionDestination;
  const textColumn = source ? settings.textSource : settings.textDestination;
  const currency = source ? settings.currencySource : settings.currencyDestination;
  const sideLabel = source ? "source" : "comparison";

  return <section className="date-amount-side" aria-label={`${label} date and amount fields`}>
    <h4>{label}</h4>
    <label><span>Transaction date</span>
      <ColumnSelect columns={columns} value={dateColumn} onSelect={(value) => onChange(source ? { dateSource: value } : { dateDestination: value })} label={`${label} date column`} />
    </label>
    <label><span>Date format</span>
      <select value={dateFormat} onChange={(event) => onChange(source ? { dateFormatSource: event.target.value as DateAmountSettings["dateFormatSource"] } : { dateFormatDestination: event.target.value as DateAmountSettings["dateFormatDestination"] })} aria-label={`${label} date format`}>
        <option value="auto">Auto-detect; flag ambiguous dates</option>
        <option value="day_first">Day first (DD/MM/YYYY)</option>
        <option value="month_first">Month first (MM/DD/YYYY)</option>
      </select>
    </label>
    <div className="date-amount-field-group">
      <span>Amount columns</span>
      <div className="segmented-control" role="group" aria-label={`${label} amount format`}>
        <button type="button" className={amountMode === "single" ? "is-active" : ""} aria-pressed={amountMode === "single"} onClick={() => onChange(source ? { amountModeSource: "single" } : { amountModeDestination: "single" })}>Signed amount</button>
        <button type="button" className={amountMode === "debit_credit" ? "is-active" : ""} aria-pressed={amountMode === "debit_credit"} onClick={() => onChange(source ? { amountModeSource: "debit_credit" } : { amountModeDestination: "debit_credit" })}>Debit + credit</button>
      </div>
      {amountMode === "single" ? <>
        <ColumnSelect columns={columns} value={amountColumn} onSelect={(value) => onChange(source ? { amountSource: value } : { amountDestination: value })} label={`${label} signed amount column`} />
        <label><span>Positive amounts represent</span>
          <select value={positiveIs} onChange={(event) => onChange(source ? { positiveIsSource: event.target.value as "debit" | "credit" } : { positiveIsDestination: event.target.value as "debit" | "credit" })} aria-label={`${label} positive amount direction`}>
            <option value="debit">Debit</option><option value="credit">Credit</option>
          </select>
        </label>
      </> : <div className="date-amount-paired-columns">
        <ColumnSelect columns={columns} value={debitColumn} onSelect={(value) => onChange(source ? { debitSource: value } : { debitDestination: value })} label={`${label} debit column`} />
        <ColumnSelect columns={columns} value={creditColumn} onSelect={(value) => onChange(source ? { creditSource: value } : { creditDestination: value })} label={`${label} credit column`} />
      </div>}
    </div>
    <label className="checkbox-line"><input type="checkbox" checked={reverseDirection} onChange={(event) => onChange(source ? { reverseDirectionSource: event.target.checked } : { reverseDirectionDestination: event.target.checked })} />Reverse debit and credit direction</label>
    <label><span>Required text to match <small>(optional)</small></span>
      <ColumnSelect columns={columns} value={textColumn} onSelect={(value) => onChange(source ? { textSource: value } : { textDestination: value })} label={`${label} required matching text column`} optional />
    </label>
    <label><span>Currency override <small>(optional)</small></span><input className="date-amount-text-input" value={currency} onChange={(event) => onChange(source ? { currencySource: event.target.value.toUpperCase() } : { currencyDestination: event.target.value.toUpperCase() })} maxLength={3} placeholder="INR" aria-label={`${label} currency override`} /></label>
    <small className="field-hint">{sideLabel} currency is read from amount values when present; this override fills in missing currency labels.</small>
  </section>;
}

/** The date, amount and mandatory dimensions used by the specialized ledger matcher. */
export function DateAmountMappingEditor({ config, onChange, sourceLabel, destinationLabel }: EditorProps) {
  const update = (change: Partial<DateAmountSettings>) => onChange({ ...config, dateAmount: { ...config.dateAmount, ...change } });
  return <section className="date-amount-editor" aria-label="Date and amount mapping">
    <div className="date-amount-side-grid">
      <DateAmountSideFields side="source" label={sourceLabel} columns={config.file1Columns} settings={config.dateAmount} onChange={update} />
      <DateAmountSideFields side="destination" label={destinationLabel} columns={config.file2Columns} settings={config.dateAmount} onChange={update} />
    </div>
    <p className="date-amount-note">Dates and amounts are normalized independently. Matching text, if selected, is a required exact dimension after case and whitespace normalization. Currency conversion is not performed.</p>
  </section>;
}

/** Verification and bounded grouping choices for Date & Amount reports. */
export function DateAmountReportOptions({ config, onChange }: Omit<EditorProps, "sourceLabel" | "destinationLabel">) {
  const settings = config.dateAmount;
  const update = (change: Partial<DateAmountSettings>) => onChange({ ...config, dateAmount: { ...settings, ...change } });
  return <section className="rule-section date-amount-report-options" aria-label="Date and amount report options">
    <div className="rule-section-heading"><div><h4>Verification and tolerances</h4><p>Aggregate periods are always reported. Transaction-level matching is a separate, stricter check.</p></div></div>
    <div className="date-amount-verification" role="group" aria-label="Transaction verification mode">
      <button type="button" className={settings.verifyTransactions ? "is-active" : ""} aria-pressed={settings.verifyTransactions} onClick={() => update({ verifyTransactions: true })}>Verify transactions</button>
      <button type="button" className={!settings.verifyTransactions ? "is-active" : ""} aria-pressed={!settings.verifyTransactions} onClick={() => update({ verifyTransactions: false })}>Aggregate balances only</button>
    </div>
    {!settings.verifyTransactions && <p className="date-amount-warning" role="note">Aggregate balances do not prove individual transactions match. The report will label transactions as not verified.</p>}
    <div className="date-amount-report-grid">
      <label><span>Amount tolerance</span><input type="number" min="0" step="0.01" value={settings.amountTolerance} onChange={(event) => update({ amountTolerance: event.target.value })} aria-label="Amount tolerance" /><small>Non-negative; up to 6 decimal places. Group totals always match exactly.</small></label>
      <label><span>Date tolerance (days)</span><input type="number" min="0" max="366" step="1" value={settings.dateToleranceDays} onChange={(event) => update({ dateToleranceDays: Math.max(0, Math.min(366, Math.round(Number(event.target.value) || 0))) })} aria-label="Date tolerance in days" /><small>Applied only to unique one-to-one candidates.</small></label>
    </div>
    <label className="checkbox-line"><input type="checkbox" checked={settings.allowGroupMatches} onChange={(event) => update({ allowGroupMatches: event.target.checked })} disabled={!settings.verifyTransactions} />Allow exact one-to-many or many-to-one groups</label>
    {settings.allowGroupMatches && settings.verifyTransactions && <label className="date-amount-group-size"><span>Maximum records in one group</span><input type="number" min="2" max="8" step="1" value={settings.maxGroupSize} onChange={(event) => update({ maxGroupSize: Math.max(2, Math.min(8, Math.round(Number(event.target.value) || 2))) })} /><small>Search is bounded; ambiguous or capped candidates remain for review.</small></label>}
  </section>;
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

  return <details className="rule-advanced fallback-matching">
    <summary>Fallback matching <span className="advanced-summary-status">{config.matchingPasses.length ? `${config.matchingPasses.length} pass${config.matchingPasses.length === 1 ? "" : "es"} active` : "Optional"}</span></summary>
    <p>{keyed
      ? "Use amount and date to find records the key did not match. Ambiguous candidates stay in review."
      : "No key selected: add a complete amount and date pass to identify records."}</p>
    <div className="button-row">
      <button type="button" className="secondary" onClick={() => add("amount_date")}><Plus size={15} />Amount + date</button>
      <button type="button" className="secondary" onClick={() => add("amount_tolerance")}><Plus size={15} />Amount within a range</button>
    </div>
    {config.matchingPasses.length === 0 && <p className="rule-empty">No extra passes: unmatched records are listed as not found.</p>}
    {config.matchingPasses.map((item, index) => <div className="pass-card" key={`pass-${index}`}>
      <div className="pass-card-heading">
        <strong>Pass {index + (keyed ? 2 : 1)} · {item.type === "amount_date" ? "Same amount, date within a window" : "Amount within a range"}</strong>
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
        {item.type === "amount_tolerance" && <label><span>Amount range (±)</span><input type="number" min={0} step="0.01" value={item.amount_tolerance} onChange={(event) => update(index, { amount_tolerance: Math.max(0, Number(event.target.value)) })} /></label>}
        {item.type === "amount_tolerance" && <label><span>or range (± %)</span><input type="number" min={0} max={100} step="0.1" value={item.amount_tolerance_percent} onChange={(event) => update(index, { amount_tolerance_percent: Math.max(0, Number(event.target.value)) })} /></label>}
        {item.narrative_source && <label><span>Reference similarity (%)</span><input type="number" min={50} max={100} value={item.narrative_threshold} onChange={(event) => update(index, { narrative_threshold: Math.min(100, Math.max(50, Number(event.target.value))) })} /></label>}
      </div>
      {config.secondaryConditions.length > 0 && <label className="checkbox-line"><input type="checkbox" checked={item.respect_secondary_keys} onChange={(event) => update(index, { respect_secondary_keys: event.target.checked })} />The "must also match" columns must still agree in this pass</label>}
      {item.date_source && config.secondaryConditions.some((condition) => condition.comparison_method === "normalized_date" && condition.source_column === item.date_source && condition.destination_column === item.date_destination) && <p className="field-hint">This pass's date window replaces the exact-date "must also match" check on {item.date_source}. Records whose key matched but whose date differed are re-checked here, only against the record with the same key.</p>}
    </div>)}
  </details>;
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

  return <details className="rule-advanced">
    <summary>Prepare values before matching <span className="advanced-summary-status">{config.transformations.length ? `${config.transformations.length} active` : "Optional"}</span></summary>
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

// ── Accepting small differences ─────────────────────────────────────────
/** What a tolerance does, shown from the ⓘ beside the "Tolerance" heading of the mapped fields. */
export function ToleranceHelp({ destinationLabel }: { destinationLabel: string }) {
  return <span className="info-tip">
    <button type="button" className="info-tip-trigger" aria-label="How tolerance works"><Info size={15} /></button>
    <span className="info-tip-body" role="tooltip">
      <strong>Accept small differences (tolerance)</strong>
      <ul>
        <li>Applied after matching, before the report is written.</li>
        <li>A difference within the tolerance is not listed as a difference.</li>
        <li>A record left with no differences moves to Matched, with a comment saying which difference was accepted and why.</li>
        <li>Larger differences stay exactly as they are.</li>
        <li>Tolerance never changes which records are paired.</li>
      </ul>
      <ul>
        <li><b>Type</b>: RecliQ picks Amount, Date or Text from the column; change it if it is wrong. Leave the boxes empty for no tolerance.</li>
        <li><b>Amount</b>: ± a fixed amount, or ± a percentage of the value in {destinationLabel}. Fill either or both; either one is enough to accept a difference.</li>
        <li><b>Date</b>: ± days apart.</li>
        <li><b>Text</b>: at least this % similar (the Similarity shown in the report).</li>
      </ul>
    </span>
  </span>;
}

/** Explains how field comparison types are selected by default. */
export function ComparisonTypeHelp() {
  return <span className="info-tip">
    <button type="button" className="info-tip-trigger" aria-label="How comparison types are selected"><Info size={15} /></button>
    <span className="info-tip-body" role="tooltip">
      <strong>Automatic by default</strong>
      RecliQ samples values from both mapped columns and considers their headers to choose a comparison type. Turn on manual selection to override this for any field.
    </span>
  </span>;
}

/** Tolerances the mapped-field rows cannot show: step-3 pass limits on offer, and "every compared field" tolerances from older setups. */
export function ToleranceNotices({ config, onChange }: Omit<EditorProps, "sourceLabel" | "destinationLabel">) {
  const fromPasses = limitsFromPasses(config);
  const addedFields = fromPasses.rules.slice(config.rules.length).map((rule) => rule.file_1_fields.join(","));
  const usePassLimits = () => onChange({
    ...config,
    rules: fromPasses.rules,
    tolerances: [...config.tolerances.filter((band) => !fromPasses.tolerances.some((item) => item.field === band.field)), ...fromPasses.tolerances],
  });
  const general = config.tolerances.filter((band) => band.field === ALL_FIELDS);
  const removeGeneral = (band: ToleranceBand) => onChange({ ...config, tolerances: config.tolerances.filter((item) => item !== band) });

  if (!fromPasses.tolerances.length && !general.length) return null;
  return <>
    {fromPasses.tolerances.length > 0 && <div className="tolerance-suggestion">
      <p>Your step-3 matching pass has limits that only pair unmatched records: {fromPasses.tolerances.map(describeTolerance).join("; ")}.{addedFields.length ? ` Using them here also compares ${addedFields.join(" and ")}.` : ""}</p>
      <button type="button" className="secondary" onClick={usePassLimits}>Accept these differences on matched records too</button>
    </div>}
    {general.map((band, index) => <div className="tolerance-general" key={`general-${index}`}>
      <span>{describeTolerance(band)}. Applies to mapped fields without a tolerance of their own.</span>
      <button type="button" className="icon-button" onClick={() => removeGeneral(band)} title="Remove this tolerance" aria-label="Remove this tolerance"><X size={16} /></button>
    </div>)}
  </>;
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
  const activeNormalizations = toggles.filter((toggle) => Boolean(config.normalization[toggle.key])).length;
  return <details className="rule-advanced">
    <summary>Dates and names <span className="advanced-summary-status">{activeNormalizations} options on · {config.dateFormat === "day_first" ? "DD/MM/YYYY" : "MM/DD/YYYY"}</span></summary>
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
