import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";

interface AuthLayoutProps {
  title: string;
  subtitle: string;
  footer?: React.ReactNode;
  children: React.ReactNode;
}

/**
 * Shared layout for all 6 auth screens.
 *
 * Renders a centered Card on the dark background.
 * Layout contract from UI-SPEC:
 * - max-width: 400px, horizontally centered, vertical center with min-h-screen
 * - Card background: --card (zinc-900), border: --border
 * - CardHeader: padding lg (24px) top + horizontal; gap sm (8px) between title and description
 * - CardContent: padding md (16px) horizontal, lg (24px) bottom
 * - CardFooter: padding md (16px), centered text
 *
 * Typography:
 * - Title: Heading (20px/600)
 * - Subtitle: Body (16px/400, muted)
 */
export function AuthLayout({ title, subtitle, footer, children }: AuthLayoutProps) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-[400px]">
        <CardHeader className="space-y-2 p-6">
          <h1 className="text-xl font-semibold leading-[1.2] tracking-tight">
            {title}
          </h1>
          <p className="text-base font-normal leading-[1.5] text-muted-foreground">
            {subtitle}
          </p>
        </CardHeader>
        <CardContent className="px-6 pb-6">
          {children}
        </CardContent>
        {footer && (
          <CardFooter className="flex flex-col items-center gap-2 px-4 py-4 text-center text-sm text-muted-foreground">
            {footer}
          </CardFooter>
        )}
      </Card>
    </div>
  );
}
