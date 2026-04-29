"""One-off: invoke kendrew-pxdesign-prod run_tool via Modal Function SDK.

Workaround for `modal run --payload` CLI rejecting `dict` type annotations.
Usage: python scripts/run_pxdesign_verify.py
"""

import json
import os
import sys
import time

import modal

EPOCH = int(time.time())

PAYLOAD = {
    "tier": "mini_pilot",
    "job_id": f"mp-final-{EPOCH}",
    "job_token": "token",
    "webhook_url": "https://example.com/nope",
    "job_spec": {
        "target_chain": "A",
        "hotspot_residues": [37, 39, 98, 106],
        "parameters": {},
    },
    "input_pdb_url": "",
}

print(f"Invoking kendrew-pxdesign-prod::run_tool with tier=mini_pilot N=1")
print(f"job_id={PAYLOAD['job_id']}", flush=True)

run_tool = modal.Function.from_name("kendrew-pxdesign-prod", "run_tool")

t0 = time.time()
result = run_tool.remote(PAYLOAD)
elapsed = time.time() - t0

print(f"\n=== Result (elapsed {elapsed:.1f}s) ===")
print(json.dumps(result, indent=2, default=str)[:4000])

smoke_result = result.get("smoke_result") if isinstance(result, dict) else None
if smoke_result:
    print(f"\n=== smoke_result summary ===")
    print(f"status: {smoke_result.get('status')}")
    candidates = smoke_result.get("output", {}).get("candidates", [])
    print(f"candidates: {len(candidates)}")
    for c in candidates:
        scores = c.get("scores", {})
        pdb_len = len(c.get("pdb_content_b64", ""))
        print(
            f"  rank={c.get('rank')} pdb_b64_len={pdb_len} "
            f"ipTM={scores.get('ipTM')} pLDDT={scores.get('pLDDT')} "
            f"pAE={scores.get('pAE')} filter={scores.get('filter_status')}"
        )
    sys.exit(0 if smoke_result.get("status") == "COMPLETED" else 1)
else:
    print("No smoke_result in return; full result printed above.")
    sys.exit(2)
