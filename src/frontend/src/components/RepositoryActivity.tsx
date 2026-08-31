import {Link} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {Octicon} from "./Octicon";

type Summary = components["schemas"]["RepositoryHomeSummaryResponse"];

export function RepositoryActivity({
  owner,
  repo,
  ref,
  ready = true,
}: {
  owner: string;
  repo: string;
  ref: string;
  ready?: boolean;
}) {
  const summary = useApiData<Summary | null>(
    `repo-summary:${owner}/${repo}:${ref}:${ready ? "ready" : "deferred"}`,
    async () => {
      if (!ready || !ref) return null;
      const {data, response} = await api.GET(
        "/api/_ui/repos/{owner}/{repo}/summary",
        {params: {path: {owner, repo}, query: {ref}}},
      );
      return requireApiData(data, response, "Could not load repository counts.");
    },
  );

  const count = (value: number | undefined): number | string => {
    if (!ready || summary.loading) return "…";
    return value ?? "—";
  };

  return (
    <nav className="repo-activity" aria-label="Repository activity">
      <Link to={`/${owner}/${repo}/commits/${encodeURIComponent(ref)}`}>
        <Octicon name="history" />
        <strong>{count(summary.data?.commit_count)}</strong> commits
      </Link>
      <Link to={`/${owner}/${repo}/branches`}>
        <Octicon name="branch" />
        <strong>{count(summary.data?.branch_count)}</strong> branches
      </Link>
      <Link to={`/${owner}/${repo}/tags`}>
        <Octicon name="tag" />
        <strong>{count(summary.data?.tag_count)}</strong> tags
      </Link>
    </nav>
  );
}
