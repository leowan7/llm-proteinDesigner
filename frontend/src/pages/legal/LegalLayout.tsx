import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";

interface LegalLayoutProps {
  title: string;
  lastUpdated: string;
  children: React.ReactNode;
}

/**
 * Shared chrome for all /legal/* pages.
 *
 * Renders:
 * - "Draft — legal review pending" banner (remove after counsel sign-off)
 * - Page title + "Last updated" date
 * - Prose-styled content area
 * - Return-to-app button at the bottom
 */
export function LegalLayout({ title, lastUpdated, children }: LegalLayoutProps) {
  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <DraftBanner />

      <header className="mb-8 border-b pb-4">
        <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Last updated: <time dateTime={lastUpdated}>{lastUpdated}</time>
        </p>
      </header>

      <div className="prose prose-slate max-w-none dark:prose-invert prose-headings:scroll-mt-24 prose-h2:mt-10 prose-h2:mb-3 prose-h3:mt-6 prose-h3:mb-2 prose-p:leading-relaxed prose-li:leading-relaxed">
        {children}
      </div>

      <footer className="mt-12 flex items-center justify-between border-t pt-6 text-sm text-muted-foreground">
        <span>© Ranomics Inc.</span>
        <Button asChild variant="outline" size="sm">
          <Link to="/">Return to Kendrew</Link>
        </Button>
      </footer>
    </div>
  );
}

function DraftBanner() {
  return (
    <div
      role="note"
      aria-label="Draft status"
      className="mb-6 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-900/20 dark:text-amber-200"
    >
      <strong className="font-semibold">Draft — legal review pending.</strong>{" "}
      This document is an operational draft pending review by qualified legal
      counsel. The binding commercial version will replace this page before
      general availability. Questions:{" "}
      <a href="mailto:legal@ranomics.com" className="underline">
        legal@ranomics.com
      </a>
      .
    </div>
  );
}
