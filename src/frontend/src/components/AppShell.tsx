import {type PropsWithChildren, useState} from "react";
import {Link, useNavigate} from "react-router-dom";

import {useSession} from "../auth/SessionContext";
import {Octicon} from "./Octicon";

export function AppShell({children}: PropsWithChildren) {
  const {user, logout} = useSession();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

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
        <label className="global-search">
          <Octicon name="search" />
          <input aria-label="Search" placeholder="Search..." />
        </label>
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
