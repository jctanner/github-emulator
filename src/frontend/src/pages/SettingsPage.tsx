import {FormEvent, useState} from "react";
import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {RepositoryHeader} from "../components/RepositoryHeader";
import {RunnersPage} from "./RunnersPage";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Repository = components["schemas"]["RepoResponse"];
type Branch = components["schemas"]["BranchResponse"];
type Protection = components["schemas"]["BranchProtectionResponse"];
type Collaborator = components["schemas"]["CollaboratorResponse"];
type Installation = components["schemas"]["InstallationResponse"];

function formText(fields: FormData, name: string): string {
  const value = fields.get(name);
  return typeof value === "string" ? value : "";
}

function GeneralSettings({owner, repo}: {owner: string; repo: string}) {
  const repository = useApiData<Repository>(
    `settings:${owner}/${repo}`,
    async () => {
      const {data, response} = await api.GET("/api/v3/repos/{owner}/{repo}", {
        params: {path: {owner, repo}},
      });
      return requireApiData(
        data,
        response,
        "Could not load repository settings.",
      );
    },
  );
  const [message, setMessage] = useState<string | null>(null);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const {response} = await api.PATCH("/api/v3/repos/{owner}/{repo}", {
      params: {path: {owner, repo}},
      body: {
        description: formText(fields, "description"),
        homepage: formText(fields, "homepage"),
        private: fields.get("private") === "on",
        has_issues: fields.get("has_issues") === "on",
        has_wiki: fields.get("has_wiki") === "on",
      },
    });
    setMessage(
      response.ok ? "Repository settings saved." : "Could not save settings.",
    );
    if (response.ok) repository.reload();
  }
  return (
    <Loadable loading={repository.loading} error={repository.error}>
      {repository.data ? (
        <form
          className="editor-form settings-form"
          onSubmit={(event) => void save(event)}
        >
          <h1>General</h1>
          {message ? (
            <p
              className={
                message.startsWith("Could") ? "flash-error" : "flash-success"
              }
            >
              {message}
            </p>
          ) : null}
          <section className="settings-section">
            <h2>Repository name</h2>
            <label>
              <span className="sr-only">Repository name</span>
              <input disabled value={repository.data.name} />
            </label>
            <p className="muted">
              Renaming repositories is not currently supported by the emulator
              API.
            </p>
          </section>
          <section className="settings-section">
            <h2>Repository details</h2>
            <label>
              Description
              <input
                name="description"
                defaultValue={repository.data.description ?? ""}
              />
            </label>
            <label>
              Homepage
              <input
                name="homepage"
                defaultValue={repository.data.homepage ?? ""}
              />
            </label>
          </section>
          <section className="settings-section">
            <h2>Visibility</h2>
            <label className="check-label">
              <input
                name="private"
                type="checkbox"
                defaultChecked={repository.data.private}
              />{" "}
              Private repository
            </label>
            <p className="muted">
              Public repositories are visible to everyone. Private repositories
              limit access to collaborators.
            </p>
          </section>
          <section className="settings-section settings-feature-box">
            <h2>Features</h2>
            <label className="check-label">
              <input
                name="has_wiki"
                type="checkbox"
                defaultChecked={repository.data.has_wiki}
              />{" "}
              <span>
                <strong>Wikis</strong>
                <small>Host documentation for this repository.</small>
              </span>
            </label>
            <label className="check-label">
              <input
                name="has_issues"
                type="checkbox"
                defaultChecked={repository.data.has_issues}
              />{" "}
              <span>
                <strong>Issues</strong>
                <small>Track bugs, ideas, and work.</small>
              </span>
            </label>
            <label className="check-label settings-unavailable">
              <input disabled type="checkbox" />{" "}
              <span>
                <strong>Projects</strong>
                <small>Not yet available in the emulator API.</small>
              </span>
            </label>
            <label className="check-label settings-unavailable">
              <input disabled type="checkbox" />{" "}
              <span>
                <strong>Discussions</strong>
                <small>Not yet available in the emulator API.</small>
              </span>
            </label>
          </section>
          <button className="button" type="submit">
            Save changes
          </button>
        </form>
      ) : null}
    </Loadable>
  );
}

