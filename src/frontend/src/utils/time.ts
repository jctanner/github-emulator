const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Relative age in GitHub's wording: "2 days ago", "yesterday", "last month". */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.floor((now.getTime() - then) / 1000));
  const plural = (count: number, unit: string) =>
    `${count} ${unit}${count === 1 ? "" : "s"} ago`;

  if (seconds < MINUTE) return "now";
  if (seconds < HOUR) return plural(Math.floor(seconds / MINUTE), "minute");
  if (seconds < DAY) return plural(Math.floor(seconds / HOUR), "hour");
  const days = Math.floor(seconds / DAY);
  if (days === 1) return "yesterday";
  // Weeks for 2-3 weeks, then days again until the month rolls over; this
  // matches what github.com shows (22 days -> "3 weeks ago", 29 -> "29 days ago").
  if (days >= 14 && days < 28) return plural(Math.floor(days / 7), "week");
  if (days < 30) return plural(days, "day");
  const months = Math.floor(days / 30);
  if (months < 2) return "last month";
  if (months < 12) return plural(months, "month");
  const years = Math.floor(days / 365);
  return years < 2 ? "last year" : plural(years, "year");
}
