import {Link, useParams, useSearchParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {LabelPill} from "../components/LabelPill";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Issue = components["schemas"]["IssueResponse"];
type Pull = components["schemas"]["PRResponse"];

export function IssuesPage() {
  const {owner = "", repo = ""} = useParams();
  const [search] = useSearchParams();
  const state = search.get("state") ?? "open";
  const page = useApiData<{issues: Issue[]}>(
    `issues:${owner}/${repo}:${state}`,
    async () => {
      const [issuesResult, pullsResult] = await Promise.all([
        api.GET("/api/v3/repos/{owner}/{repo}/issues", {
          params: {path: {owner, repo}, query: {state}},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/pulls", {
          params: {path: {owner, repo}, query: {state}},
        }),
      ]);
      const pulls = requireApiData(
        pullsResult.data,
        pullsResult.response,
        "Could not load pull requests.",
      );
      const pullNumbers = new Set(pulls.map((pull: Pull) => pull.number));
      return {
        issues: requireApiData(
          issuesResult.data,
          issuesResult.response,
          "Could not load issues.",
        ).filter((issue: Issue) => !pullNumbers.has(issue.number)),
      };
    },
  );

  return (
    <Loadable loading={page.loading} error={page.error}>
      {page.data ? (
        <>
          <div className="page-heading work-list-heading">
            <nav className="state-filters" aria-label="Issue state">
              <Link
                className={state === "open" ? "selected" : undefined}
                to="?state=open"
              >
                <Octicon name="issue" />{" "}
                {state === "open" ? page.data.issues.length : 0} Open
              </Link>
              <Link
                className={state === "closed" ? "selected" : undefined}
                to="?state=closed"
              >
                {state === "closed" ? page.data.issues.length : 0} Closed
              </Link>
            </nav>
            <div className="button-row">
              <Link className="button" to={`/${owner}/${repo}/issues/new`}>
                <Octicon name="plus" /> New issue
              </Link>
            </div>
          </div>
          <div className="list-box">
            {page.data.issues.map((issue) => (
              <article className="list-row work-item-row" key={issue.id}>
                <Octicon name="issue" />
                <div>
                  <h2>
                    <Link to={`/${owner}/${repo}/issues/${issue.number}`}>
                      {issue.title}
                    </Link>
                  </h2>
                  <div className="labels" aria-label="Issue labels">
                    {issue.labels.map((label) => (
                      <LabelPill key={label.id} label={label} />
                    ))}
                  </div>
                  <p className="muted">
                    #{issue.number} opened by {issue.user.login}
                  </p>
                </div>
              </article>
            ))}
            {page.data.issues.length === 0 ? (
              <div className="empty-state">
                <Octicon name="issue" size={24} />
                <h2>No issues found</h2>
                <p>There aren't any {state} issues.</p>
              </div>
            ) : null}
          </div>
        </>
      ) : null}
    </Loadable>
  );
}
