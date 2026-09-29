/** A run's or job's state as GitHub colours it: the conclusion once there is one, else the status. */
export function StatusPill({
  status,
  conclusion,
}: {
  status: string;
  conclusion?: string | null;
}) {
  const shown = conclusion ?? status;
  return (
    <span className={`status-pill status-${shown}`}>
      {shown.replaceAll("_", " ")}
    </span>
  );
}
