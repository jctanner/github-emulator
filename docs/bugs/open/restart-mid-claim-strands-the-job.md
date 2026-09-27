# Bug: an emulator restart during a job claim strands the job in progress

## Summary

On 2026-09-27 at 15:14:44Z the emulator container was OOM-killed while a
runner was claiming job 4949 (`Triage`, `phase2/fresh-target` run 1591).
The claim had been written - the job shows `in_progress`,
`runner_name=fullsend-agent-runner`, `started_at` equal to the kill time -
but the response never reached the runner, whose log has no
`Executing job #4949` line. The runner's next poll was answered by the new
container, which handed it job 4955; 4949 stayed `in_progress` with nobody
running it until the run was cancelled by hand.

## Two defects

1. **No recovery for a claimed-but-unacknowledged job.** The stale-runner
   reclaim (tests/actions/test_execution.py, `reclaims_job_from_stale_runner`)
   keys on a runner whose heartbeat stopped. This runner kept heartbeating;
   it simply never received the job. Real GitHub re-queues a job whose
   runner does not start it within a window. The claim should either be
   acknowledged by the runner's first log/PATCH within N seconds or return
   to `queued`.

2. **The memory spike itself.** The kernel's report is specific: the
   process killed was uvicorn with `anon-rss:1534400kB` - the Python
   process at the 1536Mi limit, not a git child (they share the cgroup,
   and none is listed). It is the **third** kill with that exact
   signature: 2026-09-23 08:45 and 13:06 as well, each ~1.5 GiB in
   uvicorn. G34 in breadboard's conformance plan (a push at a 512Mi
   limit) raised the limit to 1536Mi; these three are at the new limit.

   What it is *not*, established by replaying each under a half-second
   cgroup sampler on 2026-09-27 (peaks in parentheses, baseline 313 MiB):
   a full triage run through issue creation and route completion
   (346 MiB); a rerun followed by a cancel (310 MiB); a dashboard
   onboarding with scaffold push, PR and merge (314 MiB). Not the git
   transport either - `upload-pack` streams and both request bodies are
   spooled to disk - and not a git auto-repack: the largest repository
   has 495 loose objects, under the 6700 trigger. The ETag middleware
   buffers only JSON GET bodies. The seventeen-second issue creation
   just before the kill (four seconds now) reads as a process already
   under memory pressure, so the growth preceded that window.

   **Found the same evening and fixed (7b43453).** The watchdog's first
   live report caught a 220 MiB rise with one application request in
   flight - a pull-request creation - and 412k freshly decoded JSON
   objects. `Workflow.runs` and `WorkflowRun.jobs` were `lazy="selectin"`,
   so loading one workflow loaded every run and every job of every run
   (5,077 rows, 22.6 MiB of `steps` JSON), and loading one job chained
   back to the same; event dispatch loads a repository's workflows to
   match triggers, so every issue, pull request and comment paid for the
   repository's whole Actions history. Both collections are `lazy="raise"`
   now, with `tests/test_workflow_history_is_not_eager_loaded.py`.
   Measured: pull-request creation 12.4 s / +176 MiB before, 0.8 s /
   +3 MiB after.

   **Reopened the same evening: a fourth kill, after that fix.** 19:52:01Z,
   uvicorn again at `anon-rss:1498384kB`, git children tiny. It stranded
   job 5356 (`Review`, run 1636) exactly as defect 1 describes - the
   runner's failure report got 502 and the job stayed `in_progress` until
   cancelled by hand - so defect 1 has its second occurrence. What the
   sampler-only watchdog recorded: flat at ~175 MiB from the 19:24 roll
   until 19:40, then +200 MiB every one to two minutes through 19:52 -
   the window of the phase-3 conformance triage, the conformance reset
   (`25-reset-conformance.sh`), a second triage, and a browser polling one
   job's log page 42 times. The request listed in flight throughout was a
   phantom: a `GET /repos/admin/ansible-agent-harness` from the old runner
   pod, whose client had timed out at the proxy's 60 s and whose pod was
   then replaced, leaving the middleware entry alive; the same GET takes
   18 ms and no memory now.

   Ruled out since, each by measurement: the clone path (three full
   clones of the 112 MiB target, +1 MiB each); an ordinary triage (one
   full haiku triage under tracemalloc peaked at 353 MiB); every
   module-level container (four, all bounded); the log-append handler
   (writes to a file and returns). Not yet replayed: the reset script's
   emulator-facing work, and the two runs overlapping with it.

   Two instrument notes. tracemalloc cannot be left on: with eight frames
   a route job took fifteen minutes and a conformance run timed out. And
   `GET /admin/api/memory` takes a snapshot on the event loop when tracing
   is on - 44 s per call - so polling it while tracing is exactly wrong;
   it should snapshot off the loop or not at all. The sampler and the
   in-flight list stay on; defect 2 is open again with the window above
   as the lead.

## Workaround

Cancel the run and file a new triggering event. Read
`DATA_DIR/memory-watch.log` after any restart.
