"""Abstract base class for tool-specific pipeline implementations.

Each design tool (RFdiffusion, BindCraft, etc.) has a concrete pipeline that
translates a generic JobSpec into tool-native configuration and parses tool
output back into standardized CandidateResult objects.

Phase 2 (Modal migration) adds two concepts:
- ``pilot_preset()`` — returns the clamped parameter set that a pilot run uses.
  Subclass implementations bound the pilot to a fast, cheap validation run.
- ``estimate_cost(job_spec, gpu_price_per_second)`` — returns ``(seconds, dollars)``
  so the frontend can show a cost+time preview before submit.
"""

from abc import ABC, abstractmethod

from jobs.models import CandidateResult


# ---- Pilot-tier default pricing ---------------------------------------------
#
# These are cheap, fast runs chosen to prove a tool's infra + the user's target
# is sensible. Each tool's pilot_preset() clamps to these values regardless of
# the caller's requested parameters. The goal is "is the system up?" — not
# "produce publishable binders".
#
# Estimated runtimes (minutes) are conservative averages used for the
# pre-submit ETA shown on the frontend. They are refined nightly from
# real job observations by ``backend/jobs/progress.py`` (Phase 5).

DEFAULT_PILOT_RUNTIME_MINUTES: dict[str, int] = {
    "rfdiffusion": 15,   # num_designs=10 on A10G ~ 10-15 min
    "rfantibody": 25,    # num_designs=5 on A100-40 ~ 20-30 min
    "bindcraft": 25,     # num_designs=2 on A100-80 ~ 20-30 min (spike: 16.3 min for 1)
    "boltzgen": 20,      # budget=5 on A100-40 ~ 15-20 min
    "pxdesign": 35,      # num_designs=10 on A100-80 ~ 30-40 min
}

# Approximate per-second GPU pricing in USD for each SKU the Modal apps use.
# Kept here (not in config) because these are provider-observed rates used only
# for frontend estimates. Exact billing uses settings.gpu_price_per_second.
GPU_PRICE_USD_PER_SECOND: dict[str, float] = {
    "A10G-24GB": 0.000305,    # ~$1.10/hr
    "A100-40GB": 0.000675,    # ~$2.43/hr
    "A100-80GB": 0.000975,    # ~$3.51/hr
}


