import {FormEvent, useState} from "react";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {useSession} from "../auth/SessionContext";

type Comment = components["schemas"]["IssueCommentResponse"];

export function IssueComments({
  owner,
  repo,
  issueNumber,
  comments,
  onChanged,
}: {
  owner: string;
  repo: string;
  issueNumber: number;
  comments: Comment[];
  onChanged: () => void;
}) {
  const {user} = useSession();
  const [body, setBody] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [editBody, setEditBody] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function addComment(event: FormEvent) {
    event.preventDefault();
    const {response} = await api.POST(
      "/api/v3/repos/{owner}/{repo}/issues/{issue_number}/comments",
      {params: {path: {owner, repo, issue_number: issueNumber}}, body: {body}},
    );
    if (!response.ok) return setError("Could not add comment.");
    setBody("");
    setError(null);
    onChanged();
  }

  async function saveComment(commentId: number) {
    const {response} = await api.PATCH(
      "/api/v3/repos/{owner}/{repo}/issues/comments/{comment_id}",
      {
        params: {path: {owner, repo, comment_id: commentId}},
        body: {body: editBody},
      },
    );
    if (!response.ok) return setError("Could not edit comment.");
    setEditing(null);
    setError(null);
    onChanged();
  }

  async function deleteComment(commentId: number) {
    const {response} = await api.DELETE(
      "/api/v3/repos/{owner}/{repo}/issues/comments/{comment_id}",
      {params: {path: {owner, repo, comment_id: commentId}}},
    );
    if (!response.ok) return setError("Could not delete comment.");
    setError(null);
    onChanged();
  }

  return (
    <>
      {comments.map((comment) => (
        <article className="timeline-item" key={comment.id}>
          <strong>{comment.user.login}</strong>
          {editing === comment.id ? (
            <div className="stack-form">
              <textarea
                value={editBody}
                onChange={(event) => setEditBody(event.target.value)}
              />
              <div className="button-row">
                <button
                  className="button"
                  type="button"
                  onClick={() => void saveComment(comment.id)}
                >
                  Save
                </button>
                <button type="button" onClick={() => setEditing(null)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="markdown-body">{comment.body}</div>
          )}
          {user?.login === comment.user.login && editing !== comment.id ? (
            <div className="button-row">
              <button
                type="button"
                onClick={() => {
                  setEditing(comment.id);
                  setEditBody(comment.body);
                }}
              >
                Edit
              </button>
              <button
                type="button"
                onClick={() => void deleteComment(comment.id)}
              >
                Delete
              </button>
            </div>
          ) : null}
        </article>
      ))}
      {error ? <p className="flash-error">{error}</p> : null}
      {user ? (
        <form
          className="comment-composer stack-form"
          onSubmit={(event) => void addComment(event)}
        >
          <h2>Add a comment</h2>
          <div
            className="editor-tabs"
            role="tablist"
            aria-label="Comment editor"
          >
            <button
              className="selected"
              role="tab"
              aria-selected="true"
              type="button"
            >
              Write
            </button>
            <button disabled role="tab" aria-selected="false" type="button">
              Preview
            </button>
          </div>
          <textarea
            id="new-comment"
            required
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
          <div className="comment-actions">
            <span className="muted">Markdown is supported</span>
            <button className="button" type="submit">
              Comment
            </button>
          </div>
        </form>
      ) : null}
    </>
  );
}
