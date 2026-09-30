import {render, screen} from "@testing-library/react";
import {MemoryRouter} from "react-router-dom";
import {describe, expect, it} from "vitest";

import {RepositoryActivity} from "./RepositoryActivity";

const summary = {
  default_branch: "main",
  commit_count: 7,
  branch_count: 3,
  tag_count: 2,
};

describe("RepositoryActivity", () => {
  it("shows branch and tag counts and links to their lists", () => {
    render(
      <MemoryRouter>
        <RepositoryActivity
          owner="octo"
          repo="demo"
          summary={{data: summary, loading: false}}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", {name: /3 branches/})).toHaveAttribute(
      "href",
      "/octo/demo/branches",
    );
    expect(screen.getByRole("link", {name: /2 tags/})).toHaveAttribute(
      "href",
      "/octo/demo/tags",
    );
    expect(screen.queryByText(/commits/i)).toBeNull();
  });

  it("shows placeholders while the counts load", () => {
    render(
      <MemoryRouter>
        <RepositoryActivity
          owner="octo"
          repo="demo"
          summary={{data: null, loading: true}}
        />
      </MemoryRouter>,
    );

    expect(screen.getAllByText("…")).toHaveLength(2);
  });
});
