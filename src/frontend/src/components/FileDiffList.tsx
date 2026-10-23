import {Fragment} from "react";
import {Link} from "react-router-dom";

import type {ReviewComment} from "../api/reviews";
import {ReviewCommentCard} from "./PullReview";

export type DiffFile = {
  filename: string;
  additions: number;
  deletions: number;
  patch: string;
};

function patchLineClass(line: string): string {
  if (line.startsWith("+") && !line.startsWith("+++")) return "addition";
  if (line.startsWith("-") && !line.startsWith("---")) return "deletion";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("diff ") || line.startsWith("index ")) return "meta";
  return "";
}

function diffRows(patch: string) {
  let left = 0,
    right = 0,
    position = 0;
  let inHunk = false;
  return patch.split("\n").map((text) => {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(text);
    if (hunk) {
      left = Number(hunk[1]);
      right = Number(hunk[2]);
      if (inHunk) position++;
      inHunk = true;
      return {text, position: null, left: null, right: null};
    }
    if (!inHunk) return {text, position: null, left: null, right: null};
    position++;
    const oldLine = text.startsWith("-") || text.startsWith(" ");
    const newLine = text.startsWith("+") || text.startsWith(" ");
    return {
      text,
      position,
      left: oldLine ? left++ : null,
      right: newLine ? right++ : null,
    };
  });
}

function commentRoot(comment: ReviewComment, comments: ReviewComment[]) {
  const seen = new Set<number>();
  while (comment.in_reply_to_id && !seen.has(comment.id)) {
    seen.add(comment.id);
    const parent = comments.find((c) => c.id === comment.in_reply_to_id);
    if (!parent) break;
    comment = parent;
  }
  return comment;
}

export function FileDiffList({
  files,
  fileHref,
  reviewComments = [],
}: {
  files: DiffFile[];
  fileHref: (file: DiffFile) => string;
  reviewComments?: ReviewComment[];
}) {
  return (
    <div className="pr-file-list">
      {files.map((file) => {
        const rows = diffRows(file.patch);
        const comments = reviewComments.filter((c) => c.path === file.filename);
        const anchors = new Map(
          comments.map((comment) => {
            const root = commentRoot(comment, reviewComments);
            const index =
              root.subject_type === "file"
                ? -1
                : rows.findIndex((row) =>
                    root.line != null
                      ? (root.side === "LEFT" ? row.left : row.right) ===
                        root.line
                      : root.position != null && row.position === root.position,
                  );
            return [comment.id, index];
          }),
        );
        return (
          <article className="pr-file" key={file.filename}>
            <header>
              <Link to={fileHref(file)}>{file.filename}</Link>
              <span>
                <span className="diff-additions">+{file.additions}</span>{" "}
                <span className="diff-deletions">-{file.deletions}</span>
              </span>
            </header>
            <div className="diff-patch">
              {rows.map((row, index) => (
                <Fragment key={index}>
                  <code className={patchLineClass(row.text)}>
                    {row.text || " "}
                    {"\n"}
                  </code>
                  {comments
                    .filter((c) => anchors.get(c.id) === index)
                    .map((comment) => (
                      <ReviewCommentCard key={comment.id} comment={comment} />
                    ))}
                </Fragment>
              ))}
            </div>
            {comments.some((c) => anchors.get(c.id) === -1) ? (
              <section
                className="review-unplaced"
                aria-label={`Additional comments on ${file.filename}`}
              >
                <p className="muted">
                  File comments and comments outside the displayed diff
                </p>
                {comments
                  .filter((c) => anchors.get(c.id) === -1)
                  .map((comment) => (
                    <ReviewCommentCard key={comment.id} comment={comment} />
                  ))}
              </section>
            ) : null}
          </article>
        );
      })}
      {reviewComments
        .filter((c) => !files.some((file) => file.filename === c.path))
        .map((comment) => (
          <section className="review-unplaced" key={comment.id}>
            <p className="muted">File is not in the displayed diff</p>
            <ReviewCommentCard comment={comment} />
          </section>
        ))}
    </div>
  );
}
