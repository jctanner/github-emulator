# Bug: React repository UI is not visually and structurally at parity with the legacy UI

## Summary

The React repository UI under `/ui/{owner}/{repo}` exposes most of the same
routes as the legacy Jinja UI under `/ui-legacy/{owner}/{repo}`, but the two
surfaces are not yet visually or structurally equivalent. Differences are
systematic in the shared shell and typography, and several pages omit or
rearrange important visual hierarchy, metadata, controls, and empty states.

This is a single parity bug covering the repository-scoped migration. It should
remain open until the shared discrepancies and route-specific inventory below
are resolved or intentionally accepted in writing.

## Implementation status

Resolved in the React candidate on 2026-08-30:

- Replaced the placeholder shell with a GitHub-style global header, responsive
  mobile menu, repository context band, Octicons, active tabs, and 14px density.
- Restored compact repository tree/blob hierarchy, directories-first ordering,
  line-numbered blobs, commit metadata, branch states, and richer empty states.
- Added state filters and structured empty states to issue/pull lists; restored
  conversation tabs, timeline cards, sidebars, labels, and comment composition.
- Rebuilt Actions rows and run details around run identity, actor/time metadata,
  outcome pills, run metadata, and job cards.
- Reorganized settings into GitHub-style groups and section cards, including
  access summaries, protection rules, runner states, and app installation cards.
  Settings unsupported by the current API remain visibly disabled.
- Added responsive stacking/navigation for content, conversations, Actions, and
  settings. A repeat audit of all 76 captures reported zero horizontal overflow.

The React and legacy pages are not pixel-identical. The accepted candidate uses
the more complete GitHub-style structures above where the legacy template is
sparser, while preserving the same working API-backed operations. Routes remain
marked `candidate` until screenshot baselines are explicitly promoted.

## Reproduction

Fixture:

- Repository: `admin/ansible-agent-harness`
- Legacy: `https://github.local/ui-legacy/admin/ansible-agent-harness`
- React: `https://github.local/ui/admin/ansible-agent-harness`
- Browser: Playwright Chromium
- Viewports: 1440x1000 and 480x900
- Audit date: 2026-08-30
- Dynamic examples: issue #7, pull request #8, Actions run #1068

The Playwright audit authenticated as the local admin, followed safe repository
links, and captured paired full-page screenshots plus computed styles for 19
routes at both viewport sizes: 76 captures total. It did not submit forms or
invoke mutating controls.

Local audit evidence is in `/tmp/github-ui-parity-audit-20260830/`:

- `audit.json` contains route, navigation, text, geometry, and computed-style data.
- `desktop--<route>--ui-legacy.png` and `desktop--<route>--ui.png` are paired captures.
- Equivalent `narrow--*` captures cover the 480px viewport.
- `contact-*.png` files are grouped comparison sheets.

## Expected result

The React surface should preserve the legacy surface's information hierarchy,
controls, responsive behavior, visual states, and relative density while the
migration is in progress. Intentional improvements may differ, but they must be
documented and covered by parity tests. No repository page should overflow the
viewport at 480px.

## Actual result and discrepancy inventory

### 1. Shared application shell and repository header

These differences affect every audited route:

- The legacy global header is black, 69px high, and uses 14px typography. The
  React header is light gray, 64px high, and uses 16px typography.
- Legacy has the GitHub mark, search field, compact navigation, plus action,
  account menu, and sign-out treatment. React substitutes a dot mark, omits
  search and the plus action, adds `New repository` and `Legacy UI`, and spaces
  all links differently.
- At 480px, legacy collapses to a hamburger, centered GitHub mark, and sign-out
  button. React leaves desktop navigation in one row; it clips after `Explore`
  and does not provide a mobile menu.
- Legacy body typography is `14px/21px`; React is `16px/normal`. This changes
  row heights, card density, wrapping, and total page height throughout.
- Legacy repository context sits on a light-gray band and includes Octicons plus
  an active coral underline. React uses a white background, text-only tabs, and
  no selected-tab indicator.
- Legacy shows `Public`; React shows lowercase `public`.
- Repository tabs wrap differently at 480px (`Pull requests` becomes two lines
  in React), changing header height and alignment.

### 2. Repository home

The file/folder Octicons now match, and core content is present, but remaining
differences include:

- Branch, history, branches, tags, Add file, and README headings lack the legacy
  Octicons; the branch selector still uses a text glyph.
- Header, toolbar, activity links, file card, and README card spacing differ due
  to the global typography and shell rules.
- Legacy uses stronger selected-state and muted-icon styling.

### 3. Tree and blob views

- Legacy tree pages use a compact breadcrumb (`repo / path`). React replaces it
  with a large 32px page heading.
- React tree rows are substantially taller.
- Root ordering differs: legacy places both directories before files; React's
  tree view places `docs` after the files.
- Legacy places the branch/path context on the left and Add file on the right.
  React presents the ref as a small badge beside the page heading.
- Legacy blob view includes breadcrumbs, line numbers, `View`, and edit controls.
  React uses a large filename heading, raw `<pre>` content without line numbers,
  and differently placed Add file/Edit links.
- File card headers and code padding, font size, and border treatment differ.

### 4. Commits, branches, and tags

Commits:

- Legacy heading is `Commits on main`; React is only `Commits` with a detached
  ref badge.
- Legacy keeps commit rows compact and aligns short SHAs to the right. React
  uses taller rows and places metadata under each title.
- Legacy includes Newer/Older pagination controls; React does not.

Branches:

- React adds an inline branch-creation form that has no corresponding placement
  in legacy and dominates the page hierarchy.
- Legacy branch rows include branch icons, default/protected badges, latest
  commit summary, and timestamp. React emphasizes branch name and SHA and uses
  different protected/delete affordances.
