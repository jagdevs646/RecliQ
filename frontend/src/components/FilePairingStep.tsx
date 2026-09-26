import { ArrowRight, Link2 } from "lucide-react";
import type { UploadedFile } from "../types";

export interface WorkbookWithSheets {
  file: UploadedFile;
  sheets: { id: string; name: string }[];
}

export interface FilePairing {
  sourceFileId: string;
  destinationFileId: string;
}

interface Props {
  sourceFiles: WorkbookWithSheets[];
  destinationFiles: WorkbookWithSheets[];
  pairings: FilePairing[];
  onChange: (pairings: FilePairing[]) => void;
}

export function FilePairingStep({ sourceFiles, destinationFiles, pairings, onChange }: Props) {
  const usedDestinations = new Set(pairings.map((pairing) => pairing.destinationFileId));
  const pairedDestination = (sourceFileId: string) => pairings.find((pairing) => pairing.sourceFileId === sourceFileId)?.destinationFileId ?? "";

  function setPairing(sourceFileId: string, destinationFileId: string) {
    const next = pairings.filter((pairing) => pairing.sourceFileId !== sourceFileId && pairing.destinationFileId !== destinationFileId);
    if (destinationFileId) next.push({ sourceFileId, destinationFileId });
    onChange(next);
  }

  return <section className="sheet-pairing-step">
    <div className="section-heading"><div><h3>Pair uploaded workbooks</h3><p>Each workbook pair generates its own report and does not share reconciliation rules.</p></div></div>
    <div className="pairing-grid">
      {sourceFiles.map((source) => {
        const destinationId = pairedDestination(source.file.id);
        return <div key={source.file.id} className={`pairing-sheet-row ${destinationId ? "is-paired" : ""}`}>
          <div className="pairing-sheet-label"><span>{source.file.original_filename}</span><small>{source.sheets.length} sheet{source.sheets.length === 1 ? "" : "s"}</small></div>
          <ArrowRight size={16} className="pairing-arrow" />
          <div className="pairing-sheet-select"><select value={destinationId} onChange={(event) => setPairing(source.file.id, event.target.value)}><option value="">Select destination workbook</option>{destinationFiles.map((destination) => <option key={destination.file.id} value={destination.file.id} disabled={usedDestinations.has(destination.file.id) && destinationId !== destination.file.id}>{destination.file.original_filename}{usedDestinations.has(destination.file.id) && destinationId !== destination.file.id ? " (paired)" : ""}</option>)}</select></div>
        </div>;
      })}
    </div>
    <div className="pairing-summary"><div className="pairing-count-badge"><Link2 size={14} /><span>{pairings.length} workbook pairing{pairings.length === 1 ? "" : "s"} ready</span></div></div>
  </section>;
}
