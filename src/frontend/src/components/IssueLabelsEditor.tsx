import {useState} from "react";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {LabelPill} from "./LabelPill";

type Label = components["schemas"]["LabelResponse"];

export function IssueLabelsEditor({
  owner,
  repo,
  issueNumber,
  assigned,
  available,
  onChanged,
}: {
  owner: string;
  repo: string;
  issueNumber: number;
  assigned: Label[];
  available: Label[];
  onChanged: () => void;
}) {
  const [selected, setSelected] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function add() {
    if (!selected) return;
    const {response} = await api.POST(
      "/api/v3/repos/{owner}/{repo}/issues/{issue_number}/labels",
      {
        params: {path: {owner, repo, issue_number: issueNumber}},
        body: {labels: [selected]},
      },
    );
    if (!response.ok) return setError("Could not add label.");
    setSelected("");
    setError(null);
    onChanged();
  }
  async function remove(name: string) {
    const {response} = await api.DELETE(
      "/api/v3/repos/{owner}/{repo}/issues/{issue_number}/labels/{name}",
      {params: {path: {owner, repo, issue_number: issueNumber, name}}},
    );
    if (!response.ok) return setError("Could not remove label.");
    setError(null);
    onChanged();
  }
  const choices = available.filter(
    (label) => !assigned.some((value) => value.name === label.name),
  );
  return (
    <section className="labels-editor">
      <div className="labels" aria-label="Labels">
        {assigned.map((label) => (
          <span className="editable-label" key={label.id}>
            <LabelPill label={label} />
            <button
              aria-label={`Remove ${label.name}`}
              type="button"
              onClick={() => void remove(label.name)}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="button-row">
        <select
          aria-label="Add label"
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
        >
          <option value="">Choose a label</option>
          {choices.map((label) => (
            <option key={label.id} value={label.name}>
              {label.name}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => void add()}>
          Add
        </button>
      </div>
      {error ? <p className="flash-error">{error}</p> : null}
    </section>
  );
}
