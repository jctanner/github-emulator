import {fireEvent, render, screen, within} from "@testing-library/react";
import {MemoryRouter, Route, Routes} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";

import {CodeBrowserPage} from "./CodeBrowserPage";

afterEach(() => vi.unstubAllGlobals());

describe("CodeBrowserPage", () => {
  it("renders branch files, counts, and README using the selected ref", async () => {
    const requested: URL[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: Request) => {
        const url = new URL(input.url);
        requested.push(url);
        if (url.pathname.endsWith("/branches")) {
          return Promise.resolve(
            Response.json([
              {name: "main", commit: {sha: "a"}},
              {name: "feature/one", commit: {sha: "b"}},
            ]),
          );
        }
        if (url.pathname.endsWith("/summary")) {
          return Promise.resolve(
            Response.json({
              default_branch: "main",
              commit_count: 7,
              branch_count: 2,
              tag_count: 0,
            }),
          );
        }
        if (url.pathname.endsWith("/tree")) {
          return Promise.resolve(
            Response.json({
              ref: "feature/one",
              path: "",
              latest_commit: {
                sha: "abcdef1234567",
                short_sha: "abcdef1",
                message: "Add docs",
                author_name: "Octo",
                date: "2026-09-01T00:00:00Z",
              },
              entries: [{name: "docs", path: "docs", type: "dir"}],
            }),
          );
        }
        if (url.pathname.endsWith("/readme")) {
          return Promise.resolve(
            Response.json({
              type: "file",
              path: "README.md",
              content: "IyBGZWF0dXJlIFJFQURNRQ==",
            }),
          );
        }
        return Promise.resolve(
          Response.json([{type: "dir", name: "docs", path: "docs"}]),
        );
      }),
    );

    render(
      <MemoryRouter initialEntries={["/octo/demo/tree/feature%2Fone"]}>
        <Routes>
          <Route
            path="/:owner/:repo/tree/:ref/*"
            element={<CodeBrowserPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("# Feature README")).toBeVisible();
    expect(await screen.findByRole("link", {name: "docs"})).toHaveAttribute(
      "href",
      "/octo/demo/tree/feature%2Fone/docs",
    );
    expect(await screen.findByText("7")).toBeVisible();
    expect(screen.getByRole("link", {name: /7 Commits/})).toHaveAttribute(
      "href",
      "/octo/demo/commits/feature%2Fone",
    );
    const refRequests = requested.filter((url) =>
      ["/readme", "/summary", "/contents/", "/tree"].some((suffix) =>
        url.pathname.endsWith(suffix),
      ),
    );
    expect(refRequests.length).toBeGreaterThanOrEqual(3);
    expect(
      refRequests.every((url) => url.searchParams.get("ref") === "feature/one"),
    ).toBe(true);
  });

  it("links every breadcrumb segment, copies the path, and omits root counts in a folder", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", {clipboard: {writeText}});
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: Request) => {
        const url = new URL(input.url);
        if (url.pathname.endsWith("/branches")) {
          return Promise.resolve(Response.json([]));
        }
        if (url.pathname.endsWith("/tree")) {
          return Promise.resolve(
            Response.json({
              ref: "main",
              path: "a/b",
              latest_commit: null,
              entries: [],
            }),
          );
        }
        return Promise.resolve(Response.json([]));
      }),
    );

    render(
      <MemoryRouter initialEntries={["/octo/demo/tree/main/a/b"]}>
        <Routes>
          <Route
            path="/:owner/:repo/tree/:ref/*"
            element={<CodeBrowserPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    const path = await screen.findByLabelText("Path");
    expect(within(path).getByRole("link", {name: "demo"})).toHaveAttribute(
      "href",
      "/octo/demo/tree/main",
    );
    expect(within(path).getByRole("link", {name: "a"})).toHaveAttribute(
      "href",
      "/octo/demo/tree/main/a",
    );
    expect(within(path).getByText("b").tagName).toBe("STRONG");
    expect(within(path).getAllByText("/")).toHaveLength(3);
    expect(
      screen.queryByRole("navigation", {name: "Repository activity"}),
    ).toBeNull();

    fireEvent.click(within(path).getByRole("button", {name: "Copy path"}));
    expect(writeText).toHaveBeenCalledWith("a/b");
  });
});

describe("CodeBrowserPage file view", () => {
  const render_ = (
    path: string,
    content: string,
    downloadUrl: string | null = null,
  ) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: Request) => {
        const url = new URL(input.url);
        if (url.pathname.endsWith("/branches")) {
          return Promise.resolve(Response.json([]));
        }
        return Promise.resolve(
          Response.json({
            type: "file",
            name: path,
            path,
            content: btoa(content),
            download_url: downloadUrl,
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={[`/octo/demo/blob/main/${path}`]}>
        <Routes>
          <Route
            path="/:owner/:repo/blob/:ref/*"
            element={<CodeBrowserPage blob />}
          />
        </Routes>
      </MemoryRouter>,
    );
  };

  it("syntax-highlights a code file and keeps every line", async () => {
    render_("scripts/run.sh", '#!/bin/bash\n# note\necho "hi"\n');

    const keyword = await screen.findByText("echo");
    expect(keyword).toHaveClass("hljs-built_in");
    expect(screen.getByText("# note")).toHaveClass("hljs-comment");
    expect(document.querySelectorAll(".code-lines li")).toHaveLength(4);
  });

  it("shows a file of unknown type as plain text", async () => {
    render_("notes.unknownext", "just <b>text</b>\n");

    expect(await screen.findByText("just <b>text</b>")).toBeVisible();
    expect(document.querySelector(".code-lines [class^='hljs-']")).toBeNull();
  });

  it("renders a Markdown file, resolving relative links, with its source a click away", async () => {
    render_(
      "docs/guide.md",
      "# Guide\n\nSee [the readme](../README.md), [setup](setup.md#install), " +
        "[the docs](./) and [Markov](https://example.com/markov).\n\n![diagram](img/flow.png)\n",
      "https://github.local/octo/demo/raw/main/docs/guide.md",
    );

    expect(
      await screen.findByRole("heading", {name: "Guide"}),
    ).toBeInTheDocument();
    const href = (name: string) =>
      screen.getByRole("link", {name}).getAttribute("href");
    expect(href("the readme")).toBe("/ui/octo/demo/blob/main/README.md");
    expect(href("setup")).toBe("/ui/octo/demo/blob/main/docs/setup.md#install");
    expect(href("the docs")).toBe("/ui/octo/demo/tree/main/docs");
    expect(href("Markov")).toBe("https://example.com/markov");
    expect(screen.getByRole("img", {name: "diagram"})).toHaveAttribute(
      "src",
      "https://github.local/octo/demo/raw/main/docs/img/flow.png",
    );
    expect(document.querySelector(".code-lines")).toBeNull();

    fireEvent.click(screen.getByRole("button", {name: "Code"}));
    expect(screen.getByText("# Guide")).toBeInTheDocument();
    expect(document.querySelector(".markdown-rendered")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "Preview"}));
    expect(screen.getByRole("heading", {name: "Guide"})).toBeInTheDocument();
  });
});
