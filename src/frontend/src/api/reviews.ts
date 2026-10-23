import {api} from "./client";

// These endpoints currently return untyped dictionaries in the OpenAPI schema.
export type PullReview = {
  id: number;
  user: {login: string} | null;
  body: string | null;
  state: string;
  submitted_at: string | null;
};

export type ReviewComment = {
  id: number;
  pull_request_review_id: number | null;
  user: {login: string} | null;
  body: string;
  created_at: string;
  path: string;
  line: number | null;
  side: string;
  position: number | null;
  in_reply_to_id: number | null;
  subject_type?: string;
  commit_id?: string;
};

type PullPath = {owner: string; repo: string; pull_number: number};

async function reviewPages<T>(
  endpoint:
    | "/api/v3/repos/{owner}/{repo}/pulls/{pull_number}/reviews"
    | "/api/v3/repos/{owner}/{repo}/pulls/{pull_number}/comments",
  path: PullPath,
): Promise<T[]> {
  const items: T[] = [];
  for (let page = 1; ; page++) {
    const {data, response} = await api.GET(endpoint, {
      params: {path, query: {page, per_page: 100}},
    });
    if (!response.ok || !Array.isArray(data)) {
      throw new Error("Could not load pull request reviews and comments.");
    }
    items.push(...(data as T[]));
    if (data.length < 100) return items;
  }
}

export async function loadPullReviewActivity(path: PullPath) {
  const [reviews, comments] = await Promise.all([
    reviewPages<PullReview>(
      "/api/v3/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
      path,
    ),
    reviewPages<ReviewComment>(
      "/api/v3/repos/{owner}/{repo}/pulls/{pull_number}/comments",
      path,
    ),
  ]);
  const pending = new Set(
    reviews.filter((r) => r.state === "PENDING").map((r) => r.id),
  );
  return {
    reviews: reviews.filter((r) => r.state !== "PENDING"),
    comments: comments
      .filter((c) => !pending.has(c.pull_request_review_id ?? -1))
      .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id),
  };
}
