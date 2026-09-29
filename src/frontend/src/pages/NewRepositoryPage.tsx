import {FormEvent, useEffect, useState} from "react";
import {useNavigate, useSearchParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {useSession} from "../auth/SessionContext";

type Organization = components["schemas"]["OrganizationResponse"];

/**
 * Create a repository under the signed-in user or one of their
 * organisations. GitHub's form has the same owner dropdown; without it every
 * repository landed under the user, and an organisation could only get one
 * through the API. `?owner=` preselects an owner, so an organisation page
 * can link straight here.
 */
export function NewRepositoryPage() {
  const navigate = useNavigate();
  const {user} = useSession();
  const [params] = useSearchParams();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [owner, setOwner] = useState(params.get("owner") ?? "");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isPrivate, setPrivate] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void api.GET("/api/v3/user/orgs").then(({data}) => {
      if (active) setOrganizations(data ?? []);
    });
    return () => {
      active = false;
    };
  }, []);

  const login = user?.login ?? "";
  const selected = owner || login;
  const ownerIsUser = selected === login;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = {name, description, private: isPrivate, auto_init: true};
    const {data, response} = ownerIsUser
      ? await api.POST("/api/v3/user/repos", {body})
      : await api.POST("/api/v3/orgs/{org}/repos", {
          params: {path: {org: selected}},
          body,
        });
    if (!data || !response.ok) {
      return setError(
        response.status === 403
          ? `You cannot create repositories in ${selected}.`
          : "Could not create repository.",
      );
    }
    await navigate(`/${data.full_name}`);
  }

  return (
    <form className="editor-form" onSubmit={(event) => void submit(event)}>
      <h1>Create a new repository</h1>
      {error ? <p className="flash-error">{error}</p> : null}
      <label>
        Owner
        <select
          value={selected}
          onChange={(event) => setOwner(event.target.value)}
        >
          {login ? <option value={login}>{login} (you)</option> : null}
          {organizations.map((organization) => (
            <option value={organization.login} key={organization.id}>
              {organization.login}
            </option>
          ))}
        </select>
      </label>
      <label>
        Repository name
        <input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <p className="muted">
        Will be created as{" "}
        <strong>
          {selected || "…"}/{name || "…"}
        </strong>
      </p>
      <label>
        Description
        <input
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <label className="check-label">
        <input
          type="checkbox"
          checked={isPrivate}
          onChange={(event) => setPrivate(event.target.checked)}
        />{" "}
        Private repository
      </label>
      <button className="button" type="submit">
        Create repository
      </button>
    </form>
  );
}
