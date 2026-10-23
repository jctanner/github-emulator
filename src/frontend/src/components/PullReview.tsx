import {useEffect} from "react";
import {Link, useLocation} from "react-router-dom";

import type {ReviewComment} from "../api/reviews";

export function ReviewCommentCard({
  comment,
  filesHref,
}: {
  comment: ReviewComment;
  filesHref?: string;
}) {
  const context = `${comment.path}${comment.line != null ? `:${comment.line} (${comment.side === "LEFT" ? "original" : "new"} version)` : comment.position != null ? ` · diff position ${comment.position}` : " · file comment"}`;
  return (
    <article
      className="timeline-item review-comment"
      id={`discussion_r${comment.id}`}
    >
      <header className="timeline-item-header">
        <span className="timeline-item-author">
          <strong>{comment.user?.login ?? "Deleted user"}</strong>
          {comment.in_reply_to_id ? <span>replied</span> : null}
          <a href={`#discussion_r${comment.id}`}>
            <time dateTime={comment.created_at}>
              {new Date(comment.created_at).toLocaleString()}
            </time>
          </a>
        </span>
      </header>
      <div className="review-comment-context">
        {filesHref ? (
          <Link to={`${filesHref}#discussion_r${comment.id}`}>{context}</Link>
        ) : (
          context
        )}
        {comment.in_reply_to_id ? (
          <>
            {" "}
            ·{" "}
            <a href={`#discussion_r${comment.in_reply_to_id}`}>
              Parent comment
            </a>
          </>
        ) : null}
      </div>
      <div className="markdown-body">{comment.body}</div>
    </article>
  );
}

export function ReviewAnchor() {
  const {hash} = useLocation();
  useEffect(() => {
    if (hash)
      document
        .getElementById(hash.slice(1))
        ?.scrollIntoView?.({block: "center"});
  }, [hash]);
  return null;
}
