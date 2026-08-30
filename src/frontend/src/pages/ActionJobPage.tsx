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
    async function refresh() {
      const response = await fetch(
        `/api/v3/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions/jobs/${id}/logs`,
        {credentials: "same-origin"},
      );
      if (active && response.ok) setLogs(await response.text());
    }
    void refresh();
    const timer = globalThis.setInterval(() => void refresh(), 1500);
    return () => {
      active = false;
      globalThis.clearInterval(timer);
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
