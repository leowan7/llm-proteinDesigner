/**
 * AdminStatCard — reusable summary metric card for admin dashboard pages.
 *
 * Used in the summary cards row at the top of each admin page.
 * Typography per UI-SPEC:
 *   - Label: 12px semibold, muted-foreground
 *   - Value: 28px semibold, foreground (Display role)
 *   - SubLabel: 12px regular, muted-foreground
 *
 * Card background: --card. Border: --border. Radius: --radius (0.625rem).
 * Minimum height: 80px (enough to display large metric numbers without overflow).
 */

interface AdminStatCardProps {
  /** Short descriptor shown above the value (e.g. "Total Users") */
  label: string;
  /** Primary metric displayed in large type (e.g. "1,240" or "$4.80") */
  value: string;
  /** Optional secondary line below the value (e.g. "this month") */
  subLabel?: string;
}

/**
 * Render a summary stat card with a label, large value, and optional sub-label.
 *
 * @example
 * <AdminStatCard label="Total Users" value="47" subLabel="since launch" />
 */
export function AdminStatCard({ label, value, subLabel }: AdminStatCardProps) {
  return (
    <div className="bg-card border border-border rounded-[0.625rem] p-4 min-h-[80px]">
      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
        {label}
      </p>
      <p className="text-[28px] font-semibold leading-tight text-foreground mt-1">
        {value}
      </p>
      {subLabel && (
        <p className="text-xs text-muted-foreground mt-1">{subLabel}</p>
      )}
    </div>
  );
}
