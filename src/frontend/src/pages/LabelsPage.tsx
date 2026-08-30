import {FormEvent, useState} from "react";
import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {LabelPill} from "../components/LabelPill";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {RepositoryHeader} from "../components/RepositoryHeader";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Label = components["schemas"]["LabelResponse"];

export function LabelsPage() {
  const {owner = "", repo = ""} = useParams();
  const labels = useApiData<Label[]>(`labels:${owner}/${repo}`, async () => {
    const {data, response} = await api.GET(
      "/api/v3/repos/{owner}/{repo}/labels",
      {params: {path: {owner, repo}}},
    );
    return requireApiData(data, response, "Could not load labels.");
  });
  const [name, setName] = useState("");
  const [color, setColor] = useState("ededed");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function create(event: FormEvent) {
    event.preventDefault();
    const {response} = await api.POST("/api/v3/repos/{owner}/{repo}/labels", {
      params: {path: {owner, repo}},
      body: {name, color, description},
    });
    if (!response.ok) return setError("Could not create label.");
    setName("");
    setDescription("");
    setError(null);
    labels.reload();
  }
  async function remove(name: string) {
    const {response} = await api.DELETE(
      "/api/v3/repos/{owner}/{repo}/labels/{name}",
      {params: {path: {owner, repo, name}}},
    );
    if (!response.ok) return setError("Could not delete label.");
    labels.reload();
  }
  async function edit(label: Label) {
    const newName = globalThis.prompt("Label name", label.name);
    if (!newName) return;
    const newColor = globalThis.prompt(
      "Label color (6 hex digits)",
      label.color,
    );
    if (!newColor) return;
    const {response} = await api.PATCH(
      "/api/v3/repos/{owner}/{repo}/labels/{name}",
      {
        params: {path: {owner, repo, name: label.name}},
        body: {
          new_name: newName,
          color: newColor,
          description: label.description,
        },
      },
    );
    if (!response.ok) return setError("Could not update label.");
    setError(null);
    labels.reload();
  }
  return (
    <>
      <RepositoryHeader owner={owner} repo={repo} />
      <div className="labels-layout">
        <nav className="labels-nav" aria-label="Issue settings">
          <Link to={`/${owner}/${repo}/issues`}>
            <Octicon name="issue" /> Issues
          </Link>
          <Link className="selected" to={`/${owner}/${repo}/labels`}>
            <Octicon name="tag" /> Labels
          </Link>
        </nav>
        <section>
          <div className="page-heading">
            <h1>Labels</h1>
          </div>
          <form
            className="label-create-form"
            onSubmit={(event) => void create(event)}
          >
            <input
              aria-label="Label name"
              required
              placeholder="Label name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <input
              aria-label="Color"
              required
              pattern="[0-9a-fA-F]{6}"
              value={color}
              onChange={(event) => setColor(event.target.value)}
            />
            <input
              aria-label="Description"
              placeholder="Description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
            <button className="button" type="submit">
              New label
            </button>
          </form>
          {error ? <p className="flash-error">{error}</p> : null}
          <Loadable loading={labels.loading} error={labels.error}>
            <div className="list-box labels-list">
              <header className="list-box-header">
                {labels.data?.length ?? 0} labels
              </header>
              {labels.data?.map((label) => (
                <div className="list-row label-row" key={label.id}>
                  <LabelPill label={label} />
                  <span>{label.description}</span>
                  <div className="button-row">
                    <button type="button" onClick={() => void edit(label)}>
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => void remove(label.name)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </Loadable>
        </section>
      </div>
    </>
  );
}
