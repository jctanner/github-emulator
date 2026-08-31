import {FormEvent, useEffect, useState} from "react";
import {Navigate, useNavigate, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {useSession} from "../auth/SessionContext";
import {Loadable} from "../components/Loadable";
import {useRepositoryLayout} from "../components/RepositoryContext";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Branch = components["schemas"]["BranchResponse"];

export function NewPullPage() {
  const {owner = "", repo = ""} = useParams();
  const navigate = useNavigate();
  const session = useSession();
  const {repository, reloadNavigation} = useRepositoryLayout();
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
  const [base, setBase] = useState(repository.default_branch);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!head && branches.data?.length) {
      const comparisonBranches = branches.data.filter(
        (branch) => branch.name !== base,
      );
      setHead(
        comparisonBranches[comparisonBranches.length - 1]?.name ??
          branches.data[0].name,
      );
    }
  }, [base, branches.data, head]);
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
  if (session.loading) return <p className="loading">Loading...</p>;
  if (!session.user) return <Navigate replace to="/login" />;

  return (
    <Loadable loading={branches.loading} error={branches.error}>
      <form className="new-pull-form" onSubmit={(event) => void submit(event)}>
        <div className="new-pull-subhead">Open a pull request</div>
        {error ? <p className="flash-error">{error}</p> : null}
        <div className="new-pull-box">
          <div className="pull-ref-picker">
            <label className="pull-ref-field">
              base
              <select
                value={base}
                onChange={(event) => setBase(event.target.value)}
              >
                {branches.data?.map((branch) => (
                  <option key={branch.name}>{branch.name}</option>
                ))}
              </select>
            </label>
            <span className="pull-ref-arrow" aria-hidden="true">
              ←
            </span>
            <label className="pull-ref-field">
              compare
              <select
                required
                value={head}
                onChange={(event) => setHead(event.target.value)}
              >
                {branches.data?.map((branch) => (
                  <option key={branch.name}>{branch.name}</option>
                ))}
              </select>
            </label>
          </div>
          <hr className="pull-form-divider" />
          <label>
            <span>
              Title <span className="required-marker">*</span>
            </span>
            <input
              autoFocus
              required
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <label>
            <span>
              Description <span className="optional-marker">(optional)</span>
            </span>
            <textarea
              placeholder="Leave a comment"
              value={body}
              onChange={(event) => setBody(event.target.value)}
            />
          </label>
        </div>
        <button className="button" type="submit">
          Create pull request
        </button>
      </form>
    </Loadable>
  );
}
