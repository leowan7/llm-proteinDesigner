"""Walk every completed job with iter_all — no cursor bookkeeping.

Reads BINDWAVE_API_KEY from the environment. iter_all is a lazy generator: it
fetches one page at a time and stops when the cursor runs out.
"""

from bindwave import Client

client = Client()
for job in client.jobs.iter_all(status="complete"):
    print(f"{job.id} {job.tool} candidates={len(job.candidates)}")
