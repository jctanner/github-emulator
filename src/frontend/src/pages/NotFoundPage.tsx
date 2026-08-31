export function NotFoundPage() {
  return (
    <section className="not-found-card">
      <p className="eyebrow">404</p>
      <h1>Page not found</h1>
      <p>The requested GitHub Emulator page does not exist.</p>
      <a className="button" href="/ui/">
        Return to repositories
      </a>
    </section>
  );
}
