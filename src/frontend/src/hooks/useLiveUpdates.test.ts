import {renderHook} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

import {useLiveUpdates} from "./useLiveUpdates";

class FakeEventSource {
  static last: FakeEventSource;
  onmessage: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeEventSource.last = this;
  }
  close() {
    this.closed = true;
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useLiveUpdates", () => {
  it("reloads once for a burst of changes", () => {
    const onChange = vi.fn();
    renderHook(() => useLiveUpdates("octo", "demo", 7, onChange));
    const source = FakeEventSource.last;
    expect(source.url).toBe("/api/_ui/repos/octo/demo/issues/7/live");

    source.onopen?.();
    source.onmessage?.();
    source.onmessage?.();
    vi.advanceTimersByTime(300);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("reloads after reconnecting, but not on the first connection", () => {
    const onChange = vi.fn();
    renderHook(() => useLiveUpdates("octo", "demo", 7, onChange));
    const source = FakeEventSource.last;
    source.onopen?.();
    vi.advanceTimersByTime(300);
    expect(onChange).not.toHaveBeenCalled();

    source.onerror?.();
    source.onopen?.();
    vi.advanceTimersByTime(300);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("closes the stream when the page goes away", () => {
    const {unmount} = renderHook(() =>
      useLiveUpdates("octo", "demo", 7, vi.fn()),
    );
    unmount();
    expect(FakeEventSource.last.closed).toBe(true);
  });
});
