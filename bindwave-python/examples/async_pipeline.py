"""AsyncClient end-to-end: submit, wait until done, download results.

Reads BINDWAVE_API_KEY from the environment. The AsyncClient mirrors the sync
Client; await_until_complete polls without a hand-rolled loop, and
download_results_async writes each candidate's PDB to the working directory.
"""

import asyncio

from bindwave import AsyncClient


async def main() -> None:
    async with AsyncClient() as client:
        job = await client.jobs.submit(
            tool="rfdiffusion",
            parameters={"target_pdb": "1abc", "num_designs": 8},
        )
        await job.await_until_complete()
        paths = await job.download_results_async()
        print(paths)


asyncio.run(main())
