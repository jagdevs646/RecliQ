import { CheckCircle2 } from "lucide-react";
import type { SheetMetadata } from "../types";

interface Props {
  sheets: SheetMetadata[];
  selectedSheets: string[];
  onChange: (selected: string[]) => void;
  fileName: string;
}

export function SheetSelector({ sheets, selectedSheets, onChange, fileName }: Props) {
  if (sheets.length <= 1) {
    return null;
  }

  const toggleSheet = (sheetId: string) => {
    if (selectedSheets.includes(sheetId)) {
      if (selectedSheets.length > 1) {
        onChange(selectedSheets.filter(id => id !== sheetId));
      }
    } else {
      onChange([...selectedSheets, sheetId]);
    }
  };

  const selectAll = () => {
    onChange(sheets.map(s => s.id));
  };

  return (
    <div className="sheet-selector">
      <div className="sheet-selector-header">
        <h4>Select sheets to include from {fileName}</h4>
        <button type="button" className="text-button" onClick={selectAll}>Select all</button>
      </div>
      <div className="sheet-list">
        {sheets.map(sheet => (
          <label key={sheet.id} className={`sheet-item ${selectedSheets.includes(sheet.id) ? 'is-selected' : ''}`}>
            <input 
              type="checkbox" 
              checked={selectedSheets.includes(sheet.id)} 
              onChange={() => toggleSheet(sheet.id)} 
            />
            <span>{sheet.name}</span>
            {selectedSheets.includes(sheet.id) && <CheckCircle2 size={16} className="text-success" />}
          </label>
        ))}
      </div>
    </div>
  );
}
