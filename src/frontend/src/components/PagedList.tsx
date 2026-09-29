import {ReactNode, useState} from "react";

import {Loadable} from "./Loadable";
import {useApiData} from "../hooks/useApiData";

/** One page of a bounded server-side listing. */
export interface Page<T> {
  total_count: number;
  page: number;
  per_page: number;
  items: T[];
}

export type ListScope = "active" | "all";

export interface ListQuery {
  scope: ListScope;
  filters: Record<string, string>;
  page: number;
  perPage: number;
}

/** A filter shown in the toolbar: a select when it has options, a text box otherwise. */
export interface ListFilter {
  name: string;
  label: string;
  options?: {value: string; label: string}[];
  placeholder?: string;
}

interface PagedListProps<T> {
  /** Distinguishes this list's requests from other lists on the page. */
  storageKey: string;
  load: (query: ListQuery) => Promise<Page<T>>;
  filters?: ListFilter[];
  row: (item: T, reload: () => void) => ReactNode;
  /** What the "unfinished" scope is called for this kind of item. */
  activeLabel?: string;
  emptyTitle: string;
  emptyText: string;
  perPage?: number;
}

/**
 * A listing that shows unfinished items by default and can be widened to
 * everything, filtered, and paged. The server owns the window: this
 * component only asks for one page at a time, which is what keeps a
 * listing over a table that grows (runs, jobs) affordable to open.
 */
export function PagedList<T>({
  storageKey,
  load,
  filters = [],
  row,
  activeLabel = "Unfinished",
  emptyTitle,
  emptyText,
  perPage = 50,
}: PagedListProps<T>) {
  const [scope, setScope] = useState<ListScope>("active");
  const [values, setValues] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const query: ListQuery = {scope, filters: values, page, perPage};
  const key = `${storageKey}:${JSON.stringify(query)}`;
  const data = useApiData<Page<T>>(key, () => load(query));

  const total = data.data?.total_count ?? 0;
  const pages = Math.max(1, Math.ceil(total / perPage));

  function setFilter(name: string, value: string) {
    setValues((current) => {
      const next = {...current};
      if (value) next[name] = value;
      else delete next[name];
      return next;
    });
    setPage(1);
  }

  return (
    <>
      <div className="list-toolbar" role="group" aria-label="List controls">
        <div className="segmented" role="radiogroup" aria-label="Scope">
          {(
            [
              ["active", activeLabel],
              ["all", "All"],
            ] as [ListScope, string][]
          ).map(([value, label]) => (
            <button
              type="button"
              key={value}
              role="radio"
              aria-checked={scope === value}
              className={scope === value ? "active" : ""}
              onClick={() => {
                setScope(value);
                setPage(1);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        {filters.map((filter) => (
          <label className="list-filter" key={filter.name}>
            <span>{filter.label}</span>
            {filter.options ? (
              <select
                value={values[filter.name] ?? ""}
                onChange={(event) => setFilter(filter.name, event.target.value)}
              >
                <option value="">Any</option>
                {filter.options.map((option) => (
                  <option value={option.value} key={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="search"
                value={values[filter.name] ?? ""}
                placeholder={filter.placeholder}
                onChange={(event) => setFilter(filter.name, event.target.value)}
              />
            )}
          </label>
        ))}
        <button
          type="button"
          className="button secondary compact"
          onClick={() => data.reload()}
        >
          Refresh
        </button>
      </div>
      <Loadable loading={data.loading} error={data.error}>
        {data.data && data.data.items.length > 0 ? (
          <div className="list-box">
            {data.data.items.map((item) => row(item, data.reload))}
          </div>
        ) : (
          <div className="settings-empty">
            <h2>{emptyTitle}</h2>
            <p className="muted">{emptyText}</p>
          </div>
        )}
      </Loadable>
      <div className="list-pager" aria-label="Pagination">
        <span className="muted">
          {total} {total === 1 ? "item" : "items"} · page {page} of {pages}
        </span>
        <span className="button-row">
          <button
            type="button"
            className="button secondary compact"
            disabled={page <= 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >
            Previous
          </button>
          <button
            type="button"
            className="button secondary compact"
            disabled={page >= pages}
            onClick={() => setPage((current) => Math.min(pages, current + 1))}
          >
            Next
          </button>
        </span>
      </div>
    </>
  );
}
