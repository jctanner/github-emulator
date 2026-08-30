import {render, screen} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";

import {App} from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("App", () => {
  it("renders a typed migration fallback with a legacy-route link", async () => {
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

    expect(
      await screen.findByText("This route has not migrated yet"),
    ).toBeVisible();
    expect(
      screen.getByRole("link", {name: "Open this route in the legacy UI"}),
    ).toHaveAttribute("href", "/ui-legacy/example/repository/wiki");
  });
});
