import { useEffect, useState } from "react";
import { Shell } from "./components/Shell";
import { api } from "./services/api";
import type { Job, Page } from "./types";
import { DashboardPage } from "./pages/DashboardPage";
import { HistoryPage } from "./pages/HistoryPage";
import { ResultsPage } from "./pages/ResultsPage";
import { StatusPage } from "./pages/StatusPage";
import { UploadPage } from "./pages/UploadPage";
import { SavedReconciliationsPage } from "./pages/SavedReconciliationsPage";
import { AliasesPage } from "./pages/AliasesPage";
import { AuditLogPage } from "./pages/AuditLogPage";
import { ResolutionRulesPage } from "./pages/ResolutionRulesPage";
import { LearningPage } from "./pages/LearningPage";

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  // A finished run reopened in the wizard to change its rules and run again.
  const [rerunJob, setRerunJob] = useState<Job | null>(null);
  const [sessionReady, setSessionReady] = useState(false);

  useEffect(() => {
    void api.initializeSession().finally(() => setSessionReady(true));
  }, []);

  if (!sessionReady) {
    return (
      <div className="app-loading" role="status" aria-live="polite">
        <img src="/icon.ico" alt="" className="app-loading-logo" />
        <strong>RecliQ</strong>
        <span className="app-loading-bar" aria-hidden="true" />
        <small>Getting your workspace ready…</small>
      </div>
    );
  }

  function openJob(job: Job) {
    setActiveJob(job);
    setPage(["completed", "completed_with_errors"].includes(job.status) ? "results" : "status");
  }

  function navigate(next: Page) {
    setRerunJob(null);
    setPage(next);
  }

  return (
    <Shell activePage={page} onNavigate={navigate}>
      {page === "dashboard" && <DashboardPage onNavigateUpload={() => navigate("upload")} onOpenJob={openJob} />}
      {page === "upload" && (
        <UploadPage
          key={rerunJob?.id ?? "new"}
          rerunFrom={rerunJob}
          onStartFresh={() => setRerunJob(null)}
          onJobCreated={(job) => {
            setRerunJob(null);
            setActiveJob(job);
            setPage("status");
          }}
        />
      )}
      {page === "status" && <StatusPage job={activeJob} onJobUpdate={setActiveJob} onViewResults={() => setPage("results")} />}
      {page === "results" && <ResultsPage job={activeJob} onNewReconciliation={() => navigate("upload")} onChangeRules={() => { setRerunJob(activeJob); setPage("upload"); }} />}
      {page === "history" && <HistoryPage onOpenJob={openJob} />}
      {page === "saved" && <SavedReconciliationsPage onJobCreated={(job) => { setActiveJob(job); setPage("status"); }} onNewReconciliation={() => setPage("upload")} />}
      {page === "aliases" && <AliasesPage />}
      {page === "audit" && <AuditLogPage />}
      {page === "rules" && <ResolutionRulesPage />}
      {page === "learning" && <LearningPage onNavigate={setPage} />}
    </Shell>
  );
}
