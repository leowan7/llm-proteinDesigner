#!/usr/bin/env python
"""Reap raw output archives off the ranomics-*-raw Modal Volumes on an age policy.

Ships WITH the capture patch, deliberately. The archives it prunes are GB-scale, one per job, on
Volumes with no TTL and no cap, billed per GB-month. Without this, capture quietly becomes an
unbounded cost with no signal until the invoice.

This is the reaper migration 0021 promised in a comment and never built. Do not repeat that.

Retention defaults to 90 days to match templates/legal/terms.html:64-68, which already tells
users artifacts are kept for ninety days - rather than inventing a third window alongside the 30 in
0021 and the 90 in the Terms.

Dry-run unless --apply. Deleting an archive is irreversible and re-creating one costs a GPU run.

Usage:
    python scripts/prune_raw_volumes.py                      # dry run, 90d
    python scripts/prune_raw_volumes.py --apply
    python scripts/prune_raw_volumes.py --apply --days 30 --volume ranomics-boltzgen-raw
"""
from __future__ import annotations

import argparse
import sys
import time

# One raw Volume per prod tool, created by the capture patch as f"ranomics-{_TOOL}-raw".
TOOLS = [
    "bindcraft", "boltzgen", "rfdiffusion", "rfantibody", "pxdesign",
    "mpnn", "af2", "colabfold", "esmfold", "boltz2", "iggm", "proteina",
    "esmfold2-design",
]
DEFAULT_DAYS = 90


def _entries(vol):
    """Yield (path, mtime, size) for every file at the volume root. Tars are written flat."""
    for e in vol.iterdir("/"):
        mtime = getattr(e, "mtime", None)
        size = getattr(e, "size", 0) or 0
        if mtime is None:
            continue
        yield e.path, float(mtime), int(size)


def prune(name: str, cutoff: float, apply: bool) -> tuple[int, int]:
    import modal
    try:
        vol = modal.Volume.from_name(name)
    except Exception as exc:  # noqa: BLE001 - a missing volume is not an error here
        print(f"  {name}: unavailable ({type(exc).__name__}); skipping")
        return 0, 0

    n = freed = 0
    try:
        entries = list(_entries(vol))
    except Exception as exc:  # noqa: BLE001
        print(f"  {name}: cannot list ({type(exc).__name__}: {exc}); skipping")
        return 0, 0

    for path, mtime, size in entries:
        if mtime >= cutoff:
            continue
        age = (time.time() - mtime) / 86400.0
        print(f"  {name}: {path}  {size/1e6:8.1f} MB  {age:5.1f}d old"
              f"{'' if apply else '   [dry run]'}")
        n += 1
        freed += size
        if apply:
            try:
                vol.remove_file(path)
            except Exception as exc:  # noqa: BLE001 - one bad delete must not stop the sweep
                print(f"  {name}: FAILED to remove {path}: {exc}")
                n -= 1
                freed -= size
    if apply and n:
        try:
            vol.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: commit warning: {exc}")
    return n, freed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"retention window in days (default {DEFAULT_DAYS}, matching the Terms)")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete. Without this, nothing is removed.")
    ap.add_argument("--volume", action="append",
                    help="limit to one volume (repeatable). Default: every ranomics-*-raw.")
    a = ap.parse_args()

    try:
        import modal  # noqa: F401
    except ImportError:
        print("modal is not importable; run this where the Modal CLI is configured.")
        return 2

    names = a.volume or [f"ranomics-{t}-raw" for t in TOOLS]
    cutoff = time.time() - a.days * 86400
    mode = "DELETING" if a.apply else "DRY RUN (nothing will be removed; pass --apply)"
    print(f"Raw archive prune - {mode}; retention {a.days}d across {len(names)} volume(s)\n")

    total_n = total_freed = 0
    for name in names:
        n, freed = prune(name, cutoff, a.apply)
        total_n += n
        total_freed += freed

    verb = "removed" if a.apply else "would remove"
    print(f"\n{verb} {total_n} archive(s), {total_freed/1e9:.2f} GB")
    if not a.apply and total_n:
        print("Re-run with --apply to actually delete. This cannot be undone: re-creating one of "
              "these archives means paying for the GPU run again.")
    return 0
