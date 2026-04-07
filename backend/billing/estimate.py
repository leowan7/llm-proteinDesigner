"""GPU cost estimation for job dispatch.

Provides a cost range (low, high) in USD before a job is submitted,
accounting for tool-specific runtime ranges, design count scaling,
the base GPU price per second, and the platform markup percentage.

Runtime ranges are empirically derived from internal benchmarks on A100 80GB.
"""

from config import settings

# Expected runtime range in seconds for each tool on a single design.
# Tuple format: (low_seconds, high_seconds)
TOOL_RUNTIME_RANGES: dict[str, tuple[int, int]] = {
    "rfdiffusion": (600, 1800),    # 10–30 min
    "rfantibody": (600, 1800),     # 10–30 min
    "bindcraft": (1800, 5400),     # 30–90 min
    "boltzgen": (300, 900),        # 5–15 min
    "pxdesign": (600, 1800),      # 10–30 min (similar profile to RFdiffusion)
}


def estimate_cost_range(tool: str, num_designs: int = 1) -> tuple[float, float]:
    """Return a (low, high) cost estimate in USD for a job including markup.

    Design count scaling: runtime is multiplied by max(1, num_designs / 10).
    This reflects that batch sizes up to 10 run concurrently with minimal
    overhead; beyond 10, cost scales roughly linearly with design count.

    Args:
        tool: Tool name — one of "rfdiffusion", "rfantibody", "bindcraft", "boltzgen".
              Falls back to a conservative (600, 3600) range for unknown tools.
        num_designs: Number of designs requested. Defaults to 1.

    Returns:
        Tuple of (low_usd, high_usd) rounded to 2 decimal places.
    """
    low_sec, high_sec = TOOL_RUNTIME_RANGES.get(tool, (600, 3600))
    multiplier = max(1, num_designs / 10)  # scale with design count beyond batch of 10
    rate = settings.gpu_price_per_second
    markup = 1 + (settings.gpu_markup_percent / 100)
    return (
        round(low_sec * multiplier * rate * markup, 2),
        round(high_sec * multiplier * rate * markup, 2),
    )
