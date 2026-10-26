import DOMPurify from "dompurify";
import {Marked} from "marked";

import {highlightFence} from "./highlight";

function parser(breaks: boolean) {
  return new Marked({
    gfm: true,
    breaks,
    renderer: {
      code({text, lang}) {
        const language = (lang ?? "").split(/\s/, 1)[0];
        const highlighted = highlightFence(text, language);
        if (highlighted === null) return false;
        return `<pre><code class="hljs language-${language}">${highlighted}</code></pre>\n`;
      },
    },
  });
}

// As on GitHub, a newline in a comment is a line break; in a file it is not.
const commentParser = parser(true);
const fileParser = parser(false);

// Links out of the emulator open in a new tab without handing it this page.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (
    node.tagName === "A" &&
    /^https?:/i.test(node.getAttribute("href") ?? "")
  ) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

/** File names GitHub renders as Markdown. */
export const MARKDOWN_FILE = /\.(md|markdown|mdown|mkd)$/i;

/** Rewrites a relative link or image source; anything else is unchanged. */
export type UrlResolver = (url: string, kind: "link" | "image") => string;

/** HTML for GitHub-flavored Markdown, sanitized so it is safe to insert. */
export function renderMarkdown(
  text: string,
  {file = false, resolveUrl}: {file?: boolean; resolveUrl?: UrlResolver} = {},
): string {
  const html = (file ? fileParser : commentParser).parse(text, {async: false});
  const clean = DOMPurify.sanitize(html, {ADD_ATTR: ["target"]});
  if (!resolveUrl) return clean;
  const holder = document.createElement("template");
  holder.innerHTML = clean;
  for (const link of holder.content.querySelectorAll("a[href]")) {
    link.setAttribute("href", resolveUrl(link.getAttribute("href")!, "link"));
  }
  for (const image of holder.content.querySelectorAll("img[src]")) {
    image.setAttribute("src", resolveUrl(image.getAttribute("src")!, "image"));
  }
  return holder.innerHTML;
}

function isRelative(url: string) {
  return !/^([a-z][a-z0-9+.-]*:|\/\/|#)/i.test(url);
}

// Resolve `target` against the directory `base`; a leading "/" is the
// repository root, as on GitHub.
function joinPath(base: string, target: string) {
  const parts = target.startsWith("/") ? [] : base.split("/").filter(Boolean);
  for (const part of target.split("/")) {
    if (part === "..") parts.pop();
    else if (part && part !== ".") parts.push(part);
  }
  return parts.join("/");
}

/**
 * Resolves a repository file's relative links as GitHub does: links go to the
 * file or folder page at the same ref, images to the raw file.
 */
export function repositoryUrlResolver({
  owner,
  repo,
  ref,
  path,
  rawBase,
}: {
  owner: string;
  repo: string;
  ref: string;
  path: string;
  rawBase: string;
}): UrlResolver {
  const directory = path.split("/").slice(0, -1).join("/");
  return (url, kind) => {
    if (!isRelative(url)) return url;
    const [, target = "", suffix = ""] = /^([^?#]*)(.*)$/.exec(url) ?? [];
    if (!target) return url;
    const resolved = joinPath(directory, target);
    if (kind === "image") return `${rawBase}/${resolved}${suffix}`;
    // A trailing slash names a folder; GitHub redirects either way.
    const view = target.endsWith("/") ? "tree" : "blob";
    return `/ui/${owner}/${repo}/${view}/${encodeURIComponent(ref)}/${resolved}${suffix}`;
  };
}
