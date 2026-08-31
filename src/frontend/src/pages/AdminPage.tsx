import {FormEvent, ReactNode, useState} from "react";
import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Summary = components["schemas"]["AdminSummaryResponse"];
type User = components["schemas"]["AdminUserResponse"];
type Organization = components["schemas"]["AdminOrganizationResponse"];
type Repository = components["schemas"]["AdminRepositoryResponse"];
type Token = components["schemas"]["AdminTokenResponse"];
type Runner = components["schemas"]["AdminRunnerResponse"];
type Import = components["schemas"]["AdminImportResponse"];
type Issue = components["schemas"]["AdminIssueResponse"];
type App = components["schemas"]["AdminAppResponse"];

interface AppInstallationOptions {
  accounts: string[];
  repositories: Repository[];
}

function AdminList<T>({
  loading,
  error,
  values,
  row,
}: {
  loading: boolean;
  error: string | null;
  values: T[] | null | undefined;
  row: (value: T) => ReactNode;
}) {
  return (
    <Loadable loading={loading} error={error}>
      <div className="list-box">{values?.map(row)}</div>
    </Loadable>
  );
}

function Dashboard() {
  const data = useApiData<Summary>("admin-summary", async () => {
    const {data, response} = await api.GET("/admin/api/summary");
    return requireApiData(data, response, "Could not load admin summary.");
  });
  return (
    <>
      <h1>Site administration</h1>
      <Loadable loading={data.loading} error={data.error}>
        <div className="stat-grid">
          {data.data
            ? Object.entries(data.data).map(([name, value]) => (
                <div className="stat-card" key={name}>
                  <strong>{value}</strong>
                  <span>{name.replaceAll("_", " ")}</span>
                </div>
              ))
            : null}
        </div>
      </Loadable>
    </>
  );
}

function Users() {
  const values = useApiData<User[]>("admin-users", async () => {
    const {data, response} = await api.GET("/admin/api/users");
    return requireApiData(data, response, "Could not load users.");
  });
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  async function create(event: FormEvent) {
    event.preventDefault();
    await api.POST("/admin/api/users", {body: {login, password}});
    setLogin("");
    setPassword("");
    values.reload();
  }
  async function edit(value: User) {
    const name = globalThis.prompt("Display name", value.name ?? "");
    if (name === null) return;
    await api.PATCH("/admin/api/users/{user_id}", {
      params: {path: {user_id: value.id}},
      body: {name},
    });
    values.reload();
  }
  async function remove(id: number) {
    await api.DELETE("/admin/api/users/{user_id}", {
      params: {path: {user_id: id}},
    });
    values.reload();
  }
  return (
    <>
      <h1>Users</h1>
      <form className="inline-editor" onSubmit={(event) => void create(event)}>
        <input
          required
          placeholder="Login"
          value={login}
          onChange={(event) => setLogin(event.target.value)}
        />
        <input
          required
          type="password"
          placeholder="Password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <button className="button">Create user</button>
      </form>
      <AdminList
        {...values}
        values={values.data}
        row={(value) => (
          <div className="list-row label-row" key={value.id}>
            <strong>{value.login}</strong>
            <span>
              {value.name} {value.site_admin ? "· Site admin" : ""}
            </span>
            <div className="button-row">
              <button onClick={() => void edit(value)}>Edit</button>
              <button onClick={() => void remove(value.id)}>Delete</button>
            </div>
          </div>
        )}
      />
    </>
  );
}

