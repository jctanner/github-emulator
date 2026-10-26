import {act, renderHook, waitFor} from "@testing-library/react";
import {describe, expect, it} from "vitest";

import {useApiData} from "./useApiData";

describe("useApiData", () => {
  it("keeps the current data on screen while a reload refetches it", async () => {
    let calls = 0;
    const seen: (string | null)[] = [];
    const {result} = renderHook(() => {
      const state = useApiData("thing", () => Promise.resolve(`v${++calls}`));
      seen.push(state.loading ? "loading" : state.data);
      return state;
    });
    await waitFor(() => expect(result.current.data).toBe("v1"));
    seen.length = 0;

    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data).toBe("v2"));
    expect(seen).not.toContain("loading");
    expect(seen).not.toContain(null);
  });

  it("starts from the loading state for a new key", async () => {
    const {result, rerender} = renderHook(
      ({id}) => useApiData(`thing:${id}`, () => Promise.resolve(`data ${id}`)),
      {initialProps: {id: 1}},
    );
    await waitFor(() => expect(result.current.data).toBe("data 1"));
    rerender({id: 2});
    expect(result.current.loading).toBe(true);
    expect(result.current.data).toBeNull();
    await waitFor(() => expect(result.current.data).toBe("data 2"));
  });
});
