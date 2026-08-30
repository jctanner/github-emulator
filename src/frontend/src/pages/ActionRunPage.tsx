import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {RepositoryHeader} from "../components/RepositoryHeader";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Run = components["schemas"]["WorkflowRunResponse"];
type Jobs = components["schemas"]["WorkflowJobListResponse"];

export function ActionRunPage() {
  const {owner = "", repo = "", runId = "0"} = useParams();
  const id = Number(runId);
  const page = useApiData<{run: Run; jobs: Jobs}>(
    `run:${owner}/${repo}:${id}`,
    async () => {
      const [runResult, jobsResult] = await Promise.all([
        api.GET("/api/v3/repos/{owner}/{repo}/actions/runs/{run_id}", {
          params: {path: {owner, repo, run_id: id}},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/actions/runs/{run_id}/jobs", {
          params: {path: {owner, repo, run_id: id}},
        }),
      ]);
      return {
        run: requireApiData(
          runResult.data,
          runResult.response,
          "Could not load run.",
        ),
        jobs: requireApiData(
          jobsResult.data,
          jobsResult.response,
          "Could not load jobs.",
        ),
      };
    },
  );
  async function rerun() {
    await api.POST("/api/v3/repos/{owner}/{repo}/actions/runs/{run_id}/rerun", {
      params: {path: {owner, repo, run_id: id}},
    });
    page.reload();
  }
  async function cancel() {
    await api.POST(
      "/api/v3/repos/{owner}/{repo}/actions/runs/{run_id}/cancel",
      {params: {path: {owner, repo, run_id: id}}},
    );
    page.reload();
  }
  return (
    <>
      <RepositoryHeader owner={owner} repo={repo} />
      <Loadable loading={page.loading} error={page.error}>
        {page.data ? (
          <>
            <div className="page-heading">
              <div>
                <h1>
                  {page.data.run.name} #{page.data.run.run_number}
                </h1>
                <p className="muted">
                  <span
                    className={`status-pill status-${page.data.run.conclusion ?? page.data.run.status}`}
                  >
                    {page.data.run.conclusion ?? page.data.run.status}
                  </span>{" "}
                  triggered via {page.data.run.event}
                </p>
              </div>
              <div className="button-row">
                {page.data.run.status !== "completed" ? (
                  <button type="button" onClick={() => void cancel()}>
                    Cancel
                  </button>
                ) : null}
                <button type="button" onClick={() => void rerun()}>
                  Re-run jobs
                </button>
              </div>
            </div>
            <dl className="run-metadata">
              <div>
                <dt>Branch</dt>
                <dd>
                  <Octicon name="branch" /> {page.data.run.head_branch}
                </dd>
              </div>
              <div>
                <dt>Commit</dt>
                <dd>
                  <code>{page.data.run.head_sha.slice(0, 7)}</code>
                </dd>
              </div>
              <div>
                <dt>Actor</dt>
                <dd>{page.data.run.actor?.login ?? "github-actions"}</dd>
              </div>
              <div>
                <dt>Started</dt>
                <dd>{new Date(page.data.run.created_at).toLocaleString()}</dd>
              </div>
            </dl>
            <div className="list-box job-card">
              <div className="list-box-header">
                <strong>
                  <Octicon name="history" /> Jobs
                </strong>
              </div>
              {page.data.jobs.jobs.map((job) => (
                <div className="list-row job-row" key={job.id}>
                  <div>
                    <h2>
                      <Link to={`/${owner}/${repo}/actions/jobs/${job.id}`}>
                        {job.name}
                      </Link>
                    </h2>
                    <p className="muted">
                      {job.runner_name ?? "Waiting for runner"}
                    </p>
                  </div>
                  <span
                    className={`status-pill status-${job.conclusion ?? job.status}`}
                  >
                    {job.conclusion ?? job.status}
                  </span>
                </div>
              ))}
            </div>
          </>
        ) : null}
      </Loadable>
    </>
  );
}
