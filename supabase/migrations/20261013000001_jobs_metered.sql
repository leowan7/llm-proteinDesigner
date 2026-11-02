-- ============================================================================
-- jobs.metered: which runs' GPU time was sent to the Stripe meter
-- ============================================================================
-- Since ad00f211 a failed run that saved a design is metered to Stripe, as a
-- cancelled run already was, but every admin and customer spend and revenue
-- read summed only status = 'complete' rows, so those charges read as $0 to
-- the operator and to the customer who paid them. gpu_cost_usd cannot tell a
-- charged run from an uncharged one: the webhook writes it on every terminal
-- job it settles (backend/webhooks/router.py:299 and :306) and the reaper on
-- every job it claims (backend/worker/cleanup.py:281), billed or not.
--
-- billing.stripe_client.mark_metered sets this column; the three routes that
-- call record_gpu_usage call it first and meter only the customer it returns.
-- If record_gpu_usage then raises, the route passes the error to
-- unmark_metered (backend/webhooks/router.py:440, backend/worker/cleanup.py:385,
-- backend/jobs/service.py:191), which clears the flag only for the Stripe
-- refusals in billing.stripe_client._METER_REFUSED (stripe_client.py:217-222,
-- checked at :235). A connection error or a 5xx leaves it TRUE, so a meter
-- event Stripe never recorded can read as billed. unmark_metered logs and
-- swallows its own database error, and the flag then stays TRUE.
--
-- Leo applies migrations. railway.toml:16 runs `supabase db push` as the
-- backend's preDeployCommand, so merging also applies it to production.
-- ============================================================================

ALTER TABLE public.jobs
    ADD COLUMN metered BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.jobs.metered IS
    'TRUE once the route that settled this job sent its GPU time to the '
    'Stripe meter. Set just before the meter call and, best-effort, cleared '
    'if Stripe refuses the event; a connection error or a 5xx leaves it TRUE. '
    'Spend and revenue reads count a failed or cancelled job only when this '
    'is TRUE; a complete job counts either way.';

-- ----------------------------------------------------------------------------
-- Backfill
-- ----------------------------------------------------------------------------
-- complete: treated as metered. The reads count complete rows without looking
-- at this column, so this changes no total.
--
-- cancelled with gpu_seconds > 0 and an org that resolves to a Stripe
-- customer: the cancel route has metered these since before ad00f211
-- (a479ac38:backend/jobs/service.py:131 and :140), resolving the customer the
-- way this UPDATE does. A customer added to the org after the cancel makes the
-- row read as metered here though it was not; this backfill cannot tell.
--
-- Only those with a gpu_cost_usd are flagged. Rows cancelled before ad00f211
-- carry a NULL cost, because that version wrote the status first (:103) and
-- then guarded the cost write on the old status (:106). Flagged, they would
-- add nothing to any sum but would enter user/router.py::get_usage's
-- recent_charges with gpu_cost_usd None (user/router.py:195), which
-- frontend/src/pages/SettingsPage.tsx:380 formats with .toFixed. So those
-- metered charges stay unreported. The price that applied is a runtime
-- setting (backend/config.py), not stored, so their cost is not reconstructed.
--
-- failed: left FALSE. A billed failed run -- BindCraft from 1e392cd4 to
-- ad00f211, and any tool on the webhook or reaper route from ad00f211 until
-- this column exists -- was gated on a Storage listing taken at settle time
-- that the row does not record. Those charges stay unreported until
-- reconciled by hand.
UPDATE public.jobs
   SET metered = TRUE
 WHERE status = 'complete'
    OR (status = 'cancelled'
        AND gpu_seconds > 0
        AND gpu_cost_usd IS NOT NULL
        AND public.org_stripe_customer(organization_id) IS NOT NULL);
