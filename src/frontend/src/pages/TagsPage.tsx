import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {RepositoryHeader} from "../components/RepositoryHeader";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Tag = components["schemas"]["TagResponse"];

export function TagsPage() {
  const {owner = "", repo = ""} = useParams();
  const result = useApiData<Tag[]>(`tags:${owner}/${repo}`, async () => {
    const {data, response} = await api.GET(
      "/api/v3/repos/{owner}/{repo}/tags",
      {
        params: {path: {owner, repo}},
      },
    );
    return requireApiData(data, response, "Could not load tags.");
  });
  return (
    <>
      <RepositoryHeader owner={owner} repo={repo} />
      <div className="page-heading">
        <h1>Tags</h1>
      </div>
      <Loadable loading={result.loading} error={result.error}>
        <div className={result.data?.length ? "list-box" : "empty-state"}>
          {result.data?.length ? (
            result.data.map((tag) => (
              <div className="list-row" key={tag.name}>
                <h2>
                  <Link to={`/${owner}/${repo}/tree/${tag.name}`}>
                    {tag.name}
                  </Link>
                </h2>
                <code>{tag.commit.sha.slice(0, 7)}</code>
              </div>
            ))
          ) : (
            <>
              <Octicon name="tag" size={24} />
              <h2>No tags</h2>
              <p>This repository has no tags yet.</p>
            </>
          )}
        </div>
      </Loadable>
    </>
  );
}
