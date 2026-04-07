"""arq worker entry point.

Run with:
    arq worker.main.WorkerSettings

The worker processes job execution tasks queued by the dispatch module.
One job at a time per worker instance (max_jobs=1) per design decision —
GPU jobs are long-running and memory-intensive; parallelism handled at the
infrastructure level by running multiple worker containers.
"""

from arq.connections import RedisSettings
from arq.cron import cron

from config import settings
from worker.cleanup import cleanup_orphan_pods, detect_stale_jobs
from worker.tasks import run_job


class WorkerSettings:
    """arq worker configuration class.

    arq discovers this class by name when started with:
        arq worker.main.WorkerSettings
    """

    functions = [run_job]

    # Run orphan pod cleanup every 10 minutes.
    # Run stale job detection every 10 minutes (offset by 2 min to avoid overlap).
    cron_jobs = [
        cron(cleanup_orphan_pods, minute={0, 10, 20, 30, 40, 50}),
        cron(detect_stale_jobs, minute={2, 12, 22, 32, 42, 52}),
    ]

    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 1  # One GPU job at a time per worker — design decision
