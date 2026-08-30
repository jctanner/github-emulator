import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {FileTypeIcon} from "../components/FileTypeIcon";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {RepositoryHeader} from "../components/RepositoryHeader";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {decodeBase64Content} from "../utils/content";

type Content = components["schemas"]["ContentResponse"];

export function CodeBrowserPage({blob = false}: {blob?: boolean}) {
  const {owner = "", repo = "", ref = "main", "*": path = ""} = useParams();
  const result = useApiData<Content | Content[]>(
    `contents:${owner}/${repo}:${ref}:${path}`,
    async () => {
      const {data, response} = await api.GET(
        "/api/v3/repos/{owner}/{repo}/contents/{path}",
        {params: {path: {owner, repo, path}, query: {ref}}},
      );
      return requireApiData(
        data,
        response,
        "Could not load repository content.",
      );
    },
  );

  const items = Array.isArray(result.data) ? result.data : null;
  const file = result.data && !Array.isArray(result.data) ? result.data : null;
  const sortedItems = items
    ? [...items].sort((left, right) => {
        if (left.type !== right.type) return left.type === "dir" ? -1 : 1;
        return left.name.localeCompare(right.name);
      })
    : null;
  const content = file ? decodeBase64Content(file.content) : "";

  return (
    <>
      <RepositoryHeader owner={owner} repo={repo} />
      <Loadable loading={result.loading} error={result.error}>
        <div className="code-browser-heading">
          <div className="breadcrumbs" aria-label="Path">
            <Link to={`/${owner}/${repo}/tree/${ref}`}>{repo}</Link>
            {path ? <span>/</span> : null}
            {path ? <strong>{path}</strong> : null}
          </div>
          <div className="button-row">
            <span className="badge ref-badge">
              <Octicon name="branch" /> {ref}
            </span>
            {!file ? (
              <Link
                className="button compact"
                to={`/${owner}/${repo}/new/${ref}/${path}`}
              >
                <Octicon name="plus" /> Add file
              </Link>
            ) : null}
            {file ? (
              <>
                {file.download_url ? (
                  <a href={file.download_url}>View raw</a>
                ) : null}
                <Link to={`/${owner}/${repo}/edit/${ref}/${path}`}>Edit</Link>
              </>
            ) : null}
          </div>
        </div>
        {sortedItems ? (
          <div className="list-box code-tree" aria-label="Repository files">
            {sortedItems.map((item) => (
              <div className="list-row file-row" key={item.path}>
                <FileTypeIcon type={item.type} />
                <Link
                  to={`/${owner}/${repo}/${item.type === "dir" ? "tree" : "blob"}/${ref}/${item.path}`}
                >
                  {item.name}
                </Link>
              </div>
            ))}
          </div>
        ) : null}
        {file ? (
          <section className="file-view blob-view">
            <header>{file.path}</header>
            <ol className="code-lines">
              {content.split("\n").map((line, index) => (
                <li key={`${index}-${line.slice(0, 20)}`}>
                  <code>{line || " "}</code>
                </li>
              ))}
            </ol>
          </section>
        ) : null}
        {blob && items ? (
          <p className="flash-error">This path is a directory.</p>
        ) : null}
      </Loadable>
    </>
  );
}
