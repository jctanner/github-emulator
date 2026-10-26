# Login page opens a basic-auth dialog

## Symptom

When signed out, opening the web UI could pop up the browser's native
username/password dialog over the login page. Headless Chrome stalled on the
same request.

## Cause

On load, the UI fetches `GET /api/_ui/session`. Signed out, that returns 401
from `require_auth` with `WWW-Authenticate: Basic realm="GitHub Emulator"`,
and `http_401_handler` passes the header through. A Basic challenge on a
fetch makes the browser prompt for credentials.

## Fix

`http_401_handler` leaves out `WWW-Authenticate` when the request came from a
page script. Browsers mark such a request with a `Sec-Fetch-Mode` other than
`navigate`. API clients, git clients and a browser navigating directly to an
API URL still get the challenge. Git Smart HTTP sets its own challenge and is
unchanged.

## Verification

- `tests/test_browser_session_api.py::test_signed_out_page_requests_get_no_basic_challenge`.
- Full backend suite: 753 passed. The frontend-build security-headers test
  was deselected as usual.
- Live in Chrome: signing in through the login form worked. The signed-out
  session check returned 401 with no challenge, and `curl` to `/api/v3/user`
  still gets `WWW-Authenticate: Basic`.
