import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {MemoryRouter, Route, Routes} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";

import {PullDetailPage} from "./PullDetailPage";
import {PullFilesPage} from "./PullFilesPage";

const session = vi.hoisted(() => ({user: null as {login: string} | null}));
vi.mock("../auth/SessionContext", () => ({useSession: () => session}));
vi.mock("../components/RepositoryContext", () => ({
  useRepositoryLayout: () => ({reloadNavigation: vi.fn()}),
}));
afterEach(() => {
  vi.unstubAllGlobals();
  session.user = null;
});

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
function mockApi(posted: unknown[] = []) {
  const issueComments = [
    {
      id: 1,
      user: {login: "admin"},
      body: "/mk-review",
      created_at: "2026-10-23T09:00:00Z",
    },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (request.method === "POST" && path.endsWith("/reviews")) {
        posted.push(await request.json());
        return Response.json({id: 71, state: "APPROVED"}, {status: 201});
      }
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
        return Response.json(issueComments);
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

  it("submits an approval from Files changed", async () => {
    const posted: unknown[] = [];
    mockApi(posted);
    session.user = {login: "reviewer"};
    render(
      <MemoryRouter initialEntries={["/experiment/demo/pulls/2/files"]}>
        <Routes>
          <Route
            path="/:owner/:repo/pulls/:number/files"
            element={<PullFilesPage />}
          />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(
      await screen.findByRole("button", {name: /Review changes/}),
    );
    fireEvent.change(screen.getByLabelText("Review summary"), {
      target: {value: "Looks good"},
    });
    fireEvent.click(screen.getByRole("radio", {name: /Approve/}));
    fireEvent.click(screen.getByRole("button", {name: "Submit review"}));
    await waitFor(() =>
      expect(posted).toEqual([{event: "APPROVE", body: "Looks good"}]),
    );
  });

  it("lets authors only comment on their own pull request", async () => {
    mockApi();
    session.user = {login: "admin"};
    render(
      <MemoryRouter initialEntries={["/experiment/demo/pulls/2/files"]}>
        <Routes>
          <Route
            path="/:owner/:repo/pulls/:number/files"
            element={<PullFilesPage />}
          />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(
      await screen.findByRole("button", {name: /Review changes/}),
    );
    expect(screen.getByRole("radio", {name: /Comment/})).toBeEnabled();
    expect(screen.getByRole("radio", {name: /Approve/})).toBeDisabled();
    expect(screen.getByRole("radio", {name: /Request changes/})).toBeDisabled();
  });

  it("shows each reviewer's verdict in the conversation sidebar", async () => {
    mockApi();
    render(
      <MemoryRouter initialEntries={["/experiment/demo/pulls/2"]}>
        <Routes>
          <Route
            path="/:owner/:repo/pulls/:number"
            element={<PullDetailPage />}
          />
        </Routes>
      </MemoryRouter>,
    );
    const reviewers = (
      await screen.findByRole("heading", {name: "Reviewers"})
    ).closest("section")!;
    expect(reviewers).toHaveTextContent("markov-botCommented");
  });
});
