import {requireApiData} from "../hooks/useApiData";
import {api} from "./client";
import type {components} from "./schema";

export type Branch = components["schemas"]["BranchResponse"];

// The branches API caps per_page at 100, so walk every page; a short page
// marks the end of the list.
export async function loadAllBranches(
  owner: string,
  repo: string,
): Promise<Branch[]> {
  const branches: Branch[] = [];
  for (let page = 1; ; page++) {
    const {data, response} = await api.GET(
      "/api/v3/repos/{owner}/{repo}/branches",
      {params: {path: {owner, repo}, query: {per_page: 100, page}}},
    );
    const batch = requireApiData(data, response, "Could not load branches.");
    branches.push(...batch);
    if (batch.length < 100) return branches;
  }
}
