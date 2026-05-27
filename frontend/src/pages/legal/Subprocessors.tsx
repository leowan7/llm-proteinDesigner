import { LegalLayout } from "./LegalLayout";
import { SUBPROCESSORS_VERSION } from "./versions";

interface SubprocessorRow {
  name: string;
  service: string;
  dataHandled: string;
  region: string;
  privacyUrl: string;
  dpaNote: string;
}

const SUBPROCESSORS: SubprocessorRow[] = [
  {
    name: "Supabase",
    service: "Authentication + Postgres database",
    dataHandled:
      "Email, hashed password, user UUID, session records, job metadata (not job content), billing customer identifier.",
    region: "US East (primary); region pinning on request for enterprise customers.",
    privacyUrl: "https://supabase.com/privacy",
    dpaNote: "SCC-backed DPA in place.",
  },
  {
    name: "Cloudflare",
    service: "R2 object storage + edge networking",
    dataHandled:
      "Uploaded protein structures, job output artefacts (PDB, CSV, reports) under per-job keys. Encrypted at rest.",
    region: "Global; primary bucket in US/EU dual-region config.",
    privacyUrl: "https://www.cloudflare.com/privacypolicy/",
    dpaNote: "Cloudflare DPA with SCCs executed.",
  },
  {
    name: "Modal Labs",
    service: "GPU compute (primary)",
    dataHandled:
      "Job payloads (structures + parameters) transmitted at dispatch and held ephemerally on the worker; outputs written back to our object store via presigned URLs.",
    region: "US (multi-region within Modal).",
    privacyUrl: "https://modal.com/legal/privacy",
    dpaNote: "Modal Trust & Security terms with SCCs.",
  },
  {
    name: "RunPod",
    service: "GPU compute (emergency fallback only)",
    dataHandled:
      "Same as Modal — used only if Modal is unavailable and GPU_PROVIDER is manually flipped to runpod_emergency. No routine traffic.",
    region: "US (primary); EU pods on request.",
    privacyUrl: "https://www.runpod.io/legal/privacy-policy",
    dpaNote: "Standard DPA; engaged only during declared incidents.",
  },
  {
    name: "Stripe",
    service: "Billing + payment processing",
    dataHandled:
      "Email, billing name and address, payment method token, tax identifiers, meter events (GPU-seconds), invoice history.",
    region: "US (Stripe HQ); EU/UK billing entities for customers in those regions.",
    privacyUrl: "https://stripe.com/privacy",
    dpaNote: "Stripe DPA with SCCs and UK IDTA executed.",
  },
  {
    name: "Anthropic",
    service: "Claude API (agent conversations, title generation)",
    dataHandled:
      "User chat messages, structure identifiers (e.g. PDB accession strings), and tool results used to drive the design workflow. Structure files themselves are not sent to Claude.",
    region: "US (Anthropic API).",
    privacyUrl: "https://www.anthropic.com/legal/privacy",
    dpaNote: "Anthropic Commercial Terms with zero-data-retention opt-in enabled for our workspace.",
  },
  {
    name: "Resend",
    service: "Transactional email delivery",
    dataHandled:
      "Recipient email address, email subject and body (job completion, failure, retention warnings, account deletion confirmations).",
    region: "US.",
    privacyUrl: "https://resend.com/legal/privacy-policy",
    dpaNote: "Resend DPA with SCCs.",
  },
  {
    name: "Sentry",
    service: "Error tracking + application monitoring",
    dataHandled:
      "User UUID, endpoint path, error stack trace, user-agent, sanitized request parameters. Request bodies, structures, and job payloads are scrubbed at ingestion.",
    region: "US (primary); EU ingestion region available.",
    privacyUrl: "https://sentry.io/privacy/",
    dpaNote: "Sentry DPA with SCCs.",
  },
];

export default function SubprocessorsPage() {
  return (
    <LegalLayout title="Subprocessors" lastUpdated={SUBPROCESSORS_VERSION}>
      <p>
        This page lists the third-party service providers that Ranomics Inc. engages
        to deliver Bindwave. Each subprocessor processes personal data only under
        written data-processing terms, solely for the purposes described below, and
        may not use the data for its own purposes.
      </p>
      <p>
        We give registered users at least 30 days' advance notice before adding a new
        subprocessor or materially changing how an existing one processes personal
        data. Notice is sent to the email address on file and posted here with the
        updated <code>Last updated</code> date below.
      </p>

      <h2 id="public-apis">Public APIs (not subprocessors)</h2>
      <p>
        Bindwave also queries the public <a href="https://www.rcsb.org/">RCSB Protein
        Data Bank</a> and <a href="https://www.uniprot.org/">UniProt</a> APIs using
        accession identifiers (for example, <code>4ZS7</code> or <code>P08887</code>).
        These queries do not transmit user identifiers or uploaded content. We do not
        classify these endpoints as subprocessors.
      </p>

      <h2 id="list">Current subprocessors</h2>

      <div className="not-prose overflow-x-auto">
        <table className="my-6 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b bg-muted/30 text-left">
              <th className="px-3 py-2 font-semibold">Subprocessor</th>
              <th className="px-3 py-2 font-semibold">Service</th>
              <th className="px-3 py-2 font-semibold">Data handled</th>
              <th className="px-3 py-2 font-semibold">Region</th>
              <th className="px-3 py-2 font-semibold">Details</th>
            </tr>
          </thead>
          <tbody>
            {SUBPROCESSORS.map((sp) => (
              <tr key={sp.name} className="border-b align-top">
                <td className="px-3 py-3 font-medium">{sp.name}</td>
                <td className="px-3 py-3">{sp.service}</td>
                <td className="px-3 py-3">{sp.dataHandled}</td>
                <td className="px-3 py-3">{sp.region}</td>
                <td className="px-3 py-3">
                  <a
                    href={sp.privacyUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline"
                  >
                    Privacy policy
                  </a>
                  <span className="block text-xs text-muted-foreground">
                    {sp.dpaNote}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 id="contact">Contact</h2>
      <p>
        Enterprise procurement and due-diligence requests:{" "}
        <a href="mailto:procurement@ranomics.com">procurement@ranomics.com</a>.
      </p>
    </LegalLayout>
  );
}
