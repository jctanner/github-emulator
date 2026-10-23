import {render, screen} from "@testing-library/react";
import {MemoryRouter, Route, Routes} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";

import {PullDetailPage} from "./PullDetailPage";
import {PullFilesPage} from "./PullFilesPage";

vi.mock("../auth/SessionContext", () => ({useSession: () => ({user: null})}));
vi.mock("../components/RepositoryContext", () => ({
  useRepositoryLayout: () => ({reloadNavigation: vi.fn()}),
}));
afterEach(() => vi.unstubAllGlobals());

const comment = {
  id: 20,
  pull_request_review_id: 70,
  user: {login: "markov-bot"},
  body: "Inline finding",
  created_at: "2026-10-23T10:00:00Z",
  path: "README.md",
  line: 13,
  side: "RIGHT",
  position: null,
  in_reply_to_id: null,
};
function mockApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/reviews"))
        return Response.json([
          {
            id: 70,
            user: {login: "markov-bot"},
            state: "COMMENTED",
            body: "Reviewed README",
            submitted_at: "2026-10-23T10:00:00Z",
          },
        ]);
      if (path.endsWith("/pulls/2/comments")) return Response.json([comment]);
      if (path.endsWith("/issues/2/comments"))
        return Response.json([
          {
            id: 1,
            user: {login: "admin"},
            body: "/mk-review",
            created_at: "2026-10-23T09:00:00Z",
          },
        ]);
      if (path.endsWith("/files"))
        return Response.json([
          {
            filename: "README.md",
            additions: 1,
            deletions: 0,
            patch: "@@ -12,0 +13 @@\n+hello",
          },
        ]);
      if (path.endsWith("/events") || path.endsWith("/labels"))
        return Response.json([]);
      if (path.endsWith("/issues/2")) return Response.json({labels: []});
      return Response.json({
        number: 2,
        title: "README fix",
        body: "Description",
        state: "open",
        user: {login: "admin"},
        created_at: "2026-10-23T08:00:00Z",
        head: {label: "feature", ref: "feature"},
        base: {label: "main"},
      });
    }),
  );
}

describe("pull request review pages", () => {
  it("loads API-posted reviews into the conversation with links to inline comments", async () => {
    mockApi();
    const {container} = render(
      <MemoryRouter initialEntries={["/experiment/demo/pulls/2"]}>
        <Routes>
          <Route
            path="/:owner/:repo/pulls/:number"
            element={<PullDetailPage />}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Reviewed README")).toBeVisible();
    expect(screen.getByText("Inline finding")).toBeVisible();
    expect(
      container.querySelector("#pullrequestreview-70"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", {name: "README.md:13 (new version)"}),
    ).toHaveAttribute("href", "/experiment/demo/pulls/2/files#discussion_r20");
    expect(
      screen
        .getByText("/mk-review")
        .compareDocumentPosition(screen.getByText("Reviewed README")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("loads review comments beside the changed line on Files changed", async () => {
    mockApi();
    const {container} = render(
      <MemoryRouter initialEntries={["/experiment/demo/pulls/2/files"]}>
        <Routes>
          <Route
            path="/:owner/:repo/pulls/:number/files"
            element={<PullFilesPage />}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Inline finding")).toBeVisible();
    expect(
      container.querySelector("#discussion_r20")?.previousElementSibling,
    ).toHaveTextContent("+hello");
  });
});
