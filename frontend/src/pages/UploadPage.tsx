import { AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, Download, Play, Plus, RefreshCw, Sparkles, Loader2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { FileDropzone } from "../components/FileDropzone";
import { MappingBuilder } from "../components/MappingBuilder";
import { ReportColumnPicker } from "../components/ReportColumnPicker";
import { WorkflowSteps } from "../components/WorkflowSteps";
import { SheetSelector } from "../components/SheetSelector";
import { SheetPairingStep } from "../components/SheetPairingStep";
import type { SheetPairing } from "../components/SheetPairingStep";
import { FilePairingStep } from "../components/FilePairingStep";
import type { FilePairing, WorkbookWithSheets } from "../components/FilePairingStep";
import { api } from "../services/api";
import type { GstConfiguration, Job, UploadedFile, SheetMetadata, SheetRuleDraft, SecondaryMatchCondition } from "../types";

interface Props {
  onJobCreated: (job: Job) => void;
}

const GST_REPORT_SHEETS = ["Mismatched invoices", "Present in source only", "Present in destination only", "Match confidence review"];

function missingGstColumns(columns: string[], config: GstConfiguration | null) {
  if (!config) return [];
  const available = new Set(columns.map((column) => column.trim().toUpperCase()));
  return config.required_columns.filter((column) => !available.has(column.toUpperCase()));
}

const DATE_COLUMN_HINT = /date|\bdt\b/i;
const DATE_ONLY_WARNING = "Date-only matching can be ambiguous because multiple transactions may occur on the same date. Select at least one additional identifying column.";

/**
 * A one-column key on a date column. The key analyzer's composite advice (it
 * recommends [low-uniqueness date, partner]) also marks the column as a date.
 */
function isDateOnlyKey(config: SheetRuleDraft): boolean {
  if (config.primaryKeySource.length !== 1 || config.primaryKeyDestination.length !== 1) return false;
  const [source] = config.primaryKeySource;
  const [destination] = config.primaryKeyDestination;
  // Values decide first (the analyzer reports date-valued columns); names are a fallback.
  const dateValued = Boolean(config.analysis?.date_columns_1?.includes(source) || config.analysis?.date_columns_2?.includes(destination));
  return dateValued || DATE_COLUMN_HINT.test(source) || DATE_COLUMN_HINT.test(destination);
}

function ruleIsReady(config: SheetRuleDraft | undefined): boolean {
  return Boolean(
    config
    && config.primaryKeySource.length
    && config.primaryKeySource.length === config.primaryKeyDestination.length
    && [...config.primaryKeySource, ...config.primaryKeyDestination].every(Boolean)
    && config.secondaryConditions.every((condition) => condition.source_column && condition.destination_column)
    && (!isDateOnlyKey(config) || config.dateOnlyOverride),
  );
}

/** Deep-copies another rule's settings, keeping only columns that exist in the target sheets. */
function copyRuleSettings(source: SheetRuleDraft, target: SheetRuleDraft): SheetRuleDraft {
  const copy = structuredClone(source);
  const has1 = new Set(target.file1Columns);
  const has2 = new Set(target.file2Columns);
  const keyPairs = copy.primaryKeySource.map((column, index) => [column, copy.primaryKeyDestination[index]] as const)
    .filter(([left, right]) => has1.has(left) && right !== undefined && has2.has(right));
  return {
    ...target,
    primaryKeySource: keyPairs.map(([left]) => left),
    primaryKeyDestination: keyPairs.map(([, right]) => right),
    secondaryConditions: copy.secondaryConditions.filter((condition) => has1.has(condition.source_column) && has2.has(condition.destination_column)),
    similarityPolicy: copy.similarityPolicy,
    dateOnlyOverride: false,
    rules: copy.rules.filter((rule) => rule.file_1_fields.every((field) => has1.has(field)) && rule.file_2_fields.every((field) => has2.has(field))),
    includeFile1: copy.includeFile1.filter((column) => has1.has(column)),
    includeFile2: copy.includeFile2.filter((column) => has2.has(column)),
  };
}

const COMPARISON_LABELS: Record<SecondaryMatchCondition["comparison_method"], string> = {
  exact_text: "exact text",
  normalized_date: "normalized date",
  numeric_tolerance: "numeric tolerance",
  matcher_based: "explicit fuzzy match",
};

function describeCondition(condition: SecondaryMatchCondition): string {
  const tolerance = condition.comparison_method === "numeric_tolerance" ? ` ±${condition.numeric_tolerance ?? 0}` : "";
  return `${condition.source_column} ↔ ${condition.destination_column} (${COMPARISON_LABELS[condition.comparison_method]}${tolerance})`;
}

function fileStem(filename: string): string {
  return filename.replace(/\.(xlsx|xls|csv)$/i, "");
}

export function UploadPage({ onJobCreated }: Props) {
  const [jobType, setJobType] = useState<"generic" | "gst">("generic");
  const [orientation, setOrientation] = useState("vertical");
  const [step, setStep] = useState(1);
  const [file1, setFile1] = useState<UploadedFile | null>(null);
  const [file2, setFile2] = useState<UploadedFile | null>(null);
  const [file1Sheets, setFile1Sheets] = useState<SheetMetadata[]>([]);
  const [file2Sheets, setFile2Sheets] = useState<SheetMetadata[]>([]);
  const [additionalSourceFiles, setAdditionalSourceFiles] = useState<WorkbookWithSheets[]>([]);
  const [additionalDestinationFiles, setAdditionalDestinationFiles] = useState<WorkbookWithSheets[]>([]);
  const [filePairings, setFilePairings] = useState<FilePairing[]>([]);
  const [selectedSheets1, setSelectedSheets1] = useState<string[]>([]);
  const [selectedSheets2, setSelectedSheets2] = useState<string[]>([]);
  const [pairings, setPairings] = useState<SheetPairing[]>([]);
  const [pairConfigs, setPairConfigs] = useState<Record<string, SheetRuleDraft>>({});
  
  const [file1Columns, setFile1Columns] = useState<string[]>([]);
  const [file2Columns, setFile2Columns] = useState<string[]>([]);
  const [gstConfig, setGstConfig] = useState<GstConfiguration | null>(null);
  const [gstConfigError, setGstConfigError] = useState("");
  const [gstTextThreshold, setGstTextThreshold] = useState(85);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState<1 | 2 | null>(null);
  const [uploadProgress, setUploadProgress] = useState({ 1: 0, 2: 0 });
  // What the page is doing right now, so a large workbook never looks stuck.
  const [uploadStage, setUploadStage] = useState<Record<1 | 2, string>>({ 1: "", 2: "" });
  const [analysisStatus, setAnalysisStatus] = useState("");
  const analysisInFlight = useRef(new Set<string>());

  const sourceWorkbooks = useMemo<WorkbookWithSheets[]>(() => file1 ? [{ file: file1, sheets: file1Sheets }, ...additionalSourceFiles] : additionalSourceFiles, [file1, file1Sheets, additionalSourceFiles]);
  const destinationWorkbooks = useMemo<WorkbookWithSheets[]>(() => file2 ? [{ file: file2, sheets: file2Sheets }, ...additionalDestinationFiles] : additionalDestinationFiles, [file2, file2Sheets, additionalDestinationFiles]);
  const hasBothFiles = sourceWorkbooks.length > 0 && destinationWorkbooks.length > 0;
  const completedThrough = step === 1 ? 0 : step - 1;
  const missingGstFile1 = useMemo(() => missingGstColumns(file1Columns, gstConfig), [file1Columns, gstConfig]);
  const missingGstFile2 = useMemo(() => missingGstColumns(file2Columns, gstConfig), [file2Columns, gstConfig]);
  const gstReady = Boolean(gstConfig && hasBothFiles && !missingGstFile1.length && !missingGstFile2.length);

  const file1Name = file1?.original_filename || "File 1";
  const file2Name = file2?.original_filename || "File 2";
  const pairId = (pairing: SheetPairing) => `${pairing.sourceFileId ?? file1?.id ?? "source"}::${pairing.sheet1.id}::${pairing.destinationFileId ?? file2?.id ?? "destination"}::${pairing.sheet2.id}`;
  const selectedFile1Sheets = file1Sheets.filter((sheet) => selectedSheets1.includes(sheet.id));
  const selectedFile2Sheets = file2Sheets.filter((sheet) => selectedSheets2.includes(sheet.id));
  const genericConfigurationsReady = pairings.length > 0 && pairings.every((pairing) => {
    const config = pairConfigs[pairId(pairing)];
    return ruleIsReady(config) && Boolean(config?.rules.length);
  });

  const canContinue = step === 1
    ? hasBothFiles && (file1Sheets.length === 0 || selectedSheets1.length > 0) && (file2Sheets.length === 0 || selectedSheets2.length > 0)
    : step === 2
      ? pairings.length > 0
      : jobType === "gst"
        ? gstReady
        : step === 3
          ? pairings.length > 0 && pairings.every((pairing) => ruleIsReady(pairConfigs[pairId(pairing)]))
          : step === 4
            ? genericConfigurationsReady
            : true;

  function updatePairConfig(pairing: SheetPairing, update: (current: SheetRuleDraft) => SheetRuleDraft) {
    const id = pairId(pairing);
    setPairConfigs((current) => current[id] ? { ...current, [id]: update(current[id]) } : current);
  }

  async function configurePairings(nextPairings: SheetPairing[]) {
    if (jobType !== "generic") return;
    // Skip pairs already configured or being analyzed: pairing effects can fire
    // twice, and duplicate analyses of a large sheet compete for the server.
    const missing = nextPairings.filter((pairing) => !pairConfigs[pairId(pairing)] && !analysisInFlight.current.has(pairId(pairing)));
    missing.forEach((pairing) => analysisInFlight.current.add(pairId(pairing)));
    setBusy(true);
    try {
      let finished = 0;
      if (missing.length) setAnalysisStatus(`Reading ${missing.length} sheet pair${missing.length === 1 ? "" : "s"}… large sheets are read once, then reused.`);
      const configured = await Promise.all(missing.map(async (pairing) => {
        const sourceWorkbook = workbookFor("source", pairing.sourceFileId ?? file1?.id ?? "");
        const destinationWorkbook = workbookFor("destination", pairing.destinationFileId ?? file2?.id ?? "");
        if (!sourceWorkbook || !destinationWorkbook) throw new Error("A sheet pairing refers to an unavailable workbook.");
        const [sourceColumns, destinationColumns, pairAnalysis] = await Promise.all([
          api.getColumns(sourceWorkbook.file.id, orientation, pairing.sheet1.id),
          api.getColumns(destinationWorkbook.file.id, orientation, pairing.sheet2.id),
          api.analyzeFiles({
            source_files_1: [{ file_id: sourceWorkbook.file.id, sheet_id: pairing.sheet1.id }],
            source_files_2: [{ file_id: destinationWorkbook.file.id, sheet_id: pairing.sheet2.id }],
            orientation,
          }),
        ]);
        finished += 1;
        setAnalysisStatus(`Analyzed ${finished} of ${missing.length}: ${pairing.sheet1.name} → ${pairing.sheet2.name}`);
        // Suggestions are offered for one-click selection; nothing is pre-applied.
        const draft: SheetRuleDraft = {
          file1Columns: sourceColumns,
          file2Columns: destinationColumns,
          primaryKeySource: [],
          primaryKeyDestination: [],
          secondaryConditions: [],
          similarityPolicy: {},
          dateOnlyOverride: false,
          rules: [],
          includeFile1: [],
          includeFile2: [],
          analysis: pairAnalysis,
        };
        return [pairId(pairing), draft] as const;
      }));
      setPairConfigs((current) => {
        const retained = Object.fromEntries(nextPairings.flatMap((pairing) => {
          const id = pairId(pairing);
          return current[id] ? [[id, current[id]]] : [];
        }));
        return { ...retained, ...Object.fromEntries(configured) };
      });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not configure every sheet pair");
    } finally {
      missing.forEach((pairing) => analysisInFlight.current.delete(pairId(pairing)));
      setBusy(analysisInFlight.current.size > 0);
      setAnalysisStatus("");
    }
  }

  function handlePairingsChange(nextPairings: SheetPairing[]) {
    setPairings(nextPairings);
    if (jobType === "generic") configurePairings(nextPairings).catch(() => undefined);
    else refreshColumns(nextPairings).catch(() => undefined);
  }

  function handleWorkbookPairingsChange(nextFilePairings: FilePairing[]) {
    setFilePairings(nextFilePairings);
    const permitted = new Set(nextFilePairings.map((pairing) => `${pairing.sourceFileId}::${pairing.destinationFileId}`));
    const retained = pairings.filter((pairing) => permitted.has(`${pairing.sourceFileId}::${pairing.destinationFileId}`));
    setPairings(retained);
    setPairConfigs((current) => Object.fromEntries(Object.entries(current).filter(([id]) => retained.some((pairing) => pairId(pairing) === id))));
  }

  function handleWorkbookSheetPairings(workbookPairing: FilePairing, nextSheets: SheetPairing[]) {
    const enriched = nextSheets.map((pairing) => ({ ...pairing, sourceFileId: workbookPairing.sourceFileId, destinationFileId: workbookPairing.destinationFileId }));
    const retained = pairings.filter((pairing) => pairing.sourceFileId !== workbookPairing.sourceFileId || pairing.destinationFileId !== workbookPairing.destinationFileId);
    handlePairingsChange([...retained, ...enriched]);
  }

  function removeAdditionalWorkbook(side: "source" | "destination", fileId: string) {
    if (side === "source") {
      setAdditionalSourceFiles((current) => current.filter((workbook) => workbook.file.id !== fileId));
    } else {
      setAdditionalDestinationFiles((current) => current.filter((workbook) => workbook.file.id !== fileId));
    }
    handleWorkbookPairingsChange(filePairings.filter((pairing) => (
      side === "source" ? pairing.sourceFileId !== fileId : pairing.destinationFileId !== fileId
    )));
  }

  async function upload(which: 1 | 2, file: File) {
    setUploading(which);
    setUploadProgress((current) => ({ ...current, [which]: 0 }));
    setMessage("");
    const stage = (text: string) => setUploadStage((current) => ({ ...current, [which]: text }));
    try {
      stage(`Uploading ${file.name}…`);
      const stored = await api.uploadFile(file, (progress) => setUploadProgress((current) => ({ ...current, [which]: progress })));
      stage("Reading the sheet list…");
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
      // The workbook is usable now: sheet selection and Continue do not wait
      // for the column headers, and the server parses the data in the background.
      setUploading(null);
      if (sheetIds.length > 0) {
        stage("Reading column headers…");
        const columns = await api.getColumns(stored.id, orientation, sheetIds[0]);
        if (which === 1) {
          setFile1Columns(columns);
        } else {
          setFile2Columns(columns);
        }
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setUploading(null);
      stage("");
    }
  }

  async function uploadAdditional(side: "source" | "destination", file: File) {
    setBusy(true);
    setMessage("");
    try {
      const stored = await api.uploadFile(file);
      const metadata = await api.getFileMetadata(stored.id);
      const workbook = { file: stored, sheets: metadata.sheets };
      if (side === "source") setAdditionalSourceFiles((current) => [...current, workbook]);
      else setAdditionalDestinationFiles((current) => [...current, workbook]);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  function workbookFor(side: "source" | "destination", fileId: string): WorkbookWithSheets | undefined {
    return (side === "source" ? sourceWorkbooks : destinationWorkbooks).find((workbook) => workbook.file.id === fileId);
  }

  function workbookNameForPair(side: "source" | "destination", pairing: SheetPairing) {
    const fileId = side === "source" ? pairing.sourceFileId : pairing.destinationFileId;
    return workbookFor(side, fileId ?? "")?.file.original_filename ?? (side === "source" ? "File 1" : "File 2");
  }

  const multiWorkbook = sourceWorkbooks.length > 1 || destinationWorkbooks.length > 1;
  function ruleTitle(pairing: SheetPairing) {
    return multiWorkbook
      ? `${workbookNameForPair("source", pairing)} › ${pairing.sheet1.name} → ${workbookNameForPair("destination", pairing)} › ${pairing.sheet2.name}`
      : `${pairing.sheet1.name} → ${pairing.sheet2.name}`;
  }

  function copyOptionsFor(target: SheetPairing) {
    return pairings
      .filter((other) => pairId(other) !== pairId(target) && pairConfigs[pairId(other)])
      .map((other) => ({ id: pairId(other), label: ruleTitle(other) }));
  }

  function copySettings(target: SheetPairing, sourceId: string) {
    const source = pairConfigs[sourceId];
    if (source) updatePairConfig(target, (current) => copyRuleSettings(source, current));
  }

  function ruleStatus(config: SheetRuleDraft) {
    if (![...config.primaryKeySource, ...config.primaryKeyDestination].every(Boolean) || !config.primaryKeySource.length) return "Choose the key columns";
    if (config.secondaryConditions.some((condition) => !condition.source_column || !condition.destination_column)) return "Finish the \"must also match\" columns";
    if (config.primaryKeySource.length !== config.primaryKeyDestination.length) return "Key column counts differ";
    if (isDateOnlyKey(config) && !config.dateOnlyOverride) return "Date-only key needs attention";
    return `${config.primaryKeySource.join(" + ")} · ${config.secondaryConditions.length} must-also-match column${config.secondaryConditions.length === 1 ? "" : "s"} · ${config.rules.length} mapping${config.rules.length === 1 ? "" : "s"}`;
  }

  /** Canonical plan: one file pair per workbook pairing, one independent sheet rule per sheet pairing. */
  function buildFilePairs() {
    const groups = new Map<string, { sourceFileId: string; destinationFileId: string; pairings: SheetPairing[] }>();
    for (const pairing of pairings) {
      const sourceFileId = pairing.sourceFileId ?? file1?.id;
      const destinationFileId = pairing.destinationFileId ?? file2?.id;
      if (!sourceFileId || !destinationFileId) continue;
      const groupId = `${sourceFileId}::${destinationFileId}`;
      const group = groups.get(groupId) ?? { sourceFileId, destinationFileId, pairings: [] };
      group.pairings.push(pairing);
      groups.set(groupId, group);
    }
    return Array.from(groups.values()).map((group, index) => {
      const sourceName = workbookFor("source", group.sourceFileId)?.file.original_filename ?? "Source";
      const destinationName = workbookFor("destination", group.destinationFileId)?.file.original_filename ?? "Destination";
      return {
        file_pair_id: `pair-${index + 1}`,
        source_files: [{ file_id: group.sourceFileId }],
        destination_files: [{ file_id: group.destinationFileId }],
        report_metadata: { label: `${fileStem(sourceName)} vs ${fileStem(destinationName)}` },
        sheet_rules: group.pairings.map((pairing, ruleIndex) => {
          const config = pairConfigs[pairId(pairing)];
          return {
            sheet_rule_id: `rule-${index + 1}-${ruleIndex + 1}`,
            source_sheets: [pairing.sheet1.id],
            destination_sheets: [pairing.sheet2.id],
            matching_strategy: {
              primary_key_source: config.primaryKeySource,
              primary_key_destination: config.primaryKeyDestination,
              secondary_conditions: config.secondaryConditions,
              similarity_policy: config.similarityPolicy,
              date_only_override: isDateOnlyKey(config) && config.dateOnlyOverride,
            },
            reconciliation_mapping: config.rules,
            include_columns_file_1: config.includeFile1,
            include_columns_file_2: config.includeFile2,
            report_label: `${pairing.sheet1.name} -> ${pairing.sheet2.name}`,
          };
        }),
      };
    });
  }

  async function refreshColumns(activePairings?: SheetPairing[]) {
    if (!file1 && !file2) return;
    // Headers only (fast); this never disables navigation.
    try {
      // Use the first pairing's sheet IDs for column analysis if available
      const activePairs = activePairings ?? pairings;
      const sheet1Id = activePairs[0]?.sheet1?.id ?? selectedSheets1[0] ?? file1Sheets[0]?.id;
      const sheet2Id = activePairs[0]?.sheet2?.id ?? selectedSheets2[0] ?? file2Sheets[0]?.id;

      if (file1) {
        const columns = await api.getColumns(file1.id, orientation, sheet1Id);
        setFile1Columns(columns);
      }
      if (file2) {
        const columns = await api.getColumns(file2.id, orientation, sheet2Id);
        setFile2Columns(columns);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not analyze the files");
    }
  }

  // Uploads already read their headers; only an orientation change needs a re-read.
  useEffect(() => {
    if (file1 || file2) {
      refreshColumns().catch(() => undefined);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orientation]);
  useEffect(() => {
    api.getGstConfiguration()
      .then((config) => { setGstConfig(config); setGstConfigError(""); })
      .catch((error: Error) => setGstConfigError(error.message || "Could not load the GST configuration."));
  }, []);

  async function start() {
    if (!hasBothFiles) {
      setMessage("Upload both Excel files first.");
      return;
    }
    const primarySource = file1 ?? sourceWorkbooks[0]?.file;
    const primaryDestination = file2 ?? destinationWorkbooks[0]?.file;
    if (!primarySource || !primaryDestination) {
      setMessage("Upload a source and destination workbook first.");
      return;
    }
    if (jobType === "gst" && (!file1 || !file2)) {
      setMessage("GST reconciliation uses the first source and destination workbook.");
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
      const source_files_1 = s1.length > 0 ? s1.map(id => ({ file_id: primarySource.id, sheet_id: id })) : [{ file_id: primarySource.id }];
      const source_files_2 = s2.length > 0 ? s2.map(id => ({ file_id: primaryDestination.id, sheet_id: id })) : [{ file_id: primaryDestination.id }];

      const job = jobType === "gst"
        ? await api.startGst({
            file_1_id: primarySource.id,
            file_2_id: primaryDestination.id,
            source_files_1,
            source_files_2,
            orientation,
            text_threshold: gstTextThreshold
          })
        : await api.startGeneric({
            file_1_id: primarySource.id,
            file_2_id: primaryDestination.id,
            orientation,
            file_pairs: buildFilePairs(),
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
            {uploadStage[1] && <p className="upload-stage"><Loader2 size={14} className="animate-spin" />{uploadStage[1]}</p>}
            <SheetSelector sheets={file1Sheets} selectedSheets={selectedSheets1} onChange={setSelectedSheets1} fileName={file1Name} />
          </div>
          <div>
            <FileDropzone label={file2Name} fileName={file2?.original_filename} fileSize={file2?.size_bytes} columnCount={file2Columns.length} uploading={uploading === 2} progress={uploadProgress[2]} onFile={(file) => upload(2, file)} />
            {uploadStage[2] && <p className="upload-stage"><Loader2 size={14} className="animate-spin" />{uploadStage[2]}</p>}
            <SheetSelector sheets={file2Sheets} selectedSheets={selectedSheets2} onChange={setSelectedSheets2} fileName={file2Name} />
          </div>
        </div>
        {jobType === "generic" && <div className="upload-grid mt-4"><AdditionalWorkbookUpload label="Add another source workbook" onFile={(file) => uploadAdditional("source", file)} disabled={busy} /><AdditionalWorkbookUpload label="Add another destination workbook" onFile={(file) => uploadAdditional("destination", file)} disabled={busy} /></div>}
        {(additionalSourceFiles.length > 0 || additionalDestinationFiles.length > 0) && <><div className="info-callout"><Sparkles size={18} /><span>{sourceWorkbooks.length} source and {destinationWorkbooks.length} destination workbooks are ready for explicit pairing.</span></div><WorkbookList label="Additional source workbooks" workbooks={additionalSourceFiles} onRemove={(fileId) => removeAdditionalWorkbook("source", fileId)} /><WorkbookList label="Additional destination workbooks" workbooks={additionalDestinationFiles} onRemove={(fileId) => removeAdditionalWorkbook("destination", fileId)} /></>}
      </div>}
      {step === 2 && <div className="step-content">
        <div className="section-heading"><div><h2>Pair your sheets</h2><p>Map each source sheet to its destination counterpart. Each pair is reconciled independently.</p></div></div>
        {busy && <div className="loading-state"><Loader2 className="animate-spin" /> {analysisStatus || "Preparing sheet pairs…"}</div>}
        {sourceWorkbooks.length === 1 && destinationWorkbooks.length === 1 ? <SheetPairingStep file1Sheets={selectedFile1Sheets} file2Sheets={selectedFile2Sheets} file1Name={file1Name} file2Name={file2Name} pairings={pairings} onChange={(next) => handleWorkbookSheetPairings({ sourceFileId: sourceWorkbooks[0].file.id, destinationFileId: destinationWorkbooks[0].file.id }, next)} /> : <><FilePairingStep sourceFiles={sourceWorkbooks} destinationFiles={destinationWorkbooks} pairings={filePairings} onChange={handleWorkbookPairingsChange} />{filePairings.map((workbookPairing) => { const source = workbookFor("source", workbookPairing.sourceFileId); const destination = workbookFor("destination", workbookPairing.destinationFileId); if (!source || !destination) return null; const scoped = pairings.filter((pairing) => pairing.sourceFileId === source.file.id && pairing.destinationFileId === destination.file.id); return <section className="mapping-workspace" key={`${source.file.id}-${destination.file.id}`}><h3>{source.file.original_filename} <ArrowRight size={16} /> {destination.file.original_filename}</h3><SheetPairingStep file1Sheets={source.sheets} file2Sheets={destination.sheets} file1Name={source.file.original_filename} file2Name={destination.file.original_filename} pairings={scoped} onChange={(next) => handleWorkbookSheetPairings(workbookPairing, next)} /></section>; })}</>}
      </div>}
      {step === 3 && <div className="step-content">
        {jobType === "generic" ? <>
          <div className="section-heading"><div><h2>Configure matching for each sheet pair</h2><p>Tell RecliQ how to recognise the same record in both files. Each sheet pair is set up on its own.</p></div></div>
          {busy && <div className="loading-state"><Loader2 className="animate-spin" /> {analysisStatus || "Analyzing sheet pairs…"}</div>}
          {pairings.map((pairing, index) => {
            const config = pairConfigs[pairId(pairing)];
            return config ? <RulePanel key={pairId(pairing)} index={index} title={ruleTitle(pairing)} status={ruleStatus(config)} ready={ruleIsReady(config)}>
              <SheetRuleConfiguration pairing={pairing} config={config} onChange={(next) => updatePairConfig(pairing, () => next)} copyOptions={copyOptionsFor(pairing)} onCopyFrom={(sourceId) => copySettings(pairing, sourceId)} sourceLabel={`${workbookNameForPair("source", pairing)} · ${pairing.sheet1.name}`} destinationLabel={`${workbookNameForPair("destination", pairing)} · ${pairing.sheet2.name}`} />
            </RulePanel> : null;
          })}
        </> : <GstMatchingKeyStep config={gstConfig} missingFile1={missingGstFile1} missingFile2={missingGstFile2} error={gstConfigError} file1Name={file1Name} file2Name={file2Name} />}
      </div>}
      {step === 4 && <div className="step-content">{jobType === "generic" ? pairings.map((pairing, index) => { const config = pairConfigs[pairId(pairing)]; return config ? <RulePanel key={pairId(pairing)} index={index} title={ruleTitle(pairing)} status={`${config.rules.length} mapped field${config.rules.length === 1 ? "" : "s"}`} ready={config.rules.length > 0}><MappingBuilder file1Columns={config.file1Columns} file2Columns={config.file2Columns} rules={config.rules} onRulesChange={(nextRules) => updatePairConfig(pairing, (current) => ({ ...current, rules: nextRules }))} primaryFile1={config.primaryKeySource} primaryFile2={config.primaryKeyDestination} file1Name={`${workbookNameForPair("source", pairing)} - ${pairing.sheet1.name}`} file2Name={`${workbookNameForPair("destination", pairing)} - ${pairing.sheet2.name}`} suggestions={(config.analysis?.recommended_mappings ?? []).filter((mapping) => mapping.target && mapping.confidence !== "Low" && mapping.confidence !== "None").map((mapping) => ({ source: mapping.source, target: mapping.target as string }))} /></RulePanel> : null; }) : <GstColumnMappingStep config={gstConfig} missingFile1={missingGstFile1} missingFile2={missingGstFile2} file1Name={file1Name} file2Name={file2Name} />}</div>}
      {step === 5 && <div className="step-content">{jobType === "generic" ? pairings.map((pairing, index) => { const config = pairConfigs[pairId(pairing)]; return config ? <RulePanel key={pairId(pairing)} index={index} title={ruleTitle(pairing)} status={`${config.includeFile1.length + config.includeFile2.length} context column${config.includeFile1.length + config.includeFile2.length === 1 ? "" : "s"}`} ready><ReportColumnPicker file1Columns={config.file1Columns.filter((column) => !config.primaryKeySource.includes(column))} file2Columns={config.file2Columns.filter((column) => !config.primaryKeyDestination.includes(column))} selectedFile1={config.includeFile1} selectedFile2={config.includeFile2} onChangeFile1={(includeFile1) => updatePairConfig(pairing, (current) => ({ ...current, includeFile1 }))} onChangeFile2={(includeFile2) => updatePairConfig(pairing, (current) => ({ ...current, includeFile2 }))} file1Name={`${workbookNameForPair("source", pairing)} - ${pairing.sheet1.name}`} file2Name={`${workbookNameForPair("destination", pairing)} - ${pairing.sheet2.name}`} /></RulePanel> : null; }) : <GstReportSetup threshold={gstTextThreshold} onThresholdChange={setGstTextThreshold} />}</div>}
      {step === 6 && <div className="ready-card"><div><span className="eyebrow">Ready to reconcile</span><h2>{jobType === "gst" ? "GST invoice reconciliation" : "General reconciliation"}</h2><p>Review the setup below, then let RecliQ generate your report.</p></div><dl>{jobType === "generic" && multiWorkbook ? <><div><dt>Workbooks</dt><dd>{sourceWorkbooks.length} source · {destinationWorkbooks.length} destination</dd></div><div><dt>File pairs</dt><dd>{new Set(pairings.map((pairing) => `${pairing.sourceFileId}::${pairing.destinationFileId}`)).size} (one report each, delivered as a ZIP)</dd></div></> : <><div><dt>Source file ({file1Name})</dt><dd>{file1?.original_filename}</dd></div><div><dt>Destination file ({file2Name})</dt><dd>{file2?.original_filename}</dd></div></>}<div><dt>Sheet rules</dt><dd>{pairings.length > 0 ? `${pairings.length} independent rule${pairings.length !== 1 ? "s" : ""}` : "Single sheet"}</dd></div><div><dt>Matching key</dt><dd>{jobType === "gst" ? "GSTR + Invoice No." : pairings.length === 1 ? `${pairConfigs[pairId(pairings[0])]?.primaryKeySource.join(" + ")} → ${pairConfigs[pairId(pairings[0])]?.primaryKeyDestination.join(" + ")}` : "Configured per sheet rule (below)"}</dd></div><div><dt>Mapped fields</dt><dd>{jobType === "gst" ? `${gstConfig?.required_columns.length ?? 0} verified GST fields` : pairings.reduce((count, pairing) => count + (pairConfigs[pairId(pairing)]?.rules.length ?? 0), 0)}</dd></div><div><dt>Report columns</dt><dd>{jobType === "gst" ? `GST report (confidence ${gstTextThreshold}%)` : pairings.reduce((count, pairing) => { const config = pairConfigs[pairId(pairing)]; return count + (config?.includeFile1.length ?? 0) + (config?.includeFile2.length ?? 0); }, 0)}</dd></div><div><dt>Orientation</dt><dd>{orientation === "vertical" ? "Column headers" : "Row headers"}</dd></div></dl>{jobType === "generic" && <section className="review-rules" aria-label="Sheet rule review">{pairings.map((pairing, index) => { const config = pairConfigs[pairId(pairing)]; if (!config) return null; const dateOnly = isDateOnlyKey(config); return <details key={pairId(pairing)} className="rule-panel" open={pairings.length <= 3}><summary>Rule {index + 1} · {ruleTitle(pairing)}<small>{ruleStatus(config)}</small></summary><div className="rule-panel-body"><dl>
        <dt>Primary key</dt><dd>{config.primaryKeySource.join(" + ")} → {config.primaryKeyDestination.join(" + ")}</dd>
        <dt>Must also match</dt><dd>{config.secondaryConditions.length ? config.secondaryConditions.map(describeCondition).join("; ") : "Nothing else (key only)"}</dd>
        <dt>Key comparison</dt><dd>{config.similarityPolicy.matcher_type_override ?? "Automatic by data type"} · minimum confidence {config.similarityPolicy.threshold !== undefined ? `${config.similarityPolicy.threshold}%` : "automatic"}</dd>
        {dateOnly && <><dt>Date-only key</dt><dd className="is-warning">{config.dateOnlyOverride ? "Explicitly allowed — recorded in the report" : "Blocked until another key column is added or the override is enabled"}</dd></>}
        <dt>Mapped fields</dt><dd>{config.rules.length}</dd>
        <dt>Report context columns</dt><dd>{config.includeFile1.length + config.includeFile2.length}</dd>
      </dl></div></details>; })}</section>}<button type="button" className="primary run-button" onClick={start} disabled={busy || (jobType === "gst" && !gstReady) || (jobType === "generic" && !genericConfigurationsReady)}><Play size={18} />{busy ? "Starting reconciliation..." : "Run reconciliation"}</button></div>}
    </div>
    {message && <p className="error-text">{message}</p>}
    <div className="workflow-actions"><button type="button" className="secondary" onClick={() => setStep((current) => Math.max(1, current - 1))} disabled={step === 1 || busy}><ArrowLeft size={16} />Back</button>{step < 6 ? <button type="button" className="primary" onClick={() => setStep((current) => current + 1)} disabled={!canContinue || busy}>Continue<ArrowRight size={16} /></button> : null}</div>
  </section>;
}

function AdditionalWorkbookUpload({ label, onFile, disabled }: { label: string; onFile: (file: File) => void; disabled: boolean }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  return <div className="info-callout"><Sparkles size={18} /><span>{label}</span><button type="button" className="secondary" onClick={() => inputRef.current?.click()} disabled={disabled}>Choose workbook</button><input ref={inputRef} className="visually-hidden" type="file" accept=".xlsx,.xls,.csv" onChange={(event) => { const file = event.target.files?.item(0); if (file) onFile(file); event.currentTarget.value = ""; }} /></div>;
}

function WorkbookList({ label, workbooks, onRemove }: { label: string; workbooks: WorkbookWithSheets[]; onRemove: (fileId: string) => void }) {
  if (!workbooks.length) return null;
  return <section className="uploaded-workbook-list" aria-label={label}><h3>{label}</h3>{workbooks.map((workbook) => <div key={workbook.file.id}><span>{workbook.file.original_filename}</span><small>{workbook.sheets.length} sheet{workbook.sheets.length === 1 ? "" : "s"}</small><button type="button" className="icon-button" title={`Remove ${workbook.file.original_filename}`} aria-label={`Remove ${workbook.file.original_filename}`} onClick={() => onRemove(workbook.file.id)}><X size={16} /></button></div>)}</section>;
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

function RulePanel({ index, title, status, ready, children }: { index: number; title: string; status: string; ready: boolean; children: React.ReactNode }) {
  return <details className="rule-panel" open>
    <summary>{ready ? <CheckCircle2 size={16} color="#087d72" /> : <AlertTriangle size={16} color="#b97a10" />}Rule {index + 1} · {title}<small>{status}</small></summary>
    <div className="rule-panel-body">{children}</div>
  </details>;
}

interface SheetRuleConfigurationProps {
  pairing: SheetPairing;
  config: SheetRuleDraft;
  onChange: (config: SheetRuleDraft) => void;
  copyOptions: Array<{ id: string; label: string }>;
  onCopyFrom: (sourceId: string) => void;
  sourceLabel: string;
  destinationLabel: string;
}

const COMPARISON_OPTIONS: Array<{ value: SecondaryMatchCondition["comparison_method"]; label: string }> = [
  { value: "exact_text", label: "Same text" },
  { value: "normalized_date", label: "Same date" },
  { value: "numeric_tolerance", label: "Same amount (± tolerance)" },
  { value: "matcher_based", label: "Similar text" },
];

function SheetRuleConfiguration({ pairing, config, onChange, copyOptions, onCopyFrom, sourceLabel, destinationLabel }: SheetRuleConfigurationProps) {
  // Keys are edited as ordered source ↔ destination pairs, so both sides
  // always have the same number of columns.
  const keyRows = Math.max(1, config.primaryKeySource.length, config.primaryKeyDestination.length);
  const keyPairs = Array.from({ length: keyRows }, (_, index) => [config.primaryKeySource[index] ?? "", config.primaryKeyDestination[index] ?? ""] as const);
  const setKeyPairs = (pairs: ReadonlyArray<readonly [string, string]>) => onChange({
    ...config,
    primaryKeySource: pairs.map(([source]) => source),
    primaryKeyDestination: pairs.map(([, destination]) => destination),
  });
  const updateKey = (index: number, side: 0 | 1, column: string) => setKeyPairs(keyPairs.map((pair, itemIndex) => (
    itemIndex === index ? (side === 0 ? [column, pair[1]] as const : [pair[0], column] as const) : pair
  )));
  const addCondition = () => onChange({
    ...config,
    secondaryConditions: [...config.secondaryConditions, { source_column: "", destination_column: "", comparison_method: "exact_text" }],
  });
  const updateCondition = (index: number, update: Partial<SecondaryMatchCondition>) => {
    onChange({ ...config, secondaryConditions: config.secondaryConditions.map((condition, itemIndex) => itemIndex === index ? { ...condition, ...update } : condition) });
  };
  const dateOnly = isDateOnlyKey(config);
  const suggestedSource = config.analysis?.recommended_keys_1.filter((column) => config.file1Columns.includes(column)) ?? [];
  const suggestedDestination = config.analysis?.recommended_keys_2.filter((column) => config.file2Columns.includes(column)) ?? [];
  const suggestionUsable = suggestedSource.length > 0 && suggestedSource.length === suggestedDestination.length;
  const suggestionApplied = suggestedSource.join("|") === config.primaryKeySource.join("|") && suggestedDestination.join("|") === config.primaryKeyDestination.join("|");
  const applySuggestion = () => onChange({ ...config, primaryKeySource: suggestedSource, primaryKeyDestination: suggestedDestination, dateOnlyOverride: false });

  const columnSelect = (columns: string[], value: string, onSelect: (column: string) => void, label: string) => (
    <select value={value} onChange={(event) => onSelect(event.target.value)} aria-label={label} className={value ? "" : "is-empty"}>
      <option value="">Choose a column…</option>
      {columns.map((column) => <option key={column} value={column}>{column}</option>)}
    </select>
  );

  return <section className="sheet-rule-config">
    {copyOptions.length > 0 && <label className="rule-copy-control">Copy settings from another rule
      {/* Always a deep copy: later edits to either rule never affect the other. */}
      <select value="" onChange={(event) => { if (event.target.value) onCopyFrom(event.target.value); }}>
        <option value="">Choose a rule…</option>
        {copyOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select>
      <small>Columns missing from this sheet are skipped.</small>
    </label>}

    <div className="rule-section">
      <div className="rule-section-heading">
        <div><h4>1. Match records on</h4><p>The column(s) that identify the same record in both sheets, such as an invoice or reference number. Spacing, case and punctuation (INV005 = INV/005 = INV-005) are ignored.</p></div>
      </div>
      {suggestionUsable && !suggestionApplied && <div className="suggestion-strip">
        <span>Suggested from your data</span>
        <button type="button" className="suggestion-chip" onClick={applySuggestion} title="Use this key">
          <Plus size={14} />{suggestedSource.map((column, index) => `${column} ↔ ${suggestedDestination[index]}`).join("  +  ")}
        </button>
        {config.analysis?.key_reason && <small>{config.analysis.key_reason}</small>}
      </div>}
      <div className="key-pair-header"><span>{sourceLabel}</span><span /><span>{destinationLabel}</span><span /></div>
      {keyPairs.map(([source, destination], index) => <div className="key-pair-row" key={`key-${index}`}>
        {columnSelect(config.file1Columns, source, (column) => updateKey(index, 0, column), `${pairing.sheet1.name} key column ${index + 1}`)}
        <ArrowRight size={16} />
        {columnSelect(config.file2Columns, destination, (column) => updateKey(index, 1, column), `${pairing.sheet2.name} key column ${index + 1}`)}
        <button type="button" className="icon-button" onClick={() => setKeyPairs(keyPairs.filter((_, itemIndex) => itemIndex !== index))} disabled={keyPairs.length === 1 && !source && !destination} title="Remove this key column" aria-label="Remove this key column"><X size={16} /></button>
      </div>)}
      <button type="button" className="text-command" onClick={() => setKeyPairs([...keyPairs, ["", ""]])}><Plus size={15} />Add another key column</button>
      {dateOnly && <div className="date-only-guard" role="alert">
        <span><AlertTriangle size={16} /> {DATE_ONLY_WARNING}</span>
        <label><input type="checkbox" checked={config.dateOnlyOverride} onChange={(event) => onChange({ ...config, dateOnlyOverride: event.target.checked })} />Allow date-only matching for this rule anyway (recorded as a warning in the report)</label>
      </div>}
    </div>

    <div className="rule-section">
      <div className="rule-section-heading">
        <div><h4>2. Must also match <span className="optional-tag">optional</span></h4><p>Records are paired only when the key above <strong>and</strong> every column here agree, for example Invoice No. and Vendor Name. Rows that share all of them are added up before matching.</p></div>
        <button type="button" className="secondary" onClick={addCondition}><Plus size={15} />Add column</button>
      </div>
      {config.secondaryConditions.length === 0 && <p className="rule-empty">No extra columns: records are paired on the key alone.</p>}
      {config.secondaryConditions.map((condition, index) => <div className="key-pair-row has-method" key={`condition-${index}`}>
        {columnSelect(config.file1Columns, condition.source_column, (column) => updateCondition(index, { source_column: column }), `${pairing.sheet1.name} column`)}
        <ArrowRight size={16} />
        {columnSelect(config.file2Columns, condition.destination_column, (column) => updateCondition(index, { destination_column: column }), `${pairing.sheet2.name} column`)}
        <div className="method-control">
          <select value={condition.comparison_method} onChange={(event) => updateCondition(index, { comparison_method: event.target.value as SecondaryMatchCondition["comparison_method"] })} aria-label="How to compare">
            {COMPARISON_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          {condition.comparison_method === "numeric_tolerance" && <input type="number" min="0" step="0.01" value={condition.numeric_tolerance ?? 0} onChange={(event) => updateCondition(index, { numeric_tolerance: Number(event.target.value) })} aria-label="Tolerance" />}
        </div>
        <button type="button" className="icon-button" onClick={() => onChange({ ...config, secondaryConditions: config.secondaryConditions.filter((_, itemIndex) => itemIndex !== index) })} title="Remove this column" aria-label="Remove this column"><X size={16} /></button>
      </div>)}
    </div>

    <details className="rule-advanced">
      <summary>Advanced: slightly different keys</summary>
      <p>When a key is not found exactly, RecliQ looks for a record with a <em>similar</em> key whose "must also match" columns agree, and lists it under Match Review for you to confirm.</p>
      <div className="advanced-grid">
        <label><span>Compare keys as</span><select value={config.similarityPolicy.matcher_type_override ?? ""} onChange={(event) => onChange({ ...config, similarityPolicy: { ...config.similarityPolicy, matcher_type_override: event.target.value || undefined } })}><option value="">Automatic (from the data)</option><option value="identifier">Codes / reference numbers</option><option value="company_name">Company names</option><option value="person_name">Person names</option><option value="text">Plain text</option></select></label>
        <label><span>How similar (%)</span><input type="number" min="0" max="100" placeholder="Automatic" value={config.similarityPolicy.threshold ?? ""} onChange={(event) => onChange({ ...config, similarityPolicy: { ...config.similarityPolicy, threshold: event.target.value === "" ? undefined : Number(event.target.value) } })} /></label>
      </div>
    </details>
  </section>;
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
