import {createContext, useContext} from "react";

import type {components} from "../api/schema";

type Repository = components["schemas"]["RepoResponse"];

export interface RepositoryLayoutValue {
  repository: Repository;
  reload: () => void;
  reloadNavigation: () => void;
}

export const RepositoryContext = createContext<RepositoryLayoutValue | null>(
  null,
);

export function useRepositoryLayout(): RepositoryLayoutValue {
  const value = useContext(RepositoryContext);
  if (!value) {
    throw new Error("useRepositoryLayout must be used within RepositoryLayout");
  }
  return value;
}

export function useRepository(): Repository {
  return useRepositoryLayout().repository;
}
