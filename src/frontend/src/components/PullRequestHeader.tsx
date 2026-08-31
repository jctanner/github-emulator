import {NavLink} from "react-router-dom";

import type {components} from "../api/schema";

type Pull = components["schemas"]["PRResponse"];

export function PullRequestHeader({
  owner,
  repo,
  pull,
}: {
  owner: string;
  repo: string;
  pull: Pull;
}) {
  const root = `/${owner}/${repo}/pulls/${pull.number}`;
  return (
    <>
      <header className="conversation-heading">
        <h1>
          {pull.title} <span className="muted">#{pull.number}</span>
        </h1>
        <span className={`state state-${pull.state}`}>{pull.state}</span>
        <p>
          {pull.head.label} wants to merge into {pull.base.label}
        </p>
      </header>
      <nav className="pr-tabs" aria-label="Pull request">
        <NavLink end to={root}>
          Conversation
        </NavLink>
        <NavLink to={`${root}/commits`}>Commits {pull.commits}</NavLink>
        <NavLink to={`${root}/files`}>
          Files changed {pull.changed_files}
        </NavLink>
      </nav>
    </>
  );
}
