import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";

import {App} from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("App", () => {
  it("routes the site admin Apps page before repository coordinates", async () => {
    const requests: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((request: Request) => {
        const path = new URL(request.url).pathname;
        requests.push(path);
        if (path === "/api/_ui/session") {
          return Promise.resolve(
            Response.json({
              user: {
                id: 1,
                login: "admin",
                name: "Admin",
                email: null,
                avatar_url: "",
                html_url: "",
                site_admin: true,
              },
              csrf_token: "test-csrf",
            }),
          );
        }
        if (path === "/admin/api/apps") {
          return Promise.resolve(Response.json([]));
        }
        return Promise.resolve(Response.json({detail: "Not Found"}, {status: 404}));
      }),
    );
    window.history.replaceState({}, "", "/ui/_admin/apps");

    render(<App />);

    expect(await screen.findByRole("heading", {name: "GitHub Apps"})).toBeVisible();
    expect(requests).toContain("/admin/api/apps");
    expect(requests).not.toContain("/api/v3/repos/_admin/apps");
  });

  it("renders a typed not-found page for unknown routes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({detail: "unauthorized"}), {
            status: 401,
            headers: {"Content-Type": "application/json"},
          }),
        ),
      ),
    );
    window.history.replaceState({}, "", "/ui/example/repository/wiki");

    render(<App />);

    expect(await screen.findByText("Page not found")).toBeVisible();
    expect(
      screen.getByRole("link", {name: "Return to repositories"}),
    ).toHaveAttribute("href", "/ui/");
  });

  it("submits the navbar search to repository search", async () => {
    const requests: URL[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((request: Request) => {
        const url = new URL(request.url);
        requests.push(url);
        if (url.pathname === "/api/_ui/session") {
          return Promise.resolve(
            Response.json({user: null, csrf_token: "test-csrf"}),
          );
        }
        if (url.pathname === "/api/v3/search/repositories") {
          return Promise.resolve(
            Response.json({
              total_count: 1,
              incomplete_results: false,
              items: [
                {
                  id: 1,
                  full_name: "admin/ansible-agent-harness",
                  description: "Harness",
                },
              ],
            }),
          );
        }
        return Promise.resolve(Response.json([]));
      }),
    );
    window.history.replaceState({}, "", "/ui/");

    render(<App />);

    const search = await screen.findByRole("searchbox", {name: "Search"});
    fireEvent.change(search, {target: {value: "ansible harness"}});
    fireEvent.click(screen.getByRole("button", {name: "Submit search"}));

    expect(await screen.findByRole("heading", {name: "Search"})).toBeVisible();
    expect(
      await screen.findByRole("link", {name: "admin/ansible-agent-harness"}),
    ).toBeVisible();
    expect(window.location.pathname).toBe("/ui/search");
    expect(window.location.search).toBe("?q=ansible%20harness");
    expect(
      requests.some(
        (url) =>
          url.pathname === "/api/v3/search/repositories" &&
          url.searchParams.get("q") === "ansible harness",
      ),
    ).toBe(true);
  });
});
