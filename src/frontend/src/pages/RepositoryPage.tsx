import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {FileTypeIcon} from "../components/FileTypeIcon";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {RepositoryHeader} from "../components/RepositoryHeader";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {decodeBase64Content} from "../utils/content";

type Repository = components["schemas"]["RepoResponse"];
type Content = components["schemas"]["ContentResponse"];
type Commit = components["schemas"]["CommitResponse"];
type Branch = components["schemas"]["BranchResponse"];
type Tag = components["schemas"]["TagResponse"];

interface RepositoryHomeData {
  repository: Repository;
  contents: Content[];
  readme: Content | null;
  commits: Commit[];
  branches: Branch[];
  tags: Tag[];
}

export function RepositoryPage() {
  const {owner = "", repo = ""} = useParams();
  const result = useApiData<RepositoryHomeData>(
    `repo-home:${owner}/${repo}`,
    async () => {
      const repositoryResult = await api.GET("/api/v3/repos/{owner}/{repo}", {
        params: {path: {owner, repo}},
      });
      const repository = requireApiData(
        repositoryResult.data,
        repositoryResult.response,
        "Could not load repository.",
      );
      const ref = repository.default_branch;
      const [
        contentsResult,
        readmeResult,
        commitsResult,
        branchesResult,
        tagsResult,
      ] = await Promise.all([
        api.GET("/api/v3/repos/{owner}/{repo}/contents/{path}", {
          params: {path: {owner, repo, path: ""}, query: {ref}},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/readme", {
          params: {path: {owner, repo}, query: {ref}},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/commits", {
          params: {path: {owner, repo}, query: {sha: ref, per_page: 100}},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/branches", {
          params: {path: {owner, repo}, query: {per_page: 100}},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/tags", {
          params: {path: {owner, repo}},
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
        repository,
        contents,
        readme:
          readmeResult.response.status === 404
            ? null
            : requireApiData(
                readmeResult.data,
                readmeResult.response,
                "Could not load README.",
              ),
        commits: requireApiData(
          commitsResult.data,
          commitsResult.response,
          "Could not load commits.",
        ),
        branches: requireApiData(
          branchesResult.data,
          branchesResult.response,
          "Could not load branches.",
        ),
        tags: requireApiData(
          tagsResult.data,
          tagsResult.response,
          "Could not load tags.",
        ),
      };
    },
  );

  const data = result.data;
  const ref = data?.repository.default_branch ?? "main";

  return (
    <Loadable loading={result.loading} error={result.error}>
      {data ? (
        <>
          <RepositoryHeader repository={data.repository} />
          <div className="repo-home-toolbar">
            <Link
              className="branch-selector"
              to={`/${owner}/${repo}/tree/${ref}`}
            >
              <Octicon name="branch" /> {ref}
            </Link>
            <Link className="button" to={`/${owner}/${repo}/new/${ref}/`}>
              <Octicon name="plus" /> Add file
            </Link>
          </div>
          <nav className="repo-activity" aria-label="Repository activity">
            <Link to={`/${owner}/${repo}/commits/${ref}`}>
              <Octicon name="history" />
              <strong>{data.commits.length}</strong> commits
            </Link>
            <Link to={`/${owner}/${repo}/branches`}>
              <Octicon name="branch" />
              <strong>{data.branches.length}</strong> branches
            </Link>
            <Link to={`/${owner}/${repo}/tags`}>
              <Octicon name="tag" />
              <strong>{data.tags.length}</strong> tags
            </Link>
          </nav>
          <section
            className="list-box repo-home-files"
            aria-label="Repository files"
          >
            <h2 className="list-box-header">Files</h2>
            {data.contents.map((item) => (
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
          {data.readme ? (
            <section className="file-view readme-view">
              <h2>
                <Octicon name="book" /> README
              </h2>
              <pre>{decodeBase64Content(data.readme.content)}</pre>
            </section>
          ) : null}
        </>
      ) : null}
    </Loadable>
  );
}
