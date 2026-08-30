import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {MemoryRouter, Route, Routes} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";

import {RepositoryLayout} from "./RepositoryLayout";

afterEach(() => vi.unstubAllGlobals());

describe("RepositoryLayout", () => {
  it("keeps the repository shell mounted while child routes change", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = input instanceof Request ? input.url : input.toString();
      const payload = url.endsWith("/navigation")
        ? {open_issues_count: 3, open_pulls_count: 2}
        : {
            id: 1,
            name: "demo",
            full_name: "octo/demo",
            visibility: "public",
            owner: {login: "octo"},
          };
      return Promise.resolve(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: {"Content-Type": "application/json"},
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/octo/demo"]}>
        <Routes>
          <Route path="/:owner/:repo" element={<RepositoryLayout />}>
            <Route index element={<p>Code content</p>} />
            <Route path="issues" element={<p>Issue content</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Code content")).toBeVisible();
    expect(await screen.findByText("3")).toBeVisible();
    expect(await screen.findByText("2")).toBeVisible();
    const navigation = screen.getByRole("navigation", {name: "Repository"});
    fireEvent.click(screen.getByRole("link", {name: /Issues/}));
    expect(await screen.findByText("Issue content")).toBeVisible();
    expect(screen.getByRole("navigation", {name: "Repository"})).toBe(
      navigation,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const metadataRequests = fetchMock.mock.calls.filter(([input]) => {
      const url = input instanceof Request ? input.url : input.toString();
      return url.endsWith("/api/v3/repos/octo/demo");
    });
    expect(metadataRequests).toHaveLength(1);
  });
});
