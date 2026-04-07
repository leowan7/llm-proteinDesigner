/**
 * AppHeader — slim application header for the authenticated app shell.
 *
 * Contains:
 * - Skip navigation link (sr-only, visible on keyboard focus) — WCAG 2.4.1
 * - Sidebar toggle trigger (when used inside SidebarProvider)
 * - Kendrew logo and wordmark
 * - Optional session title
 *
 * The skip nav link must be the FIRST focusable element in the page.
 * It links to #main-content, which must exist as an id on the main element.
 */

interface AppHeaderProps {
  /** Optional title for the current session or page context. */
  title?: string;
}

export function AppHeader({ title }: AppHeaderProps) {
  return (
    <header className="flex items-center gap-3 px-4 py-3 border-b border-border shrink-0">
      {/* Skip navigation link — first focusable element per WCAG 2.4.1.
          sr-only hides it visually; focus:not-sr-only makes it visible on keyboard focus.
          "Skip to main content" is the standard accessible label. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:px-4 focus:py-2 focus:bg-background focus:border focus:border-primary focus:rounded-md focus:text-sm focus:text-foreground"
      >
        Skip to main content
      </a>

      {/* Logo */}
      <span className="text-xl font-semibold text-foreground">
        Kendrew<span className="text-primary">.AI</span>
      </span>

      {/* Optional session title */}
      {title && (
        <span className="text-sm text-muted-foreground truncate max-w-[240px]">
          {title}
        </span>
      )}
    </header>
  );
}
