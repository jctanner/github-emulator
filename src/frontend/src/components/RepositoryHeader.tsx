import {Link, useLocation} from "react-router-dom";

import type {components} from "../api/schema";
import {Octicon} from "./Octicon";

type Repository = components["schemas"]["RepoResponse"];
type Navigation = components["schemas"]["RepositoryNavigationResponse"];

export function RepositoryHeader({
  repository,
  navigation,
}: {
  repository: Repository;
  navigation: Navigation | null;
}) {
  const owner = repository.owner.login;
  const repo = repository.name;
  const root = `/${owner}/${repo}`;
  const location = useLocation();
  const tabs = [
    {
      label: "Code",
      to: root,
      icon: "code" as const,
      match: "code",
      count: undefined,
    },
    {
      label: "Issues",
      to: `${root}/issues`,
      icon: "issue" as const,
      match: "issues",
      count: navigation?.open_issues_count,
    },
    {
      label: "Pull requests",
      to: `${root}/pulls`,
      icon: "pull-request" as const,
      match: "pulls",
      count: navigation?.open_pulls_count,
    },
    {
      label: "Actions",
      to: `${root}/actions`,
      icon: "history" as const,
      match: "actions",
      count: undefined,
    },
    {
      label: "Settings",
      to: `${root}/settings`,
      icon: "gear" as const,
      match: "settings",
      count: undefined,
    },
  ];
  const section =
    location.pathname.slice(root.length + 1).split("/")[0] || "code";

  return (
    <div className="repo-context">
      <div className="repo-context-inner">
        <div className="repo-heading">
          <Octicon name="book" />
          <Link to={`/${owner}`}>{owner}</Link>
          <span>/</span>
          <Link to={root}>
            <strong>{repo}</strong>
          </Link>
          <span className="badge visibility-badge">
            {repository.visibility[0].toUpperCase() +
              repository.visibility.slice(1)}
          </span>
        </div>
        <nav className="repo-nav" aria-label="Repository">
          {tabs.map((tab) => (
            <Link
              className={section === tab.match ? "selected" : undefined}
              to={tab.to}
              key={tab.label}
            >
              <Octicon name={tab.icon} />
              {tab.label}
              {tab.count !== undefined ? (
                <span className="repo-nav-count">{tab.count}</span>
              ) : null}
            </Link>
          ))}
        </nav>
      </div>
    </div>
  );
}
