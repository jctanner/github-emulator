import {api} from "../api/client";
import type {components} from "../api/schema";
import {requireApiData, useApiData} from "./useApiData";

export type RepositorySummary =
  components["schemas"]["RepositoryHomeSummaryResponse"];

/** Commit, branch and tag counts for a ref; deferred until `ready`. */
export function useRepositorySummary(
  owner: string,
  repo: string,
  ref: string,
  ready = true,
) {
  return useApiData<RepositorySummary | null>(
    `repo-summary:${owner}/${repo}:${ref}:${ready ? "ready" : "deferred"}`,
    async () => {
      if (!ready || !ref) return null;
      const {data, response} = await api.GET(
        "/api/_ui/repos/{owner}/{repo}/summary",
        {params: {path: {owner, repo}, query: {ref}}},
      );
      return requireApiData(
        data,
        response,
        "Could not load repository counts.",
      );
    },
  );
}
