import {useState} from "react";
import {Link} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {LabelPill} from "./LabelPill";

type Label = components["schemas"]["LabelResponse"];

export function LabelManager({
  owner,
  repo,
  issueNumber,
  subject,
  assigned,
  available,
  onChanged,
}: {
  owner: string;
  repo: string;
  issueNumber: number;
  subject: "issue" | "pull request";
  assigned: Label[];
  available: Label[];
  onChanged: () => void;
}) {
  const [filter, setFilter] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const assignedNames = new Set(assigned.map((label) => label.name));
  const normalizedFilter = filter.trim().toLowerCase();
  const matches = (label: Label) =>
    !normalizedFilter || label.name.toLowerCase().includes(normalizedFilter);
  const selected = available.filter(
    (label) => assignedNames.has(label.name) && matches(label),
  );
  const unselected = available.filter(
    (label) => !assignedNames.has(label.name) && matches(label),
  );

  async function setAssigned(label: Label, checked: boolean) {
    setPending(label.name);
    setError(null);
    const {response} = checked
      ? await api.POST(
          "/api/v3/repos/{owner}/{repo}/issues/{issue_number}/labels",
          {
            params: {path: {owner, repo, issue_number: issueNumber}},
            body: {labels: [label.name]},
          },
        )
      : await api.DELETE(
          "/api/v3/repos/{owner}/{repo}/issues/{issue_number}/labels/{name}",
          {
            params: {
              path: {owner, repo, issue_number: issueNumber, name: label.name},
            },
          },
        );
    setPending(null);
    if (!response.ok) {
      setError(`Could not ${checked ? "add" : "remove"} label.`);
      return;
    }
    onChanged();
  }

  function options(labels: Label[], emptyMessage: string) {
    if (!labels.length)
      return <p className="label-manager-empty">{emptyMessage}</p>;
    return labels.map((label) => (
      <label className="label-manager-option" key={label.id}>
        <input
          type="checkbox"
          checked={assignedNames.has(label.name)}
          disabled={pending !== null}
          onChange={(event) => void setAssigned(label, event.target.checked)}
        />
        <span
          className="label-manager-dot"
          style={{backgroundColor: `#${label.color}`}}
        />
        <span>{label.name}</span>
      </label>
    ));
  }

  return (
    <section className="label-manager">
      <div className="label-manager-heading">
        <h2>Labels</h2>
        <details>
          <summary aria-label="Manage labels" title="Manage labels">
            ⚙
          </summary>
          <div className="label-manager-popover">
            <h3>Apply labels to this {subject}</h3>
            <input
              type="search"
              aria-label="Filter labels"
              placeholder="Filter labels"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
            <h4>Selected</h4>
            <div className="label-manager-options">
              {options(selected, "No selected labels")}
            </div>
            <h4>Labels</h4>
            <div className="label-manager-options">
              {options(
                unselected,
                available.length
                  ? "No matching labels"
                  : "No labels have been created.",
              )}
            </div>
            {error ? <p className="flash-error">{error}</p> : null}
            <footer>
              <Link to={`/${owner}/${repo}/settings/labels`}>Edit labels</Link>
            </footer>
          </div>
        </details>
      </div>
      <div className="label-manager-current" aria-label={`${subject} labels`}>
        {assigned.length ? (
          assigned.map((label) => <LabelPill key={label.id} label={label} />)
        ) : (
          <span className="muted">None yet</span>
        )}
      </div>
    </section>
  );
}