class ToolPipeline(ABC):
    """Abstract base defining the per-tool config generation and result parsing contract.

    Subclasses must implement:
    - generate_config: Translate JobSpec dict into tool-native configuration.
    - parse_results: Normalize GPU-provider handler output into CandidateResult list.
    - execution_timeout_ms: Per-tool execution timeout in milliseconds.
    - pilot_preset: Clamped parameters a pilot run uses.
    - gpu_sku: Modal GPU SKU identifier (for cost estimation).
    """

    @abstractmethod
    def generate_config(self, job_spec: dict, target_local_path: str) -> dict:
        """Translate a JobSpec dict into tool-native configuration.

        Implementations that respect pilot presets must call ``apply_pilot_preset``
        or merge ``self.pilot_preset()`` into the effective parameters when
        ``job_spec.get("job_tier") == "pilot"``.

        Args:
            job_spec: Deserialized JobSpec dict from the database. May contain
                ``job_tier`` (``"pilot"`` or ``"full_design"``).
            target_local_path: Local filesystem path where the target PDB will
                be available inside the container.

        Returns:
            Tool-specific configuration dict.
        """

    @abstractmethod
    def parse_results(self, output: dict) -> list[CandidateResult]:
        """Normalize handler output into a list of CandidateResult objects.

        Args:
            output: The 'output' field from the webhook payload or status
                response. Structure is tool-dependent.

        Returns:
            List of CandidateResult objects sorted by rank (1-indexed).
        """

    @property
    @abstractmethod
    def execution_timeout_ms(self) -> int:
        """Per-tool execution timeout in milliseconds.

        For full-design jobs this is the single-session cap (Modal's 23hr max).
        For pilots this is still used as the Modal ``@app.function`` timeout,
        though actual pilot runtime is much shorter.
        """

    @abstractmethod
    def pilot_preset(self) -> dict:
        """Return the parameter-overrides dict a pilot run must use.

        The returned dict is *merged* with the caller's job_spec.parameters —
        keys present here clamp the parameter, keys absent leave the caller's
        value intact. Override in the config generator by calling
        ``merge_pilot_params(job_spec, self.pilot_preset())``.

        Returns:
            Dict of tool-specific parameter overrides for pilot tier.
        """

    @property
    def gpu_sku(self) -> str:
        """Modal GPU SKU identifier. Defaults to A100-40GB.

        Override in subclasses to claim a specific SKU. Used by
        ``estimate_cost()`` and for display in the frontend submit form.
        """
        return "A100-40GB"

    @property
    def tool_name(self) -> str:
        """Lowercase tool slug (e.g. ``"bindcraft"``). Derived from class name."""
        return self.__class__.__name__.replace("Pipeline", "").lower()

    def estimate_cost(self, job_spec: dict) -> tuple[int, float]:
        """Estimate (seconds, dollars) for a job.

        Pilot tier uses the baked-in ``DEFAULT_PILOT_RUNTIME_MINUTES`` table.
        Full-design tier uses ``job_spec.get("total_budget_hours", 4)`` as the
        upper bound — users are told "this job may run up to N hours at $X/hr".

        Args:
            job_spec: Deserialized JobSpec dict. May carry ``job_tier``
                and ``total_budget_hours``.

        Returns:
            Tuple of ``(seconds, dollars)``. Rounded to int seconds and to 2dp USD.
        """
        tier = job_spec.get("job_tier", "pilot")

        if tier == "pilot":
            minutes = DEFAULT_PILOT_RUNTIME_MINUTES.get(self.tool_name, 30)
            seconds = minutes * 60
        else:  # full_design
            budget_hours = max(1, int(job_spec.get("total_budget_hours", 4)))
            seconds = budget_hours * 3600

        price_per_second = GPU_PRICE_USD_PER_SECOND.get(self.gpu_sku, 0.000675)
        dollars = round(seconds * price_per_second, 2)
        return seconds, dollars

    @property
    def presigned_url_expiry_seconds(self) -> int:
        """Expiry for presigned S3 URLs passed to the container.

        Defaults to 1.5x the execution timeout (converted to seconds), with
        a minimum of 7200 seconds (2 hours). Override in subclass if the tool
        requires a longer window (e.g. BindCraft).
        """
        computed = int(self.execution_timeout_ms * 1.5 / 1000)
        return max(7200, computed)


def merge_pilot_params(job_spec: dict, preset: dict) -> dict:
    """Merge a pilot preset into the caller's job_spec parameters.

    If ``job_spec.get("job_tier") != "pilot"``, the spec is returned unchanged.
    For pilot tier, preset keys *override* user-supplied values — the user
    cannot request more designs than the pilot allows. For dict-valued keys
    (e.g. ``binder_length``), nested keys are merged so the user can still
    narrow a range within the pilot bounds.

    Args:
        job_spec: The incoming JobSpec dict (not mutated).
        preset: The per-tool pilot preset dict from ``ToolPipeline.pilot_preset()``.

    Returns:
        A new dict with the user's ``parameters`` section clamped by the preset.
        Other top-level keys (tool, target, hotspot_residues, etc.) pass through.
    """
    if job_spec.get("job_tier") != "pilot":
        return job_spec

    params = dict(job_spec.get("parameters", {}))
    for key, preset_value in preset.items():
        if isinstance(preset_value, dict) and isinstance(params.get(key), dict):
            merged = dict(params[key])
            merged.update(preset_value)  # preset wins on conflicts
            params[key] = merged
        else:
            params[key] = preset_value

    merged_spec = dict(job_spec)
    merged_spec["parameters"] = params
    return merged_spec
