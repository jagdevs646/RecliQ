import { ArrowRight, CheckCircle2, Link2 } from "lucide-react";
import type { SheetMetadata } from "../types";

export interface SheetPairing {
  sheet1: SheetMetadata;
  sheet2: SheetMetadata;
}

interface Props {
  file1Sheets: SheetMetadata[];
  file2Sheets: SheetMetadata[];
  file1Name: string;
  file2Name: string;
  pairings: SheetPairing[];
  onChange: (pairings: SheetPairing[]) => void;
}

export function SheetPairingStep({
  file1Sheets,
  file2Sheets,
  file1Name,
  file2Name,
  pairings,
  onChange,
}: Props) {
  const pairedSheet2Ids = new Set(pairings.map((p) => p.sheet2.id));

  function getPairedSheet2(sheet1Id: string): SheetMetadata | null {
    return pairings.find((p) => p.sheet1.id === sheet1Id)?.sheet2 ?? null;
  }

  function handlePair(sheet1: SheetMetadata, sheet2Id: string) {
    const sheet2 = file2Sheets.find((s) => s.id === sheet2Id);
    if (!sheet2) return;
    const filtered = pairings.filter(
      (p) => p.sheet1.id !== sheet1.id && p.sheet2.id !== sheet2Id
    );
    onChange([...filtered, { sheet1, sheet2 }]);
  }

  function handleRemovePairing(sheet1Id: string) {
    onChange(pairings.filter((p) => p.sheet1.id !== sheet1Id));
  }

  const isSingleSheet = file1Sheets.length === 1 && file2Sheets.length === 1;

  if (isSingleSheet) {
    const autoSheet1 = file1Sheets[0];
    const autoSheet2 = file2Sheets[0];
    const paired = pairings.some(
      (p) => p.sheet1.id === autoSheet1.id && p.sheet2.id === autoSheet2.id
    );
    if (!paired) {
      setTimeout(() => onChange([{ sheet1: autoSheet1, sheet2: autoSheet2 }]), 0);
    }
    return (
      <div className="pairing-auto-notice">
        <CheckCircle2 size={16} />
        <span>
          Single sheet detected — automatically paired: <strong>{autoSheet1.name}</strong> ? <strong>{autoSheet2.name}</strong>
        </span>
      </div>
    );
  }

  return (
    <div className="sheet-pairing-step">
      <div className="pairing-grid">
        {file1Sheets.map((sheet1) => {
          const pairedWith = getPairedSheet2(sheet1.id);
          return (
            <div key={sheet1.id} className={`pairing-sheet-row ${pairedWith ? "is-paired" : ""}`}>
              <div className="pairing-sheet-label">
                {pairedWith && <CheckCircle2 size={14} />}
                <span>{sheet1.name}</span>
                <small>{file1Name}</small>
              </div>
              <ArrowRight size={16} className="pairing-arrow" />
              <div className="pairing-sheet-select">
                <select
                  value={pairedWith?.id ?? ""}
                  onChange={(e) => {
                    if (e.target.value === "") handleRemovePairing(sheet1.id);
                    else handlePair(sheet1, e.target.value);
                  }}
                >
                  <option value="">— Select sheet from {file2Name} —</option>
                  {file2Sheets.map((sheet2) => (
                    <option
                      key={sheet2.id}
                      value={sheet2.id}
                      disabled={pairedSheet2Ids.has(sheet2.id) && pairedWith?.id !== sheet2.id}
                    >
                      {sheet2.name}{pairedSheet2Ids.has(sheet2.id) && pairedWith?.id !== sheet2.id ? " (paired)" : ""}
                    </option>
                  ))}
                </select>
                {pairedWith && (
                  <button type="button" className="text-button" onClick={() => handleRemovePairing(sheet1.id)}>
                    Remove
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="pairing-summary">
        {pairings.length === 0 ? (
          <p className="muted">No pairings yet. Map each sheet from {file1Name} to a sheet in {file2Name}.</p>
        ) : (
          <div className="pairing-count-badge">
            <Link2 size={14} />
            <span>{pairings.length} pairing{pairings.length !== 1 ? "s" : ""} ready</span>
          </div>
        )}
      </div>
    </div>
  );
}
