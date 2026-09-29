import type {components} from "../api/schema";
import {StatusPill} from "./StatusPill";

export type AdminJob = components["schemas"]["AdminJobResponse"];

/**
 * One job across repositories and runs: who is running it, what it is
 * waiting on, and how it ended. The current step's message is where a
 * settled job says why (a lost runner, a shutdown signal), which the run
 * list never shows.
 */
export function JobRow({
  job,
  onRequeue,
  onFail,
}: {
  job: AdminJob;
  onRequeue?: (id: number) => void;
  onFail?: (id: number) => void;
}) {
  const step = job.current_step;
  return (
    <div className="list-row" data-testid={`job-${job.id}`}>
      <div>
        <strong>
          <a href={job.url ?? "#"}>{job.name}</a>
        </strong>{" "}
        <span className="muted">
          {job.repository} #{job.run_number} · {job.workflow} · {job.event}
        </span>
      </div>
      <div>
        <StatusPill status={job.status} conclusion={job.conclusion} />{" "}
        <span className="muted">
          {job.runner_name ? `on ${job.runner_name}` : "no runner"}
          {job.labels.length > 0 ? ` · needs [${job.labels.join(", ")}]` : ""}
          {job.started_at
            ? ` · started ${job.started_at}`
            : ` · created ${job.created_at ?? "?"}`}
          {job.completed_at ? ` · finished ${job.completed_at}` : ""}
        </span>
      </div>
      {step ? (
        <div className="muted">
          step {step.number ?? "?"} {step.name ?? ""}:{" "}
          {step.conclusion ?? step.status ?? "?"}
          {step.message ? <em> — {step.message}</em> : null}
        </div>
      ) : null}
      {/* The two settlements the emulator performs on its own for a lost
          runner, offered to the operator for a job they can see is stranded. */}
      <span className="button-row">
        {job.status !== "completed" && onRequeue ? (
          <button
            type="button"
            className="button secondary compact"
            onClick={() => onRequeue(job.id)}
          >
            Requeue
          </button>
        ) : null}
        {job.status === "in_progress" && onFail ? (
          <button
            type="button"
            className="button secondary compact"
            onClick={() => onFail(job.id)}
          >
            Fail as lost
          </button>
        ) : null}
      </span>
    </div>
  );
}
