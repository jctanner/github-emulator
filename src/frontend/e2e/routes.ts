export type ParityState = "fallback" | "candidate" | "parity";

export interface ParityRoute {
  id: string;
  path: string;
  state: ParityState;
}

const owner = process.env.PARITY_OWNER ?? "fullsend-dev";
const repository = process.env.PARITY_REPOSITORY ?? "triage-target";
const issue = process.env.PARITY_ISSUE ?? "1";
const pull = process.env.PARITY_PULL ?? "__latest_pull__";
const run = process.env.PARITY_RUN ?? "__latest_run__";
const blob = process.env.PARITY_BLOB ?? "README.md";

export const parityFixture = {owner, repository};

export const parityRoutes: ParityRoute[] = [
  {id: "landing", path: "/", state: "candidate"},
  {id: "login", path: "/login", state: "candidate"},
  {id: "search", path: "/search?q=test", state: "candidate"},
  {id: "profile", path: `/${owner}`, state: "candidate"},
  {id: "repository", path: `/${owner}/${repository}`, state: "candidate"},
  {
    id: "repository-tree",
    path: `/${owner}/${repository}/tree/main/`,
    state: "candidate",
  },
  {
    id: "repository-blob",
    path: `/${owner}/${repository}/blob/main/${blob}`,
    state: "candidate",
  },
  {
    id: "commits",
    path: `/${owner}/${repository}/commits/main`,
    state: "candidate",
  },
  {
    id: "branches",
    path: `/${owner}/${repository}/branches`,
    state: "candidate",
  },
  {id: "tags", path: `/${owner}/${repository}/tags`, state: "candidate"},
  {id: "issues", path: `/${owner}/${repository}/issues`, state: "candidate"},
  {
    id: "settings-labels",
    path: `/${owner}/${repository}/settings/labels`,
    state: "candidate",
  },
  {
    id: "issue-detail",
    path: `/${owner}/${repository}/issues/${issue}`,
    state: "candidate",
  },
  {id: "pulls", path: `/${owner}/${repository}/pulls`, state: "candidate"},
  {
    id: "pull-detail",
    path: `/${owner}/${repository}/pulls/${pull}`,
    state: "candidate",
  },
  {id: "actions", path: `/${owner}/${repository}/actions`, state: "candidate"},
  {
    id: "action-run",
    path: `/${owner}/${repository}/actions/runs/${run}`,
    state: "candidate",
  },
  {
    id: "settings",
    path: `/${owner}/${repository}/settings`,
    state: "candidate",
  },
  {
    id: "settings-access",
    path: `/${owner}/${repository}/settings/access`,
    state: "candidate",
  },
  {
    id: "settings-branches",
    path: `/${owner}/${repository}/settings/branches`,
    state: "candidate",
  },
  {
    id: "settings-runners",
    path: `/${owner}/${repository}/settings/actions/runners`,
    state: "candidate",
  },
  {
    id: "settings-apps",
    path: `/${owner}/${repository}/settings/installations`,
    state: "candidate",
  },
  {id: "admin", path: "/_admin/", state: "candidate"},
];
