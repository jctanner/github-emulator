# Bug: Actions OIDC tokens and discovery differ from GitHub's

Status: fixed 2026-11-14.

## Summary

Checked against GitHub's live issuer (`token.actions.githubusercontent.com`)
and a real token:

- Tokens had no `repository_owner_id`. Relying parties pin an owner by that
  id, because a name can be reused after an organization is deleted; an
  inference gateway that admits `<repository_owner_id>/<repository>` refused
  every emulator token.
- Tokens also lacked `ref_type`, `ref_protected`, `head_ref`, `base_ref`,
  `repository_visibility` and `check_run_id`.
- `nbf` was `iat - 5`; GitHub's is `iat - 300`.
- Discovery advertised `jwks_uri` `/.well-known/jwks.json`. GitHub's issuer
  serves its keys at `/.well-known/jwks`, and configs written for GitHub use
  that path directly. The document also lacked `subject_types_supported`,
  `response_types_supported`, `scopes_supported` and `claims_supported`.

## Fix

- `repository_owner_id` is the id the REST API reports as the repository's
  `owner.id`: the organization's id for an organization-owned repository.
  Users and organizations have separate id sequences in the emulator, unlike
  on GitHub, so a user and an organization can share an id.
- The other claims are added as strings, from the run: `ref_protected` from
  the branch's protection, `head_ref` and `base_ref` from a `pull_request`
  payload (empty otherwise), `check_run_id` from the job's id (on GitHub a
  job's check run shares it; the emulator creates no check run for a job).
- `nbf` is `iat - 300`.
- Keys are served at `/.well-known/jwks` as well as `/.well-known/jwks.json`;
  discovery advertises the first and carries GitHub's other fields.
  `claims_supported` lists only the claims the emulator issues; GitHub's also
  lists `enterprise`, `enterprise_id`, `workflow_sha`, `environment`,
  `environment_node_id` and `issuer_scope`.

The `sub` claim keeps GitHub's documented `repo:OWNER/REPO:ref:REF` form.

Tests: `tests/actions/test_oidc_claims.py`.
