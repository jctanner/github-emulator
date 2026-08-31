import {useState} from "react";
import {useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {useSession} from "../auth/SessionContext";
import {IssueComments} from "../components/IssueComments";
import {LabelManager} from "../components/LabelManager";
import {Loadable} from "../components/Loadable";
import {Octicon} from "../components/Octicon";
import {PullRequestHeader} from "../components/PullRequestHeader";
import {useRepositoryLayout} from "../components/RepositoryContext";
import {requireApiData, useApiData} from "../hooks/useApiData";

type Pull = components["schemas"]["PRResponse"];
type Issue = components["schemas"]["IssueResponse"];
type Comment = components["schemas"]["IssueCommentResponse"];
type IssueEvent = components["schemas"]["IssueEventResponse"];

export function PullDetailPage() {
  const {owner = "", repo = "", number = "0"} = useParams();
  const pullNumber = Number(number);
  const {reloadNavigation} = useRepositoryLayout();
  const {user} = useSession();
  const [mutationError, setMutationError] = useState<string | null>(null);
  const page = useApiData<{
    pull: Pull;
    issue: Issue;
    comments: Comment[];
    events: IssueEvent[];
    labels: components["schemas"]["LabelResponse"][];
  }>(`pull:${owner}/${repo}:${pullNumber}`, async () => {
    const pullPath = {owner, repo, pull_number: pullNumber};
    const issuePath = {owner, repo, issue_number: pullNumber};
    const [
      pullResult,
      issueResult,
      commentsResult,
      eventsResult,
      labelsResult,
    ] = await Promise.all([
      api.GET("/api/v3/repos/{owner}/{repo}/pulls/{pull_number}", {
        params: {path: pullPath},
      }),
      api.GET("/api/v3/repos/{owner}/{repo}/issues/{issue_number}", {
        params: {path: issuePath},
      }),
      api.GET("/api/v3/repos/{owner}/{repo}/issues/{issue_number}/comments", {
        params: {path: issuePath},
      }),
      api.GET("/api/v3/repos/{owner}/{repo}/issues/{issue_number}/events", {
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
      events: requireApiData(
        eventsResult.data,
        eventsResult.response,
        "Could not load pull request history.",
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
          <PullRequestHeader owner={owner} repo={repo} pull={page.data.pull} />
          <div className="conversation-layout">
            <main className="conversation-main">
              <article className="timeline-item">
                <header className="timeline-item-header">
                  <strong>{page.data.pull.user.login}</strong>
                  {user &&
                  (user.login === page.data.pull.user.login ||
                    user.site_admin) ? (
                    <details className="comment-actions-menu">
                      <summary
                        aria-label={`Actions for ${page.data.pull.user.login}'s description`}
                        title="More actions"
                      >
                        <Octicon name="kebab-horizontal" size={16} />
                      </summary>
                      <div className="comment-actions-popover" role="menu">
                        <button
                          role="menuitem"
                          type="button"
                          onClick={() => void editPull()}
                        >
                          Edit
                        </button>
                      </div>
                    </details>
                  ) : null}
                </header>
                <div className="markdown-body">
                  {page.data.pull.body ?? "No description provided."}
                </div>
              </article>
              <IssueComments
                owner={owner}
                repo={repo}
                issueNumber={pullNumber}
                comments={page.data.comments}
                events={page.data.events}
                onChanged={page.reload}
              />
              {mutationError ? (
                <p className="flash-error">{mutationError}</p>
              ) : null}
              {user && !page.data.pull.merged ? (
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
