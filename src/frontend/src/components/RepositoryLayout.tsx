import {Outlet, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {Loadable} from "./Loadable";
import {RepositoryContext} from "./RepositoryContext";
import {RepositoryHeader} from "./RepositoryHeader";

type Repository = components["schemas"]["RepoResponse"];
type Navigation = components["schemas"]["RepositoryNavigationResponse"];

export function RepositoryLayout() {
  const {owner = "", repo = ""} = useParams();
  const metadata = useApiData<Repository>(
    `repository-layout:${owner}/${repo}`,
    async () => {
      const {data, response} = await api.GET("/api/v3/repos/{owner}/{repo}", {
        params: {path: {owner, repo}},
      });
      return requireApiData(data, response, "Could not load repository.");
    },
  );
  const navigation = useApiData<Navigation>(
    `repository-navigation:${owner}/${repo}`,
    async () => {
      const {data, response} = await api.GET(
        "/api/_ui/repos/{owner}/{repo}/navigation",
        {params: {path: {owner, repo}}},
      );
      return requireApiData(
        data,
        response,
        "Could not load repository counts.",
      );
    },
  );

  return (
    <Loadable loading={metadata.loading} error={metadata.error}>
      {metadata.data ? (
        <RepositoryContext.Provider
          value={{
            repository: metadata.data,
            reload: metadata.reload,
            reloadNavigation: navigation.reload,
          }}
        >
          <RepositoryHeader
            repository={metadata.data}
            navigation={navigation.data}
          />
          <Outlet />
        </RepositoryContext.Provider>
      ) : null}
    </Loadable>
  );
}
