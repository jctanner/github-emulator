import {type FormEvent, type PropsWithChildren, useEffect, useState} from "react";
import {Link, useLocation, useNavigate} from "react-router-dom";

import {useSession} from "../auth/SessionContext";
import {Octicon} from "./Octicon";

export function AppShell({children}: PropsWithChildren) {
  const {user, logout} = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const routeQuery =
    location.pathname === "/search"
      ? new URLSearchParams(location.search).get("q") ?? ""
      : "";
  const [search, setSearch] = useState(routeQuery);

  useEffect(() => setSearch(routeQuery), [routeQuery]);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    const query = search.trim();
    void navigate(query ? `/search?q=${encodeURIComponent(query)}` : "/search");
  }

  async function signOut() {
    await logout();
    await navigate("/");
  }

  return (
    <>
      <header className={`app-header${menuOpen ? " menu-open" : ""}`}>
        <button
          className="mobile-menu-toggle"
          type="button"
          aria-label="Toggle navigation"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <Octicon name="menu" />
        </button>
        <Link className="brand" to="/" aria-label="GitHub Emulator home">
          <Octicon name="mark-github" size={32} />
          <span>GitHub Emulator</span>
        </Link>
        <form
          aria-label="Global search"
          className="global-search"
          role="search"
          onSubmit={submitSearch}
        >
          <button
            aria-label="Submit search"
            className="global-search-submit"
            type="submit"
          >
            <Octicon name="search" />
          </button>
          <input
            aria-label="Search"
            placeholder="Search repositories..."
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </form>
        <nav className="global-nav" aria-label="Global navigation">
          <Link to="/">Dashboard</Link>
          <Link to="/search">Explore</Link>
          <Link to="/_admin/">Admin</Link>
        </nav>
        {user ? (
          <Link className="header-create" to="/new" aria-label="New repository">
            <Octicon name="plus" />
          </Link>
        ) : null}
        <div className="account">
          {user ? (
            <>
              <Link className="account-name" to={`/${user.login}`}>
                {user.login}
              </Link>
              <button
                className="link-button sign-out"
                type="button"
                onClick={() => void signOut()}
              >
                Sign out
              </button>
            </>
          ) : (
            <Link to="/login">Sign in</Link>
          )}
        </div>
      </header>
      <main className="page">{children}</main>
      <footer>© GitHub Emulator v0.1.0</footer>
    </>
  );
}
