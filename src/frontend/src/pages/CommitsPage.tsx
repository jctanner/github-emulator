import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Commit = components["schemas"]["CommitResponse"];

export function CommitsPage() {
  const {owner = "", repo = "", ref = "main"} = useParams();
  const result = useApiData<Commit[]>(
    `commits:${owner}/${repo}:${ref}`,
    async () => {
      const {data, response} = await api.GET(
        "/api/v3/repos/{owner}/{repo}/commits",
        {
          params: {path: {owner, repo}, query: {sha: ref}},
        },
      );
      return requireApiData(data, response, "Could not load commits.");
    },
  );
  return (
    <>
      <div className="page-heading">
        <h1>
          <Octicon name="history" /> Commits on {ref}
        </h1>
      </div>
      <Loadable loading={result.loading} error={result.error}>
        <div className="list-box">
          {result.data?.map((item) => (
            <div className="list-row commit-row" key={item.sha}>
              <div>
                <h2>
                  <Link to={`/${owner}/${repo}/commit/${item.sha}`}>
                    {item.commit.message}
                  </Link>
                </h2>
                <span className="muted">
                  {item.commit.author.name} committed{" "}
                  {new Date(item.commit.author.date).toLocaleString()}
                </span>
              </div>
              <code>{item.sha.slice(0, 7)}</code>
            </div>
          ))}
        </div>
        <div className="pagination" aria-label="Commit pagination">
          <button disabled type="button">
            Newer
          </button>
          <button disabled type="button">
            Older
          </button>
        </div>
      </Loadable>
    </>
  );
}
