import {Link, useLocation} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {requireApiData, useApiData} from "../hooks/useApiData";
import {Octicon} from "./Octicon";

type Repository = components["schemas"]["RepoResponse"];

type RepositoryHeaderProps =
  | {repository: Repository; owner?: never; repo?: never}
  | {repository?: never; owner: string; repo: string};

export function RepositoryHeader(props: RepositoryHeaderProps) {
  const owner = props.repository?.owner.login ?? props.owner ?? "";
  const repo = props.repository?.name ?? props.repo ?? "";
  const root = `/${owner}/${repo}`;
  const location = useLocation();
  const metadata = useApiData<Repository>(
    `repo-header:${owner}/${repo}`,
    async () => {
      if (props.repository) return props.repository;
      const {data, response} = await api.GET("/api/v3/repos/{owner}/{repo}", {
        params: {path: {owner, repo}},
      });
      return requireApiData(data, response, "Could not load repository.");
    },
  );
  const repository = props.repository ?? metadata.data;
  const tabs = [
    {label: "Code", to: root, icon: "code" as const, match: "code"},
    {
      label: "Issues",
      to: `${root}/issues`,
      icon: "issue" as const,
      match: "issues",
    },
    {
      label: "Pull requests",
      to: `${root}/pulls`,
      icon: "pull-request" as const,
      match: "pulls",
    },
    {
      label: "Actions",
      to: `${root}/actions`,
      icon: "history" as const,
      match: "actions",
    },
    {
      label: "Settings",
      to: `${root}/settings`,
      icon: "gear" as const,
      match: "settings",
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
          {repository ? (
            <span className="badge visibility-badge">
              {repository.visibility[0].toUpperCase() +
                repository.visibility.slice(1)}
            </span>
          ) : null}
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
            </Link>
          ))}
        </nav>
      </div>
    </div>
  );
}
