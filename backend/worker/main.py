"""arq worker entry point.

Run with:
    arq worker.main.WorkerSettings

The worker processes job execution tasks queued by the dispatch module.
One job at a time per worker instance (max_jobs=1) per design decision —
GPU jobs are long-running and memory-intensive; parallelism handled at the
infrastructure level by running multiple worker containers.
"""

from arq.connections import RedisSettings

from config import settings
from worker.tasks import run_job


class WorkerSettings:
    """arq worker configuration class.

    arq discovers this class by name when started with:
        arq worker.main.WorkerSettings
    """

    functions = [run_job]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 1  # One GPU job at a time per worker — design decision