function Organizations() {
  const values = useApiData<Organization[]>("admin-orgs", async () => {
    const {data, response} = await api.GET("/admin/api/organizations");
    return requireApiData(data, response, "Could not load organizations.");
  });
  const [login, setLogin] = useState("");
  async function create(event: FormEvent) {
    event.preventDefault();
    await api.POST("/admin/api/organizations", {body: {login}});
    setLogin("");
    values.reload();
  }
  async function edit(value: Organization) {
    const name = globalThis.prompt("Organization name", value.name ?? "");
    if (name === null) return;
    await api.PATCH("/admin/api/organizations/{org_id}", {
      params: {path: {org_id: value.id}},
      body: {name},
    });
    values.reload();
  }
  async function remove(id: number) {
    await api.DELETE("/admin/api/organizations/{org_id}", {
      params: {path: {org_id: id}},
    });
    values.reload();
  }
  return (
    <>
      <h1>Organizations</h1>
      <form className="inline-editor" onSubmit={(event) => void create(event)}>
        <input
          required
          placeholder="Organization login"
          value={login}
          onChange={(event) => setLogin(event.target.value)}
        />
        <button className="button">Create organization</button>
      </form>
      <AdminList
        {...values}
        values={values.data}
        row={(value) => (
          <div className="list-row label-row" key={value.id}>
            <strong>{value.login}</strong>
            <span>{value.name}</span>
            <div className="button-row">
              <button onClick={() => void edit(value)}>Edit</button>
              <button onClick={() => void remove(value.id)}>Delete</button>
            </div>
          </div>
        )}
      />
    </>
  );
}

function Repositories() {
  const values = useApiData<Repository[]>("admin-repos", async () => {
    const {data, response} = await api.GET("/admin/api/repositories");
    return requireApiData(data, response, "Could not load repositories.");
  });
  async function remove(id: number) {
    await api.DELETE("/admin/api/repositories/{repo_id}", {
      params: {path: {repo_id: id}},
    });
    values.reload();
  }
  return (
    <>
      <h1>Repositories</h1>
      <AdminList
        {...values}
        values={values.data}
        row={(value) => (
          <div className="list-row label-row" key={value.id}>
            <Link to={`/${value.full_name}`}>
              <strong>{value.full_name}</strong>
            </Link>
            <span>
              {value.private ? "Private" : "Public"} · {value.default_branch}
            </span>
            <button onClick={() => void remove(value.id)}>Delete</button>
          </div>
        )}
      />
    </>
  );
}

function Tokens() {
  const values = useApiData<Token[]>("admin-tokens", async () => {
    const {data, response} = await api.GET("/admin/api/tokens");
    return requireApiData(data, response, "Could not load tokens.");
  });
  const users = useApiData<User[]>("admin-token-users", async () => {
    const {data, response} = await api.GET("/admin/api/users");
    return requireApiData(data, response, "Could not load users.");
  });
  const [userId, setUserId] = useState("");
  const [name, setName] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  async function create(event: FormEvent) {
    event.preventDefault();
    const {data} = await api.POST("/admin/api/tokens", {
      body: {user_id: Number(userId), name, scopes: ["repo", "workflow"]},
    });
    if (data) setCreated(data.token);
    values.reload();
  }
  async function remove(id: number) {
    await api.DELETE("/admin/api/tokens/{token_id}", {
      params: {path: {token_id: id}},
    });
    values.reload();
  }
  return (
    <>
      <h1>Personal access tokens</h1>
      {created ? (
        <p className="one-time-secret">
          <strong>Copy this token now:</strong> <code>{created}</code>
        </p>
      ) : null}
      <form className="inline-editor" onSubmit={(event) => void create(event)}>
        <select
          required
          value={userId}
          onChange={(event) => setUserId(event.target.value)}
        >
          <option value="">Owner</option>
          {users.data?.map((user) => (
            <option value={user.id} key={user.id}>
              {user.login}
            </option>
          ))}
        </select>
        <input
          required
          placeholder="Token name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <button className="button">Create token</button>
      </form>
      <AdminList
        {...values}
        values={values.data}
        row={(value) => (
          <div className="list-row label-row" key={value.id}>
            <strong>{value.name}</strong>
            <span>
              {value.owner} · {value.token_prefix}
            </span>
            <button onClick={() => void remove(value.id)}>Revoke</button>
          </div>
        )}
      />
    </>
  );
}

