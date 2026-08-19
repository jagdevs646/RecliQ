import { ArrowLeft, ArrowRight, Settings2 } from "lucide-react";
import { useState } from "react";
import type { ReportCustomConfig } from "../types";

interface Props {
  source1Name?: string;
  source2Name?: string;
  onGenerate: (config: ReportCustomConfig) => void;
  onCancel: () => void;
}

export function ReportCustomizer({ source1Name = "Source 1", source2Name = "Source 2", onGenerate, onCancel }: Props) {
  const [config, setConfig] = useState<ReportCustomConfig>({
    include_summary: true,
    include_exceptions: true,
    include_matched: false, // Default to false to save size
    include_missing_file_1: true,
    include_missing_file_2: true,
    include_field_differences: true,
    include_controls: true,
    date_format: "YYYY-MM-DD",
    number_format: "#,##0.00"
  });

  const handleChange = (key: keyof ReportCustomConfig, value: string | boolean) => {
    setConfig(prev => ({ ...prev, [key]: value }));
  };

  const handleGenerate = () => {
    onGenerate(config);
  };

  const selectedCount = Object.values({
    include_summary: config.include_summary,
    include_exceptions: config.include_exceptions,
    include_matched: config.include_matched,
    include_missing_file_1: config.include_missing_file_1,
    include_missing_file_2: config.include_missing_file_2,
    include_field_differences: config.include_field_differences,
    include_controls: config.include_controls,
  }).filter(Boolean).length;

  return (
    <section className="page customizer-page">
      <div className="page-title">
        <div>
          <span className="eyebrow">Report Customization</span>
          <h1>Customize Your Report</h1>
          <p>Choose what to include in your final reconciliation report.</p>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button type="button" className="secondary" onClick={onCancel}>
            <ArrowLeft size={18} /> Back to Results
          </button>
          <button type="button" className="primary" onClick={handleGenerate}>
            Generate Report <ArrowRight size={18} />
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '20px' }}>
        
        <div className="checklist-panel">
          <div className="checklist-title">
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Settings2 size={18} color="#087d72" />
              <strong>Report Contents</strong>
            </div>
            <span className="eyebrow">{selectedCount} Selected</span>
          </div>
          <div className="checklist-items" style={{ maxHeight: 'none', padding: '12px' }}>
            <label className="checkbox-row">
              <input type="checkbox" checked={config.include_summary} onChange={(e) => handleChange("include_summary", e.target.checked)} />
              <span>Summary (KPIs and overview)</span>
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={config.include_exceptions} onChange={(e) => handleChange("include_exceptions", e.target.checked)} />
              <span>Exceptions (All mismatched records)</span>
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={config.include_matched} onChange={(e) => handleChange("include_matched", e.target.checked)} />
              <span>Matched Records (Large datasets may take longer)</span>
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={config.include_missing_file_1} onChange={(e) => handleChange("include_missing_file_1", e.target.checked)} />
              <span>Missing in {source1Name}</span>
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={config.include_missing_file_2} onChange={(e) => handleChange("include_missing_file_2", e.target.checked)} />
              <span>Missing in {source2Name}</span>
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={config.include_field_differences} onChange={(e) => handleChange("include_field_differences", e.target.checked)} />
              <span>Field Differences (Detailed breakdown)</span>
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={config.include_controls} onChange={(e) => handleChange("include_controls", e.target.checked)} />
              <span>Control Checks (Data integrity checks)</span>
            </label>
          </div>
        </div>

        <div className="checklist-panel">
          <div className="checklist-title">
            <strong>Formatting</strong>
          </div>
          <div style={{ padding: '16px', display: 'grid', gap: '16px' }}>
            <label>
              <span>Number Format</span>
              <select value={config.number_format} onChange={(e) => handleChange("number_format", e.target.value)}>
                <option value="#,##0.00">1,234.56 (Decimal)</option>
                <option value="#,##0">1,235 (Integer)</option>
                <option value="$#,##0.00">$1,234.56 (Currency)</option>
              </select>
            </label>
            <label>
              <span>Date Format</span>
              <select value={config.date_format} onChange={(e) => handleChange("date_format", e.target.value)}>
                <option value="YYYY-MM-DD">YYYY-MM-DD</option>
                <option value="DD-MM-YYYY">DD-MM-YYYY</option>
                <option value="MM/DD/YYYY">MM/DD/YYYY</option>
              </select>
            </label>
          </div>
        </div>

      </div>
    </section>
  );
}
