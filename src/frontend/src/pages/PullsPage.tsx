import {Link, useParams, useSearchParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Pull = components["schemas"]["PRResponse"];

export function PullsPage() {
  const {owner = "", repo = ""} = useParams();
  const [search] = useSearchParams();
  const state = search.get("state") ?? "open";
  const page = useApiData<{pulls: Pull[]}>(
    `pulls:${owner}/${repo}:${state}`,
    async () => {
      const pullsResult = await api.GET("/api/v3/repos/{owner}/{repo}/pulls", {
        params: {path: {owner, repo}, query: {state}},
      });
      return {
        pulls: requireApiData(
          pullsResult.data,
          pullsResult.response,
          "Could not load pull requests.",
        ),
      };
    },
  );

  return (
    <Loadable loading={page.loading} error={page.error}>
      {page.data ? (
        <>
          <div className="page-heading work-list-heading">
            <nav className="state-filters" aria-label="Pull request state">
              <Link
                className={state === "open" ? "selected" : undefined}
                to="?state=open"
              >
                <Octicon name="pull-request" />{" "}
                {state === "open" ? page.data.pulls.length : 0} Open
              </Link>
              <Link
                className={state === "closed" ? "selected" : undefined}
                to="?state=closed"
              >
                {state === "closed" ? page.data.pulls.length : 0} Closed
              </Link>
            </nav>
            <Link className="button" to={`/${owner}/${repo}/pulls/new`}>
              <Octicon name="plus" /> New pull request
            </Link>
          </div>
          <div className="list-box">
            {page.data.pulls.map((pull) => (
              <article className="list-row work-item-row" key={pull.id}>
                <Octicon name="pull-request" />
                <div>
                  <h2>
                    <Link to={`/${owner}/${repo}/pulls/${pull.number}`}>
                      {pull.title}
                    </Link>
                  </h2>
                  <p className="muted">
                    #{pull.number} opened by {pull.user.login} ·{" "}
                    {pull.head.label} → {pull.base.label}
                  </p>
                </div>
              </article>
            ))}
            {page.data.pulls.length === 0 ? (
              <div className="empty-state">
                <Octicon name="pull-request" size={24} />
                <h2>No pull requests found</h2>
                <p>There aren't any {state} pull requests.</p>
              </div>
            ) : null}
          </div>
        </>
      ) : null}
    </Loadable>
  );
}