function Apps() {
  const values = useApiData<App[]>("admin-apps", async () => {
    const {data, response} = await api.GET("/admin/api/apps");
    return requireApiData(data, response, "Could not load Apps.");
  });
  const installationOptions = useApiData<AppInstallationOptions>(
    "admin-app-installation-options",
    async () => {
      const [usersResult, organizationsResult, repositoriesResult] =
        await Promise.all([
          api.GET("/admin/api/users"),
          api.GET("/admin/api/organizations"),
          api.GET("/admin/api/repositories"),
        ]);
      const users = requireApiData(
        usersResult.data,
        usersResult.response,
        "Could not load users.",
      );
      const organizations = requireApiData(
        organizationsResult.data,
        organizationsResult.response,
        "Could not load organizations.",
      );
      const repositories = requireApiData(
        repositoriesResult.data,
        repositoriesResult.response,
        "Could not load repositories.",
      );
      return {
        accounts: [
          ...new Set([
            ...users.map((user) => user.login),
            ...organizations.map((organization) => organization.login),
            ...repositories.map((repository) =>
              repository.full_name.split("/", 1)[0],
            ),
          ]),
        ].sort((left, right) => left.localeCompare(right)),
        repositories,
      };
    },
  );
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [repo, setRepo] = useState("");
  const [appId, setAppId] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);
  const repositoryOptions = installationOptions.data?.repositories.filter(
    (repository) => repository.full_name.startsWith(`${owner}/`),
  );
  async function create(event: FormEvent) {
    event.preventDefault();
    const {data} = await api.POST("/admin/api/apps", {body: {name}});
    if (data?.private_key) setSecret(data.private_key);
    setName("");
    values.reload();
  }
  async function install(event: FormEvent) {
    event.preventDefault();
    if (!installationOptions.data?.accounts.includes(owner)) {
      return setInstallError("Select an account from the available options.");
    }
    if (
      !repositoryOptions?.some(
        (repository) => repository.full_name === `${owner}/${repo}`,
      )
    ) {
      return setInstallError(
        "Select a repository belonging to the selected account.",
      );
    }
    const {response} = await api.POST(
      "/admin/api/apps/{app_id}/installations",
      {
        params: {path: {app_id: appId}},
        body: {owner, repo},
      },
    );
    if (!response.ok) return setInstallError("Could not create installation.");
    setInstallError(null);
    setRepo("");
    values.reload();
  }
  async function remove(id: string) {
    await api.DELETE("/admin/api/apps/{app_id}", {
      params: {path: {app_id: id}},
    });
    values.reload();
  }
  return (
    <>
      <h1>GitHub Apps</h1>
      {secret ? <pre className="one-time-secret">{secret}</pre> : null}
      <form className="inline-editor" onSubmit={(event) => void create(event)}>
        <input
          required
          placeholder="App name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <button className="button">Register App</button>
      </form>
      {installationOptions.error ? (
        <p className="flash-error">{installationOptions.error}</p>
      ) : null}
      {installError ? <p className="flash-error">{installError}</p> : null}
      <form
        className="inline-editor app-installation-editor"
        onSubmit={(event) => void install(event)}
      >
        <label className="searchable-field">
          <span>App</span>
          <select
            required
            value={appId}
            onChange={(event) => setAppId(event.target.value)}
          >
            <option value="">Select an App</option>
            {values.data?.map((app) => (
              <option key={app.app_id} value={app.app_id}>
                {app.name}
              </option>
            ))}
          </select>
        </label>
        <label className="searchable-field">
          <span>Account</span>
          <input
            required
            autoComplete="off"
            list="app-installation-accounts"
            placeholder="Search accounts..."
            value={owner}
            onChange={(event) => {
              setOwner(event.target.value);
              setRepo("");
              setInstallError(null);
            }}
          />
        </label>
        <datalist id="app-installation-accounts">
          {installationOptions.data?.accounts.map((account) => (
            <option key={account} value={account} />
          ))}
        </datalist>
        <label className="searchable-field">
          <span>Repository</span>
          <input
            required
            autoComplete="off"
            disabled={!owner}
            list="app-installation-repositories"
            placeholder={owner ? "Search repositories..." : "Select account first"}
            value={repo}
            onChange={(event) => {
              setRepo(event.target.value);
              setInstallError(null);
            }}
          />
        </label>
        <datalist id="app-installation-repositories">
          {repositoryOptions?.map((repository) => (
            <option key={repository.id} value={repository.name} />
          ))}
        </datalist>
        <button className="button" disabled={installationOptions.loading}>
          Create installation
        </button>
      </form>
      <AdminList
        {...values}
        values={values.data}
        row={(value) => (
          <div className="list-row label-row" key={value.app_id}>
            <strong>{value.name}</strong>
            <span>
              {value.slug} · {value.installations_count} installations
            </span>
            <button onClick={() => void remove(value.app_id)}>Delete</button>
          </div>
        )}
      />
    </>
  );
}

