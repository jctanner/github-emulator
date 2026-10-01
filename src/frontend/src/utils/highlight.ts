import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import makefile from "highlight.js/lib/languages/makefile";
import markdown from "highlight.js/lib/languages/markdown";
import perl from "highlight.js/lib/languages/perl";
import python from "highlight.js/lib/languages/python";
import ruby from "highlight.js/lib/languages/ruby";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

import "highlight.js/styles/github.css";

const languages = {
  bash,
  c,
  cpp,
  css,
  diff,
  dockerfile,
  go,
  ini,
  java,
  javascript,
  json,
  makefile,
  markdown,
  perl,
  python,
  ruby,
  rust,
  sql,
  typescript,
  xml,
  yaml,
};
for (const [name, definition] of Object.entries(languages)) {
  hljs.registerLanguage(name, definition);
}

// Files above this size are shown as plain text rather than slowing the page.
const MAX_CHARS = 500_000;

const FILENAMES: Record<string, string> = {
  makefile: "makefile",
  gnumakefile: "makefile",
  dockerfile: "dockerfile",
  containerfile: "dockerfile",
  ".bashrc": "bash",
  ".bash_profile": "bash",
  ".profile": "bash",
  ".zshrc": "bash",
};

const SHEBANGS: [RegExp, string][] = [
  [/\b(ba|z|k|da)?sh\b/, "bash"],
  [/\bpython/, "python"],
  [/\b(node|nodejs)\b/, "javascript"],
  [/\bruby\b/, "ruby"],
  [/\bperl\b/, "perl"],
];

/** The highlight.js language for a file, from its name or its shebang. */
export function languageFor(path: string, firstLine = ""): string | null {
  const name = (path.split("/").pop() ?? "").toLowerCase();
  const byName = FILENAMES[name];
  if (byName) return byName;
  if (/^(dockerfile|containerfile)\./.test(name)) return "dockerfile";
  const dot = name.lastIndexOf(".");
  if (dot > 0) {
    // Aliases cover sh, py, js, ts, yml, md, rs, toml (as ini) and so on.
    const extension = name.slice(dot + 1);
    if (hljs.getLanguage(extension)) return extension;
  }
  if (firstLine.startsWith("#!")) {
    for (const [pattern, language] of SHEBANGS) {
      if (pattern.test(firstLine)) return language;
    }
  }
  return null;
}

/**
 * Split highlighted HTML into one string per source line. A span that is open
 * at a line break is closed there and reopened on the next line, so every
 * line is valid HTML on its own.
 */
export function splitHighlightedLines(html: string): string[] {
  const tag = /<span[^>]*>|<\/span>/g;
  const open: string[] = [];
  return html.split("\n").map((text) => {
    const line = open.join("") + text;
    for (const match of text.matchAll(tag)) {
      if (match[0] === "</span>") open.pop();
      else open.push(match[0]);
    }
    return line + "</span>".repeat(open.length);
  });
}

/**
 * Highlighted HTML for each line of `code`, or null when the language is not
 * known or the file is too large. The HTML is produced and escaped by
 * highlight.js; the number of lines always matches `code.split("\n")`.
 */
export function highlightLines(code: string, path: string): string[] | null {
  if (!code || code.length > MAX_CHARS) return null;
  const language = languageFor(path, code.split("\n", 1)[0]);
  if (!language || !hljs.getLanguage(language)) return null;
  const lines = splitHighlightedLines(
    hljs.highlight(code, {language, ignoreIllegals: true}).value,
  );
  return lines.length === code.split("\n").length ? lines : null;
}