function BranchSettings({owner, repo}: {owner: string; repo: string}) {
  const branches = useApiData<Branch[]>(
    `settings-branches:${owner}/${repo}`,
    async () => {
      const {data, response} = await api.GET(
        "/api/v3/repos/{owner}/{repo}/branches",
        {params: {path: {owner, repo}}},
      );
      return requireApiData(data, response, "Could not load branches.");
    },
  );
  const [branch, setBranch] = useState("");
  const selected = branch || branches.data?.[0]?.name || "";
  const protection = useApiData<Protection | null>(
    `protection:${owner}/${repo}:${selected}`,
    async () => {
      if (!selected) return null;
      const {data, response} = await api.GET(
        "/api/v3/repos/{owner}/{repo}/branches/{branch}/protection",
        {params: {path: {owner, repo, branch: selected}}},
      );
      if (response.status === 404) return null;
      return requireApiData(
        data,
        response,
        "Could not load branch protection.",
      );
    },
  );
  const [message, setMessage] = useState<string | null>(null);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const contexts = formText(fields, "contexts")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    const {response} = await api.PUT(
      "/api/v3/repos/{owner}/{repo}/branches/{branch}/protection",
      {
        params: {path: {owner, repo, branch: selected}},
        body: {
          required_status_checks: contexts.length
            ? {strict: fields.get("strict") === "on", contexts}
            : null,
          required_pull_request_reviews:
            fields.get("reviews") === "on"
              ? {
                  required_approving_review_count: Number(
                    fields.get("review_count") ?? 1,
                  ),
                  dismiss_stale_reviews: fields.get("dismiss_stale") === "on",
                }
              : null,
          enforce_admins: fields.get("enforce_admins") === "on",
          required_linear_history: fields.get("linear") === "on",
          allow_force_pushes: fields.get("force") === "on",
          allow_deletions: fields.get("deletions") === "on",
          restrictions: null,
        },
      },
    );
    setMessage(
      response.ok
        ? "Branch protection saved."
        : "Could not save branch protection.",
    );
    if (response.ok) protection.reload();
  }
  const checks = protection.data?.required_status_checks as
    {contexts?: string[]} | null | undefined;
  const reviews = protection.data?.required_pull_request_reviews as
    | {
        required_approving_review_count?: number;
        dismiss_stale_reviews?: boolean;
      }
    | null
    | undefined;
  return (
    <>
      <h1>Branches</h1>
      <p className="muted">
        Protect important branches by requiring reviews, status checks, or a
        linear history before changes can be merged.
      </p>
      <Loadable loading={branches.loading} error={branches.error}>
        <section className="settings-section">
          <h2>Branch protection rules</h2>
          <div className="list-box">
            {branches.data?.map((item) => (
              <div className="list-row branch-rule-row" key={item.name}>
                <span>
                  <Octicon name="branch" /> <strong>{item.name}</strong>
                </span>
                <span className="badge">
                  {item.protected ? "Protected" : "Not protected"}
                </span>
              </div>
            ))}
          </div>
        </section>
        <form
          className="editor-form settings-form"
          key={`${selected}:${protection.loading}`}
          onSubmit={(event) => void save(event)}
        >
          <h2>Protect a branch</h2>
          <label>
            Branch
            <select
              value={selected}
              onChange={(event) => setBranch(event.target.value)}
            >
              {branches.data?.map((item) => (
                <option key={item.name}>{item.name}</option>
              ))}
            </select>
          </label>
          {message ? <p>{message}</p> : null}
          <div className="settings-rule-group">
            <h3>Require status checks to pass before merging</h3>
            <label>
              Status checks (comma separated)
              <input
                name="contexts"
                defaultValue={checks?.contexts?.join(", ") ?? ""}
              />
            </label>
            <label className="check-label">
              <input
                name="strict"
                type="checkbox"
                defaultChecked={Boolean(
                  protection.data?.required_status_checks &&
                  (protection.data.required_status_checks as {strict?: boolean})
                    .strict,
                )}
              />{" "}
              Require branches to be up to date
            </label>
          </div>
          <div className="settings-rule-group">
            <h3>Require a pull request before merging</h3>
            <label className="check-label">
              <input
                name="reviews"
                type="checkbox"
                defaultChecked={Boolean(reviews)}
              />{" "}
              Require pull request reviews
            </label>
            <label>
              Required approvals
              <input
                name="review_count"
                type="number"
                min="0"
                max="6"
                defaultValue={reviews?.required_approving_review_count ?? 1}
              />
            </label>
            <label className="check-label">
              <input
                name="dismiss_stale"
                type="checkbox"
                defaultChecked={reviews?.dismiss_stale_reviews ?? false}
              />{" "}
              Dismiss stale reviews
            </label>
          </div>
          <div className="settings-rule-group">
            <h3>Additional rules</h3>
            <label className="check-label">
              <input
                name="enforce_admins"
                type="checkbox"
                defaultChecked={Boolean(
                  protection.data?.enforce_admins.enabled,
                )}
              />{" "}
              Include administrators
            </label>
            <label className="check-label">
              <input
                name="linear"
                type="checkbox"
                defaultChecked={Boolean(
                  protection.data?.required_linear_history.enabled,
                )}
              />{" "}
              Require linear history
            </label>
            <label className="check-label">
              <input
                name="force"
                type="checkbox"
                defaultChecked={Boolean(
                  protection.data?.allow_force_pushes.enabled,
                )}
              />{" "}
              Allow force pushes
            </label>
          </div>
          <label className="check-label">
            <input
              name="deletions"
              type="checkbox"
              defaultChecked={Boolean(protection.data?.allow_deletions.enabled)}
            />{" "}
            Allow deletions
          </label>
          <button className="button" type="submit">
            Save branch protection
          </button>
        </form>
      </Loadable>
    </>
  );
}

