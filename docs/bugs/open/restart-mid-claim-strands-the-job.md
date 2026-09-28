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

1. **No recovery for a claimed-but-unacknowledged job.** *Fixed 2026-09-27:*
   `_requeue_stale_jobs` also returns to the queue any job that has been
   `in_progress` for longer than `RUNNER_CLAIM_ACK_SECONDS` (120) with no
   step started - a claim whose response was lost - while leaving a job
   whose runner has begun a step alone. Pinned in
   `tests/actions/test_consolidation_bug_fixes.py`. The original text: The stale-runner
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

   Ruled out since, each by measurement, with the pod's cgroup sampled
   twice a second and the application frames sampled by py-spy: the
   clone path (three full clones of the 112 MiB target, +1 MiB each); the
   reset script (`25-reset-conformance.sh`, 27 s, +15 MiB); an ordinary
   triage (164 -> 227 MiB, +60 net, the steps landing a few seconds after
   each job completion, none during the agent); event dispatch (thirty
   `issues: edited` events, each a dispatch and a route job, +5 MiB);
   forty fetches of a 180 KiB job log and twenty UI page loads (flat);
   every module-level container (four, all bounded); the log-append
   handler (writes a file and returns). The py-spy samples put the
   emulator's own CPU in `dispatch_event -> materialize_reusable_workflows
   -> _resolve_reusable_workflow -> detect_workflows`, which is cost, not
   retention.

   So nothing identifiable from the window reproduces its rate, and the
   container's access log went with the pod. The watchdog now counts
   completed requests per endpoint between reports (2026-09-27, memory
   watch counters), which is the shape that was invisible: many quick,
   repeating requests. The next report will name the hot endpoint.

   Two instrument notes. tracemalloc cannot be left on: with eight frames
   a route job took fifteen minutes and a conformance run timed out. And
   `GET /admin/api/memory` takes a snapshot on the event loop when tracing
   is on - 44 s per call - so polling it while tracing is exactly wrong;
   it should snapshot off the loop or not at all. The sampler and the
   in-flight list stay on; defect 2 is open again with the window above
   as the lead.

   **Replayed again on 2026-09-28, with the pod's cgroup and the uvicorn
   process sampled every five seconds.** Every distinctive event of the
   19:40-19:52 window, run against the current code on a pod at 331 MiB
   after a day of conformance, review and onboarding traffic, and none of
   them moved it more than ten megabytes: the reset script (22 s, flat); a
   runner deployment rolled while its triage job was in progress, the
   window's own trigger, watched for twelve minutes (331 to 341 MiB, no
   trend); the Fullsend dashboard's polling shape aimed straight at the
   emulator for four minutes, the last eight runs and each of their job
   lists every five seconds (+4 MiB); twenty loads of the Breadboard
   dashboard's root page, which pages through every issue and pull
   request (flat); and a client-disconnect battery of sixty dropped
   runner long-polls, sixty dropped upstream-runner message polls, six
   hundred JSON listings dropped mid-transfer, and twenty clones aborted
   mid-transfer (flat, within noise). Threads stayed at 13 to 15 and file
   descriptors at 40, so nothing is leaking connections either.

   So the window still does not reproduce, and the code it ran on has
   since changed in the areas every hypothesis pointed at. The instrument
   stays armed: the watchdog's per-endpoint counters have been live since
   the evening of 2026-09-27 and no report has fired since, across roughly
   thirty conformance runs. If the growth returns, the report names the
   endpoint; until then this is an unreproduced kill on superseded code,
   not an open lead.

   One defect did fall out of the replay. The rollout left the triage job
   `in_progress` for the whole 900 s conformance timeout: the new pod
   registered under the same name through the site-wide route, which
   reused the row and re-keyed it but did not return the row's held jobs
   to the queue the way the repository and enterprise routes do, and the
   stale-runner rule could not fire because the shared row kept
   heartbeating. Fixed: every registration route that reuses a row
   settles its held jobs (`_release_held_jobs`), with site-wide tests.
   Settling is GitHub's rule rather than a blanket requeue: a job the
   runner never started goes back to the queue, a job it had started
   fails with "The self-hosted runner lost communication with the
   server" on the step it was on, and its run concludes. The first
   version requeued every held job; the re-run triage then failed anyway,
   because the dead runner's sandbox was still holding the provider
   profile the new attempt wanted to replace. Re-running an agent on top
   of what a dead runner left is not a recovery, and GitHub does not try.
   The stale-heartbeat rule applies the same distinction.

## Workaround

Cancel the run and file a new triggering event. Read
`DATA_DIR/memory-watch.log` after any restart.
