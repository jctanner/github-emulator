import {act, render, screen} from "@testing-library/react";
import {MemoryRouter, Route, Routes} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";

import {ActionJobPage} from "./ActionJobPage";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function stubJob(statuses: string[]) {
  const logRequests: string[] = [];
  let jobReads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((input: Request | string) => {
      const url = new URL(typeof input === "string" ? input : input.url, "http://localhost");
      if (url.pathname.endsWith("/logs")) {
        logRequests.push(url.pathname);
        return Promise.resolve(new Response(`log line ${logRequests.length}`));
      }
      // The heading's read goes through the API client (a Request); only the
      // poller's reads (plain URLs) advance the scripted statuses.
      const status = statuses[Math.min(jobReads, statuses.length - 1)];
      if (typeof input === "string") jobReads += 1;
      return Promise.resolve(
        Response.json({id: 7, name: "Code", status, conclusion: status === "completed" ? "success" : null}),
      );
    }),
  );
  return logRequests;
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/ui/o/r/actions/jobs/7"]}>
      <Routes>
        <Route path="/ui/:owner/:repo/actions/jobs/:jobId" element={<ActionJobPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("ActionJobPage", () => {
  it("fetches a completed job's log once and stops polling", async () => {
    vi.useFakeTimers({shouldAdvanceTime: true});
    const logRequests = stubJob(["completed"]);
    renderPage();
    expect(await screen.findByText("log line 1")).toBeTruthy();
    await advance(10_000);
    expect(logRequests).toHaveLength(1);
  });

  it("polls while the job runs and stops once it completes", async () => {
    vi.useFakeTimers({shouldAdvanceTime: true});
    const logRequests = stubJob(["in_progress", "in_progress", "completed"]);
    renderPage();
    expect(await screen.findByText("log line 1")).toBeTruthy();
    await advance(1_500);
    await advance(1_500);
    expect(await screen.findByText("log line 3")).toBeTruthy();
    await advance(10_000);
    expect(logRequests).toHaveLength(3);
  });
});
