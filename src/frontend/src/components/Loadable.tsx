import type {PropsWithChildren} from "react";

interface LoadableProps extends PropsWithChildren {
  loading: boolean;
  error: string | null;
}

export function Loadable({loading, error, children}: LoadableProps) {
  if (loading) return <p className="loading">Loading…</p>;
  if (error)
    return (
      <div className="flash-error" role="alert">
        {error}
      </div>
    );
  return <>{children}</>;
}
