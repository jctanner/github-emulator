import {Link} from "react-router-dom";

import type {RepositorySummary} from "../hooks/useRepositorySummary";
import {Octicon} from "./Octicon";

/** Branch and tag counts, shown beside the branch selector. */
export function RepositoryActivity({
  owner,
  repo,
  summary,
  ready = true,
}: {
  owner: string;
  repo: string;
  summary: {data: RepositorySummary | null; loading: boolean};
  ready?: boolean;
}) {
  const count = (value: number | undefined): number | string => {
    if (!ready || summary.loading) return "…";
    return value ?? "—";
  };

  return (
    <nav className="repo-activity" aria-label="Repository activity">
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
