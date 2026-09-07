import { AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, Download, Play, RefreshCw, Sparkles, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { FileDropzone } from "../components/FileDropzone";
import { MappingBuilder } from "../components/MappingBuilder";
import { ReportColumnPicker } from "../components/ReportColumnPicker";
import { WorkflowSteps } from "../components/WorkflowSteps";
import { SheetSelector } from "../components/SheetSelector";
import { SheetPairingStep } from "../components/SheetPairingStep";
import type { SheetPairing } from "../components/SheetPairingStep";
import { SmartMappingReview } from "../components/SmartMappingReview";
import { api } from "../services/api";
import type { GstConfiguration, Job, RuleMapping, UploadedFile, SheetMetadata, AnalysisResponse } from "../types";

interface Props {
  onJobCreated: (job: Job) => void;
}

const GST_REPORT_SHEETS = ["Mismatched invoices", "Present in source only", "Present in destination only", "Match confidence review"];

function missingGstColumns(columns: string[], config: GstConfiguration | null) {
  if (!config) return [];
  const available = new Set(columns.map((column) => column.trim().toUpperCase()));
  return config.required_columns.filter((column) => !available.has(column.toUpperCase()));
}

export function UploadPage({ onJobCreated }: Props) {
  const [jobType, setJobType] = useState<"generic" | "gst">("generic");
  const [orientation, setOrientation] = useState("vertical");
  const [step, setStep] = useState(1);
  const [file1, setFile1] = useState<UploadedFile | null>(null);
  const [file2, setFile2] = useState<UploadedFile | null>(null);
  const [file1Sheets, setFile1Sheets] = useState<SheetMetadata[]>([]);
  const [file2Sheets, setFile2Sheets] = useState<SheetMetadata[]>([]);
  const [selectedSheets1, setSelectedSheets1] = useState<string[]>([]);
  const [selectedSheets2, setSelectedSheets2] = useState<string[]>([]);
  const [pairings, setPairings] = useState<SheetPairing[]>([]);
  
  const [file1Columns, setFile1Columns] = useState<string[]>([]);
  const [file2Columns, setFile2Columns] = useState<string[]>([]);
  const [key1, setKey1] = useState("");
  const [key2, setKey2] = useState("");
  const [rules, setRules] = useState<RuleMapping[]>([]);
  
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [include1, setInclude1] = useState<string[]>([]);
  const [include2, setInclude2] = useState<string[]>([]);
  const [gstConfig, setGstConfig] = useState<GstConfiguration | null>(null);
  const [gstConfigError, setGstConfigError] = useState("");
  const [gstTextThreshold, setGstTextThreshold] = useState(85);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState<1 | 2 | null>(null);
  const [uploadProgress, setUploadProgress] = useState({ 1: 0, 2: 0 });

  const hasBothFiles = Boolean(file1 && file2);
  const completedThrough = step === 1 ? 0 : step - 1;
  const estimatedFields = useMemo(() => `${file1Columns.length + file2Columns.length} fields available`, [file1Columns.length, file2Columns.length]);
  const missingGstFile1 = useMemo(() => missingGstColumns(file1Columns, gstConfig), [file1Columns, gstConfig]);
  const missingGstFile2 = useMemo(() => missingGstColumns(file2Columns, gstConfig), [file2Columns, gstConfig]);
  const gstReady = Boolean(gstConfig && hasBothFiles && !missingGstFile1.length && !missingGstFile2.length);

  const file1Name = file1?.original_filename || "File 1";
  const file2Name = file2?.original_filename || "File 2";

  const canContinue = step === 1
    ? hasBothFiles && (file1Sheets.length === 0 || selectedSheets1.length > 0) && (file2Sheets.length === 0 || selectedSheets2.length > 0)
    : step === 2
      ? pairings.length > 0 || (file1Sheets.length <= 1 && file2Sheets.length <= 1)
      : jobType === "gst"
        ? gstReady
        : step === 3
          ? Boolean(key1 && key2)
          : step === 4
            ? rules.length > 0
            : true;

  async function upload(which: 1 | 2, file: File) {
    setUploading(which);
    setUploadProgress((current) => ({ ...current, [which]: 0 }));
    setMessage("");
    try {
      const stored = await api.uploadFile(file, (progress) => setUploadProgress((current) => ({ ...current, [which]: progress })));
      const metadata = await api.getFileMetadata(stored.id);
      
      const sheetIds = metadata.sheets.length > 0 ? [metadata.sheets[0].id] : [];
      
      if (which === 1) {
        setFile1(stored);
        setFile1Sheets(metadata.sheets);
        setSelectedSheets1(sheetIds);
      } else {
        setFile2(stored);
        setFile2Sheets(metadata.sheets);
        setSelectedSheets2(sheetIds);
      }
      
      // Auto-refresh columns after upload
      if (sheetIds.length > 0) {
        const columns = await api.getColumns(stored.id, orientation, sheetIds[0]);
        if (which === 1) {
          setFile1Columns(columns);
          setKey1(columns[0] ?? "");
        } else {
          setFile2Columns(columns);
          setKey2(columns[0] ?? "");
        }
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setUploading(null);
    }
  }

  async function refreshColumns(activePairings?: SheetPairing[]) {
    if (!file1 && !file2) return;
    setBusy(true);
    try {
      // Use the first pairing's sheet IDs for column analysis if available
      const activePairs = activePairings ?? pairings;
      const sheet1Id = activePairs[0]?.sheet1?.id ?? selectedSheets1[0] ?? file1Sheets[0]?.id;
      const sheet2Id = activePairs[0]?.sheet2?.id ?? selectedSheets2[0] ?? file2Sheets[0]?.id;

      if (file1) {
        const columns = await api.getColumns(file1.id, orientation, sheet1Id);
        setFile1Columns(columns);
        setKey1((current) => columns.includes(current) ? current : columns[0] ?? "");
      }
      if (file2) {
        const columns = await api.getColumns(file2.id, orientation, sheet2Id);
        setFile2Columns(columns);
        setKey2((current) => columns.includes(current) ? current : columns[0] ?? "");
      }

      if (jobType === "generic" && file1 && file2) {
        const source_files_1 = sheet1Id ? [{ file_id: file1.id, sheet_id: sheet1Id }] : [{ file_id: file1.id }];
        const source_files_2 = sheet2Id ? [{ file_id: file2.id, sheet_id: sheet2Id }] : [{ file_id: file2.id }];
        const analysisData = await api.analyzeFiles({ source_files_1, source_files_2, orientation });
        setAnalysis(analysisData);

        if (analysisData.recommended_keys_1.length > 0) setKey1(analysisData.recommended_keys_1[0]);
        if (analysisData.recommended_keys_2.length > 0) setKey2(analysisData.recommended_keys_2[0]);

        const suggestedRules = analysisData.recommended_mappings
          .filter(m => m.target && (m.confidence === "High" || m.confidence === "Medium"))
          .map(m => ({
            file_1_fields: [m.source],
            file_2_fields: [m.target as string]
          }));
        setRules(suggestedRules);
      } else {
        setRules([]);
      }

      setInclude1([]);
      setInclude2([]);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not analyze the files");
    } finally {
      setBusy(false);
    }
  }

  // Trigger analysis when orientation or selected sheets change
  useEffect(() => {
    if (file1 && file2) {
      refreshColumns().catch(() => undefined);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orientation, file1, file2]);
  useEffect(() => {
    api.getGstConfiguration()
      .then((config) => { setGstConfig(config); setGstConfigError(""); })
      .catch((error: Error) => setGstConfigError(error.message || "Could not load the GST configuration."));
  }, []);

  async function start() {
    if (!file1 || !file2) {
      setMessage("Upload both Excel files first.");
      return;
    }
    if (jobType === "gst" && !gstReady) {
      setMessage("Both files must contain all mandatory GST fields before reconciliation can start.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const s1 = selectedSheets1.length > 0 ? selectedSheets1 : (file1Sheets.length > 0 ? [file1Sheets[0].id] : []);
      const s2 = selectedSheets2.length > 0 ? selectedSheets2 : (file2Sheets.length > 0 ? [file2Sheets[0].id] : []);
      const source_files_1 = s1.length > 0 ? s1.map(id => ({ file_id: file1.id, sheet_id: id })) : [{ file_id: file1.id }];
      const source_files_2 = s2.length > 0 ? s2.map(id => ({ file_id: file2.id, sheet_id: id })) : [{ file_id: file2.id }];

      const job = jobType === "gst"
        ? await api.startGst({
            file_1_id: file1.id,
            file_2_id: file2.id,
            source_files_1,
            source_files_2,
            orientation,
            text_threshold: gstTextThreshold
          })
        : await api.startGeneric({
            file_1_id: file1.id,
            file_2_id: file2.id,
            orientation,
            // Build structured pairs from sheet pairings
            pairs: pairings.length > 0
              ? pairings.map(p => ({
                  source_file_1: { file_id: file1.id, sheet_id: p.sheet1.id },
                  source_file_2: { file_id: file2.id, sheet_id: p.sheet2.id },
                  key_file_1: key1,
                  key_file_2: key2,
                  rules,
                  include_columns_file_1: include1,
                  include_columns_file_2: include2,
                }))
              : undefined,
            // Legacy flat fields as fallback if no pairings
            source_files_1: pairings.length === 0 ? source_files_1 : undefined,
            source_files_2: pairings.length === 0 ? source_files_2 : undefined,
            key_file_1: pairings.length === 0 ? key1 : undefined,
            key_file_2: pairings.length === 0 ? key2 : undefined,
            rules: pairings.length === 0 ? rules : undefined,
            include_columns_file_1: pairings.length === 0 ? include1 : undefined,
            include_columns_file_2: pairings.length === 0 ? include2 : undefined,
          });
      onJobCreated(job);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not start reconciliation");
    } finally {
      setBusy(false);
    }
  }

  async function downloadSample() {
    try {
      await api.downloadSampleTemplate(jobType);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Sample download failed");
    }
  }

  return <section className="page workflow-page">
    <div className="page-title workflow-title"><div><span className="eyebrow">New reconciliation</span><h1>Set up your comparison</h1><p>Six clear steps from Excel files to a downloadable reconciliation report.</p></div><span className="workflow-status">Step {step} of 6</span></div>
    <WorkflowSteps current={step} completedThrough={completedThrough} onSelect={setStep} />
    <div className="workflow-panel">
      {step === 1 && <div className="step-content">
        <div className="section-heading"><div><h2>Upload your workbooks</h2><p>Choose the source and destination Excel files you want to compare.</p></div></div>
        <div className="setup-controls"><label>Reconciliation type<div className="segmented-control"><button type="button" className={jobType === "generic" ? "is-active" : ""} onClick={() => setJobType("generic")}>General</button><button type="button" className={jobType === "gst" ? "is-active" : ""} onClick={() => setJobType("gst")}>GST invoices</button></div></label><label>Data orientation<div className="segmented-control"><button type="button" className={orientation === "vertical" ? "is-active" : ""} onClick={() => setOrientation("vertical")}>Column headers</button><button type="button" className={orientation === "horizontal" ? "is-active" : ""} onClick={() => setOrientation("horizontal")}>Row headers</button></div></label><button type="button" className="secondary refresh-command" onClick={downloadSample}><Download size={16} />Download {jobType === "gst" ? "GST" : "General"} sample template</button><button type="button" className="secondary refresh-command" onClick={() => refreshColumns()} disabled={!hasBothFiles || busy}><RefreshCw size={16} />Refresh fields</button></div>
        <div className="upload-grid">
          <div>
            <FileDropzone label={file1Name} fileName={file1?.original_filename} fileSize={file1?.size_bytes} columnCount={file1Columns.length} uploading={uploading === 1} progress={uploadProgress[1]} onFile={(file) => upload(1, file)} />
            <SheetSelector sheets={file1Sheets} selectedSheets={selectedSheets1} onChange={setSelectedSheets1} fileName={file1Name} />
          </div>
          <div>
            <FileDropzone label={file2Name} fileName={file2?.original_filename} fileSize={file2?.size_bytes} columnCount={file2Columns.length} uploading={uploading === 2} progress={uploadProgress[2]} onFile={(file) => upload(2, file)} />
            <SheetSelector sheets={file2Sheets} selectedSheets={selectedSheets2} onChange={setSelectedSheets2} fileName={file2Name} />
          </div>
        </div>
      </div>}
      {step === 2 && <div className="step-content">
        <div className="section-heading"><div><h2>Pair your sheets</h2><p>Map each sheet from {file1Name} to its counterpart in {file2Name}. Each pair is reconciled independently.</p></div></div>
        {(file1Sheets.length > 1 || file2Sheets.length > 1)
          ? <SheetPairingStep
              file1Sheets={file1Sheets}
              file2Sheets={file2Sheets}
              file1Name={file1Name}
              file2Name={file2Name}
              pairings={pairings}
              onChange={(newPairings) => {
                setPairings(newPairings);
                if (newPairings.length > 0) refreshColumns(newPairings).catch(() => undefined);
              }}
            />
          : <div className="pairing-auto-notice info-callout"><Sparkles size={18} /><span>Single sheet in each file — automatically paired and ready to continue.</span></div>}
      </div>}
      {step === 3 && <div className="step-content">
        {jobType === "generic" ? <>
          <div className="section-heading"><div><h2>Choose the unique matching key</h2><p>Select the identifier that tells RecliQ which records belong together.</p></div><span className="key-hint">{estimatedFields}</span></div>
          {busy ? <div className="loading-state"><Loader2 className="animate-spin" /> Analyzing dataset...</div> : analysis && <SmartMappingReview analysis={analysis} file1Name={file1Name} file2Name={file2Name} />}
          <div className="key-selector-grid"><label><span>{file1Name}</span><select value={key1} onChange={(event) => setKey1(event.target.value)}>{file1Columns.map((column) => <option key={column}>{column}</option>)}</select><small>{file1Columns.length} columns detected</small></label><ArrowRight size={24} /><label><span>{file2Name}</span><select value={key2} onChange={(event) => setKey2(event.target.value)}>{file2Columns.map((column) => <option key={column}>{column}</option>)}</select><small>{file2Columns.length} columns detected</small></label></div><div className="info-callout"><Sparkles size={18} /><span>RecliQ automatically chooses the best numeric, date, identifier, or text comparison method for each mapped field.</span></div>
        </> : <GstMatchingKeyStep config={gstConfig} missingFile1={missingGstFile1} missingFile2={missingGstFile2} error={gstConfigError} file1Name={file1Name} file2Name={file2Name} />}
      </div>}
      {step === 4 && <div className="step-content">{jobType === "generic" ? <MappingBuilder file1Columns={file1Columns} file2Columns={file2Columns} rules={rules} onRulesChange={setRules} primaryFile1={key1} primaryFile2={key2} file1Name={file1Name} file2Name={file2Name} /> : <GstColumnMappingStep config={gstConfig} missingFile1={missingGstFile1} missingFile2={missingGstFile2} file1Name={file1Name} file2Name={file2Name} />}</div>}
      {step === 5 && <div className="step-content">{jobType === "generic" ? <ReportColumnPicker file1Columns={file1Columns.filter((column) => column !== key1)} file2Columns={file2Columns.filter((column) => column !== key2)} selectedFile1={include1} selectedFile2={include2} onChangeFile1={setInclude1} onChangeFile2={setInclude2} file1Name={file1Name} file2Name={file2Name} /> : <GstReportSetup threshold={gstTextThreshold} onThresholdChange={setGstTextThreshold} />}</div>}
      {step === 6 && <div className="ready-card"><div><span className="eyebrow">Ready to reconcile</span><h2>{jobType === "gst" ? "GST invoice reconciliation" : "General reconciliation"}</h2><p>Review the setup below, then let RecliQ generate your report.</p></div><dl><div><dt>Source file ({file1Name})</dt><dd>{file1?.original_filename}</dd></div><div><dt>Destination file ({file2Name})</dt><dd>{file2?.original_filename}</dd></div><div><dt>Sheet pairs</dt><dd>{pairings.length > 0 ? `${pairings.length} pair${pairings.length !== 1 ? "s" : ""}` : "Single sheet"}</dd></div><div><dt>Matching key</dt><dd>{jobType === "gst" ? "GSTR + Invoice No." : `${key1} → ${key2}`}</dd></div><div><dt>Mapped fields</dt><dd>{jobType === "gst" ? `${gstConfig?.required_columns.length ?? 0} verified GST fields` : rules.length}</dd></div><div><dt>Report columns</dt><dd>{jobType === "gst" ? `GST report (confidence ${gstTextThreshold}%)` : include1.length + include2.length}</dd></div><div><dt>Orientation</dt><dd>{orientation === "vertical" ? "Column headers" : "Row headers"}</dd></div></dl><button type="button" className="primary run-button" onClick={start} disabled={busy || (jobType === "gst" && !gstReady)}><Play size={18} />{busy ? "Starting reconciliation..." : "Run reconciliation"}</button></div>}
    </div>
    {message && <p className="error-text">{message}</p>}
    <div className="workflow-actions"><button type="button" className="secondary" onClick={() => setStep((current) => Math.max(1, current - 1))} disabled={step === 1 || busy}><ArrowLeft size={16} />Back</button>{step < 6 ? <button type="button" className="primary" onClick={() => setStep((current) => current + 1)} disabled={!canContinue || busy}>Continue<ArrowRight size={16} /></button> : null}</div>
  </section>;
}

interface GstStepProps {
  config: GstConfiguration | null;
  missingFile1: string[];
  missingFile2: string[];
  file1Name?: string;
  file2Name?: string;
}

function GstMatchingKeyStep({ config, missingFile1, missingFile2, error, file1Name = "File 1", file2Name = "File 2" }: GstStepProps & { error: string }) {
  return <section className="gst-workflow-step"><div className="section-heading"><div><h2>Confirm GST matching keys</h2><p>GST reconciliations use a fixed business key to keep invoice matching accurate and auditable.</p></div></div><div className="gst-key-list">{(config?.matching_fields ?? []).map((field) => <div key={field}><CheckCircle2 size={17} /><span>{field}</span><small>Required in both files ({file1Name} & {file2Name})</small></div>)}</div><GstValidationAlert config={config} missingFile1={missingFile1} missingFile2={missingFile2} error={error} file1Name={file1Name} file2Name={file2Name} /></section>;
}

function GstColumnMappingStep({ config, missingFile1, missingFile2, file1Name = "File 1", file2Name = "File 2" }: GstStepProps) {
  const missingSource = new Set(missingFile1);
  const missingDestination = new Set(missingFile2);
  return <section className="gst-workflow-step"><div className="section-heading"><div><h2>Review GST field mapping</h2><p>GST uses its documented standard fields. RecliQ maps each required header to the same header in the other file.</p></div><span className="mapping-count">{config?.required_columns.length ?? 0} standard fields</span></div><div className="gst-field-map"><div className="gst-field-map-header"><span>GST field</span><span>{file1Name}</span><span>{file2Name}</span></div>{(config?.required_columns ?? []).map((field) => <div className="gst-field-map-row" key={field}><strong>{field}</strong><span className={missingSource.has(field) ? "is-missing" : "is-present"}>{missingSource.has(field) ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}{missingSource.has(field) ? "Missing" : "Verified"}</span><span className={missingDestination.has(field) ? "is-missing" : "is-present"}>{missingDestination.has(field) ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}{missingDestination.has(field) ? "Missing" : "Verified"}</span></div>)}</div><GstValidationAlert config={config} missingFile1={missingFile1} missingFile2={missingFile2} error="" file1Name={file1Name} file2Name={file2Name} /></section>;
}

function GstReportSetup({ threshold, onThresholdChange }: { threshold: number; onThresholdChange: (value: number) => void }) {
  return <section className="gst-workflow-step"><div className="section-heading"><div><h2>Configure GST report</h2><p>Choose how cautious the engine should be when supplier names need an intelligent text comparison.</p></div></div><label className="gst-threshold"><span>Supplier name confidence threshold</span><div><input type="range" min="70" max="100" step="1" value={threshold} onChange={(event) => onThresholdChange(Number(event.target.value))} /><output>{threshold}%</output></div><small>Names below this confidence score are added to the review sheet instead of being silently accepted.</small></label><div className="gst-report-sheets">{GST_REPORT_SHEETS.map((sheet) => <div key={sheet}><CheckCircle2 size={16} /><span>{sheet}</span><small>Included</small></div>)}</div></section>;
}

function GstValidationAlert({ config, missingFile1, missingFile2, error, file1Name = "File 1", file2Name = "File 2" }: GstStepProps & { error: string }) {
  if (error) return <p className="error-text"><AlertTriangle size={16} />{error}</p>;
  if (!config) return <p className="info-callout">Loading the GST engine configuration...</p>;
  if (!missingFile1.length && !missingFile2.length) return <p className="success-text"><CheckCircle2 size={16} />Both workbooks contain every mandatory GST field. Continue when you are ready.</p>;
  const messages = [missingFile1.length ? `${file1Name}: ${missingFile1.join(", ")}` : "", missingFile2.length ? `${file2Name}: ${missingFile2.join(", ")}` : ""].filter(Boolean);
  return <p className="error-text"><AlertTriangle size={16} />GST reconciliation cannot continue until the missing fields are supplied. {messages.join("; ")}</p>;
}
