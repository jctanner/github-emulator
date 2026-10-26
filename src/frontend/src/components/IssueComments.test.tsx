import {fireEvent, render, screen} from "@testing-library/react";
import {MemoryRouter} from "react-router-dom";
import {describe, expect, it, vi} from "vitest";

import {IssueComments} from "./IssueComments";

vi.mock("../auth/SessionContext", () => ({
  useSession: () => ({
    user: {login: "octocat", site_admin: false},
    loading: false,
  }),
}));

type Props = Parameters<typeof IssueComments>[0];

const comment = {
  id: 12,
  body: "A useful comment",
  created_at: "2026-08-30T10:00:00Z",
  updated_at: "2026-08-30T10:00:00Z",
  user: {login: "octocat"},
} as Props["comments"][number];

const labelEvent = {
  id: 21,
  event: "labeled",
  created_at: "2026-08-30T11:00:00Z",
  actor: {login: "octocat"},
  label: {
    id: 3,
    name: "documentation",
    color: "0075ca",
    description: "Documentation work",
    default: false,
  },
} as Props["events"][number];

function crossReference(
  issue: Record<string, unknown>,
  createdAt = "2026-10-26T10:00:00Z",
) {
  return {
    event: "cross-referenced",
    actor: {login: "markov-bot"},
    created_at: createdAt,
    source: {
      type: "issue",
      issue: {
        repository_url: "https://github.local/api/v3/repos/acme/app",
        state: "open",
        ...issue,
      },
    },
  } as unknown as Props["events"][number];
}

describe("IssueComments", () => {
  it("puts author controls in a kebab menu and renders label history inline", () => {
    render(
      <IssueComments
        owner="octocat"
        repo="hello-world"
        issueNumber={7}
        comments={[comment]}
        events={[labelEvent]}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByText("A useful comment")).toBeVisible();
    expect(screen.getByText("added")).toBeVisible();
    expect(screen.getByText("documentation")).toBeVisible();
    expect(
      screen.queryByRole("button", {name: "Edit"}),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Actions for octocat's comment"));
    expect(screen.getByRole("menuitem", {name: "Edit"})).toBeVisible();
    expect(screen.getByRole("menuitem", {name: "Delete"})).toBeVisible();
  });

  it("links each cross-reference back to the issue or pull request that mentioned it", () => {
    render(
      <MemoryRouter>
        <IssueComments
          owner="acme"
          repo="app"
          issueNumber={1}
          comments={[]}
          events={[
            crossReference({
              id: 11,
              number: 2,
              title: "Fix the bug",
              state: "closed",
              pull_request: {merged_at: "2026-10-26T11:00:00Z"},
            }),
            crossReference(
              {id: 12, number: 3, title: "Related report"},
              "2026-10-26T12:00:00Z",
            ),
          ]}
          onChanged={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getAllByText(/mentioned this issue/)).toHaveLength(2);
    const pull = screen.getByRole("link", {name: /Fix the bug/});
    expect(pull).toHaveAttribute("href", "/acme/app/pulls/2");
    expect(pull.querySelector(".xref-merged")).not.toBeNull();
    expect(screen.getByRole("link", {name: /Related report/})).toHaveAttribute(
      "href",
      "/acme/app/issues/3",
    );
  });
});
