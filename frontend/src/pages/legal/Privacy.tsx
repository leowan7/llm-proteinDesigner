import { LegalLayout } from "./LegalLayout";
import { PRIVACY_VERSION } from "./versions";

export default function PrivacyPage() {
  return (
    <LegalLayout title="Privacy Policy" lastUpdated={PRIVACY_VERSION}>
      <p>
        This Privacy Policy explains how Ranomics Inc. (<em>"we,"</em> <em>"us"</em>)
        collects, uses, discloses, and retains personal data in connection with
        Kendrew. It supplements the{" "}
        <a href="/legal/terms">Terms of Service</a>. If a term is defined there, it has
        the same meaning here.
      </p>
      <p>
        For the purposes of the EU General Data Protection Regulation (<em>GDPR</em>)
        and the UK GDPR, Ranomics is the data controller. For the California Consumer
        Privacy Act as amended (<em>CCPA/CPRA</em>), Ranomics is the business. A
        Canadian PIPEDA-equivalent controller designation applies for users in Canada.
      </p>

      <h2 id="scope">1. Scope</h2>
      <p>
        This policy covers personal data we receive or generate when you create an
        account, upload structures, launch jobs, receive emails from us, interact with
        our support channels, or browse our legal pages.
      </p>

      <h2 id="what-we-collect">2. What we collect</h2>

      <h3 id="account-data">Account data</h3>
      <p>
        Email address, hashed password, optional display name, preferred notification
        settings, Stripe customer identifier, and whether the account has administrator
        privileges. Legal basis (GDPR): contract (Article 6(1)(b)).
      </p>

      <h3 id="content">Content you provide</h3>
      <p>
        Protein structures in PDB or mmCIF format, chain and residue selections, job
        parameters, agent conversation messages, and any files you upload for
        post-hoc analysis. Legal basis: contract (Article 6(1)(b)).
      </p>

      <h3 id="derived-content">Derived content</h3>
      <p>
        Candidate designs, scores, interface analyses, and generated reports produced
        by the service on your behalf. Legal basis: contract (Article 6(1)(b)).
      </p>

      <h3 id="metadata">Operational metadata</h3>
      <p>
        Timestamps, GPU-seconds consumed, cost computed, provider job identifiers, IP
        address at request time (used for rate limiting and abuse prevention only; not
        stored beyond aggregate counters and security audit logs), user-agent string,
        and structured application logs. Legal basis: legitimate interests
        (Article 6(1)(f)) — keeping the service stable, secure, and billed correctly.
      </p>

      <h3 id="billing-data">Billing data</h3>
      <p>
        Stripe holds your payment method details; we do not store card numbers. We
        receive a customer identifier, the last four digits and brand of the stored
        card, billing email, and invoice history. Legal basis: contract and legal
        obligation (Article 6(1)(b) and (c)) — tax and accounting records.
      </p>

      <h3 id="cookies">Cookies</h3>
      <p>
        We use only three strictly-necessary cookies — <code>access_token</code>,{" "}
        <code>refresh_token</code>, <code>csrftoken</code> — for authentication and
        CSRF protection. We do not set analytics, advertising, or cross-site tracking
        cookies. See the <a href="/legal/cookies">Cookie Policy</a> for details.
      </p>

      <h2 id="how-we-use">3. How we use your data</h2>
      <ul>
        <li>Provide the service: run your jobs, stream status updates, store results,
          deliver email notifications.</li>
        <li>Bill you: compute metered usage, forward events to Stripe, send receipts.</li>
        <li>Keep the service secure and reliable: rate limiting, abuse prevention,
          incident response, audit logging of administrative actions.</li>
        <li>Communicate with you about the service: maintenance notices, material
          changes to these terms, responses to support requests.</li>
        <li>Comply with legal obligations: tax records, lawful requests from
          authorities, litigation holds.</li>
      </ul>
      <p>
        <strong>We do not use your uploaded structures, sequences, job parameters, or
        outputs to train, fine-tune, or otherwise improve any AI model.</strong> We do
        not sell your personal data or share it for behavioural advertising. We do not
        engage in automated decision-making that produces legal or similarly
        significant effects on you.
      </p>

      <h2 id="subprocessors">4. Who we share data with</h2>
      <p>
        We rely on a small set of subprocessors to deliver the service. Each is listed
        at <a href="/legal/subprocessors">/legal/subprocessors</a> with the service it
        provides, the data it handles, and its processing region. Every subprocessor
        is bound by written data-processing terms that restrict use to the purposes we
        instruct.
      </p>
      <p>
        We may also disclose personal data if required to do so by law, in response to
        valid legal process, to protect the rights or safety of Ranomics, our users, or
        the public, or in connection with a corporate transaction (subject to this
        policy continuing to apply to transferred data).
      </p>

      <h2 id="retention">5. Retention</h2>
      <p>Default retention windows:</p>
      <ul>
        <li>Uploaded structures and job outputs: <strong>90 calendar days from job
          creation</strong>, unless you configure a different value in settings
          (minimum 30, maximum 365 days). We email you 7 days before deletion so you
          can download or extend.</li>
        <li>Account profile and settings: retained while your account is active.</li>
        <li>Billing records (invoices, meter events): retained for 7 years to satisfy
          tax and accounting obligations.</li>
        <li>Audit logs of administrative actions: retained for 2 years.</li>
        <li>Security and application logs: retained for 90 days in rolling buckets.</li>
      </ul>
      <p>
        On account deletion, personal data and job content are hard-deleted after the
        30-day grace period, except records we are required to keep by law (for
        example, invoices). Where we retain data for legal obligations we minimise what
        is kept and restrict access.
      </p>

      <h2 id="security">6. Security</h2>
      <p>
        We apply industry-standard technical and organisational measures — TLS in
        transit, encryption at rest, HTTP-only authentication cookies, row-level
        security on all user-scoped database tables, least-privilege service accounts,
        and Sentry-backed error monitoring with scrubbing of request bodies. Access to
        production data is limited to named personnel under confidentiality
        obligations. You are responsible for keeping your own credentials confidential;
        report suspected incidents to{" "}
        <a href="mailto:security@ranomics.com">security@ranomics.com</a>.
      </p>

      <h2 id="your-rights">7. Your rights</h2>

      <h3 id="gdpr-rights">GDPR / UK GDPR / PIPEDA rights</h3>
      <ul>
        <li><strong>Access (Art. 15)</strong> — request a copy of the personal data we
          hold about you.</li>
        <li><strong>Rectification (Art. 16)</strong> — ask us to correct inaccurate or
          incomplete data.</li>
        <li><strong>Erasure (Art. 17)</strong> — request deletion of your account and
          associated data, subject to legal retention obligations.</li>
        <li><strong>Restriction (Art. 18)</strong> — ask us to limit processing while
          we address a dispute.</li>
        <li><strong>Portability (Art. 20)</strong> — export your data in a
          machine-readable format.</li>
        <li><strong>Objection (Art. 21)</strong> — object to processing based on
          legitimate interests (for example, security telemetry).</li>
        <li><strong>Withdraw consent</strong> — where we rely on consent, withdraw it
          at any time without affecting the lawfulness of prior processing.</li>
        <li><strong>Complaint</strong> — lodge a complaint with your supervisory
          authority (for example, the Office of the Privacy Commissioner of Canada,
          the ICO in the UK, or your national data protection authority in the EU).</li>
      </ul>

      <h3 id="ccpa-rights">CCPA / CPRA rights (California residents)</h3>
      <ul>
        <li><strong>Right to know</strong> — categories and specific pieces of
          personal information collected, sold, or disclosed.</li>
        <li><strong>Right to delete</strong> — request deletion, subject to statutory
          exceptions.</li>
        <li><strong>Right to correct</strong> — correct inaccurate personal
          information.</li>
        <li><strong>Right to opt out of sale or sharing</strong> — we do not sell or
          share your personal information as those terms are defined in CCPA/CPRA, so
          no opt-out is required.</li>
        <li><strong>Right to limit use of sensitive personal information</strong> — we
          do not use sensitive personal information for purposes beyond providing the
          service.</li>
        <li><strong>Right to non-discrimination</strong> — we will not discriminate
          against you for exercising any of these rights.</li>
      </ul>

      <h3 id="how-to-exercise">How to exercise your rights</h3>
      <p>
        Once the Privacy tab in Settings is available you can self-serve data export
        and account deletion from there. Until then, email{" "}
        <a href="mailto:privacy@ranomics.com">privacy@ranomics.com</a> from the address
        on file and we will respond within 30 days. We may need to verify your identity
        before acting on a request.
      </p>

      <h2 id="international-transfers">8. International data transfers</h2>
      <p>
        Your data may be processed in Canada, the United States, and the European
        Union depending on the subprocessor routing the request. Transfers are covered
        by Standard Contractual Clauses, the UK International Data Transfer Addendum,
        or equivalent safeguards where required. A list of subprocessor regions is
        maintained at <a href="/legal/subprocessors">/legal/subprocessors</a>.
      </p>

      <h2 id="children">9. Children</h2>
      <p>
        Kendrew is not directed to children under 16. We do not knowingly collect
        personal data from children under 16. If you believe a child has provided us
        with personal data, contact{" "}
        <a href="mailto:privacy@ranomics.com">privacy@ranomics.com</a> so we can
        remove it.
      </p>

      <h2 id="changes">10. Changes to this policy</h2>
      <p>
        We will post material changes here and, where material, email registered
        users at least 30 days before the changes take effect. The current version
        string is <code>{PRIVACY_VERSION}</code>.
      </p>

      <h2 id="contact">11. Contact</h2>
      <p>
        Privacy inquiries:{" "}
        <a href="mailto:privacy@ranomics.com">privacy@ranomics.com</a>
        <br />
        Data protection representative (EU/UK): contact via the same address; we will
        appoint a named representative before general availability in the EEA/UK.
        <br />
        Postal: Ranomics Inc., Toronto, Ontario, Canada.
      </p>
    </LegalLayout>
  );
}
