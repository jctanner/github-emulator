import {useState} from "react";
import {useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {IssueComments} from "../components/IssueComments";
import {IssueLabelsEditor} from "../components/IssueLabelsEditor";
import {Loadable} from "../components/Loadable";
import {RepositoryHeader} from "../components/RepositoryHeader";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Repository = components["schemas"]["RepoResponse"];
type Issue = components["schemas"]["IssueResponse"];
type Comment = components["schemas"]["IssueCommentResponse"];

export function IssueDetailPage() {
  const {owner = "", repo = "", number = "0"} = useParams();
  const issueNumber = Number(number);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const page = useApiData<{
    repository: Repository;
    issue: Issue;
    comments: Comment[];
    labels: components["schemas"]["LabelResponse"][];
  }>(`issue:${owner}/${repo}:${issueNumber}`, async () => {
    const path = {owner, repo, issue_number: issueNumber};
    const [repoResult, issueResult, commentsResult, labelsResult] =
      await Promise.all([
        api.GET("/api/v3/repos/{owner}/{repo}", {
          params: {path: {owner, repo}},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/issues/{issue_number}", {
          params: {path},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/issues/{issue_number}/comments", {
          params: {path},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/labels", {
          params: {path: {owner, repo}},
        }),
      ]);
    return {
      repository: requireApiData(
        repoResult.data,
        repoResult.response,
        "Could not load repository.",
      ),
      issue: requireApiData(
        issueResult.data,
        issueResult.response,
        "Could not load issue.",
      ),
      comments: requireApiData(
        commentsResult.data,
        commentsResult.response,
        "Could not load comments.",
      ),
      labels: requireApiData(
        labelsResult.data,
        labelsResult.response,
        "Could not load labels.",
      ),
    };
  });

  async function toggleState() {
    if (!page.data) return;
    const state = page.data.issue.state === "open" ? "closed" : "open";
    const {response} = await api.PATCH(
      "/api/v3/repos/{owner}/{repo}/issues/{issue_number}",
      {params: {path: {owner, repo, issue_number: issueNumber}}, body: {state}},
    );
    if (!response.ok)
      return setMutationError(
        `Could not ${state === "open" ? "reopen" : "close"} issue.`,
      );
    setMutationError(null);
    page.reload();
  }

  async function editIssue() {
    if (!page.data) return;
    const title = globalThis.prompt("Issue title", page.data.issue.title);
    if (!title) return;
    const body = globalThis.prompt(
      "Issue description",
      page.data.issue.body ?? "",
    );
    if (body === null) return;
    const {response} = await api.PATCH(
      "/api/v3/repos/{owner}/{repo}/issues/{issue_number}",
      {
        params: {path: {owner, repo, issue_number: issueNumber}},
        body: {title, body},
      },
    );
    if (!response.ok) return setMutationError("Could not edit issue.");
    setMutationError(null);
    page.reload();
  }

  return (
    <Loadable loading={page.loading} error={page.error}>
      {page.data ? (
        <>
          <RepositoryHeader repository={page.data.repository} />
          <header className="conversation-heading">
            <h1>
              {page.data.issue.title}{" "}
              <span className="muted">#{page.data.issue.number}</span>
            </h1>
            <span className={`state state-${page.data.issue.state}`}>
              {page.data.issue.state}
            </span>
            <button type="button" onClick={() => void editIssue()}>
              Edit
            </button>
            <p className="conversation-meta">
              <strong>{page.data.issue.user.login}</strong> opened this issue ·{" "}
              {page.data.comments.length} comments
            </p>
          </header>
          <div className="conversation-layout">
            <main className="conversation-main">
              <article className="timeline-item">
                <strong>{page.data.issue.user.login}</strong>
                <div className="markdown-body">
                  {page.data.issue.body ?? "No description provided."}
                </div>
              </article>
              <IssueComments
                owner={owner}
                repo={repo}
                issueNumber={issueNumber}
                comments={page.data.comments}
                onChanged={page.reload}
              />
              {mutationError ? (
                <p className="flash-error">{mutationError}</p>
              ) : null}
              <button
                className="button secondary"
                type="button"
                onClick={() => void toggleState()}
              >
                {page.data.issue.state === "open"
                  ? "Close issue"
                  : "Reopen issue"}
              </button>
            </main>
            <aside className="conversation-sidebar">
              <h2>Labels</h2>
              <IssueLabelsEditor
                owner={owner}
                repo={repo}
                issueNumber={issueNumber}
                assigned={page.data.issue.labels}
                available={page.data.labels}
                onChanged={page.reload}
              />
            </aside>
          </div>
        </>
      ) : null}
    </Loadable>
  );
}
