/**
 * Shared date/time formatting utilities.
 */

/**
 * Format a date string as a relative time (e.g. "2 hours ago").
 * Falls back to locale date string if the date is more than 30 days ago.
 *
 * @param isoString - ISO 8601 date string to format.
 * @returns Human-readable relative time string.
 */
export function relativeDate(isoString: string): string {
  const now = Date.now();
  const then = new Date(isoString).getTime();
  const diffMs = now - then;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);

  if (diffSec < 60) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  if (diffDay < 30) return `${diffDay}d ago`;
  return new Date(isoString).toLocaleDateString();
}
