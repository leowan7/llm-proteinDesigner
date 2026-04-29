import { LegalLayout } from "./LegalLayout";
import { TOS_VERSION } from "./versions";

export default function TermsPage() {
  return (
    <LegalLayout title="Terms of Service" lastUpdated={TOS_VERSION}>
      <p>
        These Terms of Service (the <em>"Terms"</em>) govern your access to and use of
        Kendrew, an AI protein design platform operated by Ranomics Inc.
        (<em>"Ranomics,"</em> <em>"we,"</em> <em>"us"</em>). By creating an account or
        using the service you agree to these Terms. If you are entering into these Terms
        on behalf of an organization, you represent that you have authority to bind that
        organization.
      </p>

      <h2 id="acceptance">1. Acceptance & Eligibility</h2>
      <p>
        You must be at least 16 years old and legally able to form a binding contract in
        your jurisdiction. You must provide accurate registration information and keep
        your credentials confidential. You are responsible for all activity under your
        account. Notify us immediately at{" "}
        <a href="mailto:security@ranomics.com">security@ranomics.com</a> if you suspect
        unauthorized access.
      </p>

      <h2 id="service-description">2. The Service</h2>
      <p>
        Kendrew accepts protein structures and design briefs, runs them through
        third-party AI models (including RFdiffusion, RFantibody, BindCraft, BoltzGen,
        and PXDesign) on managed GPU infrastructure, and returns ranked candidate
        sequences and structures. The service is provided on a pay-per-use basis metered
        in GPU-seconds.
      </p>
      <p>
        We do not guarantee that any generated design will be experimentally validated,
        biologically active, manufacturable, or free of third-party intellectual
        property claims. You are solely responsible for evaluating outputs before any
        laboratory, clinical, or commercial use.
      </p>

      <h2 id="ip-ownership">3. Your Content & IP Ownership</h2>
      <p>
        <strong>You retain all rights to the structures and specifications you upload
        to Kendrew, and to all designs the service produces for you.</strong> We do not
        claim any ownership, copyright, patent, or other intellectual property right in
        your content or outputs.
      </p>
      <p>
        You grant us a limited, non-exclusive, worldwide licence to host, transmit, and
        process your content solely as required to operate the service for you — for
        example, to pass a structure to a GPU worker or to display your job history.
        This licence ends automatically when you delete the content or your account
        (subject to the retention windows described below).
      </p>

      <h2 id="no-training">4. No Training on Your Content</h2>
      <p>
        <strong>We do not use customer-uploaded structures, sequences, job parameters,
        or outputs to train, fine-tune, or otherwise improve any AI model.</strong> We
        do not share your content with third parties except with the subprocessors
        listed at <a href="/legal/subprocessors">/legal/subprocessors</a>, which process
        it only to deliver the service on our behalf under written data-processing
        agreements and comparable confidentiality obligations.
      </p>
      <p>
        Aggregated and fully de-identified operational telemetry (for example, the
        total number of jobs per week or average GPU runtime per tool) may be used to
        operate, secure, and improve the service. Such telemetry never includes your
        structures, sequences, or design outputs.
      </p>

      <h2 id="acceptable-use">5. Acceptable Use</h2>
      <p>You agree not to:</p>
      <ul>
        <li>Attempt to extract, reverse-engineer, or distil the underlying models or
          infrastructure, or benchmark the service for a competing product without our
          prior written consent.</li>
        <li>Circumvent or attempt to circumvent rate limits, quotas, billing meters,
          authentication, or security controls.</li>
        <li>Share account credentials, API keys (when introduced), or session cookies
          with any other person or system.</li>
        <li>Design or request designs for agents intended to cause harm to humans,
          animals, or critical infrastructure — including biological weapons, dual-use
          pathogens, or any use prohibited by the Biological Weapons Convention or
          applicable export controls.</li>
        <li>Upload content that infringes third-party rights or violates applicable
          law in your jurisdiction.</li>
        <li>Use the service to generate content that disparages, defames, or harasses
          any identifiable person.</li>
      </ul>
      <p>
        We may suspend or terminate access for violations. For severe violations we may
        also report activity to competent authorities.
      </p>

      <h2 id="payment">6. Fees & Payment</h2>
      <p>
        Pricing is metered in GPU-seconds at the rate disclosed at the time a job is
        launched. Charges flow through Stripe as a Billing Meter event. Estimated costs
        are shown before dispatch; actual cost is computed from realised GPU time plus a
        disclosed margin. You are responsible for all taxes unless a valid exemption
        certificate is on file.
      </p>
      <p>
        All amounts are in United States Dollars unless stated otherwise. Fees are
        non-refundable except where required by law or expressly provided by us (for
        example, when a documented service incident caused a failed job).
      </p>

      <h2 id="retention">7. Data Retention</h2>
      <p>
        Uploaded structures and job outputs are retained by default for 90 calendar
        days from job creation, after which they are automatically deleted from our
        object store. You can extend retention per job (up to 365 days) or shorten it
        (minimum 30 days) from the Privacy tab in your settings. Account-level records
        (profile, billing history, audit logs) are retained while your account is
        active and for the period required by applicable law after deletion.
      </p>

      <h2 id="termination">8. Suspension & Termination</h2>
      <p>
        You may delete your account at any time from the Privacy tab in your settings.
        Upon deletion, your account enters a 30-day grace period during which you can
        reactivate via an email-verified link. After the grace period, personal data
        and job content are hard-deleted, subject to records we must retain to comply
        with tax, accounting, or other legal obligations.
      </p>
      <p>
        We may suspend or terminate your account immediately if you materially breach
        these Terms, if continued service would expose us or others to legal or
        security risk, or on 30 days' written notice for convenience. On our
        termination for convenience we will refund any pre-paid unused balance.
      </p>

      <h2 id="warranties-and-liability">9. Warranty Disclaimer & Liability</h2>
      <p>
        The service is provided <strong>"as is"</strong> and{" "}
        <strong>"as available."</strong> To the maximum extent permitted by law, we
        disclaim all implied warranties, including merchantability, fitness for a
        particular purpose, and non-infringement. We do not warrant that outputs will
        be novel, non-obvious, patentable, or free of third-party rights.
      </p>
      <p>
        To the maximum extent permitted by law, our aggregate liability arising out of
        or relating to these Terms or the service, whether in contract, tort, or
        otherwise, is limited to the greater of (a) the fees you paid us in the twelve
        months preceding the event giving rise to the claim, or (b) one hundred US
        dollars. Neither party is liable for indirect, incidental, consequential,
        special, or punitive damages. These limits do not apply to your indemnification
        obligations, your violations of the acceptable use section, or to liability
        that cannot be limited under applicable law.
      </p>

      <h2 id="indemnification">10. Indemnification</h2>
      <p>
        You will defend, indemnify, and hold harmless Ranomics and its personnel from
        any third-party claim arising out of (a) your content, (b) your use of the
        service in violation of these Terms or applicable law, or (c) your use of any
        output in research, product, or clinical development. We will defend,
        indemnify, and hold you harmless from any third-party claim that the service
        itself, as provided by us, infringes that third party's intellectual property
        rights, subject to the liability cap above.
      </p>

      <h2 id="changes">11. Changes to These Terms</h2>
      <p>
        We may revise these Terms from time to time. Material changes will be
        announced by email at least 30 days before they take effect. Your continued use
        after the effective date constitutes acceptance of the revised Terms. If you do
        not accept the revised Terms, you may terminate your account from the Privacy
        tab. The current version string for these Terms is{" "}
        <code>{TOS_VERSION}</code>.
      </p>

      <h2 id="governing-law">12. Governing Law & Disputes</h2>
      <p>
        These Terms are governed by the laws of the Province of Ontario, Canada, and
        the federal laws of Canada applicable therein, without regard to conflict-of-law
        rules. The courts of Ontario have exclusive jurisdiction for residents of
        Canada.
      </p>
      <p>
        For users outside Canada, any dispute arising out of or relating to these Terms
        or the service will be finally resolved by binding arbitration administered by
        the ADR Institute of Canada under its Arbitration Rules. The seat of
        arbitration is Toronto, Ontario. The arbitration will be conducted in English
        by a single arbitrator. The parties waive any right to participate in a class
        or representative action. Either party may still bring an individual claim in
        small-claims court where available.
      </p>

      <h2 id="miscellaneous">13. Miscellaneous</h2>
      <p>
        If any provision of these Terms is held unenforceable, the remaining provisions
        remain in effect. Our failure to enforce a right is not a waiver. You may not
        assign these Terms without our written consent; we may assign to a successor in
        connection with a corporate reorganization, merger, or sale. These Terms
        together with the Privacy Policy constitute the entire agreement between you
        and us regarding the service.
      </p>

      <h2 id="contact">14. Contact</h2>
      <p>
        Ranomics Inc.
        <br />
        Toronto, Ontario, Canada
        <br />
        Legal notices: <a href="mailto:legal@ranomics.com">legal@ranomics.com</a>
        <br />
        Security reports: <a href="mailto:security@ranomics.com">security@ranomics.com</a>
        <br />
        Privacy inquiries: <a href="mailto:privacy@ranomics.com">privacy@ranomics.com</a>
      </p>
    </LegalLayout>
  );
}
