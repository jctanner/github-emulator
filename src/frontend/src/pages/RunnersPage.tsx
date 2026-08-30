import {useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {RepositoryHeader} from "../components/RepositoryHeader";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Runners = components["schemas"]["RunnerListResponse"];

export function RunnersPage({embedded = false}: {embedded?: boolean}) {
  const {owner = "", repo = ""} = useParams();
  const runners = useApiData<Runners>(`runners:${owner}/${repo}`, async () => {
    const {data, response} = await api.GET(
      "/api/v3/repos/{owner}/{repo}/actions/runners",
      {params: {path: {owner, repo}}},
    );
    return requireApiData(data, response, "Could not load runners.");
  });
  async function remove(id: number) {
    await api.DELETE(
      "/api/v3/repos/{owner}/{repo}/actions/runners/{runner_id}",
      {params: {path: {owner, repo, runner_id: id}}},
    );
    runners.reload();
  }
  return (
    <>
      {!embedded ? <RepositoryHeader owner={owner} repo={repo} /> : null}
      <div className="page-heading">
        <div>
          <h1>Actions runners</h1>
          <p className="muted">
            Manage the machines that execute Actions workflows for this
            repository.
          </p>
        </div>
      </div>
      <Loadable loading={runners.loading} error={runners.error}>
        <div className="list-box">
          <div className="list-box-header">
            <strong>Repository runners</strong>
          </div>
          {runners.data?.runners.map((runner) => (
            <div className="list-row label-row" key={runner.id}>
              <strong>{runner.name}</strong>
              <span>
                {runner.os} · {runner.status}
                {runner.busy ? " · Busy" : ""}
              </span>
              <button type="button" onClick={() => void remove(runner.id)}>
                Remove
              </button>
            </div>
          ))}
          {runners.data?.runners.length === 0 ? (
            <div className="settings-empty">
              <h2>No repository runners</h2>
              <p className="muted">
                This repository can still use runners made available to all
                repositories.
              </p>
            </div>
          ) : null}
        </div>
      </Loadable>
    </>
  );
}
