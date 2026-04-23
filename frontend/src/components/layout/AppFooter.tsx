import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { requestOpenConsent } from "@/lib/cookieConsent";

/**
 * Persistent site footer shown on both public and authenticated layouts.
 *
 * Links to the four legal pages (Plan 10-01) and exposes a "Cookie preferences"
 * trigger that re-opens the cookie consent banner via the global custom event
 * dispatched by `requestOpenConsent()` (Plan 10-03).
 */
export function AppFooter() {
  const year = new Date().getFullYear();
  return (
    <footer
      role="contentinfo"
      className="border-t bg-background/50 px-4 py-4 text-xs text-muted-foreground"
    >
      <div className="mx-auto flex max-w-5xl flex-col items-center gap-3 sm:flex-row sm:justify-between">
        <span>© {year} Ranomics Inc.</span>
        <nav aria-label="Legal" className="flex flex-wrap items-center gap-4">
          <Link to="/legal/terms" className="hover:text-foreground hover:underline">
            Terms
          </Link>
          <Link to="/legal/privacy" className="hover:text-foreground hover:underline">
            Privacy
          </Link>
          <Link
            to="/legal/subprocessors"
            className="hover:text-foreground hover:underline"
          >
            Subprocessors
          </Link>
          <Link to="/legal/cookies" className="hover:text-foreground hover:underline">
            Cookies
          </Link>
          <Button
            variant="link"
            size="sm"
            onClick={requestOpenConsent}
            className="h-auto p-0 text-xs text-muted-foreground hover:text-foreground"
          >
            Cookie preferences
          </Button>
        </nav>
      </div>
    </footer>
  );
}
