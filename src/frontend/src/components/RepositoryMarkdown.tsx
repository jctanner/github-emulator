import {type MouseEvent, useMemo} from "react";
import {useNavigate} from "react-router-dom";

import type {components} from "../api/schema";
import {decodeBase64Content} from "../utils/content";
import {repositoryUrlResolver} from "../utils/markdown";
import {Markdown} from "./Markdown";

type Content = components["schemas"]["ContentResponse"];

// The raw-file URL prefix for a file's repository, from its download URL.
function rawBase(file: Content) {
  const url = file.download_url ?? "";
  return url.endsWith(`/${file.path}`)
    ? url.slice(0, -file.path.length - 1)
    : url;
}

/**
 * A Markdown file from a repository, rendered as on GitHub: relative links
 * open the file or folder at the same ref, relative images load the raw file,
 * and links to other emulator pages stay in the app.
 */
export function RepositoryMarkdown({
  owner,
  repo,
  gitRef,
  file,
}: {
  owner: string;
  repo: string;
  gitRef: string;
  file: Content;
}) {
  const navigate = useNavigate();
  const text = useMemo(() => decodeBase64Content(file.content), [file]);
  const resolveUrl = useMemo(
    () =>
      repositoryUrlResolver({
        owner,
        repo,
        ref: gitRef,
        path: file.path,
        rawBase: rawBase(file),
      }),
    [owner, repo, gitRef, file],
  );

  function followInternalLink(event: MouseEvent<HTMLDivElement>) {
    const link = (event.target as Element).closest("a");
    const href = link?.getAttribute("href");
    if (
      !href?.startsWith("/ui/") ||
      link?.target ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    void navigate(href.slice("/ui".length));
  }

  return (
    <div onClick={followInternalLink}>
      <Markdown file text={text} resolveUrl={resolveUrl} />
    </div>
  );
}
