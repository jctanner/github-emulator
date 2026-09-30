import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {BranchSelector} from "../components/BranchSelector";
import {Octicon} from "../components/Octicon";
import {RepositoryActivity} from "../components/RepositoryActivity";
import {RepositoryTreeList} from "../components/RepositoryTreeList";
import {useRepository} from "../components/RepositoryContext";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {useRepositorySummary} from "../hooks/useRepositorySummary";
import {decodeBase64Content} from "../utils/content";

type Content = components["schemas"]["ContentResponse"];

export function RepositoryPage() {
  const {owner = "", repo = ""} = useParams();
  const repository = useRepository();
  const ref = repository.default_branch;
  const summary = useRepositorySummary(owner, repo, ref);
  const readme = useApiData<Content | null>(
    `repo-readme:${owner}/${repo}:${ref}`,
    async () => {
      if (!ref) return null;
      const {data, response} = await api.GET(
        "/api/v3/repos/{owner}/{repo}/readme",
        {params: {path: {owner, repo}, query: {ref}}},
      );
      if (response.status === 404) return null;
      return requireApiData(data, response, "Could not load README.");
    },
  );
  return (
    <>
      <div className="repo-home-toolbar">
        <div className="repo-home-toolbar-start">
          <BranchSelector owner={owner} repo={repo} currentRef={ref} />
          <RepositoryActivity owner={owner} repo={repo} summary={summary} />
        </div>
        <Link
          className="button"
          to={`/${owner}/${repo}/new/${encodeURIComponent(ref)}/`}
        >
          <Octicon name="plus" /> Add file
        </Link>
      </div>
      <RepositoryTreeList
        owner={owner}
        repo={repo}
        ref={ref}
        commitCount={summary.data?.commit_count}
      />
      {readme.data ? (
        <section className="file-view readme-view">
          <h2>
            <Octicon name="book" /> README
          </h2>
          <pre>{decodeBase64Content(readme.data.content)}</pre>
        </section>
      ) : null}
    </>
  );
}
