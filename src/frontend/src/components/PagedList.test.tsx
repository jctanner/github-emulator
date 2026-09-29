import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";

import {PagedList} from "./PagedList";
import type {ListQuery, Page} from "./PagedList";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

interface Item {
  id: number;
  name: string;
}

function page(items: Item[], total: number, pageNumber: number): Page<Item> {
  return {total_count: total, page: pageNumber, per_page: 2, items};
}

describe("PagedList", () => {
  it("asks for unfinished items first, then all, with the page reset", async () => {
    const queries: ListQuery[] = [];
    const load = vi.fn((query: ListQuery) => {
      queries.push(query);
      return Promise.resolve(page([{id: 1, name: "one"}], 1, query.page));
    });
    render(
      <PagedList<Item>
        storageKey="t"
        load={load}
        perPage={2}
        row={(item) => <div key={item.id}>{item.name}</div>}
        emptyTitle="Empty"
        emptyText="nothing"
      />,
    );
    expect(await screen.findByText("one")).toBeVisible();
    expect(queries[0]).toMatchObject({scope: "active", page: 1, perPage: 2});

    fireEvent.click(screen.getByRole("radio", {name: "All"}));
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    expect(queries[1]).toMatchObject({scope: "all", page: 1});
    expect(screen.getByRole("radio", {name: "All"})).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("pages forward and back within the server's total", async () => {
    const load = vi.fn((query: ListQuery) =>
      Promise.resolve(
        page(
          query.page === 1
            ? [
                {id: 5, name: "five"},
                {id: 4, name: "four"},
              ]
            : [{id: 3, name: "three"}],
          3,
          query.page,
        ),
      ),
    );
    render(
      <PagedList<Item>
        storageKey="t"
        load={load}
        perPage={2}
        row={(item) => <div key={item.id}>{item.name}</div>}
        emptyTitle="Empty"
        emptyText="nothing"
      />,
    );
    expect(await screen.findByText("five")).toBeVisible();
    expect(screen.getByText(/page 1 of 2/)).toBeVisible();
    expect(screen.getByRole("button", {name: "Previous"})).toBeDisabled();

    fireEvent.click(screen.getByRole("button", {name: "Next"}));
    expect(await screen.findByText("three")).toBeVisible();
    expect(screen.getByText(/page 2 of 2/)).toBeVisible();
    expect(screen.getByRole("button", {name: "Next"})).toBeDisabled();
    expect(load.mock.calls[1][0].page).toBe(2);

    fireEvent.click(screen.getByRole("button", {name: "Previous"}));
    expect(await screen.findByText("five")).toBeVisible();
  });

  it("sends filters as query fields and drops an emptied one", async () => {
    const load = vi.fn((query: ListQuery) =>
      Promise.resolve(page([], 0, query.page)),
    );
    render(
      <PagedList<Item>
        storageKey="t"
        load={load}
        filters={[
          {
            name: "status",
            label: "Status",
            options: [{value: "queued", label: "queued"}],
          },
          {name: "runner", label: "Runner"},
        ]}
        row={(item) => <div key={item.id}>{item.name}</div>}
        emptyTitle="Empty"
        emptyText="nothing here"
      />,
    );
    expect(await screen.findByText("nothing here")).toBeVisible();

    fireEvent.change(screen.getByLabelText("Status"), {
      target: {value: "queued"},
    });
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    expect(load.mock.calls[1][0].filters).toEqual({status: "queued"});

    fireEvent.change(screen.getByLabelText("Runner"), {
      target: {value: "agent"},
    });
    await waitFor(() => expect(load).toHaveBeenCalledTimes(3));
    expect(load.mock.calls[2][0].filters).toEqual({
      status: "queued",
      runner: "agent",
    });

    fireEvent.change(screen.getByLabelText("Status"), {target: {value: ""}});
    await waitFor(() => expect(load).toHaveBeenCalledTimes(4));
    expect(load.mock.calls[3][0].filters).toEqual({runner: "agent"});
  });
});
