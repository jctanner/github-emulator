import {FormEvent, useState} from "react";
import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {useRepository} from "../components/RepositoryContext";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Branch = components["schemas"]["BranchResponse"];

export function BranchesPage() {
  const {owner = "", repo = ""} = useParams();
  const repository = useRepository();
  const result = useApiData<Branch[]>(`branches:${owner}/${repo}`, async () => {
    const {data, response} = await api.GET(
      "/api/v3/repos/{owner}/{repo}/branches",
      {
        params: {path: {owner, repo}, query: {per_page: 100}},
      },
    );
    return requireApiData(data, response, "Could not load branches.");
  });
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleBranches = result.data?.filter((branch) =>
    branch.name.toLocaleLowerCase().includes(normalizedQuery),
  );

  async function create(event: FormEvent) {
    event.preventDefault();
    const selected =
      result.data?.find((branch) => branch.name === source) ?? result.data?.[0];
    if (!selected) return setError("A source branch is required.");
    const {response} = await api.POST("/api/v3/repos/{owner}/{repo}/git/refs", {
      params: {path: {owner, repo}},
      body: {ref: `refs/heads/${name}`, sha: selected.commit.sha},
    });
    if (!response.ok) return setError("Could not create branch.");
    setName("");
    setError(null);
    result.reload();
  }

  async function remove(name: string) {
    const {response} = await api.DELETE(
      "/api/v3/repos/{owner}/{repo}/git/refs/{ref}",
      {params: {path: {owner, repo, ref: `heads/${name}`}}},
    );
    if (!response.ok) return setError("Could not delete branch.");
    result.reload();
  }
  return (
    <>
      <div className="page-heading">
        <h1>Branches</h1>
      </div>
      <div className="branches-toolbar">
        <input
          aria-label="Search branches"
          placeholder="Find a branch..."
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <details className="new-branch-disclosure">
          <summary className="button">New branch</summary>
          <form
            className="inline-editor"
            onSubmit={(event) => void create(event)}
          >
            <input
              required
              placeholder="New branch name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <select
              value={source || result.data?.[0]?.name || ""}
              onChange={(event) => setSource(event.target.value)}
            >
              {result.data?.map((branch) => (
                <option key={branch.name}>{branch.name}</option>
              ))}
            </select>
            <button className="button" type="submit">
              Create branch
            </button>
          </form>
        </details>
      </div>
      {error ? <p className="flash-error">{error}</p> : null}
      <Loadable loading={result.loading} error={result.error}>
        <div className="list-box">
          {visibleBranches?.map((branch) => (
            <div className="list-row branch-row" key={branch.name}>
              <Octicon name="branch" />
              <div>
                <h2>
                  <Link
                    to={`/${owner}/${repo}/tree/${encodeURIComponent(branch.name)}`}
                  >
                    {branch.name}
                  </Link>
                  {branch.name === repository.default_branch ? (
                    <span className="badge">Default</span>
                  ) : null}
                  {branch.protected ? (
                    <span className="badge">Protected</span>
                  ) : null}
                </h2>
                <code className="muted">{branch.commit.sha.slice(0, 7)}</code>
              </div>
              {!branch.protected &&
              branch.name !== repository.default_branch ? (
                <button type="button" onClick={() => void remove(branch.name)}>
                  Delete
                </button>
              ) : null}
            </div>
          ))}
          {!result.loading && visibleBranches?.length === 0 ? (
            <div className="empty-state">
              <h2>No branches found</h2>
              <p>Try a different branch name.</p>
            </div>
          ) : null}
        </div>
      </Loadable>
    </>
  );
}
