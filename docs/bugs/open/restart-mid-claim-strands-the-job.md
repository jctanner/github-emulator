# Bug: an emulator restart during a job claim strands the job in progress

## Summary

On 2026-09-27 at 15:14:44Z the emulator container was OOM-killed (exit 137,
limit 1536Mi; 187Mi a minute after restart) while a runner was claiming
job 4949 (`Triage`, `phase2/fresh-target` run 1591). The claim had been
written - the job shows `in_progress`, `runner_name=fullsend-agent-runner`,
`started_at` equal to the kill time - but the response never reached the
runner, whose log has no `Executing job #4949` line. The runner's next poll
was answered by the new container, which handed it job 4955; 4949 stayed
`in_progress` with nobody running it until the run was cancelled by hand.

## Two defects

1. **No recovery for a claimed-but-unacknowledged job.** The stale-runner
   reclaim (tests/actions/test_execution.py, `reclaims_job_from_stale_runner`)
   keys on a runner whose heartbeat stopped. This runner kept heartbeating;
   it simply never received the job. Real GitHub re-queues a job whose
   runner does not start it within a window. The claim should either be
   acknowledged by the runner's first log/PATCH within N seconds or return
   to `queued`.

2. **The memory spike itself.** The last requests in the previous
   container's access log were the runner's log appends for job 4948 and
   one `GET .../actions/jobs/4847/logs`. Nothing in that window explains
   1.5GiB; it is the only kill this pod has had today across four rebuilds
   and roughly twenty runs. Unknown, worth a memory profile on the log
   endpoints and the git smart-HTTP path the same job exercises
   (`fullsend-src` clone) before raising the limit.

## Workaround

Cancel the run and file a new triggering event.
