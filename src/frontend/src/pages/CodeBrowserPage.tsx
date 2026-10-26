import {Fragment, useMemo, useState} from "react";
import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {BranchSelector} from "../components/BranchSelector";
import {CopyPathButton} from "../components/CopyPathButton";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {RepositoryActivity} from "../components/RepositoryActivity";
import {RepositoryTreeList} from "../components/RepositoryTreeList";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {useRepositorySummary} from "../hooks/useRepositorySummary";
import {decodeBase64Content} from "../utils/content";
import {highlightLines} from "../utils/highlight";
import {MARKDOWN_FILE} from "../utils/markdown";
import {RepositoryMarkdown} from "../components/RepositoryMarkdown";

type Content = components["schemas"]["ContentResponse"];
interface BrowserData {
  content: Content | Content[];
  readme: Content | null;
}

export function CodeBrowserPage({blob = false}: {blob?: boolean}) {
  const {owner = "", repo = "", ref = "main", "*": path = ""} = useParams();
  // Branch/tag/commit totals belong to the repository root; folders show
  // their own History link instead.
  const atRoot = !path;
  const summary = useRepositorySummary(owner, repo, ref, atRoot);
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
  const file =
    loadedContent && !Array.isArray(loadedContent) ? loadedContent : null;
  const pathSegments = path
    .split("/")
    .filter(Boolean)
    .map((name, index, names) => ({
      name,
      path: names.slice(0, index + 1).join("/"),
    }));
  const content = file ? decodeBase64Content(file.content) : "";
  const highlighted = useMemo(
    () => (file ? highlightLines(content, file.path) : null),
    [file, content],
  );
  // A Markdown file opens rendered, as on GitHub; this remembers which path
  // was switched to its source.
  const [codePath, setCodePath] = useState<string | null>(null);
  const markdownFile = file && MARKDOWN_FILE.test(file.name) ? file : null;
  const showPreview = markdownFile !== null && codePath !== markdownFile.path;

  return (
    <>
      <Loadable loading={result.loading} error={result.error}>
        <div className="code-browser-heading">
          <div className="code-browser-heading-start">
            <BranchSelector owner={owner} repo={repo} currentRef={ref} />
            <div className="breadcrumbs" aria-label="Path">
              <Link to={`/${owner}/${repo}/tree/${encodeURIComponent(ref)}`}>
                {repo}
              </Link>
              {pathSegments.map((segment, index) => (
                <Fragment key={segment.path}>
                  <span>/</span>
                  {index === pathSegments.length - 1 ? (
                    <strong>{segment.name}</strong>
                  ) : (
                    <Link
                      to={`/${owner}/${repo}/tree/${encodeURIComponent(ref)}/${segment.path}`}
                    >
                      {segment.name}
                    </Link>
                  )}
                </Fragment>
              ))}
              {items && path ? <span>/</span> : null}
              {path ? <CopyPathButton path={path} /> : null}
            </div>
          </div>
          <div className="button-row">
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
        {items ? (
          <>
            {atRoot ? (
              <RepositoryActivity owner={owner} repo={repo} summary={summary} />
            ) : null}
            <RepositoryTreeList
              owner={owner}
              repo={repo}
              ref={ref}
              path={path}
              commitCount={atRoot ? summary.data?.commit_count : undefined}
            />
          </>
        ) : null}
        {file ? (
          <section className="file-view blob-view">
            <header>
              {markdownFile ? (
                <div
                  className="segmented-control"
                  role="group"
                  aria-label="File view"
                >
                  <button
                    type="button"
                    aria-pressed={showPreview}
                    onClick={() => setCodePath(null)}
                  >
                    Preview
                  </button>
                  <button
                    type="button"
                    aria-pressed={!showPreview}
                    onClick={() => setCodePath(markdownFile.path)}
                  >
                    Code
                  </button>
                </div>
              ) : null}
              <span>{file.path}</span>
            </header>
            {showPreview ? (
              <RepositoryMarkdown
                owner={owner}
                repo={repo}
                gitRef={ref}
                file={file}
              />
            ) : (
              <ol className="code-lines">
                {content.split("\n").map((line, index) => (
                  <li key={`${index}-${line.slice(0, 20)}`}>
                    {highlighted ? (
                      <code
                        // highlight.js escapes the source and emits only spans.
                        dangerouslySetInnerHTML={{
                          __html: highlighted[index] || " ",
                        }}
                      />
                    ) : (
                      <code>{line || " "}</code>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </section>
        ) : null}
        {result.data?.readme ? (
          <section className="file-view readme-view">
            <h2>
              <Octicon name="book" /> README
            </h2>
            {MARKDOWN_FILE.test(result.data.readme.name) ? (
              <RepositoryMarkdown
                owner={owner}
                repo={repo}
                gitRef={ref}
                file={result.data.readme}
              />
            ) : (
              <pre>{decodeBase64Content(result.data.readme.content)}</pre>
            )}
          </section>
        ) : null}
        {blob && items ? (
          <p className="flash-error">This path is a directory.</p>
        ) : null}
      </Loadable>
    </>
  );
}
