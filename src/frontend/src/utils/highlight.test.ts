import {describe, expect, it} from "vitest";

import {highlightLines, languageFor, splitHighlightedLines} from "./highlight";

const text = (html: string) =>
  new DOMParser().parseFromString(html, "text/html").body.textContent;

describe("languageFor", () => {
  it.each([
    ["scripts/run.sh", "", "sh"],
    ["fullsend.shim/verify-run.py", "", "py"],
    ["Makefile", "", "makefile"],
    ["Dockerfile", "", "dockerfile"],
    ["Dockerfile.dev", "", "dockerfile"],
    ["config.yml", "", "yml"],
    ["tool", "#!/usr/bin/env python3", "python"],
    ["runner", "#!/bin/bash", "bash"],
  ])("%s -> %s", (path, firstLine, expected) => {
    expect(languageFor(path, firstLine)).toBe(expected);
  });

  it("returns null when the language is unknown", () => {
    expect(languageFor("notes.unknownext")).toBeNull();
    expect(languageFor("LICENSE")).toBeNull();
    expect(languageFor("data", "just text")).toBeNull();
  });
});

describe("splitHighlightedLines", () => {
  it("reopens a span that is open at a line break", () => {
    expect(splitHighlightedLines('<span class="s">a\nb</span>\nc')).toEqual([
      '<span class="s">a</span>',
      '<span class="s">b</span>',
      "c",
    ]);
  });

  it("keeps nested spans balanced across lines", () => {
    const lines = splitHighlightedLines(
      '<span class="a"><span class="b">x\ny</span> z</span>',
    );
    expect(lines).toEqual([
      '<span class="a"><span class="b">x</span></span>',
      '<span class="a"><span class="b">y</span> z</span>',
    ]);
  });
});

describe("highlightLines", () => {
  it("colours a shell script and keeps the text and line count", () => {
    const code =
      '#!/bin/bash\n# hi\nset -euo pipefail\necho "$HOME" | grep x\n';
    const lines = highlightLines(code, "run.sh");
    expect(lines).not.toBeNull();
    expect(lines).toHaveLength(code.split("\n").length);
    expect(lines?.[1]).toContain("hljs-comment");
    expect(lines?.[2]).toContain("hljs-built_in");
    expect(lines?.map((line) => text(line)).join("\n")).toBe(code);
  });

  it("splits a multi-line Python docstring into balanced lines", () => {
    const code = 'def f():\n    """one\n    two"""\n    return 1\n';
    const lines = highlightLines(code, "x.py") ?? [];
    expect(lines).toHaveLength(code.split("\n").length);
    for (const line of lines) {
      expect((line.match(/<span/g) ?? []).length).toBe(
        (line.match(/<\/span>/g) ?? []).length,
      );
    }
    expect(lines[1]).toContain("hljs-string");
    expect(lines[2]).toContain("hljs-string");
  });

  it("escapes the source", () => {
    const lines = highlightLines("<script>alert(1)</script>\n", "page.html");
    expect(lines?.join("")).not.toContain("<script>");
  });

  it("returns null for unknown languages, empty files and oversized files", () => {
    expect(highlightLines("plain\n", "notes.unknownext")).toBeNull();
    expect(highlightLines("", "run.sh")).toBeNull();
    expect(highlightLines("x".repeat(500_001), "run.sh")).toBeNull();
  });
});
