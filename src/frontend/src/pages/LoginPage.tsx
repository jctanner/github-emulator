import {type FormEvent, useState} from "react";
import {Navigate, useLocation, useNavigate} from "react-router-dom";

import {useSession} from "../auth/SessionContext";

export function LoginPage() {
  const {user, login} = useSession();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  if (user) {
    return <Navigate to="/" replace />;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const usernameValue = form.get("username");
    const passwordValue = form.get("password");
    const username = typeof usernameValue === "string" ? usernameValue : "";
    const password = typeof passwordValue === "string" ? passwordValue : "";
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      const routeState = location.state as unknown;
      const destination =
        typeof routeState === "object" &&
        routeState !== null &&
        "from" in routeState &&
        typeof routeState.from === "string"
          ? routeState.from
          : "/";
      await navigate(destination, {replace: true});
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign in failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="auth-card" aria-labelledby="sign-in-heading">
      <h1 id="sign-in-heading">Sign in</h1>
      {error ? (
        <div className="flash-error" role="alert">
          {error}
        </div>
      ) : null}
      <form onSubmit={(event) => void submit(event)}>
        <label htmlFor="username">Username</label>
        <input
          id="username"
          name="username"
          autoComplete="username"
          required
          autoFocus
        />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
        />
        <button type="submit" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </section>
  );
}
