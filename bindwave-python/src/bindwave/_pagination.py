"""Cursor auto-paginator for the bindwave SDK (Phase 13, Plan 13-05).

``iter_all`` / ``iter_all_async`` walk a cursor-paginated ``list`` endpoint until
``next_cursor`` is ``None``, yielding each item lazily. They take a *resource*
(``client.jobs``) rather than the client, and call ``resource.list(cursor=...)``.

DoS note (T-13-06, accept): iteration is unbounded — the caller decides when to
stop. To bound it, wrap in ``itertools.islice(iter_all(jobs), 1000)``.
"""

from __future__ import annotations

from typing import AsyncIterator, Iterator

from bindwave.types.job import Job


def iter_all(jobs_resource, **filters) -> Iterator[Job]:
    """Yield every :class:`Job` across all pages (sync, lazy generator).

    Walks the cursor: lists a page, yields its items, advances to
    ``page.next_cursor``, and stops when the cursor is ``None``. ``filters``
    (e.g. ``status="complete"``) are forwarded to ``list`` on every page.

    Bound iteration with ``itertools.islice(iter_all(jobs), N)`` (T-13-06).
    """
    cursor = None
    while True:
        page = jobs_resource.list(cursor=cursor, **filters)
        for item in page.data:
            yield item
        cursor = page.next_cursor
        if cursor is None:
            return


async def iter_all_async(jobs_resource, **filters) -> AsyncIterator[Job]:
    """Async variant of :func:`iter_all` for :class:`~bindwave.AsyncClient`.

    ``jobs_resource.list`` is awaited on each page; items are yielded lazily.
    """
    cursor = None
    while True:
        page = await jobs_resource.list(cursor=cursor, **filters)
        for item in page.data:
            yield item
        cursor = page.next_cursor
        if cursor is None:
            return
