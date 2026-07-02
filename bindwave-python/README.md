# bindwave

Official Python SDK for the [Bindwave Public API](https://ranomics.com/api).

## Install

```bash
pip install bindwave
```

## Quickstart

Authenticate with an API key (create one in the web app under Settings → API
Keys). Pass it directly or set the `BINDWAVE_API_KEY` environment variable.

```python
import time
from bindwave import Client, JobStatus

client = Client(api_key="bw_live_...")  # or set BINDWAVE_API_KEY

job = client.jobs.submit(tool="rfdiffusion", parameters={"target_chain": "A"})
print(job.id, job.status)

while job.status not in (JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED):
    time.sleep(30)
    job = client.jobs.get(job.id)

print(f"{len(job.candidates)} candidates")
```

The client auto-generates an `Idempotency-Key` per submit, retries `429`/`5xx`
with exponential backoff (honoring `Retry-After`), and never sends an
`X-Org-Id` header — the organization is resolved server-side from the key.

### Managing keys

```python
for key in client.api_keys.list():
    print(key.prefix, key.name)

client.api_keys.revoke("key-id")
```

Errors raise a typed exception: `BindwaveAuthError` (401),
`BindwaveRateLimitError` (429), `BindwaveValidationError` (400/422),
`BindwaveJobError` (other 4xx), `BindwaveAPIError` (5xx).

See [`examples/submit_and_wait.py`](examples/submit_and_wait.py) for a runnable
script.

> **Note:** `AsyncClient` is a placeholder in 0.1.0 (ships in a later release);
> calling it raises `NotImplementedError`.

## License

MIT — see [LICENSE](LICENSE).
