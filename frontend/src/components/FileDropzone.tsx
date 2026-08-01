import { CheckCircle2, FileSpreadsheet, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";

interface Props {
  label: string;
  fileName?: string;
  fileSize?: number;
  columnCount?: number;
  uploading?: boolean;
  progress?: number;
  onFile: (file: File) => void;
}

function formatBytes(size?: number) {
  if (!size) return "Excel workbook";
  return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function FileDropzone({ label, fileName, fileSize, columnCount, uploading = false, progress = 0, onFile }: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);

  function handleFiles(files: FileList | null) {
    const file = files?.item(0);
    if (file) onFile(file);
  }

  return (
    <article className={`upload-card ${dragging ? "is-dragging" : ""} ${fileName ? "is-ready" : ""}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); handleFiles(event.dataTransfer.files); }}>
      <div className="upload-card-header"><span>{label}</span>{fileName && <span className="uploaded-state"><CheckCircle2 size={15} />Uploaded</span>}</div>
      {fileName ? <div className="file-ready"><FileSpreadsheet size={28} /><div><strong>{fileName}</strong><p>{formatBytes(fileSize)} {columnCount ? `· ${columnCount} columns detected` : ""}</p></div></div> : <div className="upload-empty"><UploadCloud size={30} /><strong>Drop an Excel file here</strong><span>or choose it from your computer</span></div>}
      {uploading && <div className="upload-progress"><div><span>Uploading</span><strong>{progress}%</strong></div><span><i style={{ width: `${progress}%` }} /></span></div>}
      <button type="button" className="secondary upload-button" onClick={() => inputRef.current?.click()} disabled={uploading}><UploadCloud size={16} />{fileName ? "Replace file" : "Choose file"}</button>
      <input ref={inputRef} className="visually-hidden" type="file" accept=".xlsx,.xls" onChange={(event) => handleFiles(event.target.files)} />
    </article>
  );
}
