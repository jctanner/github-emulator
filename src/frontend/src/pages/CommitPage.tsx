import {useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
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
          <section className="repo-summary">
            <h1>{result.data.commit.message}</h1>
            <p>
              {result.data.commit.author.name} &lt;
              {result.data.commit.author.email}&gt;
            </p>
            <p className="muted">{result.data.sha}</p>
          </section>
        ) : null}
      </Loadable>
    </>
  );
}