function Runners() {
  const values = useApiData<Runner[]>("admin-runners", async () => {
    const {data, response} = await api.GET("/admin/api/runners");
    return requireApiData(data, response, "Could not load runners.");
  });
  return (
    <>
      <h1>Actions runners</h1>
      <AdminList
        {...values}
        values={values.data}
        row={(value) => (
          <div className="list-row label-row" key={value.id}>
            <strong>{value.name}</strong>
            <span>
              {value.scope} · {value.status}
              {value.busy ? " · busy" : ""}
            </span>
            <span>{value.os}</span>
          </div>
        )}
      />
    </>
  );
}

function Imports() {
  const values = useApiData<Import[]>("admin-imports", async () => {
    const {data, response} = await api.GET("/admin/api/imports");
    return requireApiData(data, response, "Could not load imports.");
  });
  const [source, setSource] = useState("");
  async function create(event: FormEvent) {
    event.preventDefault();
    await api.POST("/admin/api/imports", {body: {source_url: source}});
    setSource("");
    values.reload();
  }
  return (
    <>
      <h1>Repository imports</h1>
      <form className="inline-editor" onSubmit={(event) => void create(event)}>
        <input
          required
          placeholder="https://github.com/owner/repository"
          value={source}
          onChange={(event) => setSource(event.target.value)}
        />
        <button className="button">Start import</button>
      </form>
      <AdminList
        {...values}
        values={values.data}
        row={(value) => (
          <div className="list-row label-row" key={value.id}>
            <strong>{value.repo_name ?? value.source_url}</strong>
            <span>
              {value.owner} · {value.status}
            </span>
            <span>{value.error_message}</span>
          </div>
        )}
      />
    </>
  );
}

function Issues() {
  const values = useApiData<Issue[]>("admin-issues", async () => {
    const {data, response} = await api.GET("/admin/api/issues");
    return requireApiData(data, response, "Could not load issues.");
  });
  return (
    <>
      <h1>Issues and pull requests</h1>
      <AdminList
        {...values}
        values={values.data}
        row={(value) => (
          <div className="list-row" key={value.id}>
            <h2>
              <Link
                to={`/${value.repository}/${value.is_pull_request ? "pulls" : "issues"}/${value.number}`}
              >
                {value.title}
              </Link>
            </h2>
            <p>
              {value.repository} #{value.number} · {value.state}
            </p>
          </div>
        )}
      />
    </>
  );
}

export function AdminPage() {
  const {section = ""} = useParams();
  const current = section || "dashboard";
  const content =
    current === "users" ? (
      <Users />
    ) : current === "organizations" ? (
      <Organizations />
    ) : current === "repositories" ? (
      <Repositories />
    ) : current === "tokens" ? (
      <Tokens />
    ) : current === "apps" ? (
      <Apps />
    ) : current === "runners" ? (
      <Runners />
    ) : current === "imports" ? (
      <Imports />
    ) : current === "issues" ? (
      <Issues />
    ) : (
      <Dashboard />
    );
  const links = [
    ["dashboard", "Overview"],
    ["users", "Users"],
    ["organizations", "Organizations"],
    ["repositories", "Repositories"],
    ["tokens", "Tokens"],
    ["apps", "GitHub Apps"],
    ["runners", "Runners"],
    ["issues", "Issues"],
    ["imports", "Imports"],
  ];
  return (
    <div className="settings-layout admin-layout">
      <nav className="settings-nav" aria-label="Site administration">
        {links.map(([path, label]) => (
          <Link
            className={current === path ? "active" : ""}
            key={path}
            to={path === "dashboard" ? "/_admin/" : `/_admin/${path}`}
          >
            {label}
          </Link>
        ))}
      </nav>
      <section className="settings-content">{content}</section>
    </div>
  );
}
