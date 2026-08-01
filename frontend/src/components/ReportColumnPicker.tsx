import { CheckSquare, GripVertical, Search, Square } from "lucide-react";
import { useMemo, useState } from "react";

interface Props {
  file1Columns: string[];
  file2Columns: string[];
  selectedFile1: string[];
  selectedFile2: string[];
  onChangeFile1: (columns: string[]) => void;
  onChangeFile2: (columns: string[]) => void;
  file1Name?: string;
  file2Name?: string;
}

type Source = "file1" | "file2";

export function ReportColumnPicker({
  file1Columns,
  file2Columns,
  selectedFile1,
  selectedFile2,
  onChangeFile1,
  onChangeFile2,
  file1Name,
  file2Name,
}: Props) {
  const [query, setQuery] = useState("");
  const [dragged, setDragged] = useState<string | null>(null);
  const matches = (value: string) => value.toLowerCase().includes(query.trim().toLowerCase());
  const selected = [...selectedFile1.map((column) => ({ column, source: "file1" as Source })), ...selectedFile2.map((column) => ({ column, source: "file2" as Source }))];
  const visibleFile1 = useMemo(() => file1Columns.filter(matches), [file1Columns, query]);
  const visibleFile2 = useMemo(() => file2Columns.filter(matches), [file2Columns, query]);

  function toggle(source: Source, column: string) {
    const values = source === "file1" ? selectedFile1 : selectedFile2;
    const next = values.includes(column) ? values.filter((item) => item !== column) : [...values, column];
    source === "file1" ? onChangeFile1(next) : onChangeFile2(next);
  }

  function setAll(source: Source, columns: string[]) {
    source === "file1" ? onChangeFile1(columns) : onChangeFile2(columns);
  }

  function moveSelected(target: string) {
    if (!dragged || dragged === target) return;
    const [source, column] = dragged.split("::") as [Source, string];
    const values = source === "file1" ? selectedFile1 : selectedFile2;
    const targetIndex = values.indexOf(target);
    const currentIndex = values.indexOf(column);
    if (targetIndex < 0 || currentIndex < 0) return;
    const next = [...values];
    next.splice(currentIndex, 1);
    next.splice(targetIndex, 0, column);
    source === "file1" ? onChangeFile1(next) : onChangeFile2(next);
  }

  return (
    <section className="report-picker">
      <div className="section-heading">
        <div><h2>Columns to include</h2><p>Select the context fields shown alongside discrepancies.</p></div>
        <label className="search-field"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search columns" /></label>
      </div>
      <div className="picker-grid">
        <ColumnChecklist label={file1Name || "Source file"} source="file1" columns={visibleFile1} selected={selectedFile1} onToggle={toggle} onSetAll={setAll} />
        <ColumnChecklist label={file2Name || "Destination file"} source="file2" columns={visibleFile2} selected={selectedFile2} onToggle={toggle} onSetAll={setAll} />
      </div>
      <div className="selected-columns" aria-label="Selected report columns">
        <div className="section-heading compact"><div><h3>Report order</h3><p>Drag fields within a file to reorder them.</p></div><strong>{selected.length} selected</strong></div>
        {selected.length === 0 ? <p className="muted">No extra columns selected. The matching key and mapped fields remain in the report.</p> : selected.map(({ column, source }) => (
          <div className="selected-column" key={`${source}-${column}`} draggable onDragStart={() => setDragged(`${source}::${column}`)} onDragEnd={() => setDragged(null)} onDragOver={(event) => event.preventDefault()} onDrop={() => moveSelected(column)}>
            <GripVertical size={16} /><span>{column}</span><small>{source === "file1" ? (file1Name || "Source") : (file2Name || "Destination")}</small>
          </div>
        ))}
      </div>
    </section>
  );
}

interface ChecklistProps {
  label: string;
  source: Source;
  columns: string[];
  selected: string[];
  onToggle: (source: Source, column: string) => void;
  onSetAll: (source: Source, columns: string[]) => void;
}

function ColumnChecklist({ label, source, columns, selected, onToggle, onSetAll }: ChecklistProps) {
  const allSelected = columns.length > 0 && columns.every((column) => selected.includes(column));
  return <div className="checklist-panel">
    <div className="checklist-title"><strong>{label}</strong><button type="button" className="text-command" onClick={() => onSetAll(source, allSelected ? [] : columns)}>{allSelected ? <Square size={15} /> : <CheckSquare size={15} />}{allSelected ? "Clear" : "Select all"}</button></div>
    <div className="checklist-items">{columns.map((column) => <label key={column} className="checkbox-row"><input type="checkbox" checked={selected.includes(column)} onChange={() => onToggle(source, column)} />{column}</label>)}</div>
  </div>;
}
