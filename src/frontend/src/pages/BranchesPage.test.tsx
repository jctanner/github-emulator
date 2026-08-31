import {fireEvent, render, screen} from "@testing-library/react";
import {MemoryRouter, Route, Routes} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";

import type {components} from "../api/schema";
import {RepositoryContext} from "../components/RepositoryContext";
import {BranchesPage} from "./BranchesPage";

afterEach(() => vi.unstubAllGlobals());

describe("BranchesPage", () => {
  it("filters branches by name while preserving slash-safe links", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json([
          {name: "main", commit: {sha: "aaaaaaaa"}, protected: true},
          {
            name: "feature/one",
            commit: {sha: "bbbbbbbb"},
            protected: false,
          },
        ]),
      ),
    );
    const repository = {
      default_branch: "main",
    } as components["schemas"]["RepoResponse"];

    render(
      <RepositoryContext.Provider
        value={{repository, reload: vi.fn(), reloadNavigation: vi.fn()}}
      >
        <MemoryRouter initialEntries={["/octo/demo/branches"]}>
          <Routes>
            <Route path="/:owner/:repo/branches" element={<BranchesPage />} />
          </Routes>
        </MemoryRouter>
      </RepositoryContext.Provider>,
    );

    expect(await screen.findByRole("link", {name: "main"})).toBeVisible();
    fireEvent.change(screen.getByRole("searchbox", {name: "Search branches"}), {
      target: {value: "feature"},
    });

    expect(screen.queryByRole("link", {name: "main"})).not.toBeInTheDocument();
    expect(screen.getByRole("link", {name: "feature/one"})).toHaveAttribute(
      "href",
      "/octo/demo/tree/feature%2Fone",
    );
  });
});
