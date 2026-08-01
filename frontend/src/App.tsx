import { useEffect, useState } from "react";
import { Shell } from "./components/Shell";
import { api } from "./services/api";
import type { Job, Page } from "./types";
import { DashboardPage } from "./pages/DashboardPage";
import { HistoryPage } from "./pages/HistoryPage";
import { ResultsPage } from "./pages/ResultsPage";
import { StatusPage } from "./pages/StatusPage";
import { UploadPage } from "./pages/UploadPage";

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [sessionReady, setSessionReady] = useState(false);

  useEffect(() => {
    void api.initializeSession().finally(() => setSessionReady(true));
  }, []);

  if (!sessionReady) {
    return <div className="app-loading">Loading RecliQ...</div>;
  }

  function openJob(job: Job) {
    setActiveJob(job);
    setPage(job.status === "completed" ? "results" : "status");
  }

  return (
    <Shell activePage={page} onNavigate={setPage}>
      {page === "dashboard" && <DashboardPage onNavigateUpload={() => setPage("upload")} onOpenJob={openJob} />}
      {page === "upload" && (
        <UploadPage
          onJobCreated={(job) => {
            setActiveJob(job);
            setPage("status");
          }}
        />
      )}
      {page === "status" && <StatusPage job={activeJob} onJobUpdate={setActiveJob} onViewResults={() => setPage("results")} />}
      {page === "results" && <ResultsPage job={activeJob} onNewReconciliation={() => setPage("upload")} />}
      {page === "history" && <HistoryPage onOpenJob={openJob} />}
    </Shell>
  );
}
