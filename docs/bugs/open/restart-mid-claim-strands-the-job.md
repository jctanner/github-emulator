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

   The cause is therefore unidentified and does not reproduce on demand.
   `app/services/memory_watch.py` now samples RSS, tracks in-flight
   requests, and on threshold crossings writes them with tracemalloc's
   largest allocation sites to `DATA_DIR/memory-watch.log`; the next
   occurrence should name a file and line. Until it does, raising the
   limit only moves the ceiling.

## Workaround

Cancel the run and file a new triggering event. Read
`DATA_DIR/memory-watch.log` after any restart.
