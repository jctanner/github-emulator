import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";

afterEach(() => cleanup());

import {JobRow} from "./JobRow";
import type {AdminJob} from "./JobRow";

const base: AdminJob = {
  id: 7,
  run_id: 3,
  repository: "fullsend-dev/triage-target",
  workflow: "fullsend",
  run_number: 12,
  event: "issues",
  name: "Triage",
  status: "in_progress",
  conclusion: null,
  runner_name: "fullsend-agent-runner",
  labels: ["fullsend"],
  created_at: "2026-09-28T23:46:00Z",
  started_at: "2026-09-28T23:46:00Z",
  completed_at: null,
  current_step: {
    number: 8,
    name: "Run triage agent",
    status: "in_progress",
    conclusion: null,
    message: null,
  },
  url: "/ui/fullsend-dev/triage-target/actions/jobs/7",
};

describe("JobRow", () => {
  it("shows who runs the job, what it needs, and offers both settlements while in progress", () => {
    const onRequeue = vi.fn();
    const onFail = vi.fn();
    render(<JobRow job={base} onRequeue={onRequeue} onFail={onFail} />);
    expect(screen.getByRole("link", {name: "Triage"})).toHaveAttribute(
      "href",
      base.url!,
    );
    expect(screen.getByText(/on fullsend-agent-runner/)).toBeVisible();
    expect(screen.getByText(/needs \[fullsend\]/)).toBeVisible();
    expect(
      screen.getByText(/step 8 Run triage agent: in_progress/),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", {name: "Requeue"}));
    fireEvent.click(screen.getByRole("button", {name: "Fail as lost"}));
    expect(onRequeue).toHaveBeenCalledWith(7);
    expect(onFail).toHaveBeenCalledWith(7);
  });

  it("shows how a settled job ended and offers nothing more", () => {
    const settled: AdminJob = {
      ...base,
      status: "completed",
      conclusion: "failure",
      completed_at: "2026-09-28T23:46:46Z",
      current_step: {
        number: 8,
        name: "Run triage agent",
        status: "completed",
        conclusion: "failure",
        message: "The self-hosted runner lost communication with the server.",
      },
    };
    render(<JobRow job={settled} onRequeue={vi.fn()} onFail={vi.fn()} />);
    expect(screen.getByText("failure")).toHaveClass("status-failure");
    expect(
      screen.getByText(/lost communication with the server/),
    ).toBeVisible();
    expect(screen.queryByRole("button", {name: "Requeue"})).toBeNull();
    expect(screen.queryByRole("button", {name: "Fail as lost"})).toBeNull();
  });

  it("offers requeue but not fail for a queued job", () => {
    render(
      <JobRow
        job={{...base, status: "queued", runner_name: null, started_at: null}}
        onRequeue={vi.fn()}
        onFail={vi.fn()}
      />,
    );
    expect(screen.getByText(/no runner/)).toBeVisible();
    expect(screen.getByRole("button", {name: "Requeue"})).toBeVisible();
    expect(screen.queryByRole("button", {name: "Fail as lost"})).toBeNull();
  });
});
