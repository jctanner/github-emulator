import type {PullReview, ReviewComment} from "../api/reviews";
import {ReviewCommentCard} from "./PullReview";
import {Markdown} from "./Markdown";

export function pullReviewEntries(
  reviews: PullReview[],
  comments: ReviewComment[],
  filesHref: string,
) {
  const reviewIds = new Set(reviews.map((review) => review.id));
  return [
    ...reviews.map((review) => ({
      id: `review-${review.id}`,
      createdAt: review.submitted_at ?? "",
      content: (
        <article
          className="timeline-item"
          id={`pullrequestreview-${review.id}`}
        >
          <header className="timeline-item-header">
            <strong>{review.user?.login ?? "Deleted user"}</strong>
            <span>
              {(
                {
                  APPROVED: "approved these changes",
                  CHANGES_REQUESTED: "requested changes",
                  COMMENTED: "reviewed",
                  DISMISSED: "review dismissed",
                } as Record<string, string>
              )[review.state] ?? review.state}
            </span>
            <a href={`#pullrequestreview-${review.id}`}>
              {review.submitted_at ? (
                <time dateTime={review.submitted_at}>
                  {new Date(review.submitted_at).toLocaleString()}
                </time>
              ) : (
                "Review"
              )}
            </a>
          </header>
          {review.body ? <Markdown text={review.body} /> : null}
          {comments
            .filter((c) => c.pull_request_review_id === review.id)
            .map((comment) => (
              <ReviewCommentCard
                key={comment.id}
                comment={comment}
                filesHref={filesHref}
              />
            ))}
        </article>
      ),
    })),
    ...comments
      .filter((c) => !reviewIds.has(c.pull_request_review_id ?? -1))
      .map((comment) => ({
        id: `review-comment-${comment.id}`,
        createdAt: comment.created_at,
        content: <ReviewCommentCard comment={comment} filesHref={filesHref} />,
      })),
  ];
}
