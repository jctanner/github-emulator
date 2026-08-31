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
type AppInstallation = components["schemas"]["AdminInstallationResponse"];

interface AppInstallationOptions {
  accounts: string[];
  repositories: Repository[];
}

type AppPermissionName = "contents" | "issues" | "pull_requests" | "metadata";
type AppPermissionLevel = "" | "read" | "write";

const APP_PERMISSION_FIELDS: {
  name: AppPermissionName;
  label: string;
  description: string;
  readOnly?: boolean;
}[] = [
  {
    name: "contents",
    label: "Repository contents",
    description: "Source code, commits, branches, and files.",
  },
  {
    name: "issues",
    label: "Issues",
    description: "Issues, labels, assignees, and comments.",
  },
  {
    name: "pull_requests",
    label: "Pull requests",
    description: "Pull requests, reviews, and review comments.",
  },
  {
    name: "metadata",
    label: "Metadata",
    description: "Basic repository information required by GitHub Apps.",
    readOnly: true,
  },
];

function defaultAppPermissions(): Record<AppPermissionName, AppPermissionLevel> {
  return {contents: "", issues: "", pull_requests: "", metadata: "read"};
}

function privateKeyFrom(value: unknown): string | null {
  if (
    typeof value === "object" &&
    value !== null &&
    "private_key" in value &&
    typeof value.private_key === "string"
  ) {
    return value.private_key;
  }
  return null;
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

function AppDetails({
  appId,
  initialSecret,
  onChanged,
}: {
  appId: string;
  initialSecret?: string | null;
  onChanged: () => void;
}) {
  const value = useApiData<App>(`admin-app:${appId}`, async () => {
    const {data, response} = await api.GET("/admin/api/apps/{app_id}", {
      params: {path: {app_id: appId}},
    });
    return requireApiData(data, response, "Could not load App details.");
  });
  const [secret, setSecret] = useState<string | null>(initialSecret ?? null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function showPrivateKey() {
    const {data, response} = await api.GET(
      "/admin/api/apps/{app_id}/private-key",
      {params: {path: {app_id: appId}}},
    );
    const privateKey = privateKeyFrom(data);
    if (!response.ok || !privateKey) {
      return setActionError("Could not load the private key.");
    }
    setActionError(null);
    setSecret(privateKey);
  }

  async function regeneratePrivateKey() {
    if (
      !globalThis.confirm(
        "Regenerate this private key? Existing copies will stop working.",
      )
    ) {
      return;
    }
    const {data, response} = await api.POST(
      "/admin/api/apps/{app_id}/private-key/regenerate",
      {params: {path: {app_id: appId}}},
    );
    const privateKey = privateKeyFrom(data);
    if (!response.ok || !privateKey) {
      return setActionError("Could not regenerate the private key.");
    }
    setActionError(null);
    setSecret(privateKey);
    value.reload();
  }

  async function removeInstallation(installation: AppInstallation) {
    const {response} = await api.DELETE(
      "/admin/api/apps/{app_id}/installations/{installation_id}",
      {
        params: {
          path: {app_id: appId, installation_id: installation.id},
        },
      },
    );
    if (!response.ok) {
      return setActionError("Could not remove the installation.");
    }
    setActionError(null);
    value.reload();
    onChanged();
  }

  return (
    <Loadable loading={value.loading} error={value.error}>
      {value.data ? (
        <section
          className="admin-app-details"
          aria-label={`${value.data.name} details`}
        >
          <div className="admin-app-details-heading">
            <div>
              <h2>{value.data.name}</h2>
              <p>Registration and installation details for this GitHub App.</p>
            </div>
            <div className="button-row">
              <button onClick={() => void showPrivateKey()}>
                View private key
              </button>
              <button onClick={() => void regeneratePrivateKey()}>
                Regenerate private key
              </button>
            </div>
          </div>
          {actionError ? <p className="flash-error">{actionError}</p> : null}
          {secret ? (
            <div className="app-private-key">
              <p>
                <strong>Private key</strong> — copy and store this securely.
              </p>
              <pre className="one-time-secret">{secret}</pre>
            </div>
          ) : null}
          <dl className="app-detail-grid">
            <div>
              <dt>Owner</dt>
              <dd>{value.data.owner}</dd>
            </div>
            <div>
              <dt>Slug</dt>
              <dd>{value.data.slug}</dd>
            </div>
            <div>
              <dt>App ID</dt>
              <dd>{value.data.app_id}</dd>
            </div>
            <div>
              <dt>Client ID</dt>
              <dd>{value.data.client_id}</dd>
            </div>
            <div>
              <dt>Private key</dt>
              <dd>{value.data.has_private_key ? "Configured" : "Not configured"}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{value.data.created_at ?? "Unknown"}</dd>
            </div>
          </dl>
          <div className="app-installations-heading">
            <h3>Installations</h3>
            <span>{value.data.installations_count}</span>
          </div>
          {value.data.installations?.length ? (
            <div className="app-installation-list">
              {value.data.installations.map((installation) => (
                <div
                  className="admin-app-installation-row"
                  key={installation.id}
                >
                  <div>
                    <strong>{installation.owner}</strong>
                    <span>
                      {installation.repositories.length
                        ? installation.repositories.join(", ")
                        : "All repositories"}
                    </span>
                  </div>
                  <div className="app-installation-meta">
                    <span>Installation #{installation.id}</span>
                    <span>{installation.created_at ?? "Unknown date"}</span>
                  </div>
                  <button onClick={() => void removeInstallation(installation)}>
                    Remove
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-note">This App has no installations.</p>
          )}
        </section>
      ) : null}
    </Loadable>
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
  const [slug, setSlug] = useState("");
  const [registrationAppId, setRegistrationAppId] = useState("");
  const [permissions, setPermissions] = useState(defaultAppPermissions);
  const [registrationError, setRegistrationError] = useState<string | null>(null);
  const [registrationOpen, setRegistrationOpen] = useState(false);
  const [owner, setOwner] = useState("");
  const [repo, setRepo] = useState("");
  const [appId, setAppId] = useState("");
  const [selectedAppId, setSelectedAppId] = useState<string | null>(null);
  const [detailRevision, setDetailRevision] = useState(0);
  const [createdSecret, setCreatedSecret] = useState<{
    appId: string;
    privateKey: string;
  } | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);
  const repositoryOptions = installationOptions.data?.repositories.filter(
    (repository) => repository.full_name.startsWith(`${owner}/`),
  );
  async function create(event: FormEvent) {
    event.preventDefault();
    const selectedPermissions = Object.fromEntries(
      Object.entries(permissions).filter(([, level]) => level),
    );
    const body: Record<string, unknown> = {
      name,
      permissions: selectedPermissions,
    };
    if (slug.trim()) body.slug = slug.trim();
    if (registrationAppId.trim()) body.app_id = registrationAppId.trim();
    const {data, response} = await api.POST("/admin/api/apps", {body});
    if (!response.ok || !data) {
      return setRegistrationError("Could not register the GitHub App.");
    }
    if (data.private_key) {
      setCreatedSecret({appId: data.app_id, privateKey: data.private_key});
    }
    setRegistrationError(null);
    setName("");
    setSlug("");
    setRegistrationAppId("");
    setPermissions(defaultAppPermissions());
    setSelectedAppId(data.app_id);
    setRegistrationOpen(false);
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
    setSelectedAppId(appId);
    setDetailRevision((value) => value + 1);
    values.reload();
  }
  async function remove(id: string) {
    await api.DELETE("/admin/api/apps/{app_id}", {
      params: {path: {app_id: id}},
    });
    if (selectedAppId === id) setSelectedAppId(null);
    values.reload();
  }
  return (
    <>
      <h1>GitHub Apps</h1>
      <button
        className="button app-registration-trigger"
        onClick={() => setRegistrationOpen(true)}
      >
        Register new GitHub App
      </button>
      {registrationOpen ? (
        <div
          className="app-registration-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setRegistrationOpen(false);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") setRegistrationOpen(false);
          }}
        >
          <section
            aria-labelledby="register-github-app-title"
            aria-modal="true"
            className="app-registration-modal"
            role="dialog"
          >
            <div className="app-registration-heading">
              <div>
                <h2 id="register-github-app-title">Register a GitHub App</h2>
                <p>
                  Configure the identity and repository access for this App.
                </p>
              </div>
              <button
                aria-label="Close registration dialog"
                className="app-registration-close"
                onClick={() => setRegistrationOpen(false)}
              >
                ×
              </button>
            </div>
            <form
              className="app-registration-form"
              onSubmit={(event) => void create(event)}
            >
              {registrationError ? (
                <p className="flash-error">{registrationError}</p>
              ) : null}
              <label>
                <span>
                  GitHub App name <span className="required-marker">*</span>
                </span>
                <input
                  autoFocus
                  required
                  placeholder="Fullsend Triage"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <div className="form-grid">
                <label>
                  <span>
                    Slug <span className="optional-marker">Optional</span>
                  </span>
                  <input
                    placeholder="Generated from the name"
                    value={slug}
                    onChange={(event) => setSlug(event.target.value)}
                  />
                </label>
                <label>
                  <span>
                    App ID <span className="optional-marker">Optional</span>
                  </span>
                  <input
                    placeholder="Generated automatically"
                    value={registrationAppId}
                    onChange={(event) => setRegistrationAppId(event.target.value)}
                  />
                </label>
              </div>
              <fieldset className="app-permissions-fieldset">
                <legend>Repository permissions</legend>
                <p>
                  Choose the minimum access this App needs. Write access also
                  includes read access.
                </p>
                <div className="app-permission-list">
                  {APP_PERMISSION_FIELDS.map((permission) => (
                    <label className="app-permission-row" key={permission.name}>
                      <span>
                        <strong>{permission.label}</strong>
                        <small>{permission.description}</small>
                      </span>
                      <select
                        aria-label={permission.label}
                        value={permissions[permission.name]}
                        disabled={permission.readOnly}
                        onChange={(event) =>
                          setPermissions((current) => ({
                            ...current,
                            [permission.name]: event.target
                              .value as AppPermissionLevel,
                          }))
                        }
                      >
                        {!permission.readOnly ? (
                          <option value="">No access</option>
                        ) : null}
                        <option value="read">Read-only</option>
                        {!permission.readOnly ? (
                          <option value="write">Read and write</option>
                        ) : null}
                      </select>
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="app-registration-actions">
                <button type="button" onClick={() => setRegistrationOpen(false)}>
                  Cancel
                </button>
                <button className="button">Create GitHub App</button>
              </div>
            </form>
          </section>
        </div>
      ) : null}
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
          <div className="list-row admin-app-row" key={value.app_id}>
            <strong>{value.name}</strong>
            <span>
              {value.slug} · {value.installations_count} installations
            </span>
            <div className="button-row">
              <button
                aria-expanded={selectedAppId === value.app_id}
                onClick={() =>
                  setSelectedAppId((current) =>
                    current === value.app_id ? null : value.app_id,
                  )
                }
              >
                {selectedAppId === value.app_id ? "Hide details" : "View details"}
              </button>
              <button onClick={() => void remove(value.app_id)}>Delete</button>
            </div>
          </div>
        )}
      />
      {selectedAppId ? (
        <AppDetails
          key={`${selectedAppId}:${detailRevision}`}
          appId={selectedAppId}
          initialSecret={
            createdSecret?.appId === selectedAppId
              ? createdSecret.privateKey
              : null
          }
          onChanged={() => values.reload()}
        />
      ) : null}
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