function AccessSettings({owner, repo}: {owner: string; repo: string}) {
  const repository = useApiData<Repository>(
    `access-repo:${owner}/${repo}`,
    async () => {
      const {data, response} = await api.GET("/api/v3/repos/{owner}/{repo}", {
        params: {path: {owner, repo}},
      });
      return requireApiData(data, response, "Could not load repository.");
    },
  );
  const collaborators = useApiData<Collaborator[]>(
    `access:${owner}/${repo}`,
    async () => {
      const {data, response} = await api.GET(
        "/api/v3/repos/{owner}/{repo}/collaborators",
        {params: {path: {owner, repo}}},
      );
      return requireApiData(data, response, "Could not load collaborators.");
    },
  );
  const [username, setUsername] = useState("");
  const [permission, setPermission] = useState("push");
  async function add(event: FormEvent) {
    event.preventDefault();
    await api.PUT("/api/v3/repos/{owner}/{repo}/collaborators/{username}", {
      params: {path: {owner, repo, username}},
      body: {permission},
    });
    setUsername("");
    collaborators.reload();
  }
  async function remove(username: string) {
    await api.DELETE("/api/v3/repos/{owner}/{repo}/collaborators/{username}", {
      params: {path: {owner, repo, username}},
    });
    collaborators.reload();
  }
  return (
    <>
      <h1>Collaborators and teams</h1>
      <section className="access-summary">
        <Octicon name="book" />
        <div>
          <strong>
            {repository.data?.private
              ? "Private repository"
              : "Public repository"}
          </strong>
          <p className="muted">
            {repository.data?.private
              ? "Only collaborators can view this repository."
              : "This repository is public and visible to anyone."}
          </p>
        </div>
      </section>
      <section className="settings-section">
        <h2>Direct access</h2>
        <p className="muted">
          {collaborators.data?.length ?? 0} collaborators have direct access to
          this repository.
        </p>
      </section>
      <section className="settings-section">
        <div className="page-heading">
          <h2>Manage access</h2>
        </div>
        <form className="inline-editor" onSubmit={(event) => void add(event)}>
          <input
            required
            placeholder="Username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
          <select
            value={permission}
            onChange={(event) => setPermission(event.target.value)}
          >
            <option value="pull">Read</option>
            <option value="triage">Triage</option>
            <option value="push">Write</option>
            <option value="maintain">Maintain</option>
            <option value="admin">Admin</option>
          </select>
          <button className="button" type="submit">
            Add people
          </button>
        </form>
        <Loadable loading={collaborators.loading} error={collaborators.error}>
          <div className="list-box">
            {collaborators.data?.map((item) => (
              <div className="list-row label-row" key={item.id}>
                <strong>{item.login}</strong>
                <span>{item.role_name}</span>
                {item.login !== owner ? (
                  <button type="button" onClick={() => void remove(item.login)}>
                    Remove
                  </button>
                ) : (
                  <span>Owner</span>
                )}
              </div>
            ))}
          </div>
        </Loadable>
      </section>
    </>
  );
}

