import {useEffect, useRef, useState} from "react";
import {Link} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {Octicon} from "./Octicon";

type Branch = components["schemas"]["BranchResponse"];

export function BranchSelector({
  owner,
  repo,
  currentRef,
}: {
  owner: string;
  repo: string;
  currentRef: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const filterInput = useRef<HTMLInputElement>(null);
  const branches = useApiData<Branch[]>(
    `branch-selector:${owner}/${repo}`,
    async () => {
      const {data, response} = await api.GET(
        "/api/v3/repos/{owner}/{repo}/branches",
        {params: {path: {owner, repo}, query: {per_page: 100}}},
      );
      return requireApiData(data, response, "Could not load branches.");
    },
  );
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleBranches = branches.data?.filter((branch) =>
    branch.name.toLocaleLowerCase().includes(normalizedQuery),
  );

  useEffect(() => {
    if (!open) return;

    filterInput.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className="branch-selector-menu">
      <button
        aria-label="Switch branches"
        aria-expanded={open}
        aria-haspopup="menu"
        className="branch-selector"
        onClick={() => setOpen((value) => !value)}
        type="button"
      >
        <Octicon name="branch" /> {currentRef}
        <span className="dropdown-caret" aria-hidden="true" />
      </button>
      {open ? <div className="branch-selector-popover">
        <strong>Switch branches</strong>
        <input
          aria-label="Filter branches"
          placeholder="Filter branches..."
          ref={filterInput}
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="branch-selector-list">
          {branches.loading ? <span className="muted">Loading...</span> : null}
          {branches.error ? (
            <span className="flash-error">{branches.error}</span>
          ) : null}
          {visibleBranches?.map((branch) => (
            <Link
              className={branch.name === currentRef ? "selected" : undefined}
              key={branch.name}
              to={`/${owner}/${repo}/tree/${encodeURIComponent(branch.name)}`}
              onClick={() => {
                setOpen(false);
                setQuery("");
              }}
            >
              <span aria-hidden="true">
                {branch.name === currentRef ? "✓" : ""}
              </span>
              {branch.name}
            </Link>
          ))}
          {!branches.loading && visibleBranches?.length === 0 ? (
            <span className="muted">No branches found</span>
          ) : null}
        </div>
      </div> : null}
    </div>
  );
}
