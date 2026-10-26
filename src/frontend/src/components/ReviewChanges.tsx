import {useState} from "react";
import {Link} from "react-router-dom";

import {api} from "../api/client";
import {reviewerStates, type PullReview} from "../api/reviews";
import type {components} from "../api/schema";
import {useSession} from "../auth/SessionContext";

type Pull = components["schemas"]["PRResponse"];
type ReviewEvent = "COMMENT" | "APPROVE" | "REQUEST_CHANGES";

const choices: {event: ReviewEvent; label: string; hint: string}[] = [
  {
    event: "COMMENT",
    label: "Comment",
    hint: "Submit general feedback without explicit approval.",
  },
  {
    event: "APPROVE",
    label: "Approve",
    hint: "Give your approval to merge these changes.",
  },
  {
    event: "REQUEST_CHANGES",
    label: "Request changes",
    hint: "Submit feedback that must be addressed before merging.",
  },
];

// GitHub's "Review changes" popover: a summary and one of three verdicts,
// posted as a single submitted review.
export function ReviewChanges({
  owner,
  repo,
  pull,
  onSubmitted,
}: {
  owner: string;
  repo: string;
  pull: Pull;
  onSubmitted: () => void;
}) {
  const {user} = useSession();
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState("");
  const [event, setEvent] = useState<ReviewEvent>("COMMENT");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  if (!user) return null;

  // As on GitHub, authors cannot approve or request changes on their own
  // pull request, and only an open one takes a verdict.
  const own = user.login === pull.user.login;
  const verdictBlocked = own || pull.state !== "open";

  async function submit() {
    if (event !== "APPROVE" && !body.trim()) {
      return setError(
        event === "COMMENT"
          ? "Write a comment to submit this review."
          : "Explain the changes you are requesting.",
      );
    }
    setSubmitting(true);
    const {response} = await api.POST(
      "/api/v3/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
      {
        params: {path: {owner, repo, pull_number: pull.number}},
        body: {event, body},
      },
    );
    setSubmitting(false);
    if (!response.ok) return setError("Could not submit review.");
    setError(null);
    setBody("");
    setEvent("COMMENT");
    setOpen(false);
    onSubmitted();
  }

  return (
    <div className="review-changes">
      <button
        className="button compact"
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        Review changes ▾
      </button>
      {open ? (
        <form
          className="review-changes-popover"
          aria-label="Review changes"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <h3>Finish your review</h3>
          <textarea
            aria-label="Review summary"
            placeholder="Leave a comment"
            rows={5}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
          {choices.map((choice) => {
            const disabled = choice.event !== "COMMENT" && verdictBlocked;
            return (
              <label
                key={choice.event}
                className={`review-choice${disabled ? " disabled" : ""}`}
              >
                <input
                  type="radio"
                  name="review-event"
                  value={choice.event}
                  checked={event === choice.event}
                  disabled={disabled}
                  onChange={() => setEvent(choice.event)}
                />
                <span>
                  <strong>{choice.label}</strong>
                  <small>
                    {disabled && own
                      ? "Pull request authors can't " +
                        (choice.event === "APPROVE"
                          ? "approve their own pull request."
                          : "request changes on their own pull request.")
                      : choice.hint}
                  </small>
                </span>
              </label>
            );
          })}
          {error ? <p className="flash-error">{error}</p> : null}
          <div className="button-row">
            <button className="button" type="submit" disabled={submitting}>
              Submit review
            </button>
            <button
              className="button secondary"
              type="button"
              onClick={() => setOpen(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

const stateLabels: Record<string, string> = {
  APPROVED: "Approved",
  CHANGES_REQUESTED: "Changes requested",
  COMMENTED: "Commented",
  DISMISSED: "Dismissed",
};

export function Reviewers({
  reviews,
  filesHref,
}: {
  reviews: PullReview[];
  filesHref: string;
}) {
  const {user} = useSession();
  const reviewers = reviewerStates(reviews);
  return (
    <section className="label-manager reviewers">
      <div className="label-manager-heading">
        <h2>Reviewers</h2>
      </div>
      {reviewers.length ? (
        <ul className="reviewer-list">
          {reviewers.map(({login, state}) => (
            <li key={login}>
              <strong>{login}</strong>
              <span className={`review-state review-state-${state}`}>
                {stateLabels[state] ?? state}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No reviews</p>
      )}
      {user ? <Link to={filesHref}>Add your review</Link> : null}
    </section>
  );
}
