import {render, screen} from "@testing-library/react";
import {MemoryRouter} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";

import {RepositoryActivity} from "./RepositoryActivity";

afterEach(() => vi.unstubAllGlobals());

describe("RepositoryActivity", () => {
  it("loads counts for and links to the selected branch", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({
        default_branch: "main",
        commit_count: 7,
        branch_count: 3,
        tag_count: 2,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <RepositoryActivity owner="octo" repo="demo" ref="feature/test" />
      </MemoryRouter>,
    );

    expect(await screen.findByText("7")).toBeVisible();
    expect(screen.getByRole("link", {name: /7 commits/})).toHaveAttribute(
      "href",
      "/octo/demo/commits/feature%2Ftest",
    );
    const requested = new URL(
      (fetchMock.mock.calls[0][0] as Request).url,
    );
    expect(requested.searchParams.get("ref")).toBe("feature/test");
  });
});