- At 480px, React overflows horizontally by 170px because the create form does
  not wrap or stack.

Tags:

- Legacy has an icon-led centered empty state with title and explanatory copy.
  React shows a large bordered empty box containing only `No tags found.`

### 5. Issue and pull-request lists

- Legacy lists use open/closed counters with state colors and icons. React uses
  a page heading and omits those counters.
- Empty states differ: legacy uses an icon, title, and explanatory sentence;
  React uses a large bordered box with one short sentence.
- New issue/new pull controls differ in iconography, sizing, and alignment.
- React exposes `Labels` at the top of the issue list; legacy's labels navigation
  is presented as a separate side-navigation treatment on the labels page.

### 6. Issue detail

- Legacy uses a large regular-weight title, prominent state badge and author/time
  line, a timeline/card treatment for events, and a right-hand labels sidebar.
  React uses a heavier title, puts edit/label controls directly beneath it, and
  stretches all content across the main column.
- React renders label management as an always-visible selector near the title;
  legacy keeps labels in the sidebar.
- Timeline events, avatars/icons, header backgrounds, author emphasis, borders,
  and markdown spacing differ substantially.
- Legacy comment composition includes Write/Preview tabs, markdown guidance,
  and separate Reopen/Comment controls. React has a plain textarea, full-width
  green Comment button, and a separately placed Reopen button.
- The same content wraps into different line lengths because React lacks the
  legacy main-column/sidebar proportions.

### 7. Labels

- Legacy has an Issues/Labels side navigation, search field, result count, sort
  affordance, and compact rows. React omits the side navigation, count, search,
  and sort controls.
- React places name/color/description creation inputs in one unlabelled row and
  uses Edit/Delete on every row; legacy uses a New label action and primarily
  exposes Delete in the list.
- Row heights, label widths, descriptions, and action alignment differ.
- At 480px, React overflows horizontally by 319px due to the creation form and
  row actions.

### 8. Pull-request detail

- Legacy provides Conversation, Commits, and Files changed tabs. React omits
  this secondary navigation.
- Legacy shows merge/ref metadata in a subtitle line and labels in a right-hand
  sidebar. React places status, edit, refs, and label controls in a dense block
  beneath the title.
- Conversation cards, review/comment distinction, markdown layout, and editor
  controls differ in the same ways as issue detail.
- The merged state is a compact legacy status message; React renders a wide
  purple bar at the bottom.

### 9. Actions list and run detail

Actions list:

- Both use a workflow sidebar, but widths, dividers, and heading alignment differ.
- Legacy rows show run number, actor, timestamp, and separate status/conclusion
  pills. React repeats only the workflow name with raw event/ref text and uses a
  small colored dot for outcome.
- React rows are taller and omit useful run identity and time information.

Run detail:

- Legacy title includes the run number and displays status/conclusion badges.
  React shows only the workflow name with a small metadata line.
- Legacy has a Run metadata card containing commit, actor, created, and updated
  fields. React omits the card.
- Legacy Jobs card has a heading/icon, runner/start information, and status pills.
  React reduces this to a single large link row.
- React adds a small Re-run jobs button in a different hierarchy.

### 10. Repository settings

Shared settings layout:

- Legacy groups sidebar entries under Access; Code, planning, and automation;
  Security; and Integrations, with disabled placeholders and separators. React
  has a flat five-link sidebar and omits group headings and placeholder entries.
- Active-row dimensions, blue indicator, sidebar width, content width, and
  heading offsets differ.
- At 480px both surfaces are dense, but React keeps the desktop two-column layout
  and leaves a narrow content column instead of providing a deliberate mobile
  settings navigation pattern.

General:

- React omits repository rename, default-branch editing, template-repository
  setting, public/private visibility controls, Projects, and Discussions.
- React combines description/homepage/privacy/issues/wiki into one simple form
  and stretches Save changes across the content width; legacy uses distinct
  sections, explanations, dividers, and compact actions.

Collaborators:

- React omits the public-repository summary, Manage visibility action, Direct
  access summary card, and Manage access empty/summary treatment.
- React uses a plain table-like list and reduces collaborator permission
  management compared with legacy's cards and per-user controls.

Branches:

- Legacy first presents all branch-protection rules, then a detailed `Protect
  main` form with descriptions and grouped options. React starts with a branch
  selector and a compact undivided form.
- Status-check contexts, approval controls, explanatory copy, and option grouping
  do not align visually.

Runners:

- Legacy shows `Actions runners`, explanatory text, and a bordered Repository
  runners empty-state card. React shows only `Runners` and a horizontal rule.

GitHub Apps:

- Legacy installation cards include app icon placeholder, installation ID,
  permission pills, repository selection, and Configure action. React renders a
  plain stacked list of app names and permission text, omitting installation IDs,
  icons, cards, and Configure controls.

## Acceptance criteria

- Shared header, repository context header, typography, active states, and mobile
  navigation match the accepted legacy reference or have documented intentional
  replacements.
- Each of the 19 audited routes has equivalent information hierarchy, metadata,
  controls, empty states, icons, spacing, and responsive behavior.
- Tree content sorts directories before files; blob pages restore breadcrumb and
  code-view hierarchy; list/detail pages retain their legacy state and metadata.
- Settings pages expose and visually organize all legacy controls that remain in
  product scope.
- No route has horizontal overflow at 480px; specifically fix Branches (170px)
  and Labels (319px).
- Playwright parity coverage captures loaded state at desktop and narrow widths,
  and promoted routes pass semantic and screenshot assertions.

## Notes

The audit is read-only. Direct navigation was required for issue #7 and pull #8
because the list pages intentionally showed no open items, and for a few legacy
secondary links that are not exposed from their chosen parent page. These were
not counted as React visual defects by themselves.
