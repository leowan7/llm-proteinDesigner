/**
 * Canonical constants shared by the legal pages.
 *
 * Downstream consumers:
 * - Signup form (Plan 10-02): stores TOS_VERSION in users.tos_version on acceptance
 * - Login middleware (Plan 10-02): compares users.tos_version to TOS_VERSION and
 *   forces re-acceptance on mismatch
 * - Cookie consent banner (Plan 10-03): stores COOKIES_VERSION in localStorage
 *
 * When revising a legal document, bump its version here AND update the "Last
 * updated" date in the corresponding page. Do not reuse dates — always roll forward.
 */
export const TOS_VERSION = "2026-04-23";
export const PRIVACY_VERSION = "2026-04-23";
export const COOKIES_VERSION = "2026-04-23";
export const SUBPROCESSORS_VERSION = "2026-04-23";

/**
 * Name of the entity that operates Bindwave, as printed in the legal pages,
 * the footer copyright line, and the postal address.
 *
 * No company is registered for Bindwave yet, so this is the bare product name
 * with no corporate suffix. When a registered name exists, change it here and
 * nowhere else: every customer-facing occurrence of the operator name reads
 * this constant.
 */
export const LEGAL_ENTITY = "Bindwave";
