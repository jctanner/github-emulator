import {render, screen} from "@testing-library/react";
import {MemoryRouter} from "react-router-dom";
import {describe, expect, it} from "vitest";

import {FileDiffList} from "./FileDiffList";

describe("FileDiffList", () => {
  it("renders file totals, links, and colored patch lines", () => {
    const {container} = render(
      <MemoryRouter>
        <FileDiffList
          files={[
            {
              filename: "example.txt",
              additions: 1,
              deletions: 1,
              patch: "@@ -1 +1 @@\n-before\n+after",
            },
          ]}
          fileHref={(file) => `/blob/abc/${file.filename}`}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", {name: "example.txt"})).toHaveAttribute(
      "href",
      "/blob/abc/example.txt",
    );
    expect(screen.getByText("+1")).toBeInTheDocument();
    expect(screen.getByText("-1")).toBeInTheDocument();
    expect(container.querySelector("code.addition")).toHaveTextContent("+after");
    expect(container.querySelector("code.deletion")).toHaveTextContent("-before");
  });
});
