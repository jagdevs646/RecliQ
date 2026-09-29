import type { Job } from "../types";

export function jobFilesLabel(job: Job): string {
  if (!job.input_file_1_name || !job.input_file_2_name) return "—";
  const extraPairs = (job.file_pair_count ?? 1) - 1;
  const more = extraPairs > 0 ? ` +${extraPairs} more pair${extraPairs > 1 ? "s" : ""}` : "";
  return `${job.input_file_1_name} vs ${job.input_file_2_name}${more}`;
}

/** The files of a job as a link-style button, so a row can be opened with the
 * keyboard and visibly reads as something to open (the row stays clickable). */
export function OpenJobButton({ job, onOpen }: { job: Job; onOpen: (job: Job) => void }) {
  return (
    <button
      type="button"
      className="row-link"
      title="Open results"
      onClick={(event) => {
        event.stopPropagation();
        onOpen(job);
      }}
    >
      {jobFilesLabel(job)}
    </button>
  );
}
