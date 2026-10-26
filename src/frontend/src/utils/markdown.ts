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

/** HTML for GitHub-flavored Markdown, sanitized so it is safe to insert. */
export function renderMarkdown(text: string, {file = false} = {}): string {
  const html = (file ? fileParser : commentParser).parse(text, {async: false});
  return DOMPurify.sanitize(html, {ADD_ATTR: ["target"]});
}
