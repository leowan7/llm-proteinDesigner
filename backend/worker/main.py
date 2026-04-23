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
from jobs.progress import refresh_live_stats
from worker.cleanup import check_daily_gpu_spend, cleanup_orphan_pods, detect_stale_jobs
from worker.deletion_cron import process_pending_deletions
from worker.retention_cron import retention_cron
from worker.session_orchestrator import resume_session
from worker.tasks import run_job


class WorkerSettings:
    """arq worker configuration class.

    arq discovers this class by name when started with:
        arq worker.main.WorkerSettings
    """

    # Registered tasks:
    #   run_job          — initial job dispatch (Phase 1)
    #   resume_session   — spawn session N+1 of a chunked full-design job (Phase 6)
    functions = [run_job, resume_session]

    # Cron jobs:
    #   cleanup_orphan_pods   — every 10 min; kills orphaned Modal function calls
    #                            or RunPod pods (Phase 7 budget-aware).
    #   detect_stale_jobs     — every 10 min (offset 2 min) marks heartbeat-stale jobs failed.
    #   check_daily_gpu_spend     — twice daily, alerts on runaway GPU costs.
    #   refresh_live_stats        — daily; refreshes per-tool ETA averages (Phase 5).
    #   process_pending_deletions — daily at 03:15 UTC; hard-deletes users past
    #                                the 30-day GDPR Art. 17 grace period (Plan 10-04).
    #   retention_cron            — daily at 04:45 UTC; warns at T-7 days and
    #                                hard-deletes job storage at retention window
    #                                expiry per data_retention_days (Plan 10-05).
    #                                Offset from refresh_live_stats (04:30) to
    #                                avoid same-minute contention.
    cron_jobs = [
        cron(cleanup_orphan_pods, minute={0, 10, 20, 30, 40, 50}),
        cron(detect_stale_jobs, minute={2, 12, 22, 32, 42, 52}),
        cron(check_daily_gpu_spend, hour={8, 20}, minute=0),
        cron(refresh_live_stats, hour=4, minute=30),
        cron(process_pending_deletions, hour=3, minute=15),
        cron(retention_cron, hour=4, minute=45),
    ]

    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 1  # One GPU job at a time per worker — design decision
