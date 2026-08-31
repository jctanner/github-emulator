import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {BranchSelector} from "../components/BranchSelector";
import {FileTypeIcon} from "../components/FileTypeIcon";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {RepositoryActivity} from "../components/RepositoryActivity";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {decodeBase64Content} from "../utils/content";

type Content = components["schemas"]["ContentResponse"];
interface BrowserData {
  content: Content | Content[];
  readme: Content | null;
}

export function CodeBrowserPage({blob = false}: {blob?: boolean}) {
  const {owner = "", repo = "", ref = "main", "*": path = ""} = useParams();
  const result = useApiData<BrowserData>(
    `contents:${owner}/${repo}:${ref}:${path}`,
    async () => {
      const {data, response} = await api.GET(
        "/api/v3/repos/{owner}/{repo}/contents/{path}",
        {params: {path: {owner, repo, path}, query: {ref}}},
      );
      const content = requireApiData(
        data,
        response,
        "Could not load repository content.",
      );
      if (path || !Array.isArray(content)) return {content, readme: null};

      const readmeResult = await api.GET(
        "/api/v3/repos/{owner}/{repo}/readme",
        {params: {path: {owner, repo}, query: {ref}}},
      );
      return {
        content,
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

  const loadedContent = result.data?.content;
  const items = Array.isArray(loadedContent) ? loadedContent : null;
  const file = loadedContent && !Array.isArray(loadedContent) ? loadedContent : null;
  const sortedItems = items
    ? [...items].sort((left, right) => {
        if (left.type !== right.type) return left.type === "dir" ? -1 : 1;
        return left.name.localeCompare(right.name);
      })
    : null;
  const content = file ? decodeBase64Content(file.content) : "";

  return (
    <>
      <Loadable loading={result.loading} error={result.error}>
        <div className="code-browser-heading">
          <div className="breadcrumbs" aria-label="Path">
            <Link to={`/${owner}/${repo}/tree/${encodeURIComponent(ref)}`}>
              {repo}
            </Link>
            {path ? <span>/</span> : null}
            {path ? <strong>{path}</strong> : null}
          </div>
          <div className="button-row">
            <BranchSelector owner={owner} repo={repo} currentRef={ref} />
            {!file ? (
              <Link
                className="button compact"
                to={`/${owner}/${repo}/new/${encodeURIComponent(ref)}/${path}`}
              >
                <Octicon name="plus" /> Add file
              </Link>
            ) : null}
            {file ? (
              <>
                {file.download_url ? (
                  <a href={file.download_url}>View raw</a>
                ) : null}
                <Link
                  to={`/${owner}/${repo}/edit/${encodeURIComponent(ref)}/${path}`}
                >
                  Edit
                </Link>
              </>
            ) : null}
          </div>
        </div>
        {sortedItems ? (
          <RepositoryActivity owner={owner} repo={repo} ref={ref} />
        ) : null}
        {sortedItems ? (
          <div className="list-box code-tree" aria-label="Repository files">
            {sortedItems.map((item) => (
              <div className="list-row file-row" key={item.path}>
                <FileTypeIcon type={item.type} />
                <Link
                  to={`/${owner}/${repo}/${item.type === "dir" ? "tree" : "blob"}/${encodeURIComponent(ref)}/${item.path}`}
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
        {result.data?.readme ? (
          <section className="file-view readme-view">
            <h2>
              <Octicon name="book" /> README
            </h2>
            <pre>{decodeBase64Content(result.data.readme.content)}</pre>
          </section>
        ) : null}
        {blob && items ? (
          <p className="flash-error">This path is a directory.</p>
        ) : null}
      </Loadable>
    </>
  );
}
