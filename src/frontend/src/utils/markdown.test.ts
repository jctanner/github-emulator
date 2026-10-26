import {describe, expect, it} from "vitest";

import {renderMarkdown} from "./markdown";

describe("renderMarkdown", () => {
  it("renders GitHub-flavored Markdown", () => {
    const html = renderMarkdown(
      "# Title\n\n**bold** and `code`\n\n- [x] done\n- [ ] todo\n\n| a | b |\n| - | - |\n| 1 | 2 |",
    );
    expect(html).toContain("<h1>Title</h1>");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<code>code</code>");
    expect(html).toContain('type="checkbox"');
    expect(html).toContain("<table>");
  });

  it("treats a newline in a comment as a line break, but not in a file", () => {
    expect(renderMarkdown("one\ntwo")).toContain("<br>");
    expect(renderMarkdown("one\ntwo", {file: true})).not.toContain("<br>");
  });

  it("highlights fenced code in a known language", () => {
    const html = renderMarkdown("```python\nimport os\n```");
    expect(html).toContain('class="hljs language-python"');
    expect(html).toContain("hljs-keyword");
    expect(renderMarkdown("```nosuchlang\nx\n```")).not.toContain("hljs");
  });

  it("strips scripts, handlers and javascript links", () => {
    const html = renderMarkdown(
      '<script>alert(1)</script><img src=x onerror="alert(1)">\n\n[x](javascript:alert(1))',
    );
    expect(html).not.toContain("<script");
    expect(html).not.toContain("onerror");
    expect(html).toContain("<a>x</a>");
  });

  it("opens outside links in a new tab", () => {
    const html = renderMarkdown("[site](https://example.com) and [here](#top)");
    expect(html).toContain(
      '<a href="https://example.com" target="_blank" rel="noopener noreferrer">',
    );
    expect(html).toContain('<a href="#top">');
  });
});
