import {fireEvent, render, screen} from "@testing-library/react";
import {MemoryRouter} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";

import {BranchSelector} from "./BranchSelector";

afterEach(() => vi.unstubAllGlobals());

describe("BranchSelector", () => {
  it("filters branches and safely links names containing slashes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json([
          {name: "main", commit: {sha: "a"}},
          {name: "feature/one", commit: {sha: "b"}},
          {name: "release/two", commit: {sha: "c"}},
        ]),
      ),
    );
    render(
      <MemoryRouter>
        <BranchSelector owner="octo" repo="demo" currentRef="main" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByLabelText("Switch branches"));
    expect(
      await screen.findByRole("link", {name: "feature/one"}),
    ).toHaveAttribute("href", "/octo/demo/tree/feature%2Fone");

    fireEvent.change(screen.getByRole("searchbox", {name: "Filter branches"}), {
      target: {value: "release"},
    });
    expect(screen.queryByRole("link", {name: "feature/one"})).toBeNull();
    expect(screen.getByRole("link", {name: "release/two"})).toBeVisible();
  });
});