function AppSettings({owner, repo}: {owner: string; repo: string}) {
  const installations = useApiData<Installation[]>(
    `installations:${owner}/${repo}`,
    async () => {
      const {data, response} = await api.GET(
        "/api/v3/repos/{owner}/{repo}/installations",
        {params: {path: {owner, repo}}},
      );
      return requireApiData(data, response, "Could not load GitHub Apps.");
    },
  );
  return (
    <>
      <h1>GitHub Apps</h1>
      <p className="muted">
        GitHub Apps installed on this repository can act with the permissions
        shown below.
      </p>
      <Loadable loading={installations.loading} error={installations.error}>
        <div className="list-box app-installations">
          {installations.data?.map((item) => (
            <div className="list-row app-installation-row" key={item.id}>
              <div className="app-mark">
                <Octicon name="mark-github" />
              </div>
              <div>
                <h2>{item.app_slug}</h2>
                <p className="muted">Installation #{item.id}</p>
                <div className="permission-list">
                  {Object.entries(item.permissions).map(([name, value]) => (
                    <span className="badge" key={name}>
                      {name}: {String(value)}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ))}
          {installations.data?.length === 0 ? (
            <p className="empty">
              No GitHub Apps are installed for this repository.
            </p>
          ) : null}
        </div>
      </Loadable>
    </>
  );
}

export function SettingsPage() {
  const {owner = "", repo = "", "*": section = ""} = useParams();
  const current = section || "general";
  const root = `/${owner}/${repo}/settings`;
  return (
    <>
      <RepositoryHeader owner={owner} repo={repo} />
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Repository settings">
          <Link className={current === "general" ? "active" : ""} to={root}>
            <Octicon name="gear" /> General
          </Link>
          <span className="settings-nav-heading">Access</span>
          <Link
            className={current === "access" ? "active" : ""}
            to={`${root}/access`}
          >
            Collaborators and teams
          </Link>
          <span className="settings-nav-disabled">Moderation</span>
          <span className="settings-nav-heading">
            Code, planning, and automation
          </span>
          <span className="settings-nav-disabled">Rulesets</span>
          <Link
            className={current === "branches" ? "active" : ""}
            to={`${root}/branches`}
          >
            <Octicon name="branch" /> Branches
          </Link>
          <Link
            className={current === "actions/runners" ? "active" : ""}
            to={`${root}/actions/runners`}
          >
            Actions
          </Link>
          <span className="settings-nav-disabled">Webhooks</span>
          <span className="settings-nav-disabled">Tags</span>
          <span className="settings-nav-heading">Security and quality</span>
          <span className="settings-nav-disabled">Deploy keys</span>
          <span className="settings-nav-disabled">Secrets and variables</span>
          <span className="settings-nav-heading">Integrations</span>
          <Link
            className={current === "installations" ? "active" : ""}
            to={`${root}/installations`}
          >
            GitHub Apps
          </Link>
          <span className="settings-nav-disabled">Email notifications</span>
        </nav>
        <section className="settings-content">
          {current === "access" ? (
            <AccessSettings owner={owner} repo={repo} />
          ) : current === "branches" ? (
            <BranchSettings owner={owner} repo={repo} />
          ) : current === "actions/runners" ? (
            <RunnersPage embedded />
          ) : current === "installations" ? (
            <AppSettings owner={owner} repo={repo} />
          ) : (
            <GeneralSettings owner={owner} repo={repo} />
          )}
        </section>
      </div>
    </>
  );
}
