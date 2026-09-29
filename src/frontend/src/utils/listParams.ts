import type {ListQuery} from "../components/PagedList";

/** Query-string parameters for a listing endpoint, from a ListQuery. */
export function listParams(query: ListQuery): Record<string, string | number> {
  return {
    scope: query.scope,
    page: query.page,
    per_page: query.perPage,
    ...query.filters,
  };
}
