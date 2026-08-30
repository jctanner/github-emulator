import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {RepositoryHeader} from "../components/RepositoryHeader";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Workflows = components["schemas"]["WorkflowListResponse"];
type Runs = components["schemas"]["WorkflowRunListResponse"];

export function ActionsPage() {
  const {owner = "", repo = ""} = useParams();
  const page = useApiData<{workflows: Workflows; runs: Runs}>(
    `actions:${owner}/${repo}`,
    async () => {
      const [workflowResult, runResult] = await Promise.all([
        api.GET("/api/v3/repos/{owner}/{repo}/actions/workflows", {
          params: {path: {owner, repo}},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/actions/runs", {
          params: {path: {owner, repo}},
        }),
      ]);
      return {
        workflows: requireApiData(
          workflowResult.data,
          workflowResult.response,
          "Could not load workflows.",
        ),
        runs: requireApiData(
          runResult.data,
          runResult.response,
          "Could not load workflow runs.",
        ),
      };
    },
  );
  return (
    <>
      <RepositoryHeader owner={owner} repo={repo} />
      <div className="actions-layout">
        <aside>
          <h2>
            <Octicon name="history" /> Workflows
          </h2>
          {page.data?.workflows.workflows.map((workflow) => (
            <div key={workflow.id}>{workflow.name}</div>
          ))}
        </aside>
        <section>
          <div className="page-heading">
            <h1>Actions</h1>
            <Link to={`/${owner}/${repo}/actions/runners`}>Runners</Link>
          </div>
          <Loadable loading={page.loading} error={page.error}>
            <div className="list-box">
              {page.data?.runs.workflow_runs.map((run) => (
                <div className="list-row run-row" key={run.id}>
                  <span
                    className={`status-dot status-${run.conclusion ?? run.status}`}
                    aria-hidden="true"
                  />
                  <div className="run-summary">
                    <h2>
                      <Link to={`/${owner}/${repo}/actions/runs/${run.id}`}>
                        {run.name || "Workflow run"} #{run.run_number}
                      </Link>
                    </h2>
                    <p className="muted">
                      {run.actor?.login ?? "github-actions"} triggered via{" "}
                      {run.event} on {run.head_branch} ·{" "}
                      {new Date(run.created_at).toLocaleString()}
                    </p>
                  </div>
                  <span
                    className={`status-pill status-${run.conclusion ?? run.status}`}
                  >
                    {run.conclusion ?? run.status}
                  </span>
                </div>
              ))}
            </div>
          </Loadable>
        </section>
      </div>
    </>
  );
}
