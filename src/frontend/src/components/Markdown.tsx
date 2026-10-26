import {useMemo} from "react";

import {renderMarkdown, type UrlResolver} from "../utils/markdown";

/** Issue, pull request, comment and README text, rendered as on GitHub. */
export function Markdown({
  text,
  file = false,
  resolveUrl,
  className = "markdown-body",
}: {
  text: string;
  file?: boolean;
  resolveUrl?: UrlResolver;
  className?: string;
}) {
  const html = useMemo(
    () => renderMarkdown(text, {file, resolveUrl}),
    [text, file, resolveUrl],
  );
  return (
    <div
      className={`${className} markdown-rendered`}
      // Sanitized by DOMPurify in renderMarkdown.
      dangerouslySetInnerHTML={{__html: html}}
    />
  );
}
