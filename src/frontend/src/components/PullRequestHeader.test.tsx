import {render, screen} from "@testing-library/react";
import {MemoryRouter} from "react-router-dom";
import {describe, expect, it} from "vitest";

import {PullRequestHeader} from "./PullRequestHeader";

type Pull = Parameters<typeof PullRequestHeader>[0]["pull"];

describe("PullRequestHeader", () => {
  it("links the commit and changed-file counts to their PR views", () => {
    const pull = {
      number: 12,
      title: "Improve the harness",
      state: "open",
      head: {label: "octocat:feature"},
      base: {label: "octocat:main"},
      commits: 2,
      changed_files: 7,
    } as Pull;

    render(
      <MemoryRouter initialEntries={["/octocat/demo/pulls/12"]}>
        <PullRequestHeader owner="octocat" repo="demo" pull={pull} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", {name: "Conversation"})).toHaveAttribute(
      "href",
      "/octocat/demo/pulls/12",
    );
    expect(screen.getByRole("link", {name: "Commits 2"})).toHaveAttribute(
      "href",
      "/octocat/demo/pulls/12/commits",
    );
    expect(screen.getByRole("link", {name: "Files changed 7"})).toHaveAttribute(
      "href",
      "/octocat/demo/pulls/12/files",
    );
  });
});
