import {useMemo} from "react";

import {renderMarkdown} from "../utils/markdown";

/** Issue, pull request, comment and README text, rendered as on GitHub. */
export function Markdown({
  text,
  file = false,
  className = "markdown-body",
}: {
  text: string;
  file?: boolean;
  className?: string;
}) {
  const html = useMemo(() => renderMarkdown(text, {file}), [text, file]);
  return (
    <div
      className={`${className} markdown-rendered`}
      // Sanitized by DOMPurify in renderMarkdown.
      dangerouslySetInnerHTML={{__html: html}}
    />
  );
}
