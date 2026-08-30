import {FormEvent, useState} from "react";
import {useNavigate, useParams} from "react-router-dom";

import {api} from "../api/client";
import {useRepositoryLayout} from "../components/RepositoryContext";

export function NewIssuePage() {
  const {owner = "", repo = ""} = useParams();
  const navigate = useNavigate();
  const {reloadNavigation} = useRepositoryLayout();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const {data, response} = await api.POST(
      "/api/v3/repos/{owner}/{repo}/issues",
      {
        params: {path: {owner, repo}},
        body: {title, body},
      },
    );
    if (!data || !response.ok) return setError("Could not create issue.");
    reloadNavigation();
    await navigate(`/${owner}/${repo}/issues/${data.number}`);
  }
  return (
    <>
      <form className="editor-form" onSubmit={(event) => void submit(event)}>
        <h1>New issue</h1>
        {error ? <p className="flash-error">{error}</p> : null}
        <label>
          Title
          <input
            required
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          Description
          <textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
        </label>
        <button className="button" type="submit">
          Submit new issue
        </button>
      </form>
    </>
  );
}
