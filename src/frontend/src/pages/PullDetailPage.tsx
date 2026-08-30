import {useState} from "react";
import {useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {IssueComments} from "../components/IssueComments";
import {LabelManager} from "../components/LabelManager";
import {Loadable} from "../components/Loadable";
import {useRepositoryLayout} from "../components/RepositoryContext";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Pull = components["schemas"]["PRResponse"];
type Issue = components["schemas"]["IssueResponse"];
type Comment = components["schemas"]["IssueCommentResponse"];

export function PullDetailPage() {
  const {owner = "", repo = "", number = "0"} = useParams();
  const pullNumber = Number(number);
  const {reloadNavigation} = useRepositoryLayout();
  const [mutationError, setMutationError] = useState<string | null>(null);
  const page = useApiData<{
    pull: Pull;
    issue: Issue;
    comments: Comment[];
    labels: components["schemas"]["LabelResponse"][];
  }>(`pull:${owner}/${repo}:${pullNumber}`, async () => {
    const pullPath = {owner, repo, pull_number: pullNumber};
    const issuePath = {owner, repo, issue_number: pullNumber};
    const [pullResult, issueResult, commentsResult, labelsResult] =
      await Promise.all([
        api.GET("/api/v3/repos/{owner}/{repo}/pulls/{pull_number}", {
          params: {path: pullPath},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/issues/{issue_number}", {
          params: {path: issuePath},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/issues/{issue_number}/comments", {
          params: {path: issuePath},
        }),
        api.GET("/api/v3/repos/{owner}/{repo}/labels", {
          params: {path: {owner, repo}},
        }),
      ]);
    return {
      pull: requireApiData(
        pullResult.data,
        pullResult.response,
        "Could not load pull request.",
      ),
      issue: requireApiData(
        issueResult.data,
        issueResult.response,
        "Could not load pull request labels.",
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
    const state = page.data.pull.state === "open" ? "closed" : "open";
    const {response} = await api.PATCH(
      "/api/v3/repos/{owner}/{repo}/pulls/{pull_number}",
      {params: {path: {owner, repo, pull_number: pullNumber}}, body: {state}},
    );
    if (!response.ok) return setMutationError("Could not update pull request.");
    setMutationError(null);
    reloadNavigation();
    page.reload();
  }

  async function merge() {
    const {data, response} = await api.PUT(
      "/api/v3/repos/{owner}/{repo}/pulls/{pull_number}/merge",
      {
        params: {path: {owner, repo, pull_number: pullNumber}},
        body: {merge_method: "merge"},
      },
    );
    if (!response.ok || !data?.merged)
      return setMutationError(data?.message ?? "Could not merge pull request.");
    setMutationError(null);
    reloadNavigation();
    page.reload();
  }

  async function editPull() {
    if (!page.data) return;
    const title = globalThis.prompt("Pull request title", page.data.pull.title);
    if (!title) return;
    const body = globalThis.prompt(
      "Pull request description",
      page.data.pull.body ?? "",
    );
    if (body === null) return;
    const {response} = await api.PATCH(
      "/api/v3/repos/{owner}/{repo}/pulls/{pull_number}",
      {
        params: {path: {owner, repo, pull_number: pullNumber}},
        body: {title, body},
      },
    );
    if (!response.ok) return setMutationError("Could not edit pull request.");
    setMutationError(null);
    page.reload();
  }

  return (
    <Loadable loading={page.loading} error={page.error}>
      {page.data ? (
        <>
          <header className="conversation-heading">
            <h1>
              {page.data.pull.title}{" "}
              <span className="muted">#{page.data.pull.number}</span>
            </h1>
            <span className={`state state-${page.data.pull.state}`}>
              {page.data.pull.state}
            </span>
            <button type="button" onClick={() => void editPull()}>
              Edit
            </button>
            <p>
              {page.data.pull.head.label} wants to merge into{" "}
              {page.data.pull.base.label}
            </p>
          </header>
          <nav className="pr-tabs" aria-label="Pull request">
            <span className="selected">Conversation</span>
            <span>Commits {page.data.pull.commits}</span>
            <span>Files changed {page.data.pull.changed_files}</span>
          </nav>
          <div className="conversation-layout">
            <main className="conversation-main">
              <article className="timeline-item">
                <strong>{page.data.pull.user.login}</strong>
                <div className="markdown-body">
                  {page.data.pull.body ?? "No description provided."}
                </div>
              </article>
              <IssueComments
                owner={owner}
                repo={repo}
                issueNumber={pullNumber}
                comments={page.data.comments}
                onChanged={page.reload}
              />
              {mutationError ? (
                <p className="flash-error">{mutationError}</p>
              ) : null}
              {!page.data.pull.merged ? (
                <div className="button-row">
                  <button
                    className="button secondary"
                    type="button"
                    onClick={() => void toggleState()}
                  >
                    {page.data.pull.state === "open"
                      ? "Close pull request"
                      : "Reopen pull request"}
                  </button>
                  {page.data.pull.state === "open" ? (
                    <button
                      className="button"
                      type="button"
                      onClick={() => void merge()}
                    >
                      Merge pull request
                    </button>
                  ) : null}
                </div>
              ) : (
                <p className="merge-state">Pull request successfully merged</p>
              )}
            </main>
            <aside className="conversation-sidebar">
              <LabelManager
                owner={owner}
                repo={repo}
                issueNumber={pullNumber}
                subject="pull request"
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
