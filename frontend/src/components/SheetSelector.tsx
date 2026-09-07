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
      </div>
      <div className="sheet-list">
        {sheets.map(sheet => (
          <label key={sheet.id} className={`sheet-item ${selectedSheets[0] === sheet.id ? 'is-selected' : ''}`}>
            <input 
              type="radio" 
              name={`sheet-selector-${fileName}`}
              checked={selectedSheets[0] === sheet.id} 
              onChange={() => onChange([sheet.id])} 
            />
            <span>{sheet.name}</span>
            {selectedSheets[0] === sheet.id && <CheckCircle2 size={16} className="text-success" />}
          </label>
        ))}
      </div>
    </div>
  );
}
