"""Error classification for job failures.

Phase 5 of the Modal migration. Webhook handler + cleanup worker call
``classify_error(raw_error)`` on tool failures and stash the category on
``jobs.error_category`` so the frontend can show a meaningful message and a
tailored action button (Retry pilot / Resume from session / Contact support).

Categories:
    - ``INFRA``        — GPU container couldn't start, image pull failed,
                         network issue reaching the webhook. Retry-safe:
                         orchestrator auto-retries once before escalating.
    - ``TIMEOUT``      — Session exceeded its budget or was killed for being
                         stale. For chunked full-design jobs: suggest a
                         resume from the last checkpoint.
    - ``BAD_INPUT``    — Target PDB invalid, residue numbering broken,
                         chain missing, hotspots reference non-existent
                         residues. NOT retry-safe: user must edit their spec.
    - ``TOOL_INTERNAL``— Tool crashed mid-run with a stack trace or an OOM.
                         Retry-safe sometimes (OOM on a larger GPU SKU works)
                         but usually not. Show "contact support" with the
                         stack trace trimmed to the top 5 frames.
    - ``USER_CANCELLED``— Explicit cancel from the UI. Not an error; not
                         retryable; shown as "cancelled" not "failed" in UI.
"""

from __future__ import annotations

import re

# ---- Category string constants (so callers don't typo the value) ------------

INFRA = "INFRA"
TIMEOUT = "TIMEOUT"
BAD_INPUT = "BAD_INPUT"
TOOL_INTERNAL = "TOOL_INTERNAL"
USER_CANCELLED = "USER_CANCELLED"

ALL_CATEGORIES = (INFRA, TIMEOUT, BAD_INPUT, TOOL_INTERNAL, USER_CANCELLED)


# ---- Classification rules ---------------------------------------------------
#
# Each rule is (category, compiled-regex). Order matters — first match wins.
# Patterns are case-insensitive, matched against the concatenation of the
# error message + the last few lines of stderr.

_RULES: list[tuple[str, re.Pattern[str]]] = [
    # User cancel — matched first so we never misclassify it as TIMEOUT.
    (USER_CANCELLED, re.compile(r"user[-_\s]?cancell?ed|job cancell?ed by user", re.IGNORECASE)),

    # BAD_INPUT — user mistakes that re-running won't fix.
    (BAD_INPUT, re.compile(
        r"(no such chain|chain [a-z] not found in pdb"
        r"|hotspot residue \d+ out of range"
        r"|pdb parse (error|failed)"
        r"|invalid residue numbering"
        r"|target pdb has (no|missing) residues)",
        re.IGNORECASE,
    )),

    # TIMEOUT — session deadline, function timeout, modal cancelled us.
    (TIMEOUT, re.compile(
        r"(session deadline exceeded"
        r"|function call timed out"
        r"|modal.*timeout"
        r"|job timed out"
        r"|stale heartbeat)",
        re.IGNORECASE,
    )),

    # INFRA — container boot, network, image pull, presigned URL 403.
    (INFRA, re.compile(
        r"(imagepullbackoff|failed to pull image"
        r"|container failed to start"
        r"|cuda driver (init|initialization) failed"
        r"|webhook.*(connection refused|name or service not known)"
        r"|presigned url returned 40\d"
        r"|no space left on device"
        r"|gpu allocation failed)",
        re.IGNORECASE,
    )),

    # TOOL_INTERNAL — catch-all for Python tracebacks + CUDA OOM.
    (TOOL_INTERNAL, re.compile(
        r"(traceback \(most recent call last\)"
        r"|cuda out of memory"
        r"|runtimeerror"
        r"|assertionerror"
        r"|segmentation fault)",
        re.IGNORECASE,
    )),
]


def classify_error(raw_error: str | None) -> str:
    """Classify a raw error string into one of the ``ALL_CATEGORIES``.

    Args:
        raw_error: Free-text error from the tool, webhook payload, or log tail.
            May be None/empty, in which case ``INFRA`` is returned as a safe
            default (no tool output = container never really started).

    Returns:
        One of the category constants. Default ``INFRA`` for unrecognized input.
    """
    if not raw_error:
        return INFRA
    for category, pattern in _RULES:
        if pattern.search(raw_error):
            return category
    return TOOL_INTERNAL  # Anything unclassified is tool-internal until proven otherwise.


def is_retry_safe(category: str) -> bool:
    """Return True if the classifier category is safe to auto-retry.

    Used by the session orchestrator to decide whether to auto-retry a
    failed session once before surfacing the failure to the user.
    """
    return category in (INFRA, TIMEOUT)


def user_action_label(category: str) -> str:
    """Return the imperative user-facing action label for a category.

    Drives the button label shown on the job progress/result page for failed
    jobs. Matches the copy in the migration plan's User Confidence Model.
    """
    return {
        INFRA: "Retry — we'll try again automatically",
        TIMEOUT: "Resume from last session",
        BAD_INPUT: "Edit target & retry",
        TOOL_INTERNAL: "Contact support",
        USER_CANCELLED: "Resubmit",
    }.get(category, "Contact support")
