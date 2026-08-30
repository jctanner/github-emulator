import {FormEvent, useState} from "react";
import {useNavigate, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {useRepositoryLayout} from "../components/RepositoryContext";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Branch = components["schemas"]["BranchResponse"];

export function NewPullPage() {
  const {owner = "", repo = ""} = useParams();
  const navigate = useNavigate();
  const {reloadNavigation} = useRepositoryLayout();
  const branches = useApiData<Branch[]>(`new-pr:${owner}/${repo}`, async () => {
    const {data, response} = await api.GET(
      "/api/v3/repos/{owner}/{repo}/branches",
      {params: {path: {owner, repo}}},
    );
    return requireApiData(data, response, "Could not load branches.");
  });
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [head, setHead] = useState("");
  const [base, setBase] = useState("main");
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const {data, response} = await api.POST(
      "/api/v3/repos/{owner}/{repo}/pulls",
      {
        params: {path: {owner, repo}},
        body: {title, body, head, base},
      },
    );
    if (!data || !response.ok)
      return setError("Could not create pull request.");
    reloadNavigation();
    await navigate(`/${owner}/${repo}/pulls/${data.number}`);
  }
  return (
    <>
      <Loadable loading={branches.loading} error={branches.error}>
        <form className="editor-form" onSubmit={(event) => void submit(event)}>
          <h1>Open a pull request</h1>
          {error ? <p className="flash-error">{error}</p> : null}
          <div className="form-grid">
            <label>
              Base
              <select
                value={base}
                onChange={(event) => setBase(event.target.value)}
              >
                {branches.data?.map((branch) => (
                  <option key={branch.name}>{branch.name}</option>
                ))}
              </select>
            </label>
            <label>
              Compare
              <select
                required
                value={head}
                onChange={(event) => setHead(event.target.value)}
              >
                <option value="">Choose a branch</option>
                {branches.data?.map((branch) => (
                  <option key={branch.name}>{branch.name}</option>
                ))}
              </select>
            </label>
          </div>
          <label>
            Title
            <input
              required
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <label>
            Description
            <textarea
              value={body}
              onChange={(event) => setBody(event.target.value)}
            />
          </label>
          <button className="button" type="submit">
            Create pull request
          </button>
        </form>
      </Loadable>
    </>
  );
}
