import { AlertTriangle, CheckCircle2, Sparkles } from "lucide-react";
import type { AnalysisResponse } from "../types";

interface Props {
  analysis: AnalysisResponse;
  file1Name: string;
  file2Name: string;
}

export function SmartMappingReview({ analysis, file1Name, file2Name }: Props) {
  const { recommended_keys_1, recommended_keys_2, key_confidence, is_composite_key, recommended_mappings } = analysis;

  const getConfidenceBadge = (conf: string) => {
    switch (conf) {
      case "High": return <span className="badge success"><CheckCircle2 size={12} /> High</span>;
      case "Medium": return <span className="badge warning"><AlertTriangle size={12} /> Medium</span>;
      case "Low": return <span className="badge danger"><AlertTriangle size={12} /> Low</span>;
      default: return <span className="badge">Unmapped</span>;
    }
  };

  return (
    <div className="smart-mapping-review">
      <div className="info-callout">
        <Sparkles size={24} className="text-primary" />
        <div>
          <h4>Smart Reconciliation Analysis</h4>
          <p>RecliQ has analyzed your datasets and recommended the following mapping strategy.</p>
        </div>
      </div>
      
      <div className="key-recommendation card">
        <h4>Recommended Primary Key</h4>
        <div className="key-flex">
          <div className="key-box">
            <span>{file1Name}</span>
            <strong>{recommended_keys_1.join(" + ")}</strong>
          </div>
          <div className="key-link">
            <span>{key_confidence}% Confidence</span>
          </div>
          <div className="key-box">
            <span>{file2Name}</span>
            <strong>{recommended_keys_2.join(" + ")}</strong>
          </div>
        </div>
        {is_composite_key && (
          <p className="hint-text"><AlertTriangle size={14} /> A composite key was recommended because the primary identifier contains duplicates.</p>
        )}
      </div>

      <div className="mapping-recommendation card mt-4">
        <h4>Recommended Column Mappings</h4>
        <div className="gst-field-map">
          <div className="gst-field-map-header">
            <span>{file1Name}</span>
            <span>{file2Name}</span>
            <span>Confidence</span>
          </div>
          {recommended_mappings.map((mapping, idx) => (
            <div className="gst-field-map-row" key={idx}>
              <strong>{mapping.source}</strong>
              <span>{mapping.target || <em className="text-muted">No Match</em>}</span>
              <span>{getConfidenceBadge(mapping.confidence)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
