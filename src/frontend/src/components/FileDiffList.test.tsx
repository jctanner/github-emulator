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
    expect(container.querySelector("code.addition")).toHaveTextContent(
      "+after",
    );
    expect(container.querySelector("code.deletion")).toHaveTextContent(
      "-before",
    );
  });
});

it("keeps left/right anchors, replies, legacy positions, and unavailable locations visible", () => {
  const base = {
    pull_request_review_id: 70,
    user: {login: "reviewer"},
    created_at: "2026-10-23T10:00:00Z",
    path: "example.txt",
    line: null,
    position: null,
    side: "RIGHT",
    in_reply_to_id: null,
  };
  const {container} = render(
    <MemoryRouter>
      <FileDiffList
        files={[
          {
            filename: "example.txt",
            additions: 2,
            deletions: 1,
            patch: "@@ -5,2 +5,2 @@\n-old\n+new\n same\n@@ -20 +20 @@\n+later",
          },
        ]}
        fileHref={() => "/file"}
        reviewComments={[
          {...base, id: 1, line: 5, side: "LEFT", body: "Removed line"},
          {...base, id: 2, line: 5, body: "Added line"},
          {...base, id: 3, in_reply_to_id: 2, body: "Reply"},
          {...base, id: 4, position: 5, body: "Legacy later hunk"},
          {...base, id: 5, line: 999, body: "Outside patch"},
          {...base, id: 6, subject_type: "file", body: "Whole file"},
          {
            ...base,
            id: 7,
            path: "removed.txt",
            line: 1,
            body: "Unavailable file",
          },
        ]}
      />
    </MemoryRouter>,
  );
  expect(
    container.querySelector("#discussion_r1")?.previousElementSibling,
  ).toHaveTextContent("-old");
  expect(
    container.querySelector("#discussion_r2")?.previousElementSibling,
  ).toHaveTextContent("+new");
  expect(
    container.querySelector("#discussion_r3")?.previousElementSibling,
  ).toHaveAttribute("id", "discussion_r2");
  expect(
    container.querySelector("#discussion_r4")?.previousElementSibling,
  ).toHaveTextContent("+later");
  expect(
    screen.getByRole("region", {name: "Additional comments on example.txt"}),
  ).toHaveTextContent("Outside patch");
  expect(screen.getByText("Whole file")).toBeVisible();
  expect(screen.getByText("Unavailable file")).toBeVisible();
});
