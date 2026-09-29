import type {components} from "../api/schema";
import {StatusPill} from "./StatusPill";

export type AdminRun = components["schemas"]["AdminActiveRunResponse"];

/** One workflow run across repositories, with the jobs that explain it. */
export function RunRow({
  run,
  onCancel,
}: {
  run: AdminRun;
  onCancel?: (id: number) => void;
}) {
  const finished = run.status === "completed";
  return (
    <div className="list-row" data-testid={`run-${run.id}`}>
      <div>
        <strong>
          <a href={run.url ?? "#"}>
            {run.repository} #{run.run_number}
          </a>
        </strong>{" "}
        <span className="muted">
          {run.workflow} · {run.event} · {run.head_branch}
          {run.run_attempt > 1 ? ` · attempt ${run.run_attempt}` : ""}
        </span>
      </div>
      <div>
        <StatusPill status={run.status} conclusion={run.conclusion} />{" "}
        <span className="muted">
          {run.jobs_completed}/{run.jobs_total} jobs done · updated{" "}
          {run.updated_at ?? "never"}
        </span>
      </div>
      {/* The unfinished jobs are what explain a stuck run: which one is
          waiting, on which runner, and on which labels. A job queued on a
          label no runner registers is the common case, and it is invisible
          from the run's status alone. */}
      {run.active_jobs.length > 0 ? (
        <ul className="muted">
          {run.active_jobs.map((job) => (
            <li key={job.id}>
              {job.name} — {job.status}
              {job.runner_name ? ` on ${job.runner_name}` : ""}
              {job.labels.length > 0
                ? ` · needs [${job.labels.join(", ")}]`
                : ""}
            </li>
          ))}
        </ul>
      ) : finished ? null : (
        <span className="muted">no unfinished jobs recorded</span>
      )}
      {/* Cancelling keeps the run and its jobs and stops them being in
          flight, which is what GitHub does. Deleting would lose the record
          of what was attempted. */}
      {!finished && onCancel ? (
        <button type="button" onClick={() => onCancel(run.id)}>
          Cancel
        </button>
      ) : null}
    </div>
  );
}
