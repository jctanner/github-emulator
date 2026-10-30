import {useEffect, useState} from "react";
import {useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Job = components["schemas"]["WorkflowJobResponse"];

export function ActionJobPage() {
  const {owner = "", repo = "", jobId = "0"} = useParams();
  const id = Number(jobId);
  const job = useApiData<Job>(`job:${owner}/${repo}:${id}`, async () => {
    const {data, response} = await api.GET(
      "/api/v3/repos/{owner}/{repo}/actions/jobs/{job_id}",
      {params: {path: {owner, repo, job_id: id}}},
    );
    return requireApiData(data, response, "Could not load job.");
  });
  const [logs, setLogs] = useState("Waiting for logs…");
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const base = `/api/v3/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions/jobs/${id}`;
    // Poll only while the job runs, and schedule the next poll after this one
    // finishes. A fixed interval kept re-fetching a finished job's whole log
    // every 1.5 s, and once a fetch took longer than that the requests piled
    // up until they starved the server.
    async function refresh() {
      let finished = false;
      try {
        const [logResponse, jobResponse] = await Promise.all([
          fetch(`${base}/logs`, {credentials: "same-origin"}),
          fetch(base, {credentials: "same-origin"}),
        ]);
        if (active && logResponse.ok) setLogs(await logResponse.text());
        if (jobResponse.ok) {
          const current = (await jobResponse.json()) as Job;
          finished = current.status === "completed";
        }
      } catch {
        // Transient network error: try again on the next poll.
      }
      if (active && !finished) timer = globalThis.setTimeout(() => void refresh(), 1500);
    }
    void refresh();
    return () => {
      active = false;
      if (timer !== undefined) globalThis.clearTimeout(timer);
    };
  }, [id, owner, repo]);
  return (
    <>
      <Loadable loading={job.loading} error={job.error}>
        {job.data ? (
          <>
            <div className="page-heading">
              <div>
                <h1>{job.data.name}</h1>
                <p className="muted">
                  {job.data.status}
                  {job.data.conclusion ? ` / ${job.data.conclusion}` : ""}
                </p>
              </div>
            </div>
            <pre className="job-log" aria-label="Job log">
              {logs}
            </pre>
          </>
        ) : null}
      </Loadable>
    </>
  );
}
