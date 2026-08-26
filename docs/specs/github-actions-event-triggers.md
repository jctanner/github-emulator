# Spec: GitHub Actions Event Triggers

## Status

Draft for implementation handoff.

## Goal

Make repository activity in the emulator trigger GitHub Actions workflows with
the same event names, activity filters, and event payload shape that a workflow
would receive from GitHub. The first consumer is Fullsend, whose triage workflow
must start when an issue is opened, edited, or labeled, and may later react to
issue comments, pull requests, and pull-request reviews.

This is an Actions-triggering specification. It is not a replacement for the
emulator's outbound repository webhook API. The two paths may share event
construction code, but an API mutation must be able to trigger an Actions run
even when no webhook is configured.

## Public behavior to emulate

The implementation should follow these GitHub references:

- [Events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [Workflow syntax for GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [Contexts reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts)
- [Default environment variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)
- [Webhook events and payloads](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
- [Reusing workflow configurations](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)

In particular:

1. on.<event>.types restricts the activity types that create a run. If no
   types filter is supplied, the event's default activity types apply.
2. github.event is the complete payload for the event that triggered the run;
   github.event_name is the event name; and github.event_path points to the JSON
   payload file on the runner.
3. issue_comment is the event for comments on an issue or pull request;
   pull_request_review is for reviews, not ordinary comments.
4. pull_request_target runs in the context of the base repository and has
   different security implications from pull_request. The emulator must keep
   the event name and payload distinction even if its isolated test environment
   does not reproduce GitHub's fork permission model.
5. A reusable workflow is called at the job level with jobs.<job_id>.uses, and
   the called workflow is selected by a repository path, workflow path, and ref.

## Required event coverage

### Fullsend MVP

The following mutations must create Actions runs when a matching workflow is
present:

| Event | Required activity types | Required payload objects |
|---|---|---|
| issues | opened, edited, labeled | issue, repository, sender; label for labeled |
| issue_comment | created | issue, comment, repository, sender |
| pull_request_target | opened, synchronize, ready_for_review, closed, labeled, unlabeled | pull_request, number, repository, sender; label for label activity |
| pull_request_review | submitted | pull_request, review, repository, sender |

The issues and issue_comment paths are the acceptance-critical portion for
automatic Fullsend triage. The pull-request events are needed by Fullsend's
dispatch/routing workflow and should be implemented in the same event model,
not as a Fullsend-specific special case.

### Preserve existing coverage

Existing push and workflow_dispatch behavior must continue to work. Their run
records and payloads should use the same event-processing path where practical.

### Follow-on coverage

These are useful extensions, but are not required to complete the Fullsend MVP:

- the remaining activity types documented for issues, pull_request_target, and
  pull_request_review;
- pull_request, pull_request_review_comment, and other Actions events;
- repository_dispatch, including event_type and client_payload;
- scheduled and other timer-driven events.

Unsupported events must be ignored or reported as unsupported without creating a
misleading successful run.

## Workflow matching

The matcher must accept the GitHub workflow forms already permitted by the
workflow syntax:

    on: issues

    on:
      issues:
        types: [opened, edited, labeled]

    on:
      issue_comment:
        types: [created]

    on: [push, issues, issue_comment]

Required matching rules:

- A scalar event name matches that event using its default activity types.
- A list matches any listed event.
- A mapping matches the event and applies its filters.
- types is an allow-list of action values. An event whose action is not in the
  list must not create a run.
- An empty event mapping, such as issues:, enables the event with its default
  activity types.
- Event names and activity names are case-sensitive and use GitHub's spelling.
- Existing branch/path filters for push and pull-request events must remain
  effective. Do not accidentally apply issue types matching to those filters.
- The YAML parser must preserve a workflow key named on; YAML 1.1 parsers that
  coerce it to a boolean need explicit handling.

The matcher should be a pure function over (workflow_definition, event) so it
can be unit tested independently of the database and runner.

## Canonical event model

Introduce one internal event object used by Actions dispatch and, where useful,
webhook recording:

    EventEnvelope
      delivery_id: unique internal identifier
      event_name: GitHub event name, e.g. "issues"
      action: activity type, e.g. "opened"
      repository: owner/name plus the repository API object
      ref: triggering ref when one exists
      sha: triggering commit when one exists
      actor: authenticated user that performed the mutation
      payload: GitHub-compatible JSON object
      occurred_at: UTC timestamp

delivery_id and occurred_at are internal metadata. They must not be added to
github.event unless GitHub includes the corresponding field in that event's
payload. The Actions payload exposed to a job must be the JSON object described
by the public event documentation.

Every payload must include the common fields required by the corresponding
GitHub event, especially action, repository, and sender. Event-specific objects
must be complete enough for normal expressions and API clients:

- issues: include the current issue object; on label activity include the
  affected label object.
- issue_comment: include the current issue object and the created, edited, or
  deleted comment object.
- pull_request_target: include the current pull request object, its number, and
  the affected label when applicable.
- pull_request_review: include the current pull request and the review object.

Use the emulator's existing REST serializers and IDs/URLs for nested objects so
the payload can be consumed by both expressions and REST clients. Do not emit
only database IDs where the public payload contains structured objects.

## Event-to-run lifecycle

For every supported mutating API operation:

1. Authenticate the request and identify the actor.
2. Persist the issue, comment, label association, pull request, or review.
3. Commit the mutation successfully.
4. Build the canonical event payload from the committed state.
5. Find workflow files from the correct repository/ref and evaluate the event
   matcher.
6. Create one Actions workflow run per matching workflow, recording the event
   name, action, actor, ref/SHA, and payload.
7. Expand the workflow into jobs and make eligible jobs available to a runner.
8. Deliver the same event payload and context to the job runner.

No Actions run may be created if the originating mutation fails or rolls back.
The event dispatcher should be shared by issue, comment, label, pull-request,
and review routes rather than duplicating trigger logic in each route.

Synchronous dispatch is acceptable for the deterministic emulator, but a clear
dispatch boundary is required so a future queue can be introduced without
changing API behavior. Nested mutations made by a job, for example a bot
comment, must enter the same event path and be distinguishable by their actor.
Workflow if conditions, not a hard-coded emulator exception, should decide
whether a bot-generated event is ignored.

## Repository/ref selection

For issue and issue-comment events, load workflows from the repository's default
branch, matching GitHub's repository-context behavior. For
pull_request_target, load the workflow from the base repository/default branch
and expose the base-repository event context. For push, retain the pushed ref
and existing workflow synchronization behavior. For manual dispatch, retain the
explicitly selected ref.

If a matching workflow cannot be read at the selected ref, record a useful
non-success result and do not fabricate an empty successful run.

## Runner context

For each job created from an event, provide:

- github.event_name equal to the event name;
- github.event equal to the complete canonical payload;
- github.event_path and GITHUB_EVENT_PATH pointing to a readable JSON file
  containing that payload;
- github.actor and the repository fields needed by the existing context
  implementation;
- GITHUB_REPOSITORY, GITHUB_REF, GITHUB_SHA, and pull-request head/base
  variables when applicable;
- a usable emulator GITHUB_TOKEN/github.token with the permissions needed by
  the workflow, or an explicit documented limitation if token permission
  emulation is not yet implemented.

The event payload must be available before any user run step starts. A step such
as this must print the original event payload rather than an empty or synthetic
object:

    steps:
      - run: |
          python - <<'PY'
          import json, os
          with open(os.environ["GITHUB_EVENT_PATH"]) as f:
              event = json.load(f)
          print(event["action"])
          PY

## Reusable workflows

Fullsend uses a caller workflow that dispatches through a reusable workflow.
The emulator therefore needs the following for the Fullsend acceptance test:

- recognize a job-level uses value pointing to a workflow file;
- resolve a local emulator repository, workflow path, and ref without reaching
  public GitHub;
- recognize the called workflow's on: workflow_call declaration;
- pass with, secrets, needs, if, and permissions according to the supported
  GitHub syntax;
- execute the called workflow's jobs as part of the caller run while preserving
  the caller's github.event and github.event_name context;
- show the resulting called jobs in the Actions API/UI with enough provenance
  to identify the caller and called workflow.

If reusable workflow expansion is intentionally deferred, the implementing agent
must state that explicitly and provide a local Fullsend test workflow without
uses as a temporary acceptance fixture. Silent flattening or treating a
job-level uses as a shell step is not acceptable.

## Authentication and permissions

Event generation must preserve the authenticated actor and the actor's user
object in sender. Job mutations must be attributable to the workflow token or
bot identity used by the runner. At minimum, the Fullsend triage workflow must
be able to read issues and create/update issue comments and labels.

The emulator is isolated and may simplify GitHub App installation and fork
security semantics, but the simplification must be documented in tests and must
not change event names or payload fields. Do not pass host credentials or real
production tokens into event payloads.

## Acceptance criteria

### Generic Actions tests

- [x] A repository workflow with on: issues: types: [opened] creates one run
      when an issue is opened.
- [x] The run records event=issues, action=opened, the authenticated actor, and
      a payload containing issue, repository, and sender.
- [x] edited and labeled create runs only when listed in types.
- [x] An unlisted issue action creates no run.
- [x] An issue_comment workflow receives issue, comment, and sender, and
      GITHUB_EVENT_PATH contains the same JSON payload exposed as github.event.
- [x] Pull-request target and review workflows match the required actions and
      expose pull_request, review, and label as applicable.
- [x] Existing push and workflow-dispatch tests continue to pass.
- [ ] A failed mutation creates no Actions run.
- [ ] A bot-created comment follows the same event path and remains observable as
      a separate actor/event.

### Fullsend acceptance test

- [x] Seed a repository containing the Fullsend-style workflow:

      on:
        issues:
          types: [opened, edited, labeled]
        issue_comment:
          types: [created]

      The M9 and M10 seed workflows now retain workflow_dispatch as a manual
      fallback while also subscribing to the automatic issue/comment and
      pull-request/review event matrix.

- [ ] Create an issue through the emulator REST API or UI.
- [ ] An Actions run is created automatically without workflow_dispatch.
- [ ] The run reaches the Fullsend triage job on the configured runner.
- [ ] The job can inspect github.event.issue and posts its result through the
      emulator API.
- [ ] The Actions UI/API exposes the event name, action, run, job, and captured
      logs sufficiently to diagnose a failed trigger.

## Suggested implementation boundaries

The implementing agent should first locate the existing workflow matcher and
push/manual run creation code, then:

1. add a pure event matcher and canonical payload builders;
2. add a shared dispatcher/service entry point;
3. call it after successful issue/comment/label/pull-request/review mutations;
4. extend runner context and event-payload-file handling;
5. add reusable-workflow resolution or document the explicit temporary fixture;
6. add API/service tests before changing the frontend.

Do not couple event dispatch to Fullsend repository names, labels, or prompt text.
The emulator should implement GitHub-compatible behavior that any test repository
can use.
