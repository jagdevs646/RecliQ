import { ArrowRight, Link2, Plus, X } from "lucide-react";
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

/**
 * Pairs workbooks freely: one workbook can be paired with several on the other
 * side, e.g. a workbook with many sheets against one workbook per sheet.
 */
export function FilePairingStep({ sourceFiles, destinationFiles, pairings, onChange }: Props) {
  const pairedWith = (sourceFileId: string) => pairings.filter((pairing) => pairing.sourceFileId === sourceFileId).map((pairing) => pairing.destinationFileId);
  const add = (sourceFileId: string, destinationFileIds: string[]) => onChange([...pairings, ...destinationFileIds.map((destinationFileId) => ({ sourceFileId, destinationFileId }))]);
  const remove = (sourceFileId: string, destinationFileId: string) => onChange(pairings.filter((pairing) => pairing.sourceFileId !== sourceFileId || pairing.destinationFileId !== destinationFileId));
  const usedElsewhere = (sourceFileId: string, destinationFileId: string) => pairings.some((pairing) => pairing.destinationFileId === destinationFileId && pairing.sourceFileId !== sourceFileId);

  return <section className="sheet-pairing-step">
    <div className="section-heading"><div><h3>Pair uploaded workbooks</h3><p>Pair a workbook with one or several workbooks on the other side. Each workbook pair generates its own report and does not share reconciliation rules.</p></div></div>
    <div className="pairing-grid">
      {sourceFiles.map((source) => {
        const paired = pairedWith(source.file.id);
        const open = destinationFiles.filter((destination) => !paired.includes(destination.file.id));
        return <div key={source.file.id} className={`pairing-sheet-row is-multi ${paired.length ? "is-paired" : ""}`}>
          <div className="pairing-sheet-label"><span>{source.file.original_filename}</span><small>{source.sheets.length} sheet{source.sheets.length === 1 ? "" : "s"}</small></div>
          <ArrowRight size={16} className="pairing-arrow" />
          <div className="pairing-targets">
            {paired.map((destinationFileId) => {
              const destination = destinationFiles.find((item) => item.file.id === destinationFileId);
              return <span className="pairing-target" key={destinationFileId}>{destination?.file.original_filename ?? "Workbook"}<button type="button" onClick={() => remove(source.file.id, destinationFileId)} title="Remove this pairing" aria-label={`Remove pairing with ${destination?.file.original_filename ?? "workbook"}`}><X size={13} /></button></span>;
            })}
            {open.length > 0 && <div className="pairing-sheet-select">
              <select value="" aria-label={`Pair ${source.file.original_filename} with a destination workbook`} onChange={(event) => { if (event.target.value) add(source.file.id, [event.target.value]); }}>
                <option value="">{paired.length ? "Add another destination workbook" : "Select destination workbook"}</option>
                {open.map((destination) => <option key={destination.file.id} value={destination.file.id}>{destination.file.original_filename}{usedElsewhere(source.file.id, destination.file.id) ? " (also paired elsewhere)" : ""}</option>)}
              </select>
              {open.length > 1 && <button type="button" className="text-button" onClick={() => add(source.file.id, open.map((destination) => destination.file.id))}><Plus size={13} />Pair with all {open.length}</button>}
            </div>}
          </div>
        </div>;
      })}
    </div>
    <div className="pairing-summary"><div className="pairing-count-badge"><Link2 size={14} /><span>{pairings.length} workbook pairing{pairings.length === 1 ? "" : "s"} ready</span></div></div>
  </section>;
}
