import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {PullRequestHeader} from "../components/PullRequestHeader";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Pull = components["schemas"]["PRResponse"];
type Commit = components["schemas"]["PullCommitResponse"];

export function PullCommitsPage() {
  const {owner = "", repo = "", number = "0"} = useParams();
  const pullNumber = Number(number);
  const result = useApiData<{pull: Pull; commits: Commit[]}>(
    `pull-commits:${owner}/${repo}:${pullNumber}`,
    async () => {
      const path = {owner, repo, pull_number: pullNumber};
      const [pullResult, commitsResult] = await Promise.all([
        api.GET("/api/v3/repos/{owner}/{repo}/pulls/{pull_number}", {
          params: {path},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/pulls/{pull_number}/commits", {
          params: {path},
        }),
      ]);
      return {
        pull: requireApiData(
          pullResult.data,
          pullResult.response,
          "Could not load pull request.",
        ),
        commits: requireApiData(
          commitsResult.data,
          commitsResult.response,
          "Could not load pull-request commits.",
        ),
      };
    },
  );

  return (
    <Loadable loading={result.loading} error={result.error}>
      {result.data ? (
        <>
          <PullRequestHeader
            owner={owner}
            repo={repo}
            pull={result.data.pull}
          />
          <div className="list-box pr-commit-list">
            {result.data.commits.map((commit) => (
              <div className="list-row commit-row" key={commit.sha}>
                <div>
                  <h2>
                    <Link to={`/${owner}/${repo}/commit/${commit.sha}`}>
                      {commit.commit.message.split("\n", 1)[0]}
                    </Link>
                  </h2>
                  <span className="muted">
                    {commit.commit.author.name ?? "unknown"} committed{" "}
                    {commit.commit.author.date
                      ? new Date(commit.commit.author.date).toLocaleString()
                      : "at an unknown time"}
                  </span>
                </div>
                <code>{commit.sha.slice(0, 7)}</code>
              </div>
            ))}
          </div>
        </>
      ) : null}
    </Loadable>
  );
}
