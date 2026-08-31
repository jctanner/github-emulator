import {render, screen} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";

import {App} from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("App", () => {
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
