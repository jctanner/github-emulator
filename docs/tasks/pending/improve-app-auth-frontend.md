# Task: Improve the Apps and Authentication Frontend

## Goal

Improve the GitHub emulator's admin frontend so a developer can inspect and
exercise the emulator's GitHub Apps, installations, installation tokens, and
personal-token authentication state from the browser.

Use GitHub.com behavior and terminology as the reference point, while clearly
labeling emulator-only shortcuts and unsupported behavior. Do not attempt to
clone GitHub's entire frontend or copy GitHub branding/assets.

## Local context

Read these before changing code:

- `README.md`
- `PLAN.md`
- `docs/agentic_work_ledger.md`
- `src/app/admin/routes.py` and `src/app/admin/templates/`
- `src/app/api/apps.py`
- `src/app/models/apps.py` and `src/app/models/token.py`
- `tests/test_apps_oidc.py` and the existing admin/frontend tests

The emulator currently has JSON endpoints for development-only App creation,
App lookup, installation creation, installation listing, selected repository
listing, and installation access-token minting. The admin UI already lists and
creates personal access tokens. Reuse the existing admin session and Primer
layout patterns.

## GitHub behavior references

Use these official GitHub Docs pages when deciding labels, fields, workflow
states, and explanatory text:

- [GitHub Apps overview](https://docs.github.com/en/apps/overview)
- [Registering a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app)
- [Installing your own GitHub App](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app)
- [Authenticating with a GitHub App](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app)
- [Generating an installation access token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-src/app/generating-an-installation-access-token-for-a-github-app)
- [REST API endpoints for GitHub Apps](https://docs.github.com/en/rest/apps)
- [Authorizing OAuth apps](https://docs.github.com/en/apps/oauth-apps/using-oauth-apps/authorizing-oauth-apps)
- [Reviewing authorized OAuth apps](https://docs.github.com/en/apps/oauth-apps/using-oauth-apps/reviewing-your-authorized-oauth-apps)

Important reference concepts:

- An App is registered with a name/slug, owner, permissions, and credentials.
- An App must be installed on a user or organization account before it can
  access installation resources.
- Installation access is repository-scoped and represented by short-lived
  tokens.
- App authentication as the App (JWT) and authentication as an installation
  (installation access token) are different states.
- OAuth Apps and GitHub Apps are different integration types. Do not present
  the emulator's PAT or App UI as OAuth support unless the corresponding API
  actually exists.

## Required UI

Add an `Apps` or `GitHub Apps` area to the admin navigation and implement the
smallest coherent set of pages:

1. **App list**

   Show App name, slug, App ID, owner, permission summary, installation count,
   and creation time. Include a link to the App detail page and a link to
   create an emulator App.

2. **App creation**

   Provide a form for the fields supported by the emulator API, including name,
   slug, optional App ID, and permissions. Use grouped permission controls and
   explain that these are emulator test permissions, not a production App
   registration flow.

   The create response may display the generated private key exactly once,
   with a prominent copy/download control and a warning that it cannot be
   recovered from the UI. Never render the private key in the App list, normal
   detail page, HTML comments, hidden fields, logs, or flash messages.

3. **App detail**

   Show redacted metadata, permissions, installations, selected repositories,
   and recent installation-token metadata. Show token prefix, creation time,
   expiry time, repository selection, and permissions; never show token values
   or token hashes.

   Include a development-only action to create an installation and a separate
   action to mint a test installation token. The token value may be displayed
   once after minting, subject to the same one-time secret handling as PAT
   creation. Make it clear that this is an emulator shortcut for testing the
   REST flow.

4. **Installation detail**

   Show installation ID, App, account/login, account type, repository-selection
   mode, selected repositories, permissions, token count, and token expiry
   metadata. Do not imply that a token remains valid after its displayed
   expiry.

5. **Authentication overview**

   Add a small admin page or dashboard section that makes the available auth
   mechanisms discoverable:

   - PATs: owner, name, safe prefix, scopes, created/last-used/expiry state,
     and revoke action.
   - GitHub App JWT: explain that the API uses an App private key to validate
     App JWTs; show only App metadata and whether a key is present.
   - Installation tokens: show safe metadata and a link to the owning App and
     installation.
   - OAuth Apps: explicitly label as unsupported/deferred unless the emulator
     already has a working OAuth endpoint and data model.

   If an API or model needed for a page does not exist, add the smallest
   redacted admin read endpoint needed rather than querying the database from a
   template. Keep GitHub-compatible API responses separate from admin view
   models.

## Security and emulator boundaries

- Treat this as a resettable development service, but still avoid accidental
  secret disclosure in normal pages, logs, URLs, browser history, and test
  snapshots.
- Never add a route that returns `private_key_pem`, raw PAT values, raw
  installation tokens, or token hashes for ordinary list/detail views.
- Prefer one-time result pages or POST/redirect flows for newly generated
  secrets. Do not put secrets in query parameters.
- Use explicit `Development only` / `Emulator only` labels where the UI
  diverges from GitHub.com.
- Preserve existing admin auth, navigation, styles, and unrelated work.
- Do not add real GitHub credentials, real user data, or external network calls
  to tests or seed data.

## Acceptance criteria

- [ ] An authenticated admin can navigate to Apps from the admin UI.
- [ ] Existing and newly created Apps are visible with redacted metadata.
- [ ] An admin can create an App, install it for a local account/repository
      selection, and mint a test installation token from the UI.
- [ ] App and installation detail pages expose permissions and repository
      selection clearly enough to diagnose Fullsend authentication failures.
- [ ] PAT, App JWT, and installation-token authentication paths are visibly
      distinguished; unsupported OAuth behavior is not implied.
- [ ] No list/detail page, HTML source, or URL contains a private key, raw PAT,
      raw installation token, or token hash.
- [ ] Existing PAT administration continues to work.
- [ ] Add route/template tests covering authentication redirects, redacted
      rendering, creation flows, and one-time secret display. Add browser tests
      only if the repository already has a browser-test convention.
- [ ] Update `README.md`, `PLAN.md`, or the relevant work ledger entry if the
      project's documentation convention requires it.
- [ ] Run the focused tests and the full emulator test suite; record commands
      and results in the task/ledger before moving this file to `done/`.

## Out of scope

- Implementing the complete GitHub.com settings/developer-settings frontend.
- Implementing OAuth authorization or consent flows without a corresponding
  emulator API/model.
- Changing GitHub-compatible API contracts solely to simplify templates.
- Production-grade secret storage, key rotation, or external GitHub calls.

## Suggested implementation order

1. Add redacted admin view models/read routes and tests.
2. Add App list/detail/creation templates and navigation.
3. Add installation and one-time installation-token workflows.
4. Connect the authentication overview to PAT and App/installation metadata.
5. Run the focused/full tests, manually exercise the UI, and document any
   emulator-only deviations from the GitHub references above.
