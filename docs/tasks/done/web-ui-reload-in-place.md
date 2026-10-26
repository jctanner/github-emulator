# Web UI reloads in place

## Goal

Posting a comment on an issue or pull request should show the new comment
without the page appearing to reload.

## Cause

`useApiData` cleared its data and set `loading` on every `reload()`, so
`Loadable` swapped the whole page for "Loading…" and then mounted it again.
Every in-page change that reloads did this: comments, edits, deletes, labels,
close and reopen, merge, and reviews. Each one lost the scroll position, open
menus and any unsent text.

## Implementation

- A reload of the same key now refetches in the background and swaps in the
  new data, keeping the page mounted. Only a new key, such as navigating to
  another issue, starts from the loading state. A failed reload still shows
  its error.
- The settings branch-protection form remounted on the `loading` flip to
  re-seed its uncontrolled inputs. It is now keyed on the loaded protection
  data instead.

- The UI page itself (`index.html`, which names the current bundle) had no
  `Cache-Control` header, so browsers could cache it heuristically and keep
  running the previous build after a deploy. That is how the old remounting
  behaviour was still seen after this fix shipped. `FrontendStaticFiles` now
  sends `no-cache` for the page, so it revalidates on every load, and
  `public, max-age=31536000, immutable` for the content-hashed `assets/`.

## Verification

- `src/hooks/useApiData.test.tsx` checks that a reload never passes through
  loading or empty data, and that a new key does.
- `src/pages/PullReviews.test.tsx` posts a comment and checks that it appears
  in the same page instance with the box cleared and no "Loading…".
- Frontend: typecheck, lint and format pass, and Vitest runs 69 tests.
- Live in headless Chrome on `admin/review-ui-check` issue #2 and pull #1:
  - posting a comment kept the same document and showed no loading
    placeholder;
  - the issue page kept its scroll position;
  - the comment box was cleared.
- On the deployed build, a DOM check found that posting a comment replaced
  nothing on the page except the text in the comment box. The description
  stayed the same element on both the issue and the PR.
- `tests/test_frontend_app.py` covers the cache headers, including on a 304
  revalidation. The live host proxy returns them unchanged.
