import {useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {FileDiffList} from "../components/FileDiffList";
import {PullRequestHeader} from "../components/PullRequestHeader";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Pull = components["schemas"]["PRResponse"];
type PullFile = components["schemas"]["PullFileResponse"];

export function PullFilesPage() {
  const {owner = "", repo = "", number = "0"} = useParams();
  const pullNumber = Number(number);
  const result = useApiData<{pull: Pull; files: PullFile[]}>(
    `pull-files:${owner}/${repo}:${pullNumber}`,
    async () => {
      const path = {owner, repo, pull_number: pullNumber};
      const [pullResult, filesResult] = await Promise.all([
        api.GET("/api/v3/repos/{owner}/{repo}/pulls/{pull_number}", {
          params: {path},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/pulls/{pull_number}/files", {
          params: {path},
        }),
      ]);
      return {
        pull: requireApiData(
          pullResult.data,
          pullResult.response,
          "Could not load pull request.",
        ),
        files: requireApiData(
          filesResult.data,
          filesResult.response,
          "Could not load changed files.",
        ),
      };
    },
  );
  const data = result.data;

  return (
    <Loadable loading={result.loading} error={result.error}>
      {data ? (
        <>
          <PullRequestHeader owner={owner} repo={repo} pull={data.pull} />
          <div className="pr-diff-summary">
            <strong>{data.files.length} changed files</strong>
            <span className="diff-additions">+{data.pull.additions}</span>
            <span className="diff-deletions">-{data.pull.deletions}</span>
          </div>
          <FileDiffList
            files={data.files}
            fileHref={(file) =>
              `/${owner}/${repo}/blob/${encodeURIComponent(data.pull.head.ref)}/${file.filename}`
            }
          />
        </>
      ) : null}
    </Loadable>
  );
}
