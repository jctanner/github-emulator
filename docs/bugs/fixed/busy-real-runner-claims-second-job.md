# Busy real runner claims a second job

## Symptom

When two matching jobs arrived close together, the first completed or was
cancelled while the second remained `in_progress` forever with every step
still queued.

## Cause

The upstream Actions runner polls for control messages with status `Busy` while
executing a job. The emulator treated that poll as an ordinary job request and
reserved another queued job for the same runner. The runner acknowledged the
second message but could not execute it concurrently.

## Fix

The distributed-task broker now returns no job while the authenticated runner
is busy. The job remains queued and unassigned until a runner is available.

## Regression coverage

`tests/test_actions_execution.py` verifies that a busy pool runner cannot claim
a second queued job.
