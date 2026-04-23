import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

interface Props {
  onAccept: () => void;
}

/**
 * First-visit cookie consent banner (Plan 10-03).
 *
 * Fixed-position strip at the bottom of the viewport. Discloses the three
 * strictly-necessary cookies (access_token, refresh_token, csrftoken) and
 * links to the full /legal/cookies page. Dismissed via a single "Got it"
 * button — we do not use analytics or tracking cookies, so there is no
 * granular opt-in to offer.
 *
 * Mounted via CookieConsentProvider; do not render directly.
 */
export function CookieConsentBanner({ onAccept }: Props) {
  return (
    <div
      role="region"
      aria-label="Cookie notice"
      className="fixed inset-x-0 bottom-0 z-50 border-t border-border bg-background/95 px-4 py-3 shadow-lg backdrop-blur supports-[backdrop-filter]:bg-background/80"
    >
      <div className="mx-auto flex max-w-5xl flex-col gap-3 text-sm sm:flex-row sm:items-center sm:justify-between">
        <p className="text-muted-foreground">
          Kendrew uses only strictly-necessary cookies (
          <code className="text-foreground">access_token</code>,{" "}
          <code className="text-foreground">refresh_token</code>,{" "}
          <code className="text-foreground">csrftoken</code>) to keep you
          signed in and protect against CSRF. No analytics, advertising, or
          third-party tracking cookies are set.{" "}
          <Link
            to="/legal/cookies"
            className="underline underline-offset-2 hover:text-foreground"
          >
            Learn more
          </Link>
          .
        </p>
        <Button size="sm" onClick={onAccept} className="shrink-0">
          Got it
        </Button>
      </div>
    </div>
  );
}
