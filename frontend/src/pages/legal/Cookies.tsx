import { LegalLayout } from "./LegalLayout";
import { COOKIES_VERSION } from "./versions";

interface CookieRow {
  name: string;
  purpose: string;
  expiry: string;
  path: string;
  flags: string;
}

const COOKIES: CookieRow[] = [
  {
    name: "access_token",
    purpose:
      "Carries the short-lived access token that authenticates API requests from the browser. Signed by Supabase Auth.",
    expiry: "1 hour",
    path: "/",
    flags: "HttpOnly, Secure (in production), SameSite=Lax",
  },
  {
    name: "refresh_token",
    purpose:
      "Refreshes the access token without re-entering credentials. Scoped to the refresh endpoint to minimise exposure.",
    expiry: "30 days",
    path: "/auth/refresh",
    flags: "HttpOnly, Secure (in production), SameSite=Lax",
  },
  {
    name: "csrftoken_v2",
    purpose:
      "Double-submit CSRF token paired with the X-CSRFToken request header to defeat cross-site request forgery on state-changing endpoints.",
    expiry: "Session",
    path: "/",
    flags: "Secure (in production), SameSite=Lax, Domain=.bindwave.com (readable by our own JS to echo in header)",
  },
];

export default function CookiesPage() {
  return (
    <LegalLayout title="Cookie Policy" lastUpdated={COOKIES_VERSION}>
      <p>
        Bindwave uses only strictly-necessary cookies required to authenticate your
        session and protect against cross-site request forgery. We do not set
        analytics, advertising, marketing, or cross-site tracking cookies. No
        third-party scripts on our application pages set cookies in your browser on
        our behalf.
      </p>

      <h2 id="what-are-cookies">What are cookies?</h2>
      <p>
        A cookie is a small text file that a website stores on your device. Cookies let
        a site remember your session between page loads, hold a short-lived access
        token so you do not have to log in for every request, and detect forged
        cross-site requests.
      </p>

      <h2 id="inventory">Cookies we set</h2>

      <div className="not-prose overflow-x-auto">
        <table className="my-6 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b bg-muted/30 text-left">
              <th className="px-3 py-2 font-semibold">Name</th>
              <th className="px-3 py-2 font-semibold">Purpose</th>
              <th className="px-3 py-2 font-semibold">Expiry</th>
              <th className="px-3 py-2 font-semibold">Path</th>
              <th className="px-3 py-2 font-semibold">Flags</th>
            </tr>
          </thead>
          <tbody>
            {COOKIES.map((c) => (
              <tr key={c.name} className="border-b align-top">
                <td className="px-3 py-3 font-mono text-xs">{c.name}</td>
                <td className="px-3 py-3">{c.purpose}</td>
                <td className="px-3 py-3">{c.expiry}</td>
                <td className="px-3 py-3 font-mono text-xs">{c.path}</td>
                <td className="px-3 py-3 text-xs">{c.flags}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 id="consent">Consent</h2>
      <p>
        All three cookies above are strictly necessary to deliver the service you have
        requested. Under the EU ePrivacy Directive and comparable regimes, strictly
        necessary cookies do not require prior consent, but they must be disclosed —
        that is the purpose of this page and the banner shown on your first visit.
      </p>
      <p>
        Because we do not use analytics or advertising cookies, there is nothing to
        opt into or out of beyond the strictly necessary set. If you reject cookies
        entirely in your browser settings, Bindwave will be unable to maintain your
        authenticated session.
      </p>

      <h2 id="local-storage">Browser storage (not cookies)</h2>
      <p>
        The Bindwave frontend uses <code>localStorage</code> for a small set of
        non-identifying UI preferences — for example, the dismissal timestamp of the
        cookie banner and the collapsed/expanded state of sidebar panels. These
        entries are readable only by the Bindwave origin and are not cookies under
        European ePrivacy definitions.
      </p>

      <h2 id="changes">Changes</h2>
      <p>
        If we add a new cookie we will update the table above, bump the version string
        to a new date, and — for non-strictly-necessary cookies — introduce a granular
        consent control and prompt you before setting them. The current version string
        is <code>{COOKIES_VERSION}</code>.
      </p>

      <h2 id="contact">Contact</h2>
      <p>
        Questions about cookies or browser storage:{" "}
        <a href="mailto:privacy@ranomics.com">privacy@ranomics.com</a>.
      </p>
    </LegalLayout>
  );
}
