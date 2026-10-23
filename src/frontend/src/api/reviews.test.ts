import {afterEach, describe, expect, it, vi} from "vitest";
import {loadPullReviewActivity} from "./reviews";

afterEach(() => vi.unstubAllGlobals());

describe("pull review loading", () => {
  it("fetches every page and excludes pending review activity", async () => {
    const fetcher = vi.fn((request: Request) => {
      const url = new URL(request.url);
      const page = Number(url.searchParams.get("page"));
      expect(url.searchParams.get("per_page")).toBe("100");
      if (url.pathname.endsWith("/reviews"))
        return Response.json(
          page === 1
            ? Array.from({length: 100}, (_, id) => ({
                id,
                state: id === 0 ? "PENDING" : "COMMENTED",
              }))
            : [{id: 100, state: "APPROVED"}],
        );
      return Response.json(
        page === 1
          ? Array.from({length: 100}, (_, id) => ({
              id,
              pull_request_review_id: id,
              created_at: "2026-10-23T10:00:00Z",
            }))
          : [
              {
                id: 100,
                pull_request_review_id: 100,
                created_at: "2026-10-23T11:00:00Z",
              },
            ],
      );
    });
    vi.stubGlobal("fetch", fetcher);
    const activity = await loadPullReviewActivity({
      owner: "octo",
      repo: "demo",
      pull_number: 2,
    });
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(activity.reviews).toHaveLength(100);
    expect(activity.comments).toHaveLength(100);
    expect(activity.reviews.at(-1)?.id).toBe(100);
    expect(activity.comments.at(-1)?.id).toBe(100);
    expect(activity.comments.some((comment) => comment.id === 0)).toBe(false);
  });

  it("reports review API failures instead of silently showing no reviews", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(() =>
          Response.json({detail: "failed"}, {status: 500}),
        ),
    );
    await expect(
      loadPullReviewActivity({owner: "octo", repo: "demo", pull_number: 2}),
    ).rejects.toThrow("Could not load pull request reviews");
  });
});
