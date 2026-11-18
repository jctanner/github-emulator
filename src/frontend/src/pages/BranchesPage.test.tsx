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

  it.each([
    {total: 150, expectedPages: ["1", "2"]},
    {total: 100, expectedPages: ["1", "2"]},
  ])(
    "loads all $total branches across pages",
    async ({total, expectedPages}) => {
      const names = Array.from(
        {length: total},
        (_, index) => `branch-${String(index).padStart(3, "0")}`,
      );
      const fetchMock = vi.fn((request: Request) => {
        const url = new URL(request.url);
        const page = Number(url.searchParams.get("page"));
        const perPage = Number(url.searchParams.get("per_page"));
        const batch = names.slice((page - 1) * perPage, page * perPage);
        return Promise.resolve(
          Response.json(
            batch.map((name) => ({
              name,
              commit: {sha: "aaaaaaaa"},
              protected: false,
            })),
          ),
        );
      });
      vi.stubGlobal("fetch", fetchMock);
      const repository = {
        default_branch: "branch-000",
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

      const last = names[names.length - 1];
      expect(await screen.findByRole("link", {name: last})).toBeVisible();
      fireEvent.change(
        screen.getByRole("searchbox", {name: "Search branches"}),
        {target: {value: last}},
      );
      expect(screen.getByRole("link", {name: last})).toBeVisible();
      expect(screen.getAllByRole("link")).toHaveLength(1);
      const pages = fetchMock.mock.calls.map(([request]) =>
        new URL(request.url).searchParams.get("page"),
      );
      expect(pages).toEqual(expectedPages);
    },
  );
});
