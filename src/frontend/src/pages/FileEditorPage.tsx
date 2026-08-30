import {FormEvent, useEffect, useState} from "react";
import {useNavigate, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {Loadable} from "../components/Loadable";
import {RepositoryHeader} from "../components/RepositoryHeader";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Content = components["schemas"]["ContentResponse"];
const encode = (value: string) =>
  btoa(String.fromCharCode(...new TextEncoder().encode(value)));
const decode = (value: string) =>
  new TextDecoder().decode(
    Uint8Array.from(atob(value.replaceAll("\n", "")), (char) =>
      char.charCodeAt(0),
    ),
  );

export function FileEditorPage({create = false}: {create?: boolean}) {
  const {
    owner = "",
    repo = "",
    ref = "main",
    "*": routePath = "",
  } = useParams();
  const navigate = useNavigate();
  const existing = useApiData<Content | null>(
    `edit:${create}:${owner}/${repo}:${ref}:${routePath}`,
    async () => {
      if (create) return null;
      const {data, response} = await api.GET(
        "/api/v3/repos/{owner}/{repo}/contents/{path}",
        {params: {path: {owner, repo, path: routePath}, query: {ref}}},
      );
      const value = requireApiData(data, response, "Could not load file.");
      if (Array.isArray(value)) throw new Error("Cannot edit a directory.");
      return value;
    },
  );
  const [path, setPath] = useState(routePath);
  const [content, setContent] = useState("");
  const [message, setMessage] = useState(
    create ? "Create file" : "Update file",
  );
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (existing.data?.content) setContent(decode(existing.data.content));
  }, [existing.data]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const {response} = await api.PUT(
      "/api/v3/repos/{owner}/{repo}/contents/{path}",
      {
        params: {path: {owner, repo, path}},
        body: {
          message,
          content: encode(content),
          branch: ref,
          sha: existing.data?.sha,
        },
      },
    );
    if (!response.ok) return setError("Could not save file.");
    await navigate(`/${owner}/${repo}/blob/${ref}/${path}`);
  }
  return (
    <>
      <RepositoryHeader owner={owner} repo={repo} />
      <Loadable loading={existing.loading} error={existing.error}>
        <form className="editor-form" onSubmit={(event) => void submit(event)}>
          <h1>{create ? "Create new file" : `Edit ${routePath}`}</h1>
          {error ? <p className="flash-error">{error}</p> : null}
          <label>
            File path
            <input
              required
              value={path}
              disabled={!create}
              onChange={(event) => setPath(event.target.value)}
            />
          </label>
          <label>
            Content
            <textarea
              className="code-editor"
              value={content}
              onChange={(event) => setContent(event.target.value)}
            />
          </label>
          <label>
            Commit message
            <input
              required
              value={message}
              onChange={(event) => setMessage(event.target.value)}
            />
          </label>
          <button className="button" type="submit">
            Commit changes
          </button>
        </form>
      </Loadable>
    </>
  );
}
