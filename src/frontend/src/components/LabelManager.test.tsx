import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {MemoryRouter} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";

import {LabelManager} from "./LabelManager";

const labels = [
  {
    id: 1,
    name: "bug",
    color: "d73a4a",
    default: false,
    description: "Something is broken",
    node_id: "LA_1",
    url: "https://example.test/labels/bug",
  },
  {
    id: 2,
    name: "ready",
    color: "0e8a16",
    default: false,
    description: null,
    node_id: "LA_2",
    url: "https://example.test/labels/ready",
  },
];

afterEach(() => vi.unstubAllGlobals());

describe("LabelManager", () => {
  it("shows a compact label list and applies labels from the popover", async () => {
    const onChanged = vi.fn();
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify([labels[1]]), {
        status: 200,
        headers: {"Content-Type": "application/json"},
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <LabelManager
          owner="octo"
          repo="demo"
          issueNumber={7}
          subject="pull request"
          assigned={[labels[0]]}
          available={labels}
          onChanged={onChanged}
        />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("pull request labels")).toHaveTextContent(
      "bug",
    );
    expect(screen.queryByLabelText("Filter labels")).not.toBeVisible();
    fireEvent.click(screen.getByLabelText("Manage labels"));
    expect(screen.getByText("Apply labels to this pull request")).toBeVisible();

    fireEvent.click(screen.getByRole("checkbox", {name: "ready"}));
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    const request = fetchMock.mock.calls[0][0];
    expect(request).toBeInstanceOf(Request);
    if (!(request instanceof Request)) throw new Error("Expected a Request");
    expect(request.method).toBe("POST");
    expect(request.url).toContain("/issues/7/labels");
  });
});
