# bindwave

Official Python SDK for the [Bindwave Public API](https://ranomics.com/api).

## Install

```bash
pip install bindwave
```

## Quickstart

> **0.1.0 contract — implementation lands in Plan 13-04/13-05.** The public
> surface below is the frozen API shape; calling it in 0.1.0 raises
> `NotImplementedError`.

```python
from bindwave import Client

client = Client(api_key="bw_live_...")

job = client.jobs.submit(tool="rfdiffusion", parameters={...})
job.wait_until_complete()

results = job.download_results()
```

## License

MIT — see [LICENSE](LICENSE).
