import {describe, expect, it} from "vitest";

import {reviewerStates} from "./reviews";

describe("reviewerStates", () => {
  it("keeps a verdict through later comment-only reviews", () => {
    const by = (login: string, state: string) => ({user: {login}, state});
    expect(
      reviewerStates([
        by("ana", "COMMENTED"),
        by("ana", "APPROVED"),
        by("ana", "COMMENTED"),
        by("bo", "CHANGES_REQUESTED"),
        by("bo", "APPROVED"),
        by("cy", "COMMENTED"),
      ]),
    ).toEqual([
      {login: "ana", state: "APPROVED"},
      {login: "bo", state: "APPROVED"},
      {login: "cy", state: "COMMENTED"},
    ]);
  });
});
