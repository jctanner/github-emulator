import {Link} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {relativeTime} from "../utils/time";
import {FileTypeIcon} from "./FileTypeIcon";
import {Loadable} from "./Loadable";
import {Octicon} from "./Octicon";

type Tree = components["schemas"]["RepositoryTreeResponse"];

/**
 * A directory listing with the latest commit for the directory in its header
 * and each entry's last commit message and age in its row.
 */
export function RepositoryTreeList({
  owner,
  repo,
  ref,
  path = "",
  commitCount,
}: {
  owner: string;
  repo: string;
  ref: string;
  path?: string;
  commitCount?: number;
}) {
  const tree = useApiData<Tree | null>(
    `repo-tree:${owner}/${repo}:${ref}:${path}`,
    async () => {
      if (!ref) return null;
      const {data, response} = await api.GET(
        "/api/_ui/repos/{owner}/{repo}/tree",
        {params: {path: {owner, repo}, query: {ref, path}}},
      );
      return requireApiData(data, response, "Could not load repository files.");
    },
  );
  const latest = tree.data?.latest_commit;
  const parentPath = path.split("/").slice(0, -1).join("/");
  const treeUrl = (target: string) =>
    `/${owner}/${repo}/tree/${encodeURIComponent(ref)}${target ? `/${target}` : ""}`;

  return (
    <Loadable loading={tree.loading || !ref} error={tree.error}>
      {tree.data ? (
        <section
          className="list-box repo-home-files"
          aria-label="Repository files"
        >
          <div className="list-box-header tree-latest-commit">
            {latest ? (
              <strong className="tree-commit-author">
                {latest.author_name}
              </strong>
            ) : null}
            <span className="tree-commit-message" title={latest?.message}>
              {latest?.message ?? "No commits yet"}
            </span>
            {latest ? (
              <span className="tree-commit-meta">
                <Link
                  className="tree-commit-sha"
                  to={`/${owner}/${repo}/commit/${latest.sha}`}
                >
                  {latest.short_sha}
                </Link>
                {" · "}
                {relativeTime(latest.date)}
              </span>
            ) : null}
            {path ? (
              <Link
                className="tree-commit-count"
                to={`/${owner}/${repo}/commits/${encodeURIComponent(ref)}?path=${encodeURIComponent(path)}`}
              >
                <Octicon name="history" />
                History
              </Link>
            ) : commitCount !== undefined ? (
              <Link
                className="tree-commit-count"
                to={`/${owner}/${repo}/commits/${encodeURIComponent(ref)}`}
              >
                <Octicon name="history" />
                <strong>{commitCount}</strong> Commits
              </Link>
            ) : null}
          </div>
          {path ? (
            <>
              <div className="list-row file-row file-row-header" role="row">
                <span className="file-type-icon-spacer" />
                <span className="file-name">Name</span>
                <span className="file-commit-message">Last commit message</span>
                <span className="file-commit-age">Last commit date</span>
              </div>
              <div className="list-row file-row">
                <FileTypeIcon type="dir" />
                <Link className="file-name" to={treeUrl(parentPath)}>
                  ..
                </Link>
              </div>
            </>
          ) : null}
          {tree.data.entries.map((item) => (
            <div className="list-row file-row" key={item.path}>
              <FileTypeIcon type={item.type} />
              <Link
                className="file-name"
                to={`/${owner}/${repo}/${item.type === "dir" ? "tree" : "blob"}/${encodeURIComponent(ref)}/${item.path}`}
              >
                {item.name}
              </Link>
              <span
                className="file-commit-message"
                title={item.last_commit?.message}
              >
                {item.last_commit?.message}
              </span>
              <span className="file-commit-age">
                {item.last_commit ? relativeTime(item.last_commit.date) : ""}
              </span>
            </div>
          ))}
        </section>
      ) : null}
    </Loadable>
  );
}
