import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {FileTypeIcon} from "../components/FileTypeIcon";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {useRepository} from "../components/RepositoryContext";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {decodeBase64Content} from "../utils/content";

type Content = components["schemas"]["ContentResponse"];
type Summary = components["schemas"]["RepositoryHomeSummaryResponse"];

interface RepositoryFilesData {
  contents: Content[];
  readme: Content | null;
}

export function RepositoryPage() {
  const {owner = "", repo = ""} = useParams();
  const repository = useRepository();
  const ref = repository.default_branch;
  const files = useApiData<RepositoryFilesData | null>(
    `repo-files:${owner}/${repo}:${ref}`,
    async () => {
      if (!ref) return null;
      const [contentsResult, readmeResult] = await Promise.all([
        api.GET("/api/v3/repos/{owner}/{repo}/contents/{path}", {
          params: {path: {owner, repo, path: ""}, query: {ref}},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/readme", {
          params: {path: {owner, repo}, query: {ref}},
        }),
      ]);

      const contents = requireApiData(
        contentsResult.data,
        contentsResult.response,
        "Could not load repository files.",
      );
      if (!Array.isArray(contents)) {
        throw new Error("Repository root is not a directory.");
      }
      contents.sort((left, right) => {
        if (left.type !== right.type) return left.type === "dir" ? -1 : 1;
        return left.name.localeCompare(right.name);
      });

      return {
        contents,
        readme:
          readmeResult.response.status === 404
            ? null
            : requireApiData(
                readmeResult.data,
                readmeResult.response,
                "Could not load README.",
              ),
      };
    },
  );
  const summary = useApiData<Summary | null>(
    `repo-summary:${owner}/${repo}:${files.data ? "ready" : "deferred"}`,
    async () => {
      if (!files.data) return null;
      const {data, response} = await api.GET(
        "/api/_ui/repos/{owner}/{repo}/summary",
        {params: {path: {owner, repo}}},
      );
      return requireApiData(
        data,
        response,
        "Could not load repository counts.",
      );
    },
  );

  function count(value: number | undefined): number | string {
    if (!files.data || summary.loading) return "…";
    return value ?? "—";
  }

  return (
    <>
      <div className="repo-home-toolbar">
        <Link className="branch-selector" to={`/${owner}/${repo}/tree/${ref}`}>
          <Octicon name="branch" /> {ref}
        </Link>
        <Link className="button" to={`/${owner}/${repo}/new/${ref}/`}>
          <Octicon name="plus" /> Add file
        </Link>
      </div>
      <nav className="repo-activity" aria-label="Repository activity">
        <Link to={`/${owner}/${repo}/commits/${ref}`}>
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
      <Loadable loading={files.loading || !ref} error={files.error}>
        {files.data ? (
          <>
            <section
              className="list-box repo-home-files"
              aria-label="Repository files"
            >
              <h2 className="list-box-header">Files</h2>
              {files.data.contents.map((item) => (
                <div className="list-row file-row" key={item.path}>
                  <FileTypeIcon type={item.type} />
                  <Link
                    to={`/${owner}/${repo}/${item.type === "dir" ? "tree" : "blob"}/${ref}/${item.path}`}
                  >
                    {item.name}
                  </Link>
                </div>
              ))}
            </section>
            {files.data.readme ? (
              <section className="file-view readme-view">
                <h2>
                  <Octicon name="book" /> README
                </h2>
                <pre>{decodeBase64Content(files.data.readme.content)}</pre>
              </section>
            ) : null}
          </>
        ) : null}
      </Loadable>
    </>
  );
}
