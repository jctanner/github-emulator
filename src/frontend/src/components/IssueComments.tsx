import {FormEvent, useState} from "react";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {useSession} from "../auth/SessionContext";
import {LabelPill} from "./LabelPill";
import {Octicon} from "./Octicon";

type Comment = components["schemas"]["IssueCommentResponse"];
type IssueEvent = components["schemas"]["IssueEventResponse"];

export function IssueComments({
  owner,
  repo,
  issueNumber,
  comments,
  events,
  onChanged,
}: {
  owner: string;
  repo: string;
  issueNumber: number;
  comments: Comment[];
  events: IssueEvent[];
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
    if (!globalThis.confirm("Delete this comment?")) return;
    const {response} = await api.DELETE(
      "/api/v3/repos/{owner}/{repo}/issues/comments/{comment_id}",
      {params: {path: {owner, repo, comment_id: commentId}}},
    );
    if (!response.ok) return setError("Could not delete comment.");
    setError(null);
    onChanged();
  }

  const timeline = [
    ...comments.map((comment) => ({
      kind: "comment" as const,
      createdAt: comment.created_at,
      item: comment,
    })),
    ...events.map((issueEvent) => ({
      kind: "event" as const,
      createdAt: issueEvent.created_at,
      item: issueEvent,
    })),
  ].sort((left, right) => left.createdAt.localeCompare(right.createdAt));

  return (
    <>
      {timeline.map((entry) =>
        entry.kind === "event" ? (
          <div className="timeline-event" key={`event-${entry.item.id}`}>
            <span className="timeline-event-icon">
              <Octicon name="tag" size={16} />
            </span>
            <span>
              <strong>{entry.item.actor.login}</strong>{" "}
              {entry.item.event === "labeled" ? "added" : "removed"}{" "}
              {entry.item.label ? <LabelPill label={entry.item.label} /> : null}{" "}
              <time dateTime={entry.item.created_at}>
                {new Date(entry.item.created_at).toLocaleString()}
              </time>
            </span>
          </div>
        ) : (
          <article className="timeline-item" key={entry.item.id}>
            <header className="timeline-item-header">
              <strong>{entry.item.user.login}</strong>
              {user &&
              (user.login === entry.item.user.login || user.site_admin) ? (
                <details className="comment-actions-menu">
                  <summary
                    aria-label={`Actions for ${entry.item.user.login}'s comment`}
                    title="More actions"
                  >
                    <Octicon name="kebab-horizontal" size={16} />
                  </summary>
                  <div className="comment-actions-popover" role="menu">
                    <button
                      role="menuitem"
                      type="button"
                      onClick={() => {
                        setEditing(entry.item.id);
                        setEditBody(entry.item.body);
                      }}
                    >
                      Edit
                    </button>
                    <button
                      className="danger"
                      role="menuitem"
                      type="button"
                      onClick={() => void deleteComment(entry.item.id)}
                    >
                      Delete
                    </button>
                  </div>
                </details>
              ) : null}
            </header>
            {editing === entry.item.id ? (
              <div className="stack-form">
                <textarea
                  value={editBody}
                  onChange={(event) => setEditBody(event.target.value)}
                />
                <div className="button-row">
                  <button
                    className="button"
                    type="button"
                    onClick={() => void saveComment(entry.item.id)}
                  >
                    Save
                  </button>
                  <button type="button" onClick={() => setEditing(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="markdown-body">{entry.item.body}</div>
            )}
          </article>
        ),
      )}
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
