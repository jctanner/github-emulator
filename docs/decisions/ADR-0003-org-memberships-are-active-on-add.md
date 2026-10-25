# ADR-0003: Organization Memberships Added Through the API Are Active at Once

## Status

Accepted

## Context

`PUT /orgs/{org}/memberships/{username}` on GitHub does not add a new member
directly. It sends an invitation, and the membership stays `pending` until
the user accepts it with `PATCH /user/memberships/orgs/{org}`. A pending
member is not in `GET /orgs/{org}/members` and cannot create repositories in
the organization.

The emulator's clients are test harnesses and bootstraps, such as Markov's
`github-workflow-example` bootstrap. They add a member so the member can act
in the organization immediately. With GitHub's invitation step each of them
would need the new member's token as well, to accept the invitation, before
doing anything else.

## Decision

`PUT /orgs/{org}/memberships/{username}` creates the membership as `active`.
There is no option to create a pending one.

`OrgMembership.state` keeps its `pending` value, and the read endpoints
report it. `PATCH /user/memberships/orgs/{org}` with `{"state": "active"}`
accepts a pending membership, so a row written as pending, for example by
a fixture, behaves as on GitHub.

## Consequences

- A client that expects the invitation step, and waits for or accepts it,
  still works: the membership is already active, and the `PATCH` is accepted.
- A test of GitHub's invitation flow itself cannot be run against the
  emulator. If one is needed, add an opt-in, such as a request field or a
  setting, rather than changing the default.
