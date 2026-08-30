import {useLocation} from "react-router-dom";

export function MigrationPage() {
  const location = useLocation();
  const legacyPath = `/ui-legacy${location.pathname}${location.search}`;

  return (
    <section className="migration-card">
      <p className="eyebrow">API-client frontend migration</p>
      <h1>This route has not migrated yet</h1>
      <p>
        The typed frontend shell is active. Use the retained server-rendered
        page while this route is brought to API and visual parity.
      </p>
      <a className="button" href={legacyPath}>
        Open this route in the legacy UI
      </a>
    </section>
  );
}
