import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {MemoryRouter, Route, Routes} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";

import {SessionProvider} from "../auth/SessionContext";
import {NewRepositoryPage} from "./NewRepositoryPage";

const session = {
  user: {
    id: 1,
    login: "alice",
    name: "Alice",
    email: null,
    avatar_url: "",
    html_url: "",
    site_admin: false,
  },
  csrf_token: "test-csrf",
};

function stubFetch(orgs: string[]) {
  const posts: {path: string; body: unknown}[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/_ui/session") return Response.json(session);
      if (path === "/api/v3/user/orgs")
        return Response.json(
          orgs.map((login, index) => ({
            login,
            id: index + 10,
            node_id: `O_${index}`,
            url: "",
            repos_url: "",
            description: null,
          })),
        );
      if (request.method === "POST") {
        posts.push({path, body: await request.json()});
        const owner = path.startsWith("/api/v3/orgs/")
          ? path.split("/")[4]
          : "alice";
        return Response.json({full_name: `${owner}/demo`}, {status: 201});
      }
      return Response.json({}, {status: 404});
    }),
  );
  return posts;
}

function renderPage(initial = "/new") {
  return render(
    <SessionProvider>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/new" element={<NewRepositoryPage />} />
          <Route path="/:owner/:repo" element={<p>repository page</p>} />
        </Routes>
      </MemoryRouter>
    </SessionProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("NewRepositoryPage", () => {
  it("offers the user and their organisations as owners and creates under the user by default", async () => {
    const posts = stubFetch(["fullsend-dev", "phase2"]);
    renderPage();
    expect(
      await screen.findByRole("option", {name: "alice (you)"}),
    ).toBeVisible();
    expect(
      await screen.findByRole("option", {name: "fullsend-dev"}),
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText("Repository name"), {
      target: {value: "demo"},
    });
    expect(screen.getByText(/Will be created as/)).toHaveTextContent(
      "alice/demo",
    );
    fireEvent.click(screen.getByRole("button", {name: "Create repository"}));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe("/api/v3/user/repos");
    expect(await screen.findByText("repository page")).toBeVisible();
  });

  it("creates under the chosen organisation through the organisation route", async () => {
    const posts = stubFetch(["fullsend-dev"]);
    renderPage();
    await screen.findByRole("option", {name: "fullsend-dev"});
    fireEvent.change(screen.getByLabelText("Owner"), {
      target: {value: "fullsend-dev"},
    });
    fireEvent.change(screen.getByLabelText("Repository name"), {
      target: {value: "demo"},
    });
    expect(screen.getByText(/Will be created as/)).toHaveTextContent(
      "fullsend-dev/demo",
    );
    fireEvent.click(screen.getByRole("button", {name: "Create repository"}));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].path).toBe("/api/v3/orgs/fullsend-dev/repos");
    expect(posts[0].body).toMatchObject({
      name: "demo",
      auto_init: true,
      private: false,
    });
  });

  it("preselects an owner named in the query string", async () => {
    stubFetch(["fullsend-dev"]);
    renderPage("/new?owner=fullsend-dev");
    await screen.findByRole("option", {name: "fullsend-dev"});
    expect(screen.getByLabelText("Owner")).toHaveValue("fullsend-dev");
  });
});
