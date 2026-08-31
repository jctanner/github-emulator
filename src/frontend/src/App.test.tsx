import {render, screen} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";

import {App} from "./App";

afterEach(() => {
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
});
