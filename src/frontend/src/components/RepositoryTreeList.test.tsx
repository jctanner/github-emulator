import {render, screen, within} from "@testing-library/react";
import {MemoryRouter} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";

import {RepositoryTreeList} from "./RepositoryTreeList";

afterEach(() => vi.unstubAllGlobals());

const commit = (message: string, sha: string, date: string) => ({
  sha,
  short_sha: sha.slice(0, 7),
  message,
  author_name: "Octo",
  date,
});

describe("RepositoryTreeList", () => {
  it("shows the latest commit, the commit count and each entry's last commit", async () => {
    const hourAgo = new Date(Date.now() - 3600 * 1000).toISOString();
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({
        ref: "main",
        path: "",
        latest_commit: commit("Fix the thing", "abcdef1234567", hourAgo),
        entries: [
          {
            name: "docs",
            path: "docs",
            type: "dir",
            last_commit: commit("Add docs", "1111111aaaa", hourAgo),
          },
          {
            name: "app.py",
            path: "app.py",
            type: "file",
            last_commit: commit("Add app", "2222222bbbb", hourAgo),
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <RepositoryTreeList
          owner="octo"
          repo="demo"
          ref="main"
          commitCount={284}
        />
      </MemoryRouter>,
    );

    const files = await screen.findByRole("region", {name: "Repository files"});
    expect(within(files).queryByText("Files")).toBeNull();
    expect(within(files).getByText("Fix the thing")).toBeVisible();
    expect(within(files).getByRole("link", {name: "abcdef1"})).toHaveAttribute(
      "href",
      "/octo/demo/commit/abcdef1234567",
    );
    expect(
      within(files).getByRole("link", {name: /284 Commits/}),
    ).toHaveAttribute("href", "/octo/demo/commits/main");
    expect(within(files).getByText("Add docs")).toBeVisible();
    expect(within(files).getByText("Add app")).toBeVisible();
    expect(within(files).getAllByText("1 hour ago")).toHaveLength(2);
    expect(within(files).getByRole("link", {name: "docs"})).toHaveAttribute(
      "href",
      "/octo/demo/tree/main/docs",
    );

    const requested = new URL((fetchMock.mock.calls[0][0] as Request).url);
    expect(requested.searchParams.get("ref")).toBe("main");
    expect(requested.searchParams.get("path")).toBe("");
  });

  it("shows the author, hash and age, and a folder's header, parent row and History link", async () => {
    const weeksAgo = new Date(Date.now() - 22 * 86400 * 1000).toISOString();
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({
        ref: "main",
        path: "a/b",
        latest_commit: commit("Touch b", "abcdef1234567", weeksAgo),
        entries: [
          {
            name: "f.json",
            path: "a/b/f.json",
            type: "file",
            last_commit: commit("Touch b", "abcdef1234567", weeksAgo),
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <RepositoryTreeList
          owner="octo"
          repo="demo"
          ref="main"
          path="a/b"
          commitCount={284}
        />
      </MemoryRouter>,
    );

    const files = await screen.findByRole("region", {name: "Repository files"});
    expect(within(files).getByText("Octo")).toBeVisible();
    expect(
      within(files).getByText(/3 weeks ago/, {selector: ".tree-commit-meta"}),
    ).toBeVisible();
    expect(within(files).getByText("Last commit message")).toBeVisible();
    expect(within(files).getByText("Last commit date")).toBeVisible();
    expect(within(files).getByRole("link", {name: ".."})).toHaveAttribute(
      "href",
      "/octo/demo/tree/main/a",
    );
    expect(within(files).getByRole("link", {name: /History/})).toHaveAttribute(
      "href",
      "/octo/demo/commits/main?path=a%2Fb",
    );
    expect(within(files).queryByText(/Commits/)).toBeNull();
  });

  it("links the parent row of a top-level folder to the repository root", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        Response.json({
          ref: "main",
          path: "docs",
          latest_commit: null,
          entries: [],
        }),
      ),
    );

    render(
      <MemoryRouter>
        <RepositoryTreeList owner="octo" repo="demo" ref="main" path="docs" />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("link", {name: ".."})).toHaveAttribute(
      "href",
      "/octo/demo/tree/main",
    );
  });
});
