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
    let registeredAppBody: unknown = null;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (request: Request) => {
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
        if (path === "/admin/api/apps/1002") {
          return Response.json({
            app_id: "1002",
            name: "Example App",
            slug: "example-app",
            owner: "admin",
            client_id: "Iv1.new-client",
            installations_count: 0,
            has_private_key: true,
            created_at: "2026-08-31T12:20:00Z",
            installations: [],
          });
        }
        if (path === "/admin/api/apps/1001") {
          return Promise.resolve(
            Response.json({
              app_id: "1001",
              name: "Fullsend Triage",
              slug: "fullsend-triage",
              owner: "admin",
              client_id: "Iv1.test-client",
              installations_count: 1,
              has_private_key: true,
              created_at: "2026-08-31T12:00:00Z",
              installations: [
                {
                  id: 7,
                  app_id: "1001",
                  owner: "admin",
                  repo: "ansible-agent-harness",
                  repositories: ["admin/ansible-agent-harness"],
                  created_at: "2026-08-31T12:10:00Z",
                },
              ],
            }),
          );
        }
        if (path === "/admin/api/apps" && request.method === "POST") {
          registeredAppBody = await request.json();
          return Response.json(
            {
              app_id: "1002",
              name: "Example App",
              slug: "example-app",
              owner: "admin",
              client_id: "Iv1.new-client",
              installations_count: 0,
              private_key: "TEST PRIVATE KEY",
            },
            {status: 201},
          );
        }
        if (path === "/admin/api/apps") {
          return Promise.resolve(
            Response.json([
              {
                app_id: "1001",
                name: "Fullsend Triage",
                slug: "fullsend-triage",
                owner: "admin",
                client_id: "Iv1.test-client",
                installations_count: 1,
              },
            ]),
          );
        }
        if (path === "/admin/api/users") {
          return Promise.resolve(
            Response.json([{id: 1, login: "admin", site_admin: true}]),
          );
        }
        if (path === "/admin/api/organizations") {
          return Promise.resolve(
            Response.json([{id: 2, login: "fullsend-dev"}]),
          );
        }
        if (path === "/admin/api/repositories") {
          return Promise.resolve(
            Response.json([
              {
                id: 3,
                name: "ansible-agent-harness",
                full_name: "admin/ansible-agent-harness",
                default_branch: "main",
                owner_type: "User",
                private: false,
              },
              {
                id: 4,
                name: "triage-target",
                full_name: "fullsend-dev/triage-target",
                default_branch: "main",
                owner_type: "Organization",
                private: false,
              },
            ]),
          );
        }
        return Promise.resolve(Response.json({detail: "Not Found"}, {status: 404}));
      }),
    );
    window.history.replaceState({}, "", "/ui/_admin/apps");

    render(<App />);

    expect(await screen.findByRole("heading", {name: "GitHub Apps"})).toBeVisible();
    const account = screen.getByLabelText("Account");
    const repository = screen.getByLabelText("Repository");
    expect(account).toHaveAttribute("list", "app-installation-accounts");
    expect(repository).toBeDisabled();
    fireEvent.change(account, {target: {value: "admin"}});
    expect(repository).not.toBeDisabled();
    expect(
      Array.from(
        document.querySelectorAll<HTMLOptionElement>(
          "#app-installation-repositories option",
        ),
      ).map((option) => option.value),
    ).toEqual(["ansible-agent-harness"]);
    fireEvent.click(screen.getByText("Register new GitHub App"));
    expect(screen.getByRole("dialog", {name: "Register a GitHub App"})).toBeVisible();
    expect(screen.getByLabelText(/GitHub App name/)).toBeVisible();
    expect(screen.getByLabelText("Repository contents")).toHaveValue("");
    expect(screen.getByLabelText("Issues")).toHaveValue("");
    expect(screen.getByLabelText("Pull requests")).toHaveValue("");
    expect(screen.getByLabelText("Metadata")).toHaveValue("read");
    expect(screen.getByLabelText("Metadata")).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/GitHub App name/), {
      target: {value: "Example App"},
    });
    fireEvent.change(screen.getByLabelText("Repository contents"), {
      target: {value: "read"},
    });
    fireEvent.change(screen.getByLabelText("Issues"), {
      target: {value: "write"},
    });
    fireEvent.click(screen.getByRole("button", {name: "Create GitHub App"}));
    expect(await screen.findByText("TEST PRIVATE KEY")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(registeredAppBody).toEqual({
      name: "Example App",
      permissions: {contents: "read", issues: "write", metadata: "read"},
    });
    fireEvent.click(screen.getByRole("button", {name: "View details"}));
    expect(await screen.findByText("Iv1.test-client")).toBeVisible();
    expect(screen.getByText("admin/ansible-agent-harness")).toBeVisible();
    expect(screen.getByText("Installation #7")).toBeVisible();
    expect(requests).toContain("/admin/api/apps");
    expect(requests).toContain("/admin/api/apps/1001");
    expect(requests).toContain("/admin/api/users");
    expect(requests).toContain("/admin/api/organizations");
    expect(requests).toContain("/admin/api/repositories");
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
