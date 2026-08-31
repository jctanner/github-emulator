import {fireEvent, render, screen} from "@testing-library/react";
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
});
