import {FormEvent, useState} from "react";
import {Navigate, useNavigate, useParams} from "react-router-dom";

import {api} from "../api/client";
import {useSession} from "../auth/SessionContext";
import {useRepositoryLayout} from "../components/RepositoryContext";

export function NewIssuePage() {
  const {owner = "", repo = ""} = useParams();
  const navigate = useNavigate();
  const session = useSession();
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
  if (session.loading) return <p className="loading">Loading...</p>;
  if (!session.user) return <Navigate replace to="/login" />;

  return (
    <form className="new-issue-form" onSubmit={(event) => void submit(event)}>
      <div className="new-issue-subhead">New issue</div>
      {error ? <p className="flash-error">{error}</p> : null}
      <div className="new-issue-box">
        <label>
          <span>
            Title <span className="required-marker">*</span>
          </span>
          <input
            autoFocus
            required
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          <span>
            Description <span className="optional-marker">(optional)</span>
          </span>
          <textarea
            placeholder="Leave a comment"
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
        </label>
      </div>
      <button className="button" type="submit">
        Submit new issue
      </button>
    </form>
  );
}
