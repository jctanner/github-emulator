import {describe, expect, it} from "vitest";

import {relativeTime} from "./time";

const now = new Date("2026-09-30T12:00:00Z");
const ago = (seconds: number) =>
  new Date(now.getTime() - seconds * 1000).toISOString();

describe("relativeTime", () => {
  it.each([
    [10, "now"],
    [120, "2 minutes ago"],
    [3 * 3600, "3 hours ago"],
    [26 * 3600, "yesterday"],
    [3 * 86400, "3 days ago"],
    [13 * 86400, "13 days ago"],
    [14 * 86400, "2 weeks ago"],
    [22 * 86400, "3 weeks ago"],
    [29 * 86400, "29 days ago"],
    [35 * 86400, "last month"],
    [150 * 86400, "5 months ago"],
    [400 * 86400, "last year"],
    [800 * 86400, "2 years ago"],
  ])("formats %i seconds as %s", (seconds, expected) => {
    expect(relativeTime(ago(seconds), now)).toBe(expected);
  });

  it("returns an empty string for an unparseable date", () => {
    expect(relativeTime("not a date", now)).toBe("");
  });
});
