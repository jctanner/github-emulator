import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {FileTypeIcon} from "../components/FileTypeIcon";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {RepositoryActivity} from "../components/RepositoryActivity";
import {useRepository} from "../components/RepositoryContext";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {decodeBase64Content} from "../utils/content";

type Content = components["schemas"]["ContentResponse"];
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
      <RepositoryActivity
        owner={owner}
        repo={repo}
        ref={ref}
        ready={Boolean(files.data)}
      />
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
