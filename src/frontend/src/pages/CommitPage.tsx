import {useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {FileDiffList} from "../components/FileDiffList";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Commit = components["schemas"]["CommitResponse"];

export function CommitPage() {
  const {owner = "", repo = "", sha = ""} = useParams();
  const result = useApiData<Commit>(
    `commit:${owner}/${repo}:${sha}`,
    async () => {
      const {data, response} = await api.GET(
        "/api/v3/repos/{owner}/{repo}/commits/{sha}",
        {
          params: {path: {owner, repo, sha}},
        },
      );
      return requireApiData(data, response, "Could not load commit.");
    },
  );
  return (
    <>
      <Loadable loading={result.loading} error={result.error}>
        {result.data ? (
          <>
            <section className="repo-summary">
              <h1>{result.data.commit.message}</h1>
              <p>
                {result.data.commit.author.name} &lt;
                {result.data.commit.author.email}&gt;
              </p>
              <p className="muted">{result.data.sha}</p>
            </section>
            <div className="pr-diff-summary">
              <strong>{result.data.files?.length ?? 0} changed files</strong>
              <span className="diff-additions">
                +{result.data.stats?.additions ?? 0}
              </span>
              <span className="diff-deletions">
                -{result.data.stats?.deletions ?? 0}
              </span>
            </div>
            <FileDiffList
              files={result.data.files ?? []}
              fileHref={(file) =>
                `/${owner}/${repo}/blob/${result.data!.sha}/${file.filename}`
              }
            />
          </>
        ) : null}
      </Loadable>
    </>
  );
}
