import {render, screen} from "@testing-library/react";
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
        if (url.pathname.endsWith("/readme")) {
          return Promise.resolve(
            Response.json({type: "file", path: "README.md", content: "IyBGZWF0dXJlIFJFQURNRQ=="}),
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
    expect(screen.getByRole("link", {name: "docs"})).toHaveAttribute(
      "href",
      "/octo/demo/tree/feature%2Fone/docs",
    );
    expect(await screen.findByText("7")).toBeVisible();
    expect(screen.getByRole("link", {name: /7 commits/})).toHaveAttribute(
      "href",
      "/octo/demo/commits/feature%2Fone",
    );
    const refRequests = requested.filter((url) =>
      ["/readme", "/summary", "/contents/"].some((suffix) =>
        url.pathname.endsWith(suffix),
      ),
    );
    expect(refRequests.length).toBeGreaterThanOrEqual(3);
    expect(refRequests.every((url) => url.searchParams.get("ref") === "feature/one")).toBe(true);
  });
});
